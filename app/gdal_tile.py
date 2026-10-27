"""Run with QGIS's Python: read originals once and write a masked PNG tile."""
from __future__ import annotations

import json
import math
import sys
from collections import OrderedDict
from pathlib import Path

from osgeo import gdal, osr

DATASETS = OrderedDict()


def source_dataset(path):
    key = (str(path), path.stat().st_mtime_ns)
    if key not in DATASETS:
        DATASETS[key] = gdal.Open(str(path), gdal.GA_ReadOnly)
    DATASETS.move_to_end(key)
    while len(DATASETS) > 16:
        DATASETS.popitem(last=False)
    return DATASETS[key]


def render(manifest_path: Path, output: Path, bounds: tuple[float, ...]) -> None:
    gdal.UseExceptions()
    osr.UseExceptions()
    base = manifest_path.parent.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = []
    for name in manifest["sources"]:
        path = (base / name).resolve()
        if path.parent != base or path.suffix != ".vrt" or not path.is_file():
            raise RuntimeError("La fuente privada no es válida")
        sources.append(source_dataset(path))
    if manifest.get("mode") == "FULL":
        tile = gdal.Warp("", sources, format="MEM", dstSRS="EPSG:3857", outputBounds=bounds,
                         width=256, height=256, resampleAlg="cubic", dstAlpha=True,
                         errorThreshold=0, warpMemoryLimit=64)
        if tile is None or tile.RasterCount != 4:
            raise RuntimeError("No se produjo una imagen RGB con transparencia")
    else:
        cutline = base / "cutline.geojson"
        if not sources or not cutline.is_file():
            raise RuntimeError("No se encontró la máscara del área autorizada")
        tile = gdal.Warp("", sources, format="MEM", dstSRS="EPSG:3857", outputBounds=bounds,
                         width=256, height=256, resampleAlg="cubic", dstAlpha=True,
                         cutlineDSName=str(cutline), errorThreshold=0, warpMemoryLimit=64)
        if tile is None or tile.RasterCount != 4:
            raise RuntimeError("No se produjo una imagen RGB con transparencia")
        # Cutlines are rasterized in source pixels. Enforce the exact metric radius
        # again on output pixel centers, and clear RGB too, so transparency cannot
        # be removed to reveal imagery outside the permitted area.
        source_crs, metric_crs = osr.SpatialReference(), osr.SpatialReference()
        source_crs.ImportFromEPSG(3857)
        metric_crs.ImportFromEPSG(9377)
        for crs in (source_crs, metric_crs):
            crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transformer = osr.CoordinateTransformation(source_crs, metric_crs)
        west, south, east, north = bounds
        points = [(west + (col + .5) * (east - west) / 256, north - (row + .5) * (north - south) / 256)
                  for row in range(256) for col in range(256)]
        projected = transformer.TransformPoints(points)
        cx, cy = manifest["center"]
        radius_squared = manifest["radius_m"] ** 2
        hidden = [index for index, (x, y, *_) in enumerate(projected) if (x - cx) ** 2 + (y - cy) ** 2 > radius_squared]
        ref_x, ref_y, _ = osr.CoordinateTransformation(metric_crs, source_crs).TransformPoint(cx, cy)
        if west <= ref_x < east and south < ref_y <= north:
            col = min(255, math.floor((ref_x - west) * 256 / (east - west)))
            row = min(255, math.floor((north - ref_y) * 256 / (north - south)))
            if not tile.GetRasterBand(4).ReadRaster(col, row, 1, 1, buf_type=gdal.GDT_Byte)[0]:
                raise RuntimeError("La ortofoto no tiene imagen válida en el punto de referencia")
        for index in range(1, 5):
            band = tile.GetRasterBand(index)
            pixels = bytearray(band.ReadRaster(buf_type=gdal.GDT_Byte))
            for position in hidden:
                pixels[position] = 0
            band.WriteRaster(0, 0, 256, 256, bytes(pixels), buf_type=gdal.GDT_Byte)
    png = gdal.Translate(str(output), tile, format="PNG", outputType=gdal.GDT_Byte)
    if png is None:
        raise RuntimeError("No se produjo una tesela PNG válida")
    png = None


if __name__ == "__main__":
    if sys.argv[1] == "--serve":
        gdal.SetCacheMax(128 * 1024 * 1024)
        for line in sys.stdin:
            try:
                request = json.loads(line)
                render(Path(request['manifest']), Path(request['output']), tuple(request['bounds']))
                result = {"ok": True}
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            print(json.dumps(result), flush=True)
    else:
        render(Path(sys.argv[1]), Path(sys.argv[2]), tuple(map(float, sys.argv[3:7])))
