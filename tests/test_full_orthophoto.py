from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.full_orthophoto import full_tile_path, read_manifest
from app.gis import metres_bounds, qgis_environment, tile_xy, validate_polygon
from app.models import Job, OrthophotoSource, Person, TileWindow
from app.security import public_csrf
from app.worker import prepare_window


def test_internal_polygon_has_no_distance_limit_but_stays_valid():
    geometry = {'type': 'Polygon', 'coordinates': [[[-74, 6], [-73.99, 6], [-73.99, 6.01], [-74, 6]]]}
    with pytest.raises(ValueError, match='máximo'):
        validate_polygon(geometry, -75, 6, 2000)
    assert validate_polygon(geometry, -75, 6, None)[1] > 0
    with pytest.raises(ValueError):
        validate_polygon({'type': 'Point', 'coordinates': [-74, 6]}, -75, 6, None)


def test_full_gdal_coverage_cache_and_remote_submission(test_app):
    app, client, token, person_id = test_app
    settings = app.state.settings
    settings.full_orthophoto = True
    settings.qgis_root = Path('C:/Program Files/QGIS 3.44.13')
    python = settings.qgis_root / 'apps/Python312/python.exe'
    if not python.is_file():
        pytest.skip('La integración ráster requiere QGIS en Windows')
    env = qgis_environment(settings)
    generator = '''
from osgeo import gdal,osr
from pathlib import Path
import sys
gdal.UseExceptions(); srs=osr.SpatialReference(); srs.ImportFromEPSG(9377)
root=Path(sys.argv[1]); cx,cy=map(float,sys.argv[2:4])
for name,dx,value in [('center',0,60),('remote',20000,200)]:
 d=gdal.GetDriverByName('GTiff').Create(str(root/(name+'.tif')),200,200,3,gdal.GDT_Byte)
 d.SetProjection(srs.ExportToWkt()); d.SetGeoTransform((cx+dx-2000,20,0,cy+2000,0,-20))
 for i in range(1,4): d.GetRasterBand(i).Fill(value)
 d=None
'''
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        lon, lat = person.reference_lon, person.reference_lat
        cx, cy, _, _ = metres_bounds(lon, lat, 0)
        subprocess.run([str(python), '-c', generator, str(settings.orthophoto_root), str(cx), str(cy)], env=env, check=True, capture_output=True)
        for name, dx in [('center', 0), ('remote', 20000)]:
            db.add(OrthophotoSource(relative_path=f'{name}.tif', municipality_id=person.municipality_id,
                                   crs='EPSG:9377', resolution_x=20, resolution_y=20, status='LISTO',
                                   bounds_json=json.dumps([cx + dx - 2000, cy - 2000, cx + dx + 2000, cy + 2000])))
        job = Job(job_type='PREPARE_WINDOW', payload_json=json.dumps({'person_id': person_id}), status='EJECUTANDO')
        db.add(job)
        db.commit()
        prepare_window(db, settings, job, person_id, 100)
        db.expire_all()
        window = db.get(TileWindow, person_id)
        assert window.render_mode == 'FULL'
        first_dir, zoom = window.relative_dir, window.max_zoom
        first_manifest = read_manifest(settings, first_dir)
        assert len(first_manifest['sources']) == 2
        assert not (settings.data_dir / 'tiles' / first_dir / 'cutline.geojson').exists()
        from pyproj import Transformer
        remote_lon, remote_lat = Transformer.from_crs(9377, 4326, always_xy=True).transform(cx + 20000, cy)
        x, y = tile_xy(remote_lon, remote_lat, zoom)
        url = f'/p/{token}/tiles/{zoom}/{x}/{y}.png'
        response = client.get(url)
        assert response.status_code == 200
        tile = full_tile_path(settings, first_dir, zoom, x, y)
        inspector = 'from osgeo import gdal; import sys; d=gdal.Open(sys.argv[1]); print(max(d.GetRasterBand(1).ReadRaster())); print(max(d.GetRasterBand(4).ReadRaster()))'
        result = subprocess.run([str(python), '-c', inspector, str(tile)], env=env, check=True, capture_output=True, text=True)
        assert result.stdout.split() == ['200', '255']
        timestamp = tile.stat().st_mtime_ns
        assert client.get(url).status_code == 200
        assert tile.stat().st_mtime_ns == timestamp
        assert client.get(f'/p/invalid/tiles/{zoom}/{x}/{y}.png').status_code == 404
        assert client.get(f'/p/{token}/tiles/{zoom}/-1/{y}.png').status_code == 404
        prepare_window(db, settings, job, person_id, 50000)
        db.expire_all()
        second = db.get(TileWindow, person_id)
        assert read_manifest(settings, second.relative_dir)['cache_key'] == first_manifest['cache_key']
        assert full_tile_path(settings, second.relative_dir, zoom, x, y) == tile
        assert tile.stat().st_mtime_ns == timestamp
    page = client.get(f'/p/{token}')
    assert page.status_code == 200 and 'Ver ortofoto completa' in page.text
    assert 'Radio autorizado' not in page.text
    geometry = {'type': 'Polygon', 'coordinates': [[[remote_lon, remote_lat], [remote_lon + .001, remote_lat],
                 [remote_lon + .001, remote_lat + .001], [remote_lon, remote_lat]]]}
    assert client.post(f'/p/{token}/submit', json={'csrf': public_csrf(token, settings), 'geometry': geometry}).status_code == 200
