from __future__ import annotations

import json

from sqlalchemy import select

from app.gis import tile_xy
from app.models import Job, Person, TileConfiguration, TileWindow
from tests.conftest import login


def test_admin_configures_radius_for_future_jobs_and_preserves_drawings(test_app):
    app, client, token, person_id = test_app
    csrf = login(client)

    page = client.get("/admin/configuracion")
    assert page.status_code == 200
    assert 'value="350"' in page.text

    saved = client.post(
        "/admin/configuracion/teselas",
        data={"csrf_token": csrf, "radius_m": "600"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin/configuracion?guardado=1"

    with app.state.Session() as db:
        configuration = db.get(TileConfiguration, 1)
        person = db.get(Person, person_id)
        assert configuration is not None
        assert configuration.radius_m == 600
        person.orthophoto_status = "LISTO"
        db.add(TileWindow(person_id=person.id, relative_dir="existing-window", min_zoom=15, max_zoom=19, radius_m=350, status="LISTO"))
        drawn = Person(
            municipality_id=person.municipality_id,
            name="Dibujo conservado",
            phone="3005550011",
            reference_lon=-75.032,
            reference_lat=6.295,
            status="RESPONDIO",
            drawing_version=1,
            orthophoto_status="LISTO",
        )
        db.add(drawn)
        db.commit()
        drawn_id = drawn.id

    for zoom in range(15, 20):
        x, y = tile_xy(person.reference_lon, person.reference_lat, zoom)
        tile = app.state.settings.data_dir / "tiles" / "existing-window" / str(zoom) / str(x) / f"{y}.png"
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(b"\x89PNG\r\n\x1a\nexisting")

    existing_map = client.get(f"/p/{token}")
    assert existing_map.status_code == 200
    assert 'data-radius="350"' in existing_map.text

    queued = client.post(
        f"/admin/personas/{person_id}/preparar-teselas",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert queued.status_code == 303
    assert "teselas=solicitadas" in queued.headers["location"]

    protected = client.post(
        f"/admin/personas/{drawn_id}/preparar-teselas",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert protected.status_code == 303
    assert "teselas=conservada" in protected.headers["location"]

    with app.state.Session() as db:
        existing = db.get(TileWindow, person_id)
        assert existing is not None
        assert existing.radius_m == 350
        job = db.scalar(
            select(Job).where(
                Job.job_type == "PREPARE_WINDOW",
                Job.payload_json == json.dumps({"person_id": person_id, "radius_m": 600}, sort_keys=True, separators=(",", ":")),
            )
        )
        assert job is not None
        assert db.scalar(select(Job).where(Job.payload_json.contains(f'"person_id":{drawn_id}'))) is None


def test_admin_rejects_an_unsafe_tile_radius(test_app):
    app, client, _, _ = test_app
    csrf = login(client)

    response = client.post("/admin/configuracion/teselas", data={"csrf_token": csrf, "radius_m": "50001"})

    assert response.status_code == 422
    assert "El radio debe estar entre 100 y 50000 metros." in response.text
    with app.state.Session() as db:
        configuration = db.get(TileConfiguration, 1)
        assert configuration is not None
        assert configuration.radius_m == 350