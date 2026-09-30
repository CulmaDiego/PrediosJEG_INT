from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
import uuid
from datetime import timedelta
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .db import build_engine, build_session_factory
from .gis import bounds_intersect, inspect_raster, metres_bounds, qgis_command, qgis_environment, qgis_tool
from .models import Job, Municipality, OrthophotoSource, Person, TileWindow, utcnow


def claim_job(db: Session, worker_id: str) -> Job | None:
    stale = utcnow() - timedelta(minutes=30)
    db.execute(update(Job).where(Job.status == "EJECUTANDO", Job.locked_at < stale).values(status="PENDIENTE", locked_by=None, locked_at=None, error="Reanudado después de un bloqueo vencido"))
    db.commit()
    candidate = db.scalar(select(Job).where(Job.status == "PENDIENTE").order_by(Job.created_at).limit(1))
    if not candidate:
        return None
    changed = db.execute(
        update(Job)
        .where(Job.id == candidate.id, Job.status == "PENDIENTE")
        .values(status="EJECUTANDO", locked_by=worker_id, locked_at=utcnow(), attempts=Job.attempts + 1, progress=1)
    ).rowcount
    db.commit()
    return db.get(Job, candidate.id) if changed == 1 else None


def _crs_label(metadata: dict) -> str | None:
    stac = metadata.get("stac", {})
    if stac.get("proj:epsg"):
        return f"EPSG:{stac['proj:epsg']}"
    wkt = metadata.get("coordinateSystem", {}).get("wkt", "")
    marker = 'ID["EPSG",'
    if marker in wkt:
        tail = wkt.rsplit(marker, 1)[1]
        return f"EPSG:{tail.split(']', 1)[0]}"
    return wkt.split("\n", 1)[0][:100] or None


def scan_catalog(db: Session, settings: Settings, job: Job) -> None:
    files = sorted(path for path in settings.orthophoto_root.rglob("*") if path.is_file() and path.suffix.lower() in {".ecw", ".tif", ".tiff"})
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(settings.orthophoto_root).as_posix()
        source = db.scalar(select(OrthophotoSource).where(OrthophotoSource.relative_path == relative))
        if not source:
            source = OrthophotoSource(relative_path=relative, inferred_name=path.relative_to(settings.orthophoto_root).parts[0])
            db.add(source)
        if source.municipality_id is None and source.inferred_name:
            municipality = db.scalar(select(Municipality).where(Municipality.name == source.inferred_name))
            if municipality:
                source.municipality_id = municipality.id
        try:
            metadata = inspect_raster(settings, path)
            transform = metadata.get("geoTransform", [])
            corners = metadata.get("cornerCoordinates", {})
            ul, lr = corners.get("upperLeft"), corners.get("lowerRight")
            source.driver = metadata.get("driverShortName")
            source.width, source.height = metadata.get("size", [None, None])
            source.crs = _crs_label(metadata)
            source.resolution_x = abs(float(transform[1])) if len(transform) > 5 else None
            source.resolution_y = abs(float(transform[5])) if len(transform) > 5 else None
            source.bounds_json = json.dumps([min(ul[0], lr[0]), min(ul[1], lr[1]), max(ul[0], lr[0]), max(ul[1], lr[1])]) if ul and lr else None
            source.status = "LISTO"
            source.error = None
        except Exception as exc:
            source.status = "ERROR"
            source.error = str(exc)[:4000]
        source.scanned_at = utcnow()
        job.progress = int(index / max(len(files), 1) * 100)
        db.commit()


def prepare_window(db: Session, settings: Settings, job: Job, person_id: int) -> None:
    person = db.get(Person, person_id)
    if not person or person.reference_lon is None or person.reference_lat is None:
        raise RuntimeError("La persona no existe o no tiene coordenadas")
    window = db.get(TileWindow, person_id) or TileWindow(person_id=person_id)
    db.add(window)
    window.status = "PENDIENTE"
    window.error = None
    person.orthophoto_status = "PENDIENTE"
    person.orthophoto_error = None
    sources = db.scalars(select(OrthophotoSource).where(OrthophotoSource.municipality_id == person.municipality_id, OrthophotoSource.status == "LISTO")).all()
    requested_bounds = metres_bounds(person.reference_lon, person.reference_lat, settings.window_radius_m)
    source = next((candidate for candidate in sources if candidate.bounds_json and bounds_intersect(json.loads(candidate.bounds_json), requested_bounds)), None)
    if source is None:
        window.status = "SIN_ORTOFOTO"
        window.error = "Ninguna ortofoto asignada cubre la coordenada de referencia"
        person.orthophoto_status = "SIN_ORTOFOTO"
        person.orthophoto_error = window.error
        db.commit()
        return
    source_path = (settings.orthophoto_root / source.relative_path).resolve()
    try:
        source_path.relative_to(settings.orthophoto_root.resolve())
    except ValueError:
        raise RuntimeError("La ruta de ortofoto sale de la carpeta permitida")
    relative_dir = f"person_{person.id}_{uuid.uuid4().hex[:12]}"
    output_dir = settings.data_dir / "tiles" / relative_dir
    output_dir.mkdir(parents=True, exist_ok=False)
    vrt_path = settings.data_dir / "tmp" / f"{relative_dir}.vrt"
    xmin, ymin, xmax, ymax = requested_bounds
    warp = subprocess.run(
        [str(qgis_tool(settings, "gdalwarp")), "-overwrite", "-of", "VRT", "-b", "1", "-b", "2", "-b", "3", "-dstalpha", "-te_srs", "EPSG:9377", "-te", str(xmin), str(ymin), str(xmax), str(ymax), "-t_srs", "EPSG:3857", "-r", "bilinear", str(source_path), str(vrt_path)],
        capture_output=True,
        text=True,
        timeout=600,
        env=qgis_environment(settings),
    )
    if warp.returncode != 0:
        raise RuntimeError(f"gdalwarp: {warp.stderr.strip()}")
    job.progress = 35
    db.commit()
    gdal2tiles = qgis_command(settings, "gdal2tiles")
    tiles = subprocess.run(
        [*gdal2tiles, "--xyz", "--webviewer=none", "--processes=1", "--zoom", f"{settings.tile_min_zoom}-{settings.tile_max_zoom}", str(vrt_path), str(output_dir)],
        capture_output=True,
        text=True,
        timeout=3600,
        env=qgis_environment(settings),
    )
    vrt_path.unlink(missing_ok=True)
    if tiles.returncode != 0:
        import shutil

        shutil.rmtree(output_dir, ignore_errors=True)
        raise RuntimeError(f"gdal2tiles: {tiles.stderr.strip()}")
    if window.relative_dir:
        old = (settings.data_dir / "tiles" / window.relative_dir).resolve()
        if old.is_dir() and old != output_dir.resolve():
            import shutil

            shutil.rmtree(old)
    window.source_id = source.id
    window.relative_dir = relative_dir
    window.min_zoom = settings.tile_min_zoom
    window.max_zoom = settings.tile_max_zoom
    window.status = "LISTO"
    window.error = None
    person.orthophoto_status = "LISTO"
    person.orthophoto_error = None
    db.commit()


def execute_job(db: Session, settings: Settings, job: Job) -> None:
    payload = json.loads(job.payload_json)
    if job.job_type == "SCAN_CATALOG":
        scan_catalog(db, settings, job)
    elif job.job_type == "PREPARE_WINDOW":
        prepare_window(db, settings, job, int(payload["person_id"]))
    else:
        raise RuntimeError(f"Tipo de trabajo desconocido: {job.job_type}")


def run(once: bool = False) -> int:
    settings = Settings.from_env()
    settings.ensure_directories()
    factory = build_session_factory(build_engine(settings.database_url))
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    while True:
        with factory() as db:
            job = claim_job(db, worker_id)
            if job:
                try:
                    execute_job(db, settings, job)
                    job = db.get(Job, job.id)
                    job.status = "COMPLETADO"
                    job.progress = 100
                    job.error = None
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    failed = db.get(Job, job.id)
                    failed.status = "ERROR"
                    failed.error = str(exc)[:4000]
                    if failed.job_type == "PREPARE_WINDOW":
                        person_id = int(json.loads(failed.payload_json).get("person_id", 0))
                        person = db.get(Person, person_id)
                        window = db.get(TileWindow, person_id)
                        if person:
                            person.status = "ERROR"
                            person.orthophoto_status = "ERROR"
                            person.orthophoto_error = failed.error
                        if window:
                            window.status = "ERROR"
                            window.error = failed.error
                    db.commit()
        if once:
            return 0
        time.sleep(2 if job else 5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.once))
