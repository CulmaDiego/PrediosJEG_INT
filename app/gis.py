from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform

from .config import Settings


WGS84_TO_COLOMBIA = Transformer.from_crs("EPSG:4326", "EPSG:9377", always_xy=True)


def validate_polygon(geometry: dict, reference_lon: float, reference_lat: float, maximum_distance_m: int | None = 300) -> tuple[dict, float]:
    if maximum_distance_m is not None and maximum_distance_m < 1:
        raise ValueError("La distancia máxima del predio debe ser positiva")
    if geometry.get("type") != "Polygon":
        raise ValueError("Solo se acepta una geometría Polygon")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 1:
        raise ValueError("El polígono debe tener un solo anillo y no puede contener huecos")
    ring = coordinates[0]
    if not isinstance(ring, list) or not 4 <= len(ring) <= 101:
        raise ValueError("El polígono debe tener entre 3 y 100 vértices")
    if ring[0] != ring[-1]:
        raise ValueError("El anillo debe estar cerrado")
    for coordinate in ring:
        if not isinstance(coordinate, list) or len(coordinate) < 2:
            raise ValueError("Hay una coordenada inválida")
        lon, lat = coordinate[:2]
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            raise ValueError("Las coordenadas deben ser numéricas")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Las coordenadas deben estar en WGS84")
    polygon = shape(geometry)
    if polygon.is_empty or not polygon.is_valid:
        raise ValueError("El polígono se autointerseca o no es válido")
    projected = transform(WGS84_TO_COLOMBIA.transform, polygon)
    if projected.area <= 0:
        raise ValueError("El polígono debe tener un área positiva")
    reference = Point(*WGS84_TO_COLOMBIA.transform(reference_lon, reference_lat))
    maximum_distance = max(reference.distance(Point(*WGS84_TO_COLOMBIA.transform(*coord[:2]))) for coord in ring[:-1])
    if maximum_distance_m is not None and maximum_distance > maximum_distance_m:
        raise ValueError(f"Todo el polígono debe quedar a máximo {maximum_distance_m} m del punto de referencia")
    normalized = json.loads(json.dumps(geometry, separators=(",", ":")))
    return normalized, round(projected.area, 2)


def qgis_environment(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    root = settings.qgis_root
    proj_candidates = [root / "share" / "proj", root / "apps" / "proj" / "share" / "proj"]
    proj_dir = next((candidate for candidate in proj_candidates if (candidate / "proj.db").is_file()), None)
    if proj_dir is None:
        raise FileNotFoundError(f"No se encontró proj.db dentro de {root}; compruebe la instalación de QGIS")
    env["GDAL_DATA"] = str(root / "apps" / "gdal" / "share" / "gdal")
    env["PROJ_DATA"] = str(proj_dir)
    env["PROJ_LIB"] = str(proj_dir)
    env["GDAL_DRIVER_PATH"] = str(root / "apps" / "gdal" / "lib" / "gdalplugins")
    env["GTIFF_SRS_SOURCE"] = "EPSG"
    env["PATH"] = f"{root / 'bin'};{env.get('PATH', '')}"
    return env


def qgis_tool(settings: Settings, name: str) -> Path:
    candidates = [
        settings.qgis_root / "bin" / f"{name}.exe",
        settings.qgis_root / "bin" / f"{name}.py",
    ]
    candidates.extend(
        python.parent / "Scripts" / f"{name}.py"
        for python in sorted(settings.qgis_root.glob("apps/Python3*/python.exe"), reverse=True)
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No se encontró {name} dentro de {settings.qgis_root}")


def qgis_command(settings: Settings, name: str) -> list[str]:
    tool = qgis_tool(settings, name)
    if tool.suffix.lower() == ".py":
        bundled_python = tool.parent.parent / "python.exe" if tool.parent.name == "Scripts" else None
        if bundled_python is None or not bundled_python.is_file():
            bundled_python = next(iter(sorted(settings.qgis_root.glob("apps/Python3*/python.exe"), reverse=True)), None)
        if bundled_python is None:
            raise FileNotFoundError(f"No se encontró el Python de QGIS para ejecutar {tool}")
        return [str(bundled_python), str(tool)]
    return [str(tool)]


def inspect_raster(settings: Settings, source: Path) -> dict:
    process = subprocess.run(
        [str(qgis_tool(settings, "gdalinfo")), "-json", str(source)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=qgis_environment(settings),
    )
    if process.returncode != 0:
        raise RuntimeError((process.stderr or process.stdout).strip())
    output = process.stdout
    start = output.find("{")
    if start < 0:
        raise RuntimeError("GDAL no devolvió metadatos JSON")
    return json.loads(output[start:])


def metres_bounds(lon: float, lat: float, radius_m: int) -> tuple[float, float, float, float]:
    x, y = WGS84_TO_COLOMBIA.transform(lon, lat)
    return x - radius_m, y - radius_m, x + radius_m, y + radius_m


def bounds_intersect(a: list[float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def tile_xy(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def verify_generated_tiles(output_dir: Path, lon: float, lat: float, min_zoom: int, max_zoom: int) -> int:
    verified = 0
    for zoom in range(min_zoom, max_zoom + 1):
        x, y = tile_xy(lon, lat, zoom)
        center_tile = output_dir / str(zoom) / str(x) / f"{y}.png"
        if not center_tile.is_file() or center_tile.stat().st_size < 8:
            raise RuntimeError(f"La verificación no encontró una tesela en el punto de referencia para el zoom {zoom}")
        with center_tile.open("rb") as file:
            if file.read(8) != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError(f"La tesela central del zoom {zoom} no es un PNG válido")
        verified += 1
    return verified


def tile_range_for_bounds(lon: float, lat: float, radius_m: int, zoom: int) -> tuple[int, int, int, int]:
    lat_delta = radius_m / 111_320
    lon_delta = radius_m / (111_320 * max(math.cos(math.radians(lat)), 0.2))

    min_x, max_y = tile_xy(lon - lon_delta, lat - lat_delta, zoom)
    max_x, min_y = tile_xy(lon + lon_delta, lat + lat_delta, zoom)
    return min_x, min_y, max_x, max_y
