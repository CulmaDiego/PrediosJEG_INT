from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor

from .gis import metres_bounds, tile_xy
from .tile_rendering import COLOMBIA_TO_WEB_MERCATOR, WEB_MERCATOR_LIMIT, DynamicTileRenderer, tile_cache_path


def preload_tiles(lon: float, lat: float, radius_m: int, min_zoom: int, max_zoom: int) -> list[tuple[int, int, int]]:
    xmin, ymin, xmax, ymax = metres_bounds(lon, lat, radius_m)
    corners = [COLOMBIA_TO_WEB_MERCATOR.transform(x, y) for x in (xmin, xmax) for y in (ymin, ymax)]
    west, east = min(x for x, _ in corners), max(x for x, _ in corners)
    south, north = min(y for _, y in corners), max(y for _, y in corners)
    result = []
    for zoom in range(min_zoom, max_zoom + 1):
        size = 2 * WEB_MERCATOR_LIMIT / 2**zoom
        left, right = math.floor((west + WEB_MERCATOR_LIMIT) / size), math.floor((east + WEB_MERCATOR_LIMIT) / size)
        top, bottom = math.floor((WEB_MERCATOR_LIMIT - north) / size), math.floor((WEB_MERCATOR_LIMIT - south) / size)
        if (right - left + 1) * (bottom - top + 1) > 64:
            cx, cy = tile_xy(lon, lat, zoom)
            left, right = max(left, cx - 2), min(right, cx + 2)
            top, bottom = max(top, cy - 2), min(bottom, cy + 2)
        result.extend((zoom, x, y) for x in range(left, right + 1) for y in range(top, bottom + 1))
    return result


def warm_window(renderer: DynamicTileRenderer, relative_dir: str, lon: float, lat: float, radius_m: int,
                min_zoom: int, max_zoom: int, progress=None) -> int:
    tiles = preload_tiles(lon, lat, radius_m, min_zoom, max_zoom)
    base = renderer.settings.data_dir / 'tiles' / relative_dir
    def render(coordinates):
        zoom, x, y = coordinates
        renderer.render(base / 'source.vrt', tile_cache_path(renderer.settings, relative_dir, zoom, x, y), zoom, x, y)
    with ThreadPoolExecutor(max_workers=renderer.settings.dynamic_tile_workers) as executor:
        for index, _ in enumerate(executor.map(render, tiles), 1):
            if progress:
                progress(index, len(tiles))
    marker = base / 'preload.json'
    temporary = marker.with_suffix('.tmp')
    temporary.write_text(json.dumps({'version': 1, 'tiles': tiles}), encoding='utf-8')
    temporary.replace(marker)
    return len(tiles)
