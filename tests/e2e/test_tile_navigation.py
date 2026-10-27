from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import struct
import threading
import time
import zlib
from collections import Counter
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright
from starlette.responses import Response

from app.config import Settings
from app.gis import tile_xy
from app.main import create_app
from app.models import Municipality, Person, PublicLink, TileWindow
from app.security import rate_limiter, sha256
from app.tile_preparation import preload_tiles


def opaque_tile_png() -> bytes:
    """A real 256-pixel tile, generated without imaging or GIS dependencies."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

    pixels = b''.join(b'\0' + bytes((73, 123, 86, 255)) * 256 for _ in range(256))
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('!2I5B', 256, 256, 8, 6, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(pixels))
        + chunk(b'IEND', b'')
    )


@pytest.fixture()
def slow_tile_map(tmp_path: Path):
    rate_limiter.reset()
    data = tmp_path / 'data'
    orthos = tmp_path / 'orthos'
    orthos.mkdir()
    settings = Settings(
        environment='test',
        data_dir=data,
        orthophoto_root=orthos,
        database_url=f"sqlite:///{(data / 'navigation.db').as_posix()}",
        secret_key='e2e-navigation-stable-secret-key',
        public_base_url='http://127.0.0.1',
        cookie_secure=False,
        qgis_root=Path(r'C:\QGIS'),
        window_radius_m=1800,
        tile_min_zoom=14,
        tile_max_zoom=20,
        asset_mode='local',
    )
    app = create_app(settings, create_schema=True)
    token = 'e2e-navigation-token-with-at-least-256-bits-12345'
    lon, lat = -75.031, 6.294
    with app.state.Session() as db:
        municipality = Municipality(name='San Rafael')
        db.add(municipality)
        db.flush()
        person = Person(
            municipality_id=municipality.id, name='Prueba de navegación',
            phone='3008887766', reference_lon=lon, reference_lat=lat,
        )
        db.add(person)
        db.flush()
        db.add(PublicLink(person_id=person.id, token_hash=sha256(token)))
        db.add(TileWindow(
            person_id=person.id, relative_dir='navigation', min_zoom=14,
            max_zoom=20, radius_m=1800, status='LISTO',
        ))
        db.commit()

    png = opaque_tile_png()
    base = data / 'tiles' / 'navigation'
    for zoom in range(14, 21):
        x, y = tile_xy(lon, lat, zoom)
        tile = base / str(zoom) / str(x) / f'{y}.png'
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(png)
    # Warm the overview, leaving detailed off-center tiles to real navigation.
    (base / 'preload.json').write_text(json.dumps({'tiles': preload_tiles(lon, lat, 1800, 14, 16)}))
    state = {'delay': 0.12, 'status': None, 'fault_path': None, 'failed_path': None, 'attempts': Counter(), 'app': app}

    @app.middleware('http')
    async def controlled_tile_response(request, call_next):
        if not request.url.path.startswith(f'/p/{token}/tiles/'):
            return await call_next(request)
        path = request.url.path
        state['attempts'][path] += 1
        await asyncio.sleep(state['delay'])
        if path == state['fault_path'] and state['failed_path'] is None:
            state['failed_path'] = path
            return Response(status_code=state['status'], headers={'Retry-After': '0', 'Cache-Control': 'no-store'})
        return Response(png, media_type='image/png', headers={'Cache-Control': 'private, max-age=86400'})

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        yield f'http://127.0.0.1:{port}/p/{token}', state, lon, lat
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        app.state.tile_renderer.close()


VISIBLE_TILES = '''() => {
  const map = document.getElementById('map').getBoundingClientRect();
  return Array.from(document.querySelectorAll('#map canvas.leaflet-tile')).filter(tile => {
    const rect = tile.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.right > map.left && rect.left < map.right
      && rect.bottom > map.top && rect.top < map.bottom;
  });
}'''


def map_pixel_holes(page):
    """Sample painted pixels, independent of buffered or hidden tile counts."""
    return page.evaluate(f'''() => {{
      const map = document.getElementById('map').getBoundingClientRect();
      const tiles = ({VISIBLE_TILES})();
      const holes = [];
      for (const rx of [.15, .3, .5, .7, .85]) for (const ry of [.15, .3, .5, .7, .85]) {{
        const x = map.left + map.width * rx, y = map.top + map.height * ry;
        const painted = tiles.some(tile => {{
          const rect = tile.getBoundingClientRect();
          if (x < rect.left || x >= rect.right || y < rect.top || y >= rect.bottom) return false;
          const px = Math.floor((x - rect.left) * tile.width / rect.width);
          const py = Math.floor((y - rect.top) * tile.height / rect.height);
          return tile.getContext('2d').getImageData(px, py, 1, 1).data[3] === 255;
        }});
        if (!painted) holes.push([rx, ry]);
      }}
      return holes;
    }}''')


@pytest.mark.e2e
@pytest.mark.parametrize('transient_status', [429, 503])
def test_fast_off_center_navigation_recovers_slow_and_transient_tiles(slow_tile_map, transient_status):
    executable = next((path for path in (
        Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
        Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
    ) if path.is_file()), None)
    if executable is None:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            pytest.fail('El runner de CI necesita Chrome o Edge para ejecutar las regresiones de navegador')
        pytest.skip('No se encontró Chrome o Edge para la prueba de navegador')
    url, state, lon, lat = slow_tile_map
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
        page = browser.new_page(viewport={'width': 900, 'height': 850})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(url, wait_until='domcontentloaded')
            expect(page.locator('#map')).to_have_attribute('data-image-ready', 'true', timeout=30_000)
            expect(page.locator('#imageLoader')).to_be_hidden()
            zoom_in = page.locator('.leaflet-control-zoom-in')
            zoom_out = page.locator('.leaflet-control-zoom-out')
            while int(page.locator('#map').get_attribute('data-zoom')) < 18:
                previous = int(page.locator('#map').get_attribute('data-zoom'))
                zoom_in.click()
                expect(page.locator('#map')).to_have_attribute('data-zoom', str(previous + 1))

            state['delay'] = 0.4
            box = page.locator('#map').bounding_box()
            assert box

            def pan_east():
                page.mouse.move(box['x'] + box['width'] * 0.75, box['y'] + box['height'] * 0.55)
                page.mouse.down()
                page.mouse.move(box['x'] + box['width'] * 0.25, box['y'] + box['height'] * 0.45, steps=5)
                page.mouse.up()

            def change_zoom(button, zoom):
                button.click()
                # Wait only for the normal zoom animation; imagery remains slow.
                expect(page.locator('#map')).to_have_attribute('data-zoom', str(zoom))

            # Keep moving while earlier detail requests are still in flight.
            pan_east()
            change_zoom(zoom_out, 17)
            change_zoom(zoom_in, 18)
            change_zoom(zoom_in, 19)
            pan_east()
            change_zoom(zoom_out, 18)
            change_zoom(zoom_in, 19)
            expect(page.locator('#map')).to_have_attribute('data-zoom', '19')
            center_lon = float(page.locator('#map').get_attribute('data-center-lon'))
            center_lat = float(page.locator('#map').get_attribute('data-center-lat'))
            moved_metres = math.hypot(
                (center_lon - lon) * 111_320 * math.cos(math.radians(lat)),
                (center_lat - lat) * 111_320,
            )
            assert moved_metres > 100

            state['status'] = transient_status
            final_x, final_y = tile_xy(center_lon, center_lat, 20)
            state['fault_path'] = f'/p/{url.rsplit("/", 1)[1]}/tiles/20/{final_x + 1}/{final_y}.png'
            with page.expect_response(
                lambda response: state['fault_path'] in response.url and response.status == transient_status,
                timeout=15_000,
            ):
                zoom_in.click()
                # Loaded overview pixels remain available during slow detail.
                page.wait_for_function(
                    f'() => ({VISIBLE_TILES})().some(tile => tile.dataset.imageState === "fallback")',
                    timeout=15_000,
                )

            page.wait_for_function(
                f'''() => {{
                  const tiles = ({VISIBLE_TILES})();
                  return tiles.length > 0 && tiles.every(tile => tile.dataset.imageState === 'ready');
                }}''',
                timeout=30_000,
            )
            expect(page.locator('#mapStatus')).to_have_attribute('data-errors', '0', timeout=30_000)
            expect(page.locator('#retryMap')).to_be_hidden()
            expect(page.locator('#map')).to_have_attribute('data-image-ready', 'true')
            assert state['failed_path']
            assert state['attempts'][state['failed_path']] >= 2
            assert not errors

            # A brief pan must still finish detail for overlapping canvases.
            # Return before slow requests finish so previously buffered canvases
            # are reused rather than recreated by a zoom-level change.
            state['delay'] = 0.8
            change_zoom(zoom_out, 19)
            pan_east()
            change_zoom(zoom_in, 20)
            page.wait_for_function(
                f'() => ({VISIBLE_TILES})().some(tile => tile.dataset.imageState === "fallback")',
                timeout=15_000,
            )
            page.mouse.move(box['x'] + box['width'] * 0.5, box['y'] + box['height'] * 0.5)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width'] * 0.5 - 90, box['y'] + box['height'] * 0.5 + 20, steps=4)
            page.mouse.up()
            pan_east()
            page.mouse.move(box['x'] + box['width'] * 0.25, box['y'] + box['height'] * 0.45)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width'] * 0.75, box['y'] + box['height'] * 0.55, steps=5)
            page.mouse.up()
            expect(page.locator('#map')).to_have_attribute('data-zoom', '20')
            page.wait_for_function(
                f'''() => {{
                  const tiles = ({VISIBLE_TILES})();
                  return tiles.length > 0 && tiles.every(tile => tile.dataset.imageState === 'ready');
                }}''',
                timeout=30_000,
            )
            expect(page.locator('#mapStatus')).to_have_attribute('data-errors', '0')
            expect(page.locator('#retryMap')).to_be_hidden()
            assert not errors

            # Resize through both mobile orientations before new detail has
            # finished, then restore the wider view without a manual retry.
            change_zoom(zoom_out, 19)
            pan_east()
            change_zoom(zoom_in, 20)
            page.wait_for_function(
                f'() => ({VISIBLE_TILES})().some(tile => tile.dataset.imageState === "fallback")',
                timeout=15_000,
            )
            for width, height in ((375, 760), (760, 375), (900, 850)):
                page.set_viewport_size({'width': width, 'height': height})
                page.evaluate("window.dispatchEvent(new Event('orientationchange'))")
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                assert page.locator('body').evaluate('body => body.scrollWidth <= body.clientWidth')
                assert page.locator('#map').bounding_box()['height'] > 150
            page.wait_for_function(
                f'''() => {{
                  const tiles = ({VISIBLE_TILES})();
                  return tiles.length > 0 && tiles.every(tile => tile.dataset.imageState === 'ready');
                }}''',
                timeout=30_000,
            )
            expect(page.locator('#mapStatus')).to_have_attribute('data-errors', '0')
            expect(page.locator('#retryMap')).to_be_hidden()
            expect(page.locator('#map')).to_have_attribute('data-image-ready', 'true')
            assert not errors

            # Do not release the drag until the newly exposed area is painted.
            # Detail remains slow, so ancestor imagery must fill it immediately.
            box = page.locator('#map').bounding_box()
            assert box
            page.mouse.move(box['x'] + box['width'] * 0.8, box['y'] + box['height'] * 0.5)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width'] * 0.25, box['y'] + box['height'] * 0.5, steps=20)
            page.wait_for_timeout(300)
            page.wait_for_function(
                f'() => ({VISIBLE_TILES})().some(tile => tile.dataset.imageState === "fallback")',
                timeout=3000,
            )
            assert map_pixel_holes(page) == []
            page.mouse.up()
            page.wait_for_function(
                f'''() => {{
                  const tiles = ({VISIBLE_TILES})();
                  return tiles.length > 0 && tiles.every(tile => tile.dataset.imageState === 'ready');
                }}''',
                timeout=30_000,
            )
            expect(page.locator('#mapStatus')).to_have_attribute('data-errors', '0')
            expect(page.locator('#retryMap')).to_be_hidden()
            assert map_pixel_holes(page) == []
            assert not errors
        finally:
            page.close()
            browser.close()


@pytest.mark.e2e
@pytest.mark.parametrize("viewport", [{"width": 1000, "height": 850}, {"width": 390, "height": 844}])
def test_office_map_starts_at_center_and_navigates_complete_coverage(slow_tile_map, viewport):
    executable = next((path for path in (
        Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
        Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
    ) if path.is_file()), None)
    if executable is None:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            pytest.fail('Se necesita Chrome o Edge')
        pytest.skip('Se necesita Chrome o Edge')
    url, state, lon, lat = slow_tile_map
    app = state['app']
    app.state.settings.full_orthophoto = True
    base = app.state.settings.data_dir / 'tiles' / 'navigation'
    (base / 'source.vrt').write_text('<VRTDataset/>')
    (base / 'sources.json').write_text(json.dumps({'mode': 'FULL', 'bounds': [[lat - .2, lon - .2], [lat + .2, lon + .2]], 'sources': ['source.vrt'], 'cache_key': 'a' * 64}))
    with app.state.Session() as db:
        window = db.get(TileWindow, 1)
        window.render_mode, window.min_zoom = 'FULL', 10
        db.commit()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(executable), headless=True)
        page = browser.new_page(viewport=viewport)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            expect(page.locator('#map')).to_have_attribute('data-image-ready', 'true', timeout=30000)
            assert abs(float(page.locator('#map').get_attribute('data-center-lon')) - lon) < .001
            assert abs(float(page.locator('#map').get_attribute('data-center-lat')) - lat) < .001
            controls = page.get_by_role('button', name='Ver ortofoto completa').bounding_box()
            zoom_control = page.locator('.leaflet-control-zoom').bounding_box()
            assert controls['x'] >= zoom_control['x'] + zoom_control['width'] or controls['y'] >= zoom_control['y'] + zoom_control['height']
            page.get_by_role('button', name='Ver ortofoto completa').click()
            page.wait_for_function("Number(document.getElementById('map').dataset.zoom) <= 12")
            box = page.locator('#map').bounding_box()
            page.mouse.move(box['x'] + box['width'] * .75, box['y'] + box['height'] * .5)
            page.mouse.down()
            page.mouse.move(box['x'] + box['width'] * .25, box['y'] + box['height'] * .5, steps=10)
            page.mouse.up()
            page.wait_for_function("Math.abs(Number(document.getElementById('map').dataset.centerLon) - " + str(lon) + ") > .03")
            page.get_by_role('button', name='Punto inicial').click()
            page.wait_for_function("Math.abs(Number(document.getElementById('map').dataset.centerLon) - " + str(lon) + ") < .001")
            assert not errors
        finally:
            browser.close()
