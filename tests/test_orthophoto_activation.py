from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.gis import metres_bounds
from app.full_orthophoto import SourceSelectionChanged
from app.models import AuditLog, DrawingVersion, Job, Municipality, OrthophotoSource, Person, TileWindow
from app.worker import prepare_window, scan_catalog
from tests.conftest import login


def add_source(app, person_id):
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        source = OrthophotoSource(
            relative_path='San Rafael/urbana.tif', municipality_id=person.municipality_id,
            status='LISTO', crs='EPSG:9377', resolution_x=.2, resolution_y=.2,
            bounds_json=json.dumps(metres_bounds(person.reference_lon, person.reference_lat, 1000)),
        )
        db.add(source)
        db.commit()
        return source.id


def test_orthophoto_activation_requires_admin_and_csrf_and_updates_the_panel(test_app):
    app, client, _, person_id = test_app
    source_id = add_source(app, person_id)
    url = f'/admin/ortofotos/{source_id}/estado'
    unauthorized = client.post(url, data={'enabled': 'false', 'csrf_token': 'invalid'}, follow_redirects=False)
    assert unauthorized.status_code == 303
    assert unauthorized.headers['location'] == '/admin/login'
    csrf = login(client)
    assert 'Desactivar' in client.get('/admin/ortofotos').text
    rejected = client.post(url, data={'enabled': 'false', 'csrf_token': 'invalid'}, follow_redirects=False)
    assert rejected.status_code == 403
    with app.state.Session() as db:
        assert db.get(OrthophotoSource, source_id).enabled is True
        assert db.scalar(select(func.count()).select_from(Job).where(Job.job_type == 'PREPARE_WINDOW')) == 0
    response = client.post(url, data={'enabled': 'false', 'csrf_token': csrf}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get('/admin/ortofotos')
    assert 'Deshabilitada' in page.text and 'Activar' in page.text
    with app.state.Session() as db:
        assert db.get(OrthophotoSource, source_id).enabled is False
        assert db.scalar(select(AuditLog.id).where(AuditLog.action == 'ORTOFOTO_DESHABILITADA')) is not None
    assert client.post(url, data={'enabled': 'true', 'csrf_token': csrf}, follow_redirects=False).status_code == 303
    with app.state.Session() as db:
        assert db.get(OrthophotoSource, source_id).enabled is True
        assert db.scalar(select(AuditLog.id).where(AuditLog.action == 'ORTOFOTO_HABILITADA')) is not None


def test_selection_change_rebuilds_existing_maps_and_preserves_submitted_drawings(test_app):
    app, client, token, person_id = test_app
    source_id = add_source(app, person_id)
    with app.state.Session() as db:
        original = db.get(Person, person_id)
        original.orthophoto_status = 'LISTO'
        other_municipality = Municipality(name='Otro municipio')
        db.add(other_municipality)
        db.flush()
        responded = Person(
            municipality_id=original.municipality_id, name='Respuesta conservada', phone='3000000001',
            reference_lon=original.reference_lon, reference_lat=original.reference_lat,
            status='RESPONDIO', drawing_version=1, orthophoto_status='LISTO',
        )
        other = Person(
            municipality_id=other_municipality.id, name='Otro municipio', phone='3000000002',
            reference_lon=-75.031, reference_lat=6.294, orthophoto_status='LISTO',
        )
        no_coordinates = Person(municipality_id=original.municipality_id, name='Sin coordenada', phone='3000000003')
        inactive = Person(
            municipality_id=original.municipality_id, name='Inactiva', phone='3000000004', active=False,
            reference_lon=-75.031, reference_lat=6.294, orthophoto_status='LISTO',
        )
        db.add_all([responded, other, no_coordinates, inactive])
        db.flush()
        responded_id, other_id, inactive_id, no_coordinates_id = responded.id, other.id, inactive.id, no_coordinates.id
        geometry = '{"type":"Polygon","coordinates":[[[-75.031,6.294],[-75.030,6.294],[-75.030,6.295],[-75.031,6.294]]]}'
        drawing = DrawingVersion(person_id=responded.id, version=1, geometry_json=geometry, area_m2=500, is_current=True)
        db.add(drawing)
        for person in (original, responded, other, inactive):
            db.add(TileWindow(person_id=person.id, relative_dir=f'old-{person.id}', status='LISTO'))
        # The running generation needs a successor; a pending one can be reused.
        db.add(Job(job_type='PREPARE_WINDOW', payload_json=json.dumps({'person_id': original.id, 'radius_m': 350}, separators=(',', ':'), sort_keys=True), status='EJECUTANDO'))
        db.add(Job(job_type='PREPARE_WINDOW', payload_json=json.dumps({'person_id': responded.id, 'radius_m': 350}, separators=(',', ':'), sort_keys=True), status='PENDIENTE'))
        db.commit()
        drawing_id = drawing.id
    csrf = login(client)
    response = client.post(f'/admin/ortofotos/{source_id}/estado', data={'enabled': 'false', 'csrf_token': csrf}, follow_redirects=False)
    assert response.status_code == 303
    with app.state.Session() as db:
        pending = db.scalars(select(Job).where(Job.job_type == 'PREPARE_WINDOW', Job.status == 'PENDIENTE')).all()
        assert sorted(json.loads(job.payload_json)['person_id'] for job in pending) == sorted([person_id, responded_id])
        assert db.get(TileWindow, person_id).status == 'PENDIENTE'
        assert db.get(TileWindow, responded_id).status == 'PENDIENTE'
        assert db.get(TileWindow, other_id).status == 'LISTO'
        assert db.get(TileWindow, inactive_id).status == 'LISTO'
        assert db.get(TileWindow, no_coordinates_id) is None
        person = db.get(Person, responded_id)
        assert person.status == 'RESPONDIO' and person.drawing_version == 1
        saved = db.get(DrawingVersion, drawing_id)
        assert saved.geometry_json == geometry and saved.is_current and saved.area_m2 == 500
    assert client.get(f'/p/{token}').status_code == 409


def test_repeating_the_same_source_state_is_idempotent(test_app):
    app, client, _, person_id = test_app
    source_id = add_source(app, person_id)
    csrf = login(client)
    url = f'/admin/ortofotos/{source_id}/estado'
    for _ in range(2):
        assert client.post(url, data={'enabled': 'false', 'csrf_token': csrf}, follow_redirects=False).status_code == 303
    with app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.job_type == 'PREPARE_WINDOW')) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == 'ORTOFOTO_DESHABILITADA')) == 1


@pytest.mark.parametrize('full_mode', [False, True])
def test_disabled_sources_are_never_selected_for_new_imagery(test_app, full_mode):
    app, client, token, person_id = test_app
    app.state.settings.full_orthophoto = full_mode
    source_id = add_source(app, person_id)
    with app.state.Session() as db:
        db.get(OrthophotoSource, source_id).enabled = False
        job = Job(job_type='PREPARE_WINDOW', payload_json=json.dumps({'person_id': person_id}), status='EJECUTANDO')
        db.add(job)
        db.commit()
        prepare_window(db, app.state.settings, job, person_id, 350)
        assert db.get(TileWindow, person_id).status == 'SIN_ORTOFOTO'
        assert db.get(Person, person_id).orthophoto_status == 'SIN_ORTOFOTO'
    if full_mode:
        page = client.get(f'/p/{token}')
        assert page.status_code == 409
        assert 'No hay ortofoto habilitada' in page.text
        assert 'Administrar ortofotos' in page.text
        assert 'Estamos preparando la imagen' not in page.text


def test_catalog_rescan_preserves_disabled_sources(test_app, monkeypatch):
    app, _, _, person_id = test_app
    source_id = add_source(app, person_id)
    settings = app.state.settings
    path = settings.orthophoto_root / 'San Rafael' / 'urbana.tif'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'raster')
    monkeypatch.setattr('app.worker.inspect_raster', lambda *_: {
        'stac': {'proj:epsg': 9377}, 'driverShortName': 'GTiff', 'size': [100, 100],
        'geoTransform': [0, .2, 0, 100, 0, -.2],
        'cornerCoordinates': {'upperLeft': [0, 100], 'lowerRight': [20, 80]},
    })
    with app.state.Session() as db:
        db.get(OrthophotoSource, source_id).enabled = False
        job = Job(job_type='SCAN_CATALOG', payload_json='{}', status='EJECUTANDO')
        db.add(job)
        db.commit()
        scan_catalog(db, settings, job)
        db.expire_all()
        source = db.get(OrthophotoSource, source_id)
        assert source.enabled is False and source.status == 'LISTO'


def test_selection_changed_during_render_never_publishes_obsolete_imagery(test_app, monkeypatch):
    app, _, _, person_id = test_app
    settings = app.state.settings
    settings.full_orthophoto = True
    settings.dynamic_tile_workers = 1
    source_id = add_source(app, person_id)
    path = settings.orthophoto_root / 'San Rafael' / 'urbana.tif'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'raster')

    def translate(command, **kwargs):
        Path(command[-1]).write_text('<VRTDataset/>')
        return SimpleNamespace(returncode=0, stderr='', stdout='')

    changed = False

    def render(self, vrt, tile, zoom, x, y):
        nonlocal changed
        if not changed:
            with app.state.Session() as db:
                db.get(OrthophotoSource, source_id).enabled = False
                db.commit()
            changed = True
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(b'\x89PNG\r\n\x1a\n')
        return tile

    monkeypatch.setattr('subprocess.run', translate)
    monkeypatch.setattr('app.full_orthophoto.qgis_tool', lambda *_: Path('gdal_translate'))
    monkeypatch.setattr('app.full_orthophoto.qgis_environment', lambda *_: {})
    monkeypatch.setattr('app.full_orthophoto.DynamicTileRenderer.render', render)
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        person.status, person.drawing_version = 'RESPONDIO', 1
        db.add(TileWindow(person_id=person_id, relative_dir='previous', status='PENDIENTE'))
        job = Job(job_type='PREPARE_WINDOW', payload_json=json.dumps({'person_id': person_id}), status='EJECUTANDO')
        db.add(job)
        db.commit()
        with pytest.raises(SourceSelectionChanged):
            prepare_window(db, settings, job, person_id, 350)
        db.expire_all()
        assert changed
        assert db.get(TileWindow, person_id).relative_dir == 'previous'
        assert db.get(TileWindow, person_id).status == 'PENDIENTE'
        assert db.get(Person, person_id).status == 'RESPONDIO'
