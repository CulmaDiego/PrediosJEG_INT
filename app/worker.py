from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import socket
import subprocess
import time
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert
from pyproj import Transformer
from shapely.geometry import Point, mapping
from shapely.ops import transform

from .catalog import find_municipality, raster_files, synchronize_municipalities
from .config import Settings
from .db import build_engine, build_session_factory
from .gis import WGS84_TO_COLOMBIA, bounds_intersect, inspect_raster, metres_bounds, qgis_environment, qgis_tool, tile_range_for_bounds, tile_xy
from .tile_rendering import DynamicTileRenderer, native_max_zoom
from .tile_preparation import warm_window
from .models import Job, OrthophotoSource, Person, TileConfiguration, TileWindow, utcnow


def _tile_radius(db: Session, settings: Settings) -> int:
    configuration = db.get(TileConfiguration, 1)
    return configuration.radius_m if configuration else settings.window_radius_m

def claim_job(db: Session, worker_id: str) -> Job | None:
    stale = utcnow() - timedelta(minutes=30)
    db.execute(update(Job).where(Job.status == "EJECUTANDO", Job.locked_at < stale).values(status="PENDIENTE", locked_by=None, locked_at=None, error="Reanudado después de un bloqueo vencido"))
    db.commit()
    candidate = db.scalar(select(Job).where(Job.status == "PENDIENTE").order_by(Job.created_at).limit(1))
    if not candidate:
        return None
    changed = db.execute(
        update(Job)
        .where(Job.id == candidate.id, Job.status == "PENDIENTE")
        .values(status="EJECUTANDO", locked_by=worker_id, locked_at=utcnow(), attempts=Job.attempts + 1, progress=1)
    ).rowcount
    db.commit()
    return db.get(Job, candidate.id) if changed == 1 else None


def _crs_label(metadata: dict) -> str | None:
    stac = metadata.get("stac", {})
    if stac.get("proj:epsg"):
        return f"EPSG:{stac['proj:epsg']}"
    wkt = metadata.get("coordinateSystem", {}).get("wkt", "")
    marker = 'ID["EPSG",'
    if marker in wkt:
        tail = wkt.rsplit(marker, 1)[1]
        return f"EPSG:{tail.split(']', 1)[0]}"
    return wkt.split("\n", 1)[0][:100] or None


def scan_catalog(db: Session, settings: Settings, job: Job) -> None:
    synchronize_municipalities(db, settings.orthophoto_root)
    db.flush()
    files = raster_files(settings.orthophoto_root)
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(settings.orthophoto_root).as_posix()
        source = db.scalar(select(OrthophotoSource).where(OrthophotoSource.relative_path == relative))
        if not source:
            source = OrthophotoSource(relative_path=relative, inferred_name=path.relative_to(settings.orthophoto_root).parts[0])
            db.add(source)
        if source.municipality_id is None and source.inferred_name:
            municipality = find_municipality(db, source.inferred_name)
            if municipality:
                source.municipality_id = municipality.id
        try:
            metadata = inspect_raster(settings, path)
            transform = metadata.get("geoTransform", [])
            corners = metadata.get("cornerCoordinates", {})
            ul, lr = corners.get("upperLeft"), corners.get("lowerRight")
            source.driver = metadata.get("driverShortName")
            source.width, source.height = metadata.get("size", [None, None])
            source.crs = _crs_label(metadata)
            source.resolution_x = abs(float(transform[1])) if len(transform) > 5 else None
            source.resolution_y = abs(float(transform[5])) if len(transform) > 5 else None
            source.bounds_json = json.dumps([min(ul[0], lr[0]), min(ul[1], lr[1]), max(ul[0], lr[0]), max(ul[1], lr[1])]) if ul and lr else None
            source.status = "LISTO"
            source.error = None
        except Exception as exc:
            source.status = "ERROR"
            source.error = str(exc)[:4000]
        source.scanned_at = utcnow()
        job.progress = int(index / max(len(files), 1) * 100)
        db.commit()


def _overview_min_zoom(person: Person, radius_m: int, settings: Settings) -> int:
    """El zoom inicial debe mostrar el predio grande con pocas teselas."""
    for zoom in range(settings.tile_min_zoom, 7, -1):
        min_x, min_y, max_x, max_y = tile_range_for_bounds(person.reference_lon, person.reference_lat, radius_m, zoom)
        if (max_x - min_x + 1) * (max_y - min_y + 1) <= 4:
            return zoom
    return 8


def _resolution_metres(source: OrthophotoSource, lon: float, lat: float) -> float:
    if not source.crs or not source.resolution_x or not source.resolution_y:
        return math.inf
    x, y = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True).transform(lon, lat)
    projected = Transformer.from_crs(source.crs, "EPSG:9377", always_xy=True)
    center = projected.transform(x, y)
    return max(math.dist(center, projected.transform(x + source.resolution_x, y)), math.dist(center, projected.transform(x, y + source.resolution_y)))


def _clear_superseded_tile_failures(db: Session, person: Person, current_job_id: int) -> None:
    """Close obsolete tile errors after a newer generation succeeds."""
    if person.status == "ERROR":
        person.status = "PENDIENTE"
    failed_jobs = db.scalars(
        select(Job).where(
            Job.job_type == "PREPARE_WINDOW",
            Job.status == "ERROR",
            Job.id != current_job_id,
        )
    ).all()
    for failed_job in failed_jobs:
        try:
            failed_person_id = int(json.loads(failed_job.payload_json).get("person_id"))
        except (AttributeError, TypeError, ValueError):
            continue
        if failed_person_id == person.id:
            failed_job.status = "SUPERADO"
            failed_job.progress = 100
            failed_job.error = None


def prepare_window(db: Session, settings: Settings, job: Job, person_id: int, radius_m: int) -> None:
    job_id = job.id
    person = db.get(Person, person_id)
    if not person or person.reference_lon is None or person.reference_lat is None:
        raise RuntimeError("La persona no existe o no tiene coordenadas")
    if settings.full_orthophoto:
        from .full_orthophoto import prepare_full_window
        return prepare_full_window(db, settings, job, person)
    window = db.get(TileWindow, person_id)
    previous_dir = window.relative_dir if window else None
    sources = db.scalars(
        select(
            OrthophotoSource,
        ).where(
            OrthophotoSource.municipality_id == person.municipality_id,
            OrthophotoSource.status == "LISTO",
            OrthophotoSource.enabled.is_(True),
        )
    ).all()
    requested_bounds = metres_bounds(person.reference_lon, person.reference_lat, radius_m)
    # Catalog bounds are expressed in each raster's CRS, not necessarily 9377.
    covered = []
    center_x, center_y = WGS84_TO_COLOMBIA.transform(person.reference_lon, person.reference_lat)
    for candidate in sources:
        if not candidate.bounds_json or not candidate.crs:
            continue
        projected = Transformer.from_crs(candidate.crs, "EPSG:9377", always_xy=True).transform_bounds(*json.loads(candidate.bounds_json), densify_pts=21)
        if bounds_intersect(list(projected), requested_bounds):
            covered.append((candidate, projected))
    def resolution(candidate):
        return _resolution_metres(candidate, person.reference_lon, person.reference_lat)
    centered = [candidate for candidate, bounds in covered if bounds[0] <= center_x <= bounds[2] and bounds[1] <= center_y <= bounds[3]]
    source = min(centered, key=lambda candidate: (resolution(candidate), candidate.id), default=None)
    if source is None:
        if window is None:
            window = TileWindow(person_id=person_id)
            db.add(window)
        window.status = "SIN_ORTOFOTO"
        window.error = "Ninguna ortofoto asignada cubre la coordenada de referencia"
        person.orthophoto_status = "SIN_ORTOFOTO"
        person.orthophoto_error = window.error
        db.commit()
        return
    source_id = source.id
    # Coarse coverage first; sharper originals overlay it without resampling a
    # municipal mosaic or producing a cropped copy of the raster.
    ordered = sorted((candidate for candidate, _ in covered), key=lambda candidate: (resolution(candidate), -candidate.id), reverse=True)
    relative_dir = f"person_{person.id}_{uuid.uuid4().hex[:12]}"
    output_dir = settings.data_dir / "tiles" / relative_dir
    output_dir.mkdir(parents=True, exist_ok=False)
    vrt_path = output_dir / "source.vrt"
    try:
        source_names = []
        for candidate in ordered:
            source_path = (settings.orthophoto_root / candidate.relative_path).resolve()
            source_path.relative_to(settings.orthophoto_root.resolve())
            target = vrt_path if candidate.id == source_id else output_dir / f"source_{candidate.id}.vrt"
            result = subprocess.run(
                [str(qgis_tool(settings, "gdal_translate")), "-of", "VRT", "-b", "1", "-b", "2", "-b", "3", str(source_path), str(target)],
                capture_output=True, text=True, timeout=120, env=qgis_environment(settings),
            )
            if result.returncode != 0 or not target.is_file():
                raise RuntimeError(f"gdal_translate: {result.stderr.strip() or result.stdout.strip()}")
            source_names.append(target.name)
        (output_dir / "sources.json").write_text(json.dumps({"sources": source_names, "center": [center_x, center_y], "radius_m": radius_m}), encoding="utf-8")
        # An inscribed circle keeps every visible pixel inside the authorized
        # metric radius, including tiles that straddle the boundary.
        circle = Point(center_x, center_y).buffer(radius_m, quad_segs=128)
        to_wgs84 = Transformer.from_crs("EPSG:9377", "EPSG:4326", always_xy=True)
        cutline = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": mapping(transform(to_wgs84.transform, circle))}]}
        (output_dir / "cutline.geojson").write_text(json.dumps(cutline), encoding="utf-8")
        max_zoom = native_max_zoom(person.reference_lat, resolution(source), settings.tile_max_zoom)
        min_zoom = min(_overview_min_zoom(person, radius_m, settings), max_zoom)
        x, y = tile_xy(person.reference_lon, person.reference_lat, max_zoom)
        renderer = DynamicTileRenderer(settings)
        try:
            renderer.render(vrt_path, output_dir / str(max_zoom) / str(x) / f"{y}.png", max_zoom, x, y)
            def progress(done, total):
                job.progress = 10 + int(80 * done / total)
                db.commit()
            warm_window(renderer, relative_dir, person.reference_lon, person.reference_lat, radius_m, min_zoom, max_zoom, progress)
        finally:
            renderer.close()
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    db.expire_all()
    person = db.get(Person, person_id)
    job = db.get(Job, job_id)
    if person is None or job is None:
        shutil.rmtree(output_dir, ignore_errors=True)
        return
    values = dict(source_id=source_id, relative_dir=relative_dir, min_zoom=min_zoom, max_zoom=max_zoom, radius_m=radius_m, render_mode="ORIGINAL", status="LISTO", error=None, updated_at=utcnow())
    # Atomic replacement also makes retries safe if two workers finish together.
    db.execute(insert(TileWindow).values(person_id=person_id, **values).on_conflict_do_update(index_elements=[TileWindow.person_id], set_=values))
    person.orthophoto_status = "LISTO"
    person.orthophoto_error = None
    _clear_superseded_tile_failures(db, person, job_id)
    job.progress = 95
    db.commit()
    if previous_dir and previous_dir != relative_dir:
        old = (settings.data_dir / "tiles" / previous_dir).resolve()
        tiles_root = (settings.data_dir / "tiles").resolve()
        try:
            old.relative_to(tiles_root)
        except ValueError:
            return
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)

def execute_job(db: Session, settings: Settings, job: Job) -> None:
    payload = json.loads(job.payload_json)
    if job.job_type == "SCAN_CATALOG":
        scan_catalog(db, settings, job)
    elif job.job_type == "PREPARE_WINDOW":
        radius_m = int(payload.get("radius_m", _tile_radius(db, settings)))
        if not 100 <= radius_m <= settings.max_tile_radius_m:
            raise RuntimeError("El radio de teselas solicitado no es válido")
        prepare_window(db, settings, job, int(payload["person_id"]), radius_m)
    else:
        raise RuntimeError(f"Tipo de trabajo desconocido: {job.job_type}")


def run(once: bool = False) -> int:
    from .full_orthophoto import SourceSelectionChanged
    settings = Settings.from_env()
    settings.ensure_directories()
    factory = build_session_factory(build_engine(settings.database_url))
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        with factory() as db:
            job = claim_job(db, worker_id)
            if job:
                job_id = job.id
                try:
                    execute_job(db, settings, job)
                    job = db.get(Job, job_id)
                    if job is None:
                        continue
                    job.status = "COMPLETADO"
                    job.progress = 100
                    job.error = None
                    db.commit()
                except SourceSelectionChanged:
                    db.rollback()
                    failed = db.get(Job, job_id)
                    if failed:
                        failed.status, failed.progress, failed.error = "PENDIENTE", 0, None
                        failed.locked_by = failed.locked_at = None
                        db.commit()
                except Exception as exc:
                    db.rollback()
                    failed = db.get(Job, job_id)
                    if failed is None:
                        continue
                    failed.status = "ERROR"
                    failed.error = str(exc)[:4000]
                    if failed.job_type == "PREPARE_WINDOW":
                        person_id = int(json.loads(failed.payload_json).get("person_id", 0))
                        person = db.get(Person, person_id)
                        window = db.get(TileWindow, person_id)
                        if person:
                            if not person.drawing_version:
                                person.status = "ERROR"
                            person.orthophoto_status = "ERROR"
                            person.orthophoto_error = failed.error
                        if window:
                            window.status = "ERROR"
                            window.error = failed.error
                    db.commit()
        if once:
            return 0
        time.sleep(2 if job else 5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.once))
