from __future__ import annotations

import base64
import math
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from app.config import Settings
from app.main import create_app
from app.models import Municipality, Person, PublicLink, TileWindow
from app.security import sha256
from app.tile_preparation import preload_tiles
import json


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def tile_xy(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    scale = 2**zoom
    return int((lon + 180) / 360 * scale), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)


@pytest.mark.e2e
def test_public_mobile_map_at_required_widths(tmp_path: Path):
    browser_executable = CHROME if CHROME.exists() else EDGE
    if not browser_executable.exists():
        pytest.skip("No se encontró Chrome o Edge para la prueba de navegador")
    data = tmp_path / "data"
    orthos = tmp_path / "orthos"
    orthos.mkdir()
    settings = Settings(
        environment="test",
        data_dir=data,
        orthophoto_root=orthos,
        database_url=f"sqlite:///{(data / 'e2e.db').as_posix()}",
        secret_key="e2e-stable-secret-key",
        public_base_url="http://127.0.0.1",
        cookie_secure=False,
        qgis_root=Path(r"C:\QGIS"),
        window_radius_m=350,
        tile_min_zoom=15,
        tile_max_zoom=19,
        asset_mode="local",
    )
    app = create_app(settings, create_schema=True)
    token = "e2e-public-token-with-at-least-256-bits-12345"
    lon, lat = -75.031, 6.294
    with app.state.Session() as db:
        municipality = Municipality(name="San Rafael")
        db.add(municipality)
        db.flush()
        person = Person(municipality_id=municipality.id, name="Persona de prueba", phone="3008887777", reference_lon=lon, reference_lat=lat)
        db.add(person)
        db.flush()
        db.add(PublicLink(person_id=person.id, token_hash=sha256(token)))
        db.add(TileWindow(person_id=person.id, relative_dir="e2e", min_zoom=15, max_zoom=18, status="LISTO"))
        db.commit()
    x, y = tile_xy(lon, lat, 18)
    for tile_x in range(x - 3, x + 4):
        for tile_y in range(y - 3, y + 4):
            path = data / "tiles" / "e2e" / "18" / str(tile_x) / f"{tile_y}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(PNG)
    for zoom in range(15, 19):
        center_x, center_y = tile_xy(lon, lat, zoom)
        for tile_x in range(center_x - 3, center_x + 4):
            for tile_y in range(center_y - 3, center_y + 4):
                path = data / "tiles" / "e2e" / str(zoom) / str(tile_x) / f"{tile_y}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(PNG)
    (data / 'tiles/e2e/preload.json').write_text(json.dumps({'tiles': preload_tiles(lon, lat, 350, 15, 18)}))

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(browser_executable), headless=True)
            for width in (320, 375, 430):
                page = browser.new_page(viewport={"width": width, "height": 760})
                if width == 320:
                    # Keep the UI usable while an older server is being restarted.
                    page.route('**/preload', lambda route: route.fulfill(status=404, body='{}'))
                tile_ok = []
                page.on("response", lambda response: tile_ok.append(response.status) if "/tiles/" in response.url else None)
                page.goto(f"http://127.0.0.1:{port}/p/{token}", wait_until="networkidle")
                expect(page.locator('#map')).to_have_attribute('data-image-ready', 'true')
                expect(page.locator('#imageLoader')).to_be_hidden()
                page.wait_for_selector(".leaflet-map-pane", state="attached")
                expect(page.locator(".public-header .brand-symbol img")).to_be_visible()
                assert page.locator(".public-header .brand-symbol img").evaluate("image => image.complete && image.naturalWidth > 0")
                assert page.locator("#map").bounding_box()["height"] > 200
                assert page.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
                assert page.locator("#confirmButton").is_visible()
                assert any(status == 200 for status in tile_ok)
                if width == 375:
                    dense_ring = [
                        [lon + math.cos(index * math.tau / 48) * 0.0003, lat + math.sin(index * math.tau / 48) * 0.0003]
                        for index in range(48)
                    ]
                    dense_ring.append(dense_ring[0])
                    page.evaluate(
                        "([key, geometry]) => sessionStorage.setItem(key, JSON.stringify(geometry))",
                        [f"predios-remotos-draft-{token[:12]}", {"type": "Polygon", "coordinates": [dense_ring]}],
                    )
                    page.reload(wait_until="networkidle")
                    migrated = page.evaluate(f"JSON.parse(sessionStorage.getItem('predios-remotos-draft-{token[:12]}'))")
                    assert migrated['coordinates'][0] == dense_ring
                    minimum_zoom = float(page.locator("#map").get_attribute("data-min-display-zoom"))
                    zoom_out = page.locator(".leaflet-control-zoom-out")
                    while float(page.locator("#map").get_attribute("data-zoom")) > minimum_zoom:
                        previous_zoom = float(page.locator("#map").get_attribute("data-zoom"))
                        zoom_out.click()
                        page.wait_for_function(
                            "previous => Number(document.getElementById('map').dataset.zoom) < previous",
                            arg=previous_zoom,
                        )
                    expect(page.locator("#map")).to_have_attribute("data-zoom", str(int(minimum_zoom)))
                    map_box = page.locator("#map").bounding_box()
                    radius_box = page.locator('.leaflet-overlay-pane path[stroke-dasharray]').bounding_box()
                    assert map_box and radius_box
                    assert radius_box["x"] >= map_box["x"]
                    assert radius_box["y"] >= map_box["y"]
                    assert radius_box["x"] + radius_box["width"] <= map_box["x"] + map_box["width"]
                    assert radius_box["y"] + radius_box["height"] <= map_box["y"] + map_box["height"]
                    zoom_in = page.locator(".leaflet-control-zoom-in")
                    while float(page.locator("#map").get_attribute("data-zoom")) < 18:
                        previous_zoom = float(page.locator("#map").get_attribute("data-zoom"))
                        zoom_in.click()
                        page.wait_for_function(
                            "previous => Number(document.getElementById('map').dataset.zoom) > previous",
                            arg=previous_zoom,
                        )
                    expect(page.locator("#map")).to_have_attribute("data-zoom", "18")
                    assert page.locator(".leaflet-tile-loaded").count() > 0
                    expect(page.locator('#mapStatus')).to_have_attribute('data-errors', '0')
                    map_box = page.locator("#map").bounding_box()
                    assert map_box
                    page.locator('[data-tool="freehand"]').click()
                    center = (180, 240)
                    curve = [(center[0] + 70 * math.cos(index * math.tau / 120), center[1] + 70 * math.sin(index * math.tau / 120)) for index in range(121)]
                    page.mouse.move(map_box['x'] + curve[0][0], map_box['y'] + curve[0][1])
                    page.mouse.down()
                    for px, py in curve[1:]:
                        page.mouse.move(map_box['x'] + px, map_box['y'] + py)
                    page.mouse.up()
                    curved = page.evaluate(f"JSON.parse(sessionStorage.getItem('predios-remotos-draft-{token[:12]}'))")
                    assert 13 < len(curved['coordinates'][0]) <= 41
                    closure_gap = page.evaluate('''ring => {
                      const start = L.CRS.EPSG3857.latLngToPoint(L.latLng(ring[0][1], ring[0][0]), 18);
                      const end = ring[ring.length - 2];
                      return start.distanceTo(L.CRS.EPSG3857.latLngToPoint(L.latLng(end[1], end[0]), 18));
                    }''', curved['coordinates'][0])
                    assert closure_gap <= 22.1
                    # Reloading must keep every freehand vertex already chosen,
                    # rather than simplifying it again at a different zoom.
                    page.reload(wait_until='networkidle')
                    restored = page.evaluate(f"JSON.parse(sessionStorage.getItem('predios-remotos-draft-{token[:12]}'))")
                    assert restored == curved
                    while float(page.locator('#map').get_attribute('data-zoom')) < 18:
                        page.locator('.leaflet-control-zoom-in').click()
                    map_box = page.locator('#map').bounding_box()
                    page.locator('[data-tool="freehand"]').click()
                    points = [(150, 180), (215, 180), (215, 245), (150, 245), (150, 180)]
                    page.mouse.move(map_box["x"] + points[0][0], map_box["y"] + points[0][1])
                    page.mouse.down()
                    page.mouse.move(map_box["x"] + points[1][0], map_box["y"] + points[1][1], steps=16)
                    assert page.locator(".freehand-guide-halo").count() == 1
                    assert page.locator(".freehand-guide").count() == 1
                    for px, py in points[2:]:
                        page.mouse.move(map_box["x"] + px, map_box["y"] + py, steps=16)
                    assert page.locator("#confirmButton").is_enabled()
                    page.mouse.up()
                    draft = page.evaluate(f"JSON.parse(sessionStorage.getItem('predios-remotos-draft-{token[:12]}'))")
                    assert 4 <= len(draft["coordinates"][0]) <= 41
                    expect(page.locator('[data-tool="move"]')).to_have_class("tool active")
                    assert page.locator(".marker-icon").count() == 0
                    page.locator('[data-tool="edit"]').click()
                    vertex_count = len(draft["coordinates"][0]) - 1
                    expect(page.locator(".marker-icon-middle:visible")).to_have_count(vertex_count)
                    assert page.locator(".marker-icon:not(.marker-icon-middle):visible").count() == vertex_count
                    assert page.locator(".marker-icon:not(.marker-icon-middle):visible").first.bounding_box()["width"] >= 24
                    assert page.locator(".marker-icon-middle:visible").first.bounding_box()["width"] == 14
                    failed = {"done": False}

                    def fail_once(route):
                        failed["done"] = True
                        route.abort()

                    page.route("**/submit", fail_once, times=1)
                    page.locator("#confirmButton").click()
                    page.locator("#sendButton").click()
                    expect(page.locator("#toast")).to_contain_text("dibujo sigue")
                    assert failed["done"]
                    page.locator("#confirmButton").click()
                    page.locator("#sendButton").click()
                    expect(page.get_by_role("heading", name="Respuesta recibida")).to_be_visible()
                    expect(page.get_by_text("Ya puede cerrar esta pestaña o ventana.")).to_be_visible()
                    expect(page.locator(".message-brand .brand-symbol img")).to_be_visible()
                page.close()

            landscape = browser.new_page(viewport={"width": 667, "height": 375})
            landscape.goto(f"http://127.0.0.1:{port}/p/{token}", wait_until="networkidle")
            assert landscape.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
            assert landscape.locator(".tool-dock").is_visible()
            landscape_map = landscape.locator("#map").bounding_box()
            assert landscape_map
            landscape.mouse.move(landscape_map["x"] + 120, landscape_map["y"] + 100)
            landscape.mouse.down()
            landscape.mouse.move(landscape_map["x"] + 1000, landscape_map["y"] + 800, steps=10)
            landscape.mouse.up()
            center_lat = float(landscape.locator("#map").get_attribute("data-center-lat"))
            center_lon = float(landscape.locator("#map").get_attribute("data-center-lon"))
            assert abs(center_lat - lat) <= settings.window_radius_m / 111_320
            assert abs(center_lon - lon) <= settings.window_radius_m / (111_320 * math.cos(math.radians(lat)))
            landscape.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
