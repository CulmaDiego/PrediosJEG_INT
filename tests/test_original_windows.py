from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.gis import metres_bounds, qgis_environment, tile_xy
from app.models import Job, OrthophotoSource, Person, TileWindow
from app.tile_rendering import DynamicTileRenderer, native_max_zoom, tile_intersects_window
from app.worker import prepare_window
from app.cli import retry_failed_images
from app.tile_preparation import preload_tiles


def test_first_preparation_and_retry_replace_one_window_and_prefer_detail(test_app, monkeypatch):
    app, _, _, person_id = test_app
    commands = []

    def translate(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_text('<VRTDataset/>', encoding='utf-8')
        return SimpleNamespace(returncode=0, stderr='', stdout='')

    def render(self, vrt, tile, zoom, x, y):
        assert (vrt.parent / 'cutline.geojson').is_file()
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(b'\x89PNG\r\n\x1a\nverified')
        return tile

    monkeypatch.setattr('app.worker.subprocess.run', translate)
    monkeypatch.setattr('app.worker.qgis_tool', lambda settings, name: Path(name))
    monkeypatch.setattr('app.worker.qgis_environment', lambda settings: {})
    monkeypatch.setattr(DynamicTileRenderer, 'render', render)
    monkeypatch.setattr('app.worker.warm_window', lambda *args: 0)

    with app.state.Session() as db:
        person = db.get(Person, person_id)
        bounds = json.dumps(metres_bounds(person.reference_lon, person.reference_lat, 2000))
        coarse = OrthophotoSource(relative_path='rural.ecw', municipality_id=person.municipality_id, status='LISTO', crs='EPSG:9377', resolution_x=0.5, resolution_y=0.5, bounds_json=bounds)
        sharp = OrthophotoSource(relative_path='urban.tif', municipality_id=person.municipality_id, status='LISTO', crs='EPSG:9377', resolution_x=0.1, resolution_y=0.1, bounds_json=bounds)
        # Intersects the requested area but does not cover its reference point.
        wrong = OrthophotoSource(relative_path='neighbor.tif', municipality_id=person.municipality_id, status='LISTO', crs='EPSG:9377', resolution_x=0.05, resolution_y=0.05, bounds_json=json.dumps(metres_bounds(person.reference_lon + 0.01, person.reference_lat, 100)))
        job = Job(job_type='PREPARE_WINDOW', payload_json=json.dumps({'person_id': person_id}), status='EJECUTANDO')
        db.add_all([coarse, sharp, wrong, job])
        db.commit()

        prepare_window(db, app.state.settings, job, person_id, 2000)
        db.expire_all()
        window = db.get(TileWindow, person_id)
        first_directory = window.relative_dir
        assert window.source_id == sharp.id
        assert window.render_mode == 'ORIGINAL'
        assert window.max_zoom == 21
        assert window.status == 'LISTO'
        assert db.scalar(select(func.count()).select_from(TileWindow)) == 1
        manifest = json.loads((app.state.settings.data_dir / 'tiles' / first_directory / 'sources.json').read_text())['sources']
        assert manifest[0] == f'source_{coarse.id}.vrt'
        assert 'source.vrt' in manifest
        # Native sources stay unwarped and keep their full extent.
        assert all('-projwin' not in cmd and '-outsize' not in cmd and '-te' not in cmd for cmd in commands)

        prepare_window(db, app.state.settings, job, person_id, 350)
        db.expire_all()
        window = db.get(TileWindow, person_id)
        assert window.relative_dir != first_directory
        assert window.radius_m == 350
        assert db.scalar(select(func.count()).select_from(TileWindow)) == 1
        assert not (app.state.settings.data_dir / 'tiles' / first_directory).exists()


def test_native_zoom_matches_original_resolution():
    assert native_max_zoom(6.27, 0.2, 19) == 20
    assert native_max_zoom(6.27, 0.1, 19) == 21
    assert native_max_zoom(6.27, 0.5, 19) == 19
    assert native_max_zoom(6.27, float('inf'), 19) == 19


def test_recovery_queues_failed_images_once_and_preserves_drawings(test_app):
    app, _, _, person_id = test_app
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        person.orthophoto_status = 'ERROR'
        protected = Person(name='Respondió', phone='3009876543', municipality_id=person.municipality_id, reference_lon=-75.031, reference_lat=6.294, status='RESPONDIO', drawing_version=1, orthophoto_status='ERROR')
        db.add(protected)
        db.commit()
        protected_id = protected.id
    assert retry_failed_images(app.state.settings) == 0
    assert retry_failed_images(app.state.settings) == 0
    with app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.job_type == 'PREPARE_WINDOW')) == 1
        assert db.get(Person, person_id).orthophoto_status == 'PENDIENTE'
        assert db.get(Person, protected_id).drawing_version == 1
        assert db.get(Person, protected_id).status == 'RESPONDIO'


def test_original_public_window_rejects_tiles_outside_its_area(test_app, monkeypatch):
    app, client, token, person_id = test_app
    base = app.state.settings.data_dir / 'tiles' / 'original'
    base.mkdir(parents=True)
    (base / 'source.vrt').write_text('<VRTDataset/>')
    (base / 'sources.json').write_text('{}')
    (base / 'cutline.geojson').write_text('{}')
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        db.add(TileWindow(person_id=person_id, relative_dir='original', min_zoom=12, max_zoom=20, radius_m=350, render_mode='ORIGINAL', status='LISTO'))
        db.commit()
        x, y = tile_xy(person.reference_lon, person.reference_lat, 20)
    assert client.get(f'/p/{token}').status_code == 200
    monkeypatch.setattr(app.state.tile_renderer, 'render', lambda *args: pytest.fail('An unauthorized tile was rendered'))
    assert client.get(f'/p/{token}/tiles/20/{x + 100}/{y + 100}.png').status_code == 404
    assert client.get(f'/p/{token}/tiles/21/{x}/{y}.png').status_code == 404
    assert client.get(f'/p/invalid/tiles/20/{x}/{y}.png').status_code == 404
    (base / 'cutline.geojson').unlink()
    assert client.get(f'/p/{token}').status_code == 409
    assert client.get(f'/p/{token}/tiles/20/{x}/{y}.png').status_code == 503


def test_preload_manifest_and_cached_tiles_still_require_a_valid_link(test_app, monkeypatch):
    app, client, token, person_id = test_app
    base = app.state.settings.data_dir / 'tiles' / 'cached'
    base.mkdir(parents=True)
    for name in ('source.vrt', 'sources.json', 'cutline.geojson'):
        (base / name).write_text('{}')
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        x, y = tile_xy(person.reference_lon, person.reference_lat, 20)
        db.add(TileWindow(person_id=person_id, relative_dir='cached', min_zoom=12, max_zoom=20, radius_m=350, render_mode='ORIGINAL', status='LISTO'))
        db.commit()
    (base / 'preload.json').write_text(json.dumps({'tiles': [[20, x, y]]}))
    tile = base / '20' / str(x) / f'{y}.png'
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b'\x89PNG\r\n\x1a\ncache')
    monkeypatch.setattr(app.state.tile_renderer, '_render_original', lambda *args: pytest.fail('A cached tile started GDAL'))
    manifest = client.get(f'/p/{token}/preload')
    assert manifest.status_code == 200
    assert manifest.json() == {'version': 'cached', 'tiles': [[20, x, y]]}
    assert client.get(f'/p/{token}/tiles/20/{x}/{y}.png').status_code == 200
    assert client.get('/p/invalid/preload').status_code == 404
    with app.state.Session() as db:
        from app.models import PublicLink, utcnow
        link = db.scalar(select(PublicLink).where(PublicLink.person_id == person_id))
        link.revoked_at = utcnow()
        db.commit()
    assert client.get(f'/p/{token}/preload').status_code == 404
    assert client.get(f'/p/{token}/tiles/20/{x}/{y}.png').status_code == 404


def test_preloading_large_area_is_bounded_and_keeps_every_zoom():
    tiles = preload_tiles(-74.89064, 6.270004, 50_000, 8, 20)
    assert {z for z, _, _ in tiles} == set(range(8, 21))
    assert len(tiles) <= 64 * 13
    for z, x, y in tiles:
        assert tile_intersects_window(-74.89064, 6.270004, 50_000, z, x, y)


def test_real_gdal_keeps_sharp_overlay_and_masks_boundary(test_app, tmp_path):
    app, _, _, person_id = test_app
    qgis = Path('C:/Program Files/QGIS 3.44.13')
    python = qgis / 'apps/Python312/python.exe'
    if not python.is_file():
        pytest.skip('QGIS GDAL integration requires the installed Windows runtime')
    app.state.settings.qgis_root = qgis
    env = qgis_environment(app.state.settings)
    generator = '''
from osgeo import gdal, osr
import sys
from pathlib import Path
gdal.UseExceptions(); osr.UseExceptions()
p=Path(sys.argv[1]); srs=osr.SpatialReference();srs.ImportFromEPSG(9377)
x,y=map(float,sys.argv[2:4])
for name,size,res,value in [('rural',2000,1,50),('urban',2000,0.1,200)]:
 d=gdal.GetDriverByName('GTiff').Create(str(p/(name+'.tif')),size,size,3,gdal.GDT_Byte)
 d.SetProjection(srs.ExportToWkt());d.SetGeoTransform((x-size*res/2,res,0,y+size*res/2,0,-res))
 for i in range(1,4): d.GetRasterBand(i).Fill(value)
 d=None
'''
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        cx, cy, _, _ = metres_bounds(person.reference_lon, person.reference_lat, 0)
        subprocess.run([str(python), '-c', generator, str(app.state.settings.orthophoto_root), str(cx), str(cy)], check=True, env=env, capture_output=True, text=True)
        for name, res, radius in [('rural',1,1000),('urban',0.1,100)]:
            db.add(OrthophotoSource(relative_path=f'{name}.tif', municipality_id=person.municipality_id, crs='EPSG:9377', resolution_x=res, resolution_y=res, bounds_json=json.dumps(metres_bounds(person.reference_lon, person.reference_lat, radius)), status='LISTO'))
        job = Job(job_type='PREPARE_WINDOW', payload_json=json.dumps({'person_id':person_id}), status='EJECUTANDO')
        db.add(job)
        db.commit()
        prepare_window(db, app.state.settings, job, person_id, 350)
        db.expire_all()
        window=db.get(TileWindow,person_id)
        base=app.state.settings.data_dir/'tiles'/window.relative_dir
        zoom=17
        x,y=tile_xy(person.reference_lon,person.reference_lat,zoom)
        tile=DynamicTileRenderer(app.state.settings).render(base/'source.vrt',base/str(zoom)/str(x)/f'{y}.png',zoom,x,y)
        inspector='''
from osgeo import gdal,osr
import sys,json,math
gdal.UseExceptions();osr.UseExceptions()
d=gdal.Open(sys.argv[1]);alpha=d.GetRasterBand(4).ReadRaster();red=d.GetRasterBand(1).ReadRaster()
west,south,east,north=map(float,sys.argv[2:6]);cx,cy,radius=map(float,sys.argv[6:9])
s=osr.SpatialReference();s.ImportFromEPSG(3857);t=osr.SpatialReference();t.ImportFromEPSG(9377)
s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER);t.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
tr=osr.CoordinateTransformation(s,t)
outside=inside=outside_rgb=0
for row in range(256):
 for col in range(256):
  x,y,_=tr.TransformPoint(west+(col+.5)*(east-west)/256,north-(row+.5)*(north-south)/256)
  if math.hypot(x-cx,y-cy)>radius+0.01:
   if alpha[row*256+col]:outside+=1
   if red[row*256+col]:outside_rgb+=1
  if alpha[row*256+col]:inside+=1
print(json.dumps({'outside':outside,'outside_rgb':outside_rgb,'inside':inside,'values':sorted(set(red[i] for i,a in enumerate(alpha) if a)), 'alpha':sorted(set(alpha)), 'bands':d.RasterCount}))
'''
        from app.tile_rendering import tile_bounds_web_mercator
        bounds=tile_bounds_web_mercator(zoom,x,y)
        result=subprocess.run([str(python),'-c',inspector,str(tile),*map(str,bounds),str(cx),str(cy),'350'],check=True,env=env,capture_output=True,text=True)
        pixels=json.loads(result.stdout)
        assert pixels['outside']==0
        assert pixels['outside_rgb']==0
        assert pixels['inside']>0
        assert 50 in pixels['values'] and 200 in pixels['values']
        assert pixels['alpha']==[0,255]
        assert pixels['bands']==4
