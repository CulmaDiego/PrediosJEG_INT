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


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def tile_xy(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    scale = 2**zoom
    return int((lon + 180) / 360 * scale), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)


@pytest.mark.e2e
def test_public_mobile_map_at_required_widths(tmp_path: Path):
    if not CHROME.exists():
        pytest.skip("Google Chrome no está instalado")
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
            browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
            for width in (320, 375, 430):
                page = browser.new_page(viewport={"width": width, "height": 760})
                tile_ok = []
                page.on("response", lambda response: tile_ok.append(response.status) if "/tiles/" in response.url else None)
                page.goto(f"http://127.0.0.1:{port}/p/{token}", wait_until="networkidle")
                page.wait_for_selector(".leaflet-map-pane", state="attached")
                assert page.locator("#map").bounding_box()["height"] > 200
                assert page.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
                assert page.locator("#confirmButton").is_visible()
                assert any(status == 200 for status in tile_ok)
                if width == 375:
                    zoom_in = page.locator(".leaflet-control-zoom-in")
                    for expected_zoom in range(19, 25):
                        zoom_in.click()
                        expect(page.locator("#map")).to_have_attribute("data-zoom", str(expected_zoom))
                    expect(page.locator("#map")).to_have_attribute("data-zoom", "24")
                    assert page.locator(".leaflet-tile-loaded").count() > 0
                    map_box = page.locator("#map").bounding_box()
                    assert map_box
                    page.locator('[data-tool="freehand"]').click()
                    points = [(150, 180), (215, 180), (215, 245), (150, 245), (150, 180)]
                    page.mouse.move(map_box["x"] + points[0][0], map_box["y"] + points[0][1])
                    page.mouse.down()
                    for px, py in points[1:]:
                        page.mouse.move(map_box["x"] + px, map_box["y"] + py, steps=8)
                    page.mouse.up()
                    assert page.locator("#confirmButton").is_enabled()
                    page.locator('[data-tool="edit"]').click()
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
                    expect(page.locator("#mapStatus")).to_contain_text("Guardado")
                    page.locator('[data-tool="move"]').click()
                    page.mouse.move(map_box["x"] + 90, map_box["y"] + 140)
                    page.mouse.down()
                    page.mouse.move(map_box["x"] + 1000, map_box["y"] + 1000, steps=10)
                    page.mouse.up()
                    center_lat = float(page.locator("#map").get_attribute("data-center-lat"))
                    center_lon = float(page.locator("#map").get_attribute("data-center-lon"))
                    assert abs(center_lat - lat) <= settings.window_radius_m / 111_320
                    assert abs(center_lon - lon) <= settings.window_radius_m / (111_320 * math.cos(math.radians(lat)))
                page.close()

            landscape = browser.new_page(viewport={"width": 667, "height": 375})
            landscape.goto(f"http://127.0.0.1:{port}/p/{token}", wait_until="networkidle")
            assert landscape.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
            assert landscape.locator(".tool-dock").is_visible()
            landscape.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
