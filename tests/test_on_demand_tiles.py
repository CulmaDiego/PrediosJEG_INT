from __future__ import annotations

from pathlib import Path

import pytest

from app.gis import tile_xy
from app.models import Job, Person, TileWindow
from app.security import public_csrf
from app.tile_rendering import tile_bounds_web_mercator, tile_intersects_window
from app.worker import _clear_superseded_tile_failures


def test_on_demand_window_is_ready_without_a_prebuilt_pyramid(test_app, monkeypatch: pytest.MonkeyPatch):
    app, client, token, person_id = test_app
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        person.orthophoto_status = "LISTO"
        tile_dir = app.state.settings.data_dir / "tiles" / "on-demand"
        tile_dir.mkdir(parents=True)
        (tile_dir / "source.vrt").write_text("<VRTDataset/>", encoding="utf-8")
        db.add(
            TileWindow(
                person_id=person_id,
                relative_dir="on-demand",
                min_zoom=12,
                max_zoom=19,
                radius_m=10_000,
                render_mode="ON_DEMAND",
                status="LISTO",
            )
        )
        db.commit()
        lon, lat = person.reference_lon, person.reference_lat

    rendered: list[Path] = []

    def render(vrt: Path, tile: Path, zoom: int, x: int, y: int) -> Path:
        assert vrt.name == "source.vrt"
        assert (zoom, x, y) == (12, *tile_xy(lon, lat, 12))
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(b"\x89PNG\r\n\x1a\non-demand")
        rendered.append(tile)
        return tile

    monkeypatch.setattr(app.state.tile_renderer, "render", render)
    page = client.get(f"/p/{token}")
    assert page.status_code == 200
    assert "las zonas se cargan al navegar" in page.text

    x, y = tile_xy(lon, lat, 12)
    tile = client.get(f"/p/{token}/tiles/12/{x}/{y}.png")
    assert tile.status_code == 200
    assert tile.content.startswith(b"\x89PNG")
    assert len(rendered) == 1


def test_large_window_allows_a_polygon_within_its_radius(test_app):
    app, client, token, person_id = test_app
    with app.state.Session() as db:
        db.add(TileWindow(person_id=person_id, relative_dir="large", min_zoom=12, max_zoom=19, radius_m=2_000, status="LISTO"))
        db.commit()
    csrf = public_csrf(token, app.state.settings)
    geometry = {
        "type": "Polygon",
        "coordinates": [[[-75.040, 6.285], [-75.022, 6.285], [-75.022, 6.303], [-75.040, 6.303], [-75.040, 6.285]]],
    }
    response = client.post(f"/p/{token}/submit", json={"csrf": csrf, "geometry": geometry})
    assert response.status_code == 200


def test_successful_regeneration_clears_obsolete_tile_error(test_app):
    app, _, _, person_id = test_app
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        assert person is not None
        person.status = "ERROR"
        person.orthophoto_status = "LISTO"
        superseded = Job(
            job_type="PREPARE_WINDOW",
            payload_json='{"person_id":1,"radius_m":10000}',
            status="ERROR",
            progress=35,
            error="Tiempo agotado",
        )
        unrelated = Job(
            job_type="PREPARE_WINDOW",
            payload_json='{"person_id":999,"radius_m":10000}',
            status="ERROR",
            progress=35,
            error="Tiempo agotado",
        )
        db.add_all([superseded, unrelated])
        db.flush()
        superseded_id = superseded.id
        unrelated_id = unrelated.id

        _clear_superseded_tile_failures(db, person, current_job_id=0)
        db.commit()

        assert person.status == "PENDIENTE"
        assert db.get(Job, superseded_id).status == "SUPERADO"
        assert db.get(Job, superseded_id).error is None
        assert db.get(Job, unrelated_id).status == "ERROR"


def test_window_tile_bounds_are_validated_before_rendering():
    lon, lat = -75.031, 6.294
    x, y = tile_xy(lon, lat, 12)
    assert tile_intersects_window(lon, lat, 10_000, 12, x, y)
    assert not tile_intersects_window(lon, lat, 100, 12, x + 100, y + 100)
    with pytest.raises(ValueError):
        tile_bounds_web_mercator(25, 0, 0)
