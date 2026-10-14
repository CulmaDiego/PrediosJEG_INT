from __future__ import annotations

import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from pyproj import Transformer

from .gis import bounds_intersect, metres_bounds, qgis_environment, qgis_tool

if TYPE_CHECKING:
    from .config import Settings


WEB_MERCATOR_LIMIT = 20_037_508.342789244
COLOMBIA_TO_WEB_MERCATOR = Transformer.from_crs("EPSG:9377", "EPSG:3857", always_xy=True)


def tile_bounds_web_mercator(zoom: int, x: int, y: int) -> tuple[float, float, float, float]:
    if not 0 <= zoom <= 24:
        raise ValueError("Nivel de zoom inválido")
    tiles = 2**zoom
    if not 0 <= x < tiles or not 0 <= y < tiles:
        raise ValueError("Coordenada de tesela inválida")
    width = (WEB_MERCATOR_LIMIT * 2) / tiles
    xmin = -WEB_MERCATOR_LIMIT + x * width
    xmax = xmin + width
    ymax = WEB_MERCATOR_LIMIT - y * width
    ymin = ymax - width
    return xmin, ymin, xmax, ymax


def tile_intersects_window(lon: float, lat: float, radius_m: int, zoom: int, x: int, y: int) -> bool:
    xmin, ymin, xmax, ymax = metres_bounds(lon, lat, radius_m)
    projected = [
        COLOMBIA_TO_WEB_MERCATOR.transform(xmin, ymin),
        COLOMBIA_TO_WEB_MERCATOR.transform(xmin, ymax),
        COLOMBIA_TO_WEB_MERCATOR.transform(xmax, ymin),
        COLOMBIA_TO_WEB_MERCATOR.transform(xmax, ymax),
    ]
    window_bounds = [
        min(point[0] for point in projected),
        min(point[1] for point in projected),
        max(point[0] for point in projected),
        max(point[1] for point in projected),
    ]
    return bounds_intersect(window_bounds, tile_bounds_web_mercator(zoom, x, y))


def tile_cache_path(settings: Settings, relative_dir: str, zoom: int, x: int, y: int) -> Path:
    tile_bounds_web_mercator(zoom, x, y)
    root = (settings.data_dir / "tiles").resolve()
    base = (root / relative_dir).resolve()
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise ValueError("Directorio de teselas inválido") from exc
    tile = (base / str(zoom) / str(x) / f"{y}.png").resolve()
    try:
        tile.relative_to(base)
    except ValueError as exc:
        raise ValueError("Ruta de tesela inválida") from exc
    return tile


def _is_png(path: Path) -> bool:
    try:
        return path.stat().st_size >= 8 and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


class DynamicTileRenderer:
    """Renderiza una sola tesela desde el VRT privado y la guarda en caché."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._slots = threading.BoundedSemaphore(settings.dynamic_tile_workers)
        self._locks: dict[Path, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, tile: Path) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(tile, threading.Lock())

    def render(self, vrt_path: Path, tile: Path, zoom: int, x: int, y: int) -> Path:
        if _is_png(tile):
            return tile
        lock = self._lock_for(tile)
        with lock:
            if _is_png(tile):
                return tile
            if not vrt_path.is_file():
                raise RuntimeError("No se encontró la fuente de la ortofoto")
            xmin, ymin, xmax, ymax = tile_bounds_web_mercator(zoom, x, y)
            tile.parent.mkdir(parents=True, exist_ok=True)
            temporary = tile.with_name(f".{tile.stem}.{uuid.uuid4().hex}.png")
            try:
                with self._slots:
                    result = subprocess.run(
                        [
                            str(qgis_tool(self.settings, "gdal_translate")),
                            "-of",
                            "PNG",
                            "-projwin_srs",
                            "EPSG:3857",
                            "-projwin",
                            str(xmin),
                            str(ymax),
                            str(xmax),
                            str(ymin),
                            "-outsize",
                            "256",
                            "256",
                            str(vrt_path),
                            str(temporary),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=self.settings.dynamic_tile_timeout_seconds,
                        env=qgis_environment(self.settings),
                    )
                if result.returncode != 0:
                    raise RuntimeError(f"gdal_translate: {result.stderr.strip() or result.stdout.strip()}")
                if not _is_png(temporary):
                    raise RuntimeError("GDAL no produjo una tesela PNG válida")
                os.replace(temporary, tile)
                return tile
            finally:
                temporary.unlink(missing_ok=True)
