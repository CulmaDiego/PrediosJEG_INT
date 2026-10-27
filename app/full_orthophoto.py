"""Complete municipal imagery for the independent office installation."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor

from pyproj import Transformer
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from .gis import WGS84_TO_COLOMBIA, qgis_environment, qgis_tool, tile_xy
from .models import OrthophotoSource, TileWindow, utcnow
from .tile_rendering import DynamicTileRenderer, native_max_zoom, tile_cache_path


def read_manifest(settings, relative_dir):
    base = tile_cache_path(settings, relative_dir, 0, 0, 0).parents[2]
    return json.loads((base / 'sources.json').read_text(encoding='utf-8'))


def coverage_range(bounds, zoom):
    south, west = bounds[0]
    north, east = bounds[1]
    left, top = tile_xy(west, north, zoom)
    right, bottom = tile_xy(east, south, zoom)
    return left, top, right, bottom

def coverage_tiles(bounds, zoom):
    left, top, right, bottom = coverage_range(bounds, zoom)
    return [(zoom, x, y) for x in range(left, right + 1) for y in range(top, bottom + 1)]

def coverage_count(bounds, zoom):
    left, top, right, bottom = coverage_range(bounds, zoom)
    return (right - left + 1) * (bottom - top + 1)


def full_tile_path(settings, relative_dir, zoom, x, y):
    manifest = read_manifest(settings, relative_dir)
    key = manifest['cache_key']
    if len(key) != 64 or any(char not in '0123456789abcdef' for char in key):
        raise ValueError('Identificador de caché inválido')
    return tile_cache_path(settings, f'_full_cache/{key}', zoom, x, y)


def tile_in_coverage(settings, relative_dir, zoom, x, y):
    from .tile_rendering import tile_bounds_web_mercator
    west, south, east, north = tile_bounds_web_mercator(zoom, x, y)
    projected = Transformer.from_crs(4326, 3857, always_xy=True)
    bounds = read_manifest(settings, relative_dir)['bounds']
    xmin, ymin, xmax, ymax = projected.transform_bounds(bounds[0][1], bounds[0][0], bounds[1][1], bounds[1][0])
    return west < xmax and east > xmin and south < ymax and north > ymin


def prepare_full_window(db, settings, job, person):
    # Import here to keep the legacy preparation and its tests independent.
    import subprocess
    from .worker import _clear_superseded_tile_failures, _resolution_metres

    sources = db.scalars(select(OrthophotoSource).where(
        OrthophotoSource.municipality_id == person.municipality_id,
        OrthophotoSource.status == 'LISTO',
    )).all()
    valid = []
    centered = []
    cx, cy = WGS84_TO_COLOMBIA.transform(person.reference_lon, person.reference_lat)
    for source in sources:
        if not source.bounds_json or not source.crs:
            continue
        source_bounds = json.loads(source.bounds_json)
        metric = Transformer.from_crs(source.crs, 9377, always_xy=True).transform_bounds(*source_bounds, densify_pts=21)
        geographic = Transformer.from_crs(source.crs, 4326, always_xy=True).transform_bounds(*source_bounds, densify_pts=21)
        if not all(math.isfinite(value) for value in geographic):
            raise RuntimeError('Extensión de ortofoto inválida')
        valid.append((source, geographic))
        if metric[0] <= cx <= metric[2] and metric[1] <= cy <= metric[3]:
            centered.append(source)
    if not centered:
        raise RuntimeError('Ninguna ortofoto asignada cubre el punto de referencia')
    def resolution(source):
        return _resolution_metres(source, person.reference_lon, person.reference_lat)
    primary = min(centered, key=lambda source: (resolution(source), source.id))
    ordered = sorted((source for source, _ in valid), key=lambda source: (resolution(source), -source.id), reverse=True)
    bounds = [[min(b[1] for _, b in valid), min(b[0] for _, b in valid)],
              [max(b[3] for _, b in valid), max(b[2] for _, b in valid)]]
    # Resolution and extent belong to the entire source set, regardless of center.
    max_zoom = max(native_max_zoom(person.reference_lat, resolution(source), settings.tile_max_zoom) for source in ordered)
    min_zoom = min(settings.tile_min_zoom, max_zoom)
    while min_zoom > 0 and coverage_count(bounds, min_zoom) > 4:
        min_zoom -= 1
    # Keep enough overview levels to fit the entire image on small screens.
    min_zoom = max(0, min_zoom - 2)
    relative_dir = f'person_{person.id}_{uuid.uuid4().hex[:12]}'
    base = settings.data_dir / 'tiles' / relative_dir
    base.mkdir(parents=True)
    previous = db.get(TileWindow, person.id)
    previous_dir = previous.relative_dir if previous else None
    fingerprints, names = [], []
    try:
        for source in ordered:
            path = (settings.orthophoto_root / source.relative_path).resolve()
            path.relative_to(settings.orthophoto_root.resolve())
            stat = path.stat()
            fingerprints.append([str(path), stat.st_size, stat.st_mtime_ns, source.crs])
            target = base / ('source.vrt' if source.id == primary.id else f'source_{source.id}.vrt')
            result = subprocess.run([str(qgis_tool(settings, 'gdal_translate')), '-of', 'VRT', '-b', '1', '-b', '2', '-b', '3', str(path), str(target)],
                                    capture_output=True, text=True, timeout=120, env=qgis_environment(settings))
            if result.returncode or not target.is_file():
                raise RuntimeError(f'gdal_translate: {result.stderr or result.stdout}')
            names.append(target.name)
        key = hashlib.sha256(json.dumps(['full-v1', fingerprints], sort_keys=True).encode()).hexdigest()
        manifest = {'mode': 'FULL', 'sources': names, 'bounds': bounds, 'cache_key': key}
        (base / 'sources.json').write_text(json.dumps(manifest), encoding='utf-8')
        tiles = coverage_tiles(bounds, min_zoom)
        # Full overview first, then a small neighborhood at every detail level.
        for zoom in range(min_zoom + 1, max_zoom + 1):
            x, y = tile_xy(person.reference_lon, person.reference_lat, zoom)
            tiles.extend((zoom, tx, ty) for tx in (x, min(x + 1, 2**zoom - 1)) for ty in (y, min(y + 1, 2**zoom - 1)))
        tiles = [coords for coords in dict.fromkeys(tiles) if tile_in_coverage(settings, relative_dir, *coords)]
        renderer = DynamicTileRenderer(settings)
        def render(coords):
            z, x, y = coords
            renderer.render(base / 'source.vrt', full_tile_path(settings, relative_dir, z, x, y), z, x, y)
        try:
            with ThreadPoolExecutor(max_workers=settings.dynamic_tile_workers) as executor:
                for index, _ in enumerate(executor.map(render, tiles), 1):
                    job.progress = 10 + int(80 * index / len(tiles))
                    db.commit()
        finally:
            renderer.close()
        (base / 'preload.json').write_text(json.dumps({'version': 1, 'tiles': tiles}), encoding='utf-8')
        values = dict(source_id=primary.id, relative_dir=relative_dir, min_zoom=min_zoom, max_zoom=max_zoom,
                      radius_m=settings.window_radius_m, render_mode='FULL', status='LISTO', error=None, updated_at=utcnow())
        db.execute(insert(TileWindow).values(person_id=person.id, **values).on_conflict_do_update(index_elements=[TileWindow.person_id], set_=values))
        person.orthophoto_status, person.orthophoto_error = 'LISTO', None
        _clear_superseded_tile_failures(db, person, job.id)
        db.commit()
    except Exception:
        shutil.rmtree(base, ignore_errors=True)
        raise
    if previous_dir and previous_dir != relative_dir:
        old = (settings.data_dir / 'tiles' / previous_dir).resolve()
        old.relative_to((settings.data_dir / 'tiles').resolve())
        # Shared imagery is deliberately retained across regeneration/deletion.
        if not previous_dir.startswith('_full_cache/'):
            shutil.rmtree(old, ignore_errors=True)
