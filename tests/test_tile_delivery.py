from __future__ import annotations

import asyncio
import base64
import threading

import httpx
import pytest
from sqlalchemy import select
from starlette.requests import Request

from app.gis import tile_xy
from app.models import Person, PublicLink, TileWindow, utcnow
from app.security import ip_fingerprint, rate_limiter
from app.tile_rendering import DynamicTileRenderer, tile_cache_path


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def original_window(app, person_id):
    base = app.state.settings.data_dir / "tiles" / "delivery"
    base.mkdir(parents=True)
    for name in ("source.vrt", "sources.json", "cutline.geojson"):
        (base / name).write_text("{}", encoding="utf-8")
    with app.state.Session() as db:
        person = db.get(Person, person_id)
        center = tile_xy(person.reference_lon, person.reference_lat, 20)
        db.add(TileWindow(person_id=person_id, relative_dir="delivery", min_zoom=15, max_zoom=20,
                          radius_m=350, render_mode="ORIGINAL", status="LISTO"))
        db.commit()
    return base, center


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout=3)


def test_busy_raster_queue_deduplicates_and_keeps_other_http_routes_responsive(test_app, monkeypatch):
    app, _, token, person_id = test_app
    _, (x, y) = original_window(app, person_id)
    app.state.tile_renderer.close()
    app.state.settings.dynamic_tile_workers = 1
    renderer = DynamicTileRenderer(app.state.settings)
    app.state.tile_renderer = renderer
    started, release = threading.Event(), threading.Event()
    calls = []

    def blocked_render(vrt, tile, zoom, tile_x, tile_y):
        calls.append(tile)
        started.set()
        assert release.wait(timeout=10)
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(PNG)
        return tile

    monkeypatch.setattr(renderer, "render", blocked_render)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            tile_url = f"/p/{token}/tiles/20/{x}/{y}.png"
            requests = [asyncio.create_task(client.get(tile_url)) for _ in range(48)]
            queued = []
            try:
                await wait_until(lambda: started.is_set())
                await wait_until(lambda: len(renderer._pending) == 1)
                for offset in (1, 2, 3):
                    queued.append(asyncio.create_task(client.get(f"/p/{token}/tiles/20/{x + offset}/{y}.png")))
                await wait_until(lambda: len(renderer._pending) == 4)
                full = await asyncio.wait_for(client.get(f"/p/{token}/tiles/20/{x + 4}/{y}.png"), timeout=3)
                assert full.status_code == 503
                assert full.headers["Retry-After"] == "1"
                assert len(calls) == 1
                assert app.state.engine.pool.checkedout() == 0

                for path in ("/health", "/static/public-map.js", f"/p/{token}/preload"):
                    response = await asyncio.wait_for(client.get(path), timeout=3)
                    assert response.status_code == 200, path
                assert len(renderer._pending) == 4
            finally:
                release.set()
                responses = await asyncio.gather(*requests, *queued)
            assert all(response.status_code == 200 for response in responses)
            assert len(calls) == 4
            assert not renderer._pending

    asyncio.run(exercise())


def test_cached_tiles_ignore_generation_budget_but_keep_authorization(test_app):
    app, client, token, person_id = test_app
    base, (x, y) = original_window(app, person_id)
    tile = base / "20" / str(x) / f"{y}.png"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(PNG)
    request = Request({"type": "http", "client": ("testclient", 50000)})
    key = f"tile:{token}:{ip_fingerprint(request, app.state.settings)}"
    for _ in range(600):
        rate_limiter.check(key, 600, 60)
    assert client.get(f"/p/{token}/tiles/20/{x}/{y}.png").status_code == 200
    assert client.get(f"/p/{token}/tiles/20/{x + 1}/{y}.png").status_code == 429
    with app.state.Session() as db:
        link = db.scalar(select(PublicLink).where(PublicLink.person_id == person_id))
        link.revoked_at = utcnow()
        db.commit()
    assert client.get(f"/p/{token}/tiles/20/{x}/{y}.png").status_code == 404


def test_failed_generation_can_retry_without_leaking_queue_capacity(test_app, monkeypatch):
    app, client, token, person_id = test_app
    _, (x, y) = original_window(app, person_id)
    renderer = app.state.tile_renderer

    def failed_render(*args):
        raise OSError("Temporary raster failure")

    monkeypatch.setattr(renderer, "render", failed_render)
    url = f"/p/{token}/tiles/20/{x}/{y}.png"
    failure = client.get(url)
    assert failure.status_code == 503
    assert failure.headers["Retry-After"] == "2"
    assert not renderer._pending

    def succeeding_render(vrt, tile, *coordinates):
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(PNG)
        return tile

    monkeypatch.setattr(renderer, "render", succeeding_render)
    assert client.get(url).status_code == 200
    assert not renderer._pending


def test_cancelling_a_viewer_keeps_a_shared_tile_render_running(test_app, monkeypatch):
    app, _, _, _ = test_app
    renderer = app.state.tile_renderer
    tile = tile_cache_path(app.state.settings, "cancelled-viewer", 15, 100, 100)
    started, release = threading.Event(), threading.Event()
    calls = []

    def blocked_render(vrt, destination, *coordinates):
        calls.append(destination)
        started.set()
        assert release.wait(timeout=10)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(PNG)
        return destination

    monkeypatch.setattr(renderer, "render", blocked_render)

    async def exercise():
        first = asyncio.create_task(renderer.render_async(tile.parent / "source.vrt", tile, 15, 100, 100))
        second = None
        try:
            await wait_until(lambda: started.is_set())
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(renderer.render_async(tile.parent / "source.vrt", tile, 15, 100, 100))
            await asyncio.sleep(0)
            assert len(calls) == 1
        finally:
            release.set()
            if second:
                assert await second == tile
        assert len(calls) == 1

    asyncio.run(exercise())
