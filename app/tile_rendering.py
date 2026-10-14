from __future__ import annotations

import os
import math
import json
import queue
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
WEB_MERCATOR_TO_WGS84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


class RasterProcess:
    """A serial GDAL reader keeps original files open across tile requests."""

    def __init__(self, settings):
        python = next(iter(sorted(settings.qgis_root.glob("apps/Python3*/python.exe"), reverse=True)), None)
        if python is None:
            raise RuntimeError("No se encontró el Python de QGIS")
        self.process = subprocess.Popen(
            [str(python), str(Path(__file__).with_name("gdal_tile.py")), "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=qgis_environment(settings),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.responses = queue.Queue()
        self.diagnostics = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            try:
                response = json.loads(line)
            except ValueError:
                self.diagnostics = (self.diagnostics + [line.strip()])[-5:]
                continue
            self.responses.put(response)
        self.responses.put({"ok": False, "error": "GDAL se cerró. " + " ".join(self.diagnostics)})

    def render(self, manifest, output, bounds, timeout):
        self.process.stdin.write(json.dumps({"manifest": str(manifest), "output": str(output), "bounds": bounds}) + "\n")
        self.process.stdin.flush()
        try:
            response = self.responses.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise RuntimeError("Se agotó el tiempo para leer la ortofoto") from exc
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "GDAL no produjo una imagen"))

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is self.process.stdout:
                self.reader.join(timeout=1)
            stream.close()


def native_max_zoom(lat: float, resolution_m: float, fallback: int) -> int:
    if not math.isfinite(resolution_m) or resolution_m <= 0:
        return fallback
    metres_per_pixel_at_zero = 2 * WEB_MERCATOR_LIMIT * math.cos(math.radians(lat)) / 256
    return min(24, max(8, math.ceil(math.log2(metres_per_pixel_at_zero / resolution_m))))


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


def window_bounds_web_mercator(lon: float, lat: float, radius_m: int) -> list[float]:
    xmin, ymin, xmax, ymax = metres_bounds(lon, lat, radius_m)
    projected = [
        COLOMBIA_TO_WEB_MERCATOR.transform(xmin, ymin),
        COLOMBIA_TO_WEB_MERCATOR.transform(xmin, ymax),
        COLOMBIA_TO_WEB_MERCATOR.transform(xmax, ymin),
        COLOMBIA_TO_WEB_MERCATOR.transform(xmax, ymax),
    ]
    return [
        min(point[0] for point in projected),
        min(point[1] for point in projected),
        max(point[0] for point in projected),
        max(point[1] for point in projected),
    ]


def window_bounds_wgs84(lon: float, lat: float, radius_m: int) -> list[list[float]]:
    west, south, east, north = window_bounds_web_mercator(lon, lat, radius_m)
    return [list(reversed(WEB_MERCATOR_TO_WGS84.transform(west, south))), list(reversed(WEB_MERCATOR_TO_WGS84.transform(east, north)))]


def tile_intersects_window(lon: float, lat: float, radius_m: int, zoom: int, x: int, y: int) -> bool:
    return bounds_intersect(window_bounds_web_mercator(lon, lat, radius_m), tile_bounds_web_mercator(zoom, x, y))


def tile_cache_path(settings: Settings, relative_dir: str, zoom: int, x: int, y: int) -> Path:
    tile_bounds_web_mercator(zoom, x, y)
    def resolve(path):
        value = str(path.resolve())
        # Windows can add the extended-path prefix while another worker creates
        # a directory. Normalize that spelling after resolving junctions.
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        return Path(value)
    root = resolve(settings.data_dir / "tiles")
    base = resolve(root / relative_dir)
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise ValueError("Directorio de teselas inválido") from exc
    tile = resolve(base / str(zoom) / str(x) / f"{y}.png")
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
    """Lee las fuentes originales y limita cada tesela con la máscara privada."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._slots = threading.BoundedSemaphore(settings.dynamic_tile_workers)
        self._locks = [threading.Lock() for _ in range(128)]
        self._available = queue.LifoQueue()
        self._processes = []
        self._processes_guard = threading.Lock()

    def close(self):
        with self._processes_guard:
            for process in self._processes:
                process.close()
            self._processes.clear()
        while not self._available.empty():
            self._available.get_nowait()

    def _render_original(self, manifest, temporary, bounds):
        try:
            process = self._available.get_nowait()
        except queue.Empty:
            process = RasterProcess(self.settings)
            with self._processes_guard:
                self._processes.append(process)
        try:
            process.render(manifest, temporary, bounds, self.settings.dynamic_tile_timeout_seconds)
        except Exception:
            process.close()
            with self._processes_guard:
                self._processes.remove(process)
            raise
        else:
            self._available.put(process)

    def _lock_for(self, tile: Path) -> threading.Lock:
        return self._locks[hash(tile) % len(self._locks)]

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
                    manifest = vrt_path.parent / "sources.json"
                    if manifest.is_file():
                        self._render_original(manifest, temporary, (xmin, ymin, xmax, ymax))
                    else:
                        # Compatibility with windows prepared by previous versions.
                        command = [str(qgis_tool(self.settings, "gdal_translate")), "-of", "PNG", "-projwin_srs", "EPSG:3857",
                                   "-projwin", str(xmin), str(ymax), str(xmax), str(ymin), "-outsize", "256", "256", str(vrt_path), str(temporary)]
                        result = subprocess.run(command, capture_output=True, text=True,
                                                timeout=self.settings.dynamic_tile_timeout_seconds, env=qgis_environment(self.settings))
                        if result.returncode != 0:
                            raise RuntimeError(f"gdal_translate: {result.stderr.strip() or result.stdout.strip()}")
                if not _is_png(temporary):
                    raise RuntimeError("GDAL no produjo una tesela PNG válida")
                os.replace(temporary, tile)
                return tile
            finally:
                temporary.unlink(missing_ok=True)
                temporary.with_name(temporary.name + ".aux.xml").unlink(missing_ok=True)
