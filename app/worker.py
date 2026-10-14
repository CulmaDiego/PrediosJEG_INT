from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .catalog import find_municipality, raster_files, synchronize_municipalities
from .config import Settings
from .db import build_engine, build_session_factory
from .gis import bounds_intersect, inspect_raster, metres_bounds, qgis_environment, qgis_tool, tile_range_for_bounds
from .models import Job, OrthophotoSource, Person, TileConfiguration, TileWindow, utcnow


def _tile_radius(db: Session, settings: Settings) -> int:
    configuration = db.get(TileConfiguration, 1)
    return configuration.radius_m if configuration else settings.window_radius_m

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
    synchronize_municipalities(db, settings.orthophoto_root)
    db.flush()
    files = raster_files(settings.orthophoto_root)
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(settings.orthophoto_root).as_posix()
        source = db.scalar(select(OrthophotoSource).where(OrthophotoSource.relative_path == relative))
        if not source:
            source = OrthophotoSource(relative_path=relative, inferred_name=path.relative_to(settings.orthophoto_root).parts[0])
            db.add(source)
        if source.municipality_id is None and source.inferred_name:
            municipality = find_municipality(db, source.inferred_name)
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


def _overview_min_zoom(person: Person, radius_m: int, settings: Settings) -> int:
    """El zoom inicial debe mostrar el predio grande con pocas teselas."""
    for zoom in range(settings.tile_min_zoom, 7, -1):
        min_x, min_y, max_x, max_y = tile_range_for_bounds(person.reference_lon, person.reference_lat, radius_m, zoom)
        if (max_x - min_x + 1) * (max_y - min_y + 1) <= 16:
            return zoom
    return 8


def _clear_superseded_tile_failures(db: Session, person: Person, current_job_id: int) -> None:
    """Close obsolete tile errors after a newer generation succeeds."""
    if person.status == "ERROR":
        person.status = "PENDIENTE"
    failed_jobs = db.scalars(
        select(Job).where(
            Job.job_type == "PREPARE_WINDOW",
            Job.status == "ERROR",
            Job.id != current_job_id,
        )
    ).all()
    for failed_job in failed_jobs:
        try:
            failed_person_id = int(json.loads(failed_job.payload_json).get("person_id"))
        except (AttributeError, TypeError, ValueError):
            continue
        if failed_person_id == person.id:
            failed_job.status = "SUPERADO"
            failed_job.progress = 100
            failed_job.error = None


def prepare_window(db: Session, settings: Settings, job: Job, person_id: int, radius_m: int) -> None:
    job_id = job.id
    person = db.get(Person, person_id)
    if not person or person.reference_lon is None or person.reference_lat is None:
        raise RuntimeError("La persona no existe o no tiene coordenadas")
    window = db.get(TileWindow, person_id)
    if window is None:
        window = TileWindow(person_id=person_id, status="PENDIENTE")
        db.add(window)
    previous_dir = window.relative_dir
    sources = db.scalars(
        select(
            OrthophotoSource,
        ).where(
            OrthophotoSource.municipality_id == person.municipality_id,
            OrthophotoSource.status == "LISTO",
        )
    ).all()
    requested_bounds = metres_bounds(person.reference_lon, person.reference_lat, radius_m)
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
    vrt_path = output_dir / "source.vrt"
    xmin, ymin, xmax, ymax = requested_bounds
    try:
        warp = subprocess.run(
            [
                str(qgis_tool(settings, "gdalwarp")),
                "-overwrite",
                "-of",
                "VRT",
                "-b",
                "1",
                "-b",
                "2",
                "-b",
                "3",
                "-dstalpha",
                "-te_srs",
                "EPSG:9377",
                "-te",
                str(xmin),
                str(ymin),
                str(xmax),
                str(ymax),
                "-t_srs",
                "EPSG:3857",
                "-r",
                "bilinear",
                str(source_path),
                str(vrt_path),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            env=qgis_environment(settings),
        )
        if warp.returncode != 0 or not vrt_path.is_file():
            raise RuntimeError(f"gdalwarp: {warp.stderr.strip() or warp.stdout.strip()}")
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    db.expire_all()
    person = db.get(Person, person_id)
    job = db.get(Job, job_id)
    if person is None or job is None:
        shutil.rmtree(output_dir, ignore_errors=True)
        return
    window = db.get(TileWindow, person_id) or TileWindow(person_id=person_id)
    db.add(window)
    window.source_id = source.id
    window.relative_dir = relative_dir
    window.min_zoom = _overview_min_zoom(person, radius_m, settings)
    window.max_zoom = settings.tile_max_zoom
    window.radius_m = radius_m
    window.render_mode = "ON_DEMAND"
    window.status = "LISTO"
    window.error = None
    person.orthophoto_status = "LISTO"
    person.orthophoto_error = None
    _clear_superseded_tile_failures(db, person, job_id)
    job.progress = 95
    db.commit()
    if previous_dir and previous_dir != relative_dir:
        old = (settings.data_dir / "tiles" / previous_dir).resolve()
        tiles_root = (settings.data_dir / "tiles").resolve()
        try:
            old.relative_to(tiles_root)
        except ValueError:
            return
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)

def execute_job(db: Session, settings: Settings, job: Job) -> None:
    payload = json.loads(job.payload_json)
    if job.job_type == "SCAN_CATALOG":
        scan_catalog(db, settings, job)
    elif job.job_type == "PREPARE_WINDOW":
        radius_m = int(payload.get("radius_m", _tile_radius(db, settings)))
        if not 100 <= radius_m <= settings.max_tile_radius_m:
            raise RuntimeError("El radio de teselas solicitado no es válido")
        prepare_window(db, settings, job, int(payload["person_id"]), radius_m)
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
                job_id = job.id
                try:
                    execute_job(db, settings, job)
                    job = db.get(Job, job_id)
                    if job is None:
                        continue
                    job.status = "COMPLETADO"
                    job.progress = 100
                    job.error = None
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    failed = db.get(Job, job_id)
                    if failed is None:
                        continue
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
