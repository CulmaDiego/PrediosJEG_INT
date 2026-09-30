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


def validate_polygon(geometry: dict, reference_lon: float, reference_lat: float) -> tuple[dict, float]:
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
    if maximum_distance > 300:
        raise ValueError("Todo el polígono debe quedar a máximo 300 m del punto de referencia")
    normalized = json.loads(json.dumps(geometry, separators=(",", ":")))
    return normalized, round(projected.area, 2)


def qgis_environment(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    root = settings.qgis_root
    env["GDAL_DATA"] = str(root / "apps" / "gdal" / "share" / "gdal")
    env["PROJ_LIB"] = str(root / "share" / "proj")
    env["PROJ_DATA"] = env["PROJ_LIB"]
    env["GDAL_DRIVER_PATH"] = str(root / "apps" / "gdal" / "lib" / "gdalplugins")
    env["GTIFF_SRS_SOURCE"] = "EPSG"
    env["PATH"] = f"{root / 'bin'};{env.get('PATH', '')}"
    return env


def qgis_tool(settings: Settings, name: str) -> Path:
    candidates = [
        settings.qgis_root / "bin" / f"{name}.exe",
        settings.qgis_root / "bin" / f"{name}.py",
        settings.qgis_root / "apps" / "Python312" / "Scripts" / f"{name}.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No se encontró {name} dentro de {settings.qgis_root}")


def qgis_command(settings: Settings, name: str) -> list[str]:
    tool = qgis_tool(settings, name)
    if tool.suffix.lower() == ".py":
        return [str(settings.qgis_root / "apps" / "Python312" / "python.exe"), str(tool)]
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


def tile_range_for_bounds(lon: float, lat: float, radius_m: int, zoom: int) -> tuple[int, int, int, int]:
    lat_delta = radius_m / 111_320
    lon_delta = radius_m / (111_320 * max(math.cos(math.radians(lat)), 0.2))

    def tile_xy(x_lon: float, y_lat: float) -> tuple[int, int]:
        n = 2**zoom
        x = int((x_lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(y_lat))) / math.pi) / 2.0 * n)
        return x, y

    min_x, max_y = tile_xy(lon - lon_delta, lat - lat_delta)
    max_x, min_y = tile_xy(lon + lon_delta, lat + lat_delta)
    return min_x, min_y, max_x, max_y
