from __future__ import annotations

import io
import json
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .config import REPO_ROOT, Settings
from .db import Base, build_engine, build_session_factory
from .gis import qgis_environment, qgis_tool, validate_polygon
from .importer import validate_import
from .models import (
    Admin,
    AuditLog,
    DrawingVersion,
    ImportBatch,
    Job,
    Municipality,
    OrthophotoSource,
    Person,
    PublicLink,
    TileWindow,
    utcnow,
)
from .security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_admin_session,
    current_admin_session,
    find_public_link,
    ip_fingerprint,
    public_csrf,
    rate_limiter,
    require_admin,
    stable_public_token,
    verify_csrf,
    verify_password,
    verify_public_csrf,
)


templates = Jinja2Templates(directory=str(REPO_ROOT / "app" / "templates"))


def get_db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.Session
    db = factory()
    try:
        yield db
    finally:
        db.close()


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200) -> HTMLResponse:
    values = {"request": request, "settings": request.app.state.settings, **(context or {})}
    return templates.TemplateResponse(request=request, name=name, context=values, status_code=status_code)


def _job(db: Session, job_type: str, payload: dict) -> Job:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    existing = db.scalar(
        select(Job).where(Job.job_type == job_type, Job.payload_json == serialized, Job.status.in_({"PENDIENTE", "EJECUTANDO"}))
    )
    if existing:
        return existing
    job = Job(job_type=job_type, payload_json=serialized, status="PENDIENTE")
    db.add(job)
    return job


def _audit(db: Session, request: Request, action: str, actor: str, person_id: int | None = None, details: dict | None = None) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            person_id=person_id,
            details_json=json.dumps(details or {}, ensure_ascii=False),
            ip_hash=ip_fingerprint(request, request.app.state.settings),
        )
    )


def _safe_next(value: str | None) -> str:
    return value if value and value.startswith("/admin") and "//" not in value else "/admin"


def _public_url(person_id: int, settings: Settings) -> str:
    return f"{settings.public_base_url}/p/{stable_public_token(person_id, settings)}"


def _links_workbook(people: list[Person], settings: Settings) -> io.BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Enlaces"
    sheet.append(["NOMBRE", "CELULAR", "MUNICIPIO", "ENLACE"])
    for person in people:
        sheet.append([person.name, person.phone, person.municipality.name, _public_url(person.id, settings)])
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _quarantine_artifacts(paths: list[Path], settings: Settings) -> tuple[Path | None, list[tuple[Path, Path]]]:
    allowed_roots = [(settings.data_dir / name).resolve() for name in ("tiles", "imports", "exports")]
    safe_paths: list[Path] = []
    seen: set[Path] = set()
    for candidate in paths:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.exists():
            continue
        if not any(resolved != root and resolved.is_relative_to(root) for root in allowed_roots):
            raise RuntimeError(f"Ruta de datos fuera de las carpetas permitidas: {resolved}")
        seen.add(resolved)
        safe_paths.append(resolved)
    if not safe_paths:
        return None, []
    quarantine = settings.data_dir / "tmp" / f"eliminacion_{uuid.uuid4().hex}"
    quarantine.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    try:
        for index, original in enumerate(safe_paths):
            destination = quarantine / f"{index:04d}_{original.name}"
            shutil.move(str(original), str(destination))
            moved.append((original, destination))
    except Exception:
        for original, destination in reversed(moved):
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(original))
        shutil.rmtree(quarantine, ignore_errors=True)
        raise
    return quarantine, moved


def _restore_quarantine(quarantine: Path | None, moved: list[tuple[Path, Path]]) -> None:
    for original, destination in reversed(moved):
        if destination.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(original))
    if quarantine:
        shutil.rmtree(quarantine, ignore_errors=True)


def _delete_people(db: Session, request: Request, actor: str, person_ids: set[int], settings: Settings) -> int:
    if not person_ids:
        return 0
    people = list(db.scalars(select(Person).where(Person.id.in_(person_ids))).all())
    if not people:
        return 0
    actual_ids = {person.id for person in people}
    phones = {person.phone for person in people}
    municipality_ids = {person.municipality_id for person in people}
    fully_deleted_municipalities = {
        municipality_id
        for municipality_id in municipality_ids
        if not db.scalar(
            select(func.count())
            .select_from(Person)
            .where(Person.municipality_id == municipality_id, Person.id.not_in(actual_ids))
        )
    }
    windows = list(db.scalars(select(TileWindow).where(TileWindow.person_id.in_(actual_ids))).all())
    batches = list(db.scalars(select(ImportBatch).where(ImportBatch.municipality_id.in_(municipality_ids))).all())
    related_batches: list[ImportBatch] = []
    for batch in batches:
        try:
            rows = json.loads(batch.rows_json)
        except (TypeError, json.JSONDecodeError):
            rows = []
        if batch.municipality_id in fully_deleted_municipalities or any(
            str(row.get("phone", "")) in phones for row in rows if isinstance(row, dict)
        ):
            related_batches.append(batch)
    jobs = list(db.scalars(select(Job).where(Job.job_type == "PREPARE_WINDOW")).all())
    related_job_ids: set[int] = set()
    for job in jobs:
        try:
            if int(json.loads(job.payload_json).get("person_id", 0)) in actual_ids:
                related_job_ids.add(job.id)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    artifacts = [settings.data_dir / "tiles" / window.relative_dir for window in windows if window.relative_dir]
    artifacts.extend(settings.data_dir / "imports" / batch.stored_name for batch in related_batches)
    export_root = settings.data_dir / "exports"
    artifacts.extend(export_root.iterdir())
    quarantine, moved = _quarantine_artifacts(artifacts, settings)
    try:
        db.execute(delete(AuditLog).where(AuditLog.person_id.in_(actual_ids)))
        db.execute(delete(PublicLink).where(PublicLink.person_id.in_(actual_ids)))
        db.execute(delete(DrawingVersion).where(DrawingVersion.person_id.in_(actual_ids)))
        db.execute(delete(TileWindow).where(TileWindow.person_id.in_(actual_ids)))
        if related_job_ids:
            db.execute(delete(Job).where(Job.id.in_(related_job_ids)))
        if related_batches:
            db.execute(delete(ImportBatch).where(ImportBatch.id.in_({batch.id for batch in related_batches})))
        db.execute(delete(Person).where(Person.id.in_(actual_ids)))
        _audit(
            db,
            request,
            "PERSONAS_ELIMINADAS",
            actor,
            details={"cantidad": len(actual_ids), "municipios": sorted(municipality_ids)},
        )
        db.commit()
    except Exception:
        db.rollback()
        _restore_quarantine(quarantine, moved)
        raise
    if quarantine:
        shutil.rmtree(quarantine)
    return len(actual_ids)


def create_app(settings: Settings | None = None, create_schema: bool = False) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    engine = build_engine(settings.database_url)
    if create_schema:
        Base.metadata.create_all(engine)
    app = FastAPI(title="Predios Remotos", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.Session = build_session_factory(engine)
    app.mount("/static", StaticFiles(directory=str(REPO_ROOT / "app" / "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > settings.max_upload_bytes + 1024 * 1024:
            return JSONResponse({"detail": "La solicitud supera el tamaño permitido"}, status_code=413)
        try:
            response = await call_next(request)
        except Exception:
            if settings.environment == "development":
                raise
            response = JSONResponse({"detail": "Ocurrió un error interno"}, status_code=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "script-src 'self' https://unpkg.com; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/ready")
    def ready(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
        checks = {"database": False, "data_dir": settings.data_dir.is_dir(), "orthophotos": settings.orthophoto_root.is_dir()}
        try:
            db.execute(select(func.count()).select_from(Municipality)).scalar_one()
            checks["database"] = True
        except Exception:
            pass
        status = 200 if all(checks.values()) else 503
        return JSONResponse({"status": "ready" if status == 200 else "not_ready", "checks": checks}, status_code=status)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/admin", status_code=303)

    @app.get("/admin/login", response_class=HTMLResponse)
    def login_page(request: Request, db: Session = Depends(get_db)) -> Response:
        if current_admin_session(request, db):
            return RedirectResponse("/admin", status_code=303)
        return render(request, "login.html")

    @app.post("/admin/login")
    def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next_path: str | None = Form(None, alias="next"),
        db: Session = Depends(get_db),
    ) -> Response:
        key = f"login:{request.client.host if request.client else 'unknown'}"
        rate_limiter.check(key, 8, 300)
        admin = db.scalar(select(Admin).where(Admin.username == username.strip()))
        if not admin or not verify_password(admin.password_hash, password):
            return render(request, "login.html", {"error": "Usuario o contraseña incorrectos"}, 401)
        token, csrf = create_admin_session(db, admin, settings)
        response = RedirectResponse(_safe_next(next_path), status_code=303)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=settings.session_hours * 3600)
        response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=settings.cookie_secure, samesite="strict", max_age=settings.session_hours * 3600)
        return response

    @app.post("/admin/logout")
    def logout(request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        db.delete(session)
        db.commit()
        response = RedirectResponse("/admin/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        response.delete_cookie(CSRF_COOKIE)
        return response

    @app.get("/admin", response_class=HTMLResponse)
    def dashboard(request: Request, municipality_id: int | None = None, db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        filters = [Person.active.is_(True)]
        if municipality_id:
            filters.append(Person.municipality_id == municipality_id)
        summary = db.execute(
            select(
                func.count(Person.id),
                func.sum(case((Person.status == "RESPONDIO", 1), else_=0)),
                func.sum(case((Person.status == "ABRIO", 1), else_=0)),
                func.sum(case((Person.orthophoto_status == "SIN_ORTOFOTO", 1), else_=0)),
                func.sum(case((Person.status == "ERROR", 1), else_=0)),
            ).where(*filters)
        ).one()
        total = int(summary[0] or 0)
        counts = {
            "total": total,
            "responded": int(summary[1] or 0),
            "opened": int(summary[2] or 0),
            "without_photo": int(summary[3] or 0),
            "errors": int(summary[4] or 0),
            "rate": round((int(summary[1] or 0) / total * 100), 1) if total else 0,
        }
        municipalities = db.scalars(select(Municipality).order_by(Municipality.name)).all()
        jobs = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(8)).all()
        return render(request, "dashboard.html", {"admin": session.admin, "counts": counts, "municipalities": municipalities, "selected_municipality": municipality_id, "jobs": jobs})

    @app.get("/admin/municipios", response_class=HTMLResponse)
    def municipalities_page(request: Request, db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        municipalities = db.scalars(select(Municipality).order_by(Municipality.name)).all()
        people_counts = dict(db.execute(select(Person.municipality_id, func.count(Person.id)).group_by(Person.municipality_id)).all())
        return render(request, "municipalities.html", {"admin": session.admin, "municipalities": municipalities, "people_counts": people_counts})

    @app.post("/admin/municipios")
    def municipality_create(request: Request, name: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        clean = " ".join(name.split())[:120]
        if not clean:
            raise HTTPException(422, "El nombre es obligatorio")
        db.add(Municipality(name=clean))
        _audit(db, request, "MUNICIPIO_CREADO", session.admin.username, details={"name": clean})
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Ya existe un municipio con ese nombre")
        return RedirectResponse("/admin/municipios", status_code=303)

    @app.post("/admin/municipios/{municipality_id}")
    def municipality_update(
        municipality_id: int,
        request: Request,
        name: str = Form(...),
        active: str | None = Form(None),
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
    ) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        municipality = db.get(Municipality, municipality_id)
        if not municipality:
            raise HTTPException(404)
        municipality.name = " ".join(name.split())[:120]
        municipality.active = active == "on"
        _audit(db, request, "MUNICIPIO_EDITADO", session.admin.username, details={"municipality_id": municipality_id})
        db.commit()
        return RedirectResponse("/admin/municipios", status_code=303)

    @app.post("/admin/municipios/{municipality_id}/personas/eliminar")
    def municipality_people_delete(
        municipality_id: int,
        request: Request,
        csrf_token: str = Form(...),
        db: Session = Depends(get_db),
    ) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        municipality = db.get(Municipality, municipality_id)
        if not municipality:
            raise HTTPException(404)
        person_ids = set(db.scalars(select(Person.id).where(Person.municipality_id == municipality_id)).all())
        deleted_count = _delete_people(db, request, session.admin.username, person_ids, settings)
        return RedirectResponse(f"/admin/municipios?eliminadas={deleted_count}", status_code=303)

    @app.get("/admin/personas", response_class=HTMLResponse)
    def people_page(
        request: Request,
        q: str = "",
        status: str = "",
        municipality_id: int | None = None,
        page: int = 1,
        db: Session = Depends(get_db),
    ) -> Response:
        session = require_admin(request, db)
        page = max(page, 1)
        allowed_statuses = {"PENDIENTE", "ABRIO", "RESPONDIO", "SIN_ORTOFOTO", "ERROR"}
        conditions = [Person.active.is_(True)]
        if q:
            safe_q = q[:100]
            conditions.append(or_(Person.name.ilike(f"%{safe_q}%"), Person.phone.ilike(f"%{safe_q}%")))
        if status in allowed_statuses:
            if status == "SIN_ORTOFOTO":
                conditions.append(Person.orthophoto_status == status)
            else:
                conditions.append(Person.status == status)
        if municipality_id:
            conditions.append(Person.municipality_id == municipality_id)
        total = db.scalar(select(func.count()).select_from(Person).where(*conditions)) or 0
        people = db.scalars(select(Person).where(*conditions).order_by(Person.updated_at.desc()).offset((page - 1) * 25).limit(25)).all()
        municipalities = db.scalars(select(Municipality).order_by(Municipality.name)).all()
        return render(request, "people.html", {"admin": session.admin, "people": people, "municipalities": municipalities, "filters": {"q": q, "status": status, "municipality_id": municipality_id}, "page": page, "pages": max(1, (total + 24) // 25)})

    @app.post("/admin/personas/eliminar")
    def people_delete_selected(
        request: Request,
        csrf_token: str = Form(...),
        person_ids: list[int] | None = Form(None),
        db: Session = Depends(get_db),
    ) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        selected = {person_id for person_id in person_ids or [] if person_id > 0}
        if not selected:
            return RedirectResponse("/admin/personas?eliminadas=0", status_code=303)
        deleted_count = _delete_people(db, request, session.admin.username, selected, settings)
        return RedirectResponse(f"/admin/personas?eliminadas={deleted_count}", status_code=303)

    @app.get("/admin/personas/{person_id}", response_class=HTMLResponse)
    def person_detail(person_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(404)
        events = db.scalars(select(AuditLog).where(AuditLog.person_id == person_id).order_by(AuditLog.created_at.desc()).limit(100)).all()
        drawings = db.scalars(select(DrawingVersion).where(DrawingVersion.person_id == person_id).order_by(DrawingVersion.version.desc())).all()
        window = db.get(TileWindow, person_id)
        return render(request, "person_detail.html", {"admin": session.admin, "person": person, "events": events, "drawings": drawings, "window": window, "public_url": _public_url(person.id, settings)})

    @app.post("/admin/personas/{person_id}/reiniciar")
    def person_reset(person_id: int, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        person = db.get(Person, person_id)
        if not person:
            raise HTTPException(404)
        deleted_versions = db.scalar(select(func.count()).select_from(DrawingVersion).where(DrawingVersion.person_id == person_id)) or 0
        db.execute(delete(DrawingVersion).where(DrawingVersion.person_id == person_id))
        person.status = "PENDIENTE"
        person.last_opened_at = None
        person.responded_at = None
        person.drawing_version = 0
        _audit(db, request, "RESPUESTA_REINICIADA", session.admin.username, person_id, {"versiones_eliminadas": deleted_versions})
        db.commit()
        return RedirectResponse(f"/admin/personas/{person_id}?reiniciado=1", status_code=303)

    @app.post("/admin/personas/{person_id}/eliminar")
    def person_delete(person_id: int, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        if not db.get(Person, person_id):
            raise HTTPException(404)
        deleted_count = _delete_people(db, request, session.admin.username, {person_id}, settings)
        return RedirectResponse(f"/admin/personas?eliminadas={deleted_count}", status_code=303)

    @app.get("/admin/enlaces")
    def links_download(request: Request, municipality_id: int | None = None, db: Session = Depends(get_db)) -> Response:
        require_admin(request, db)
        query = select(Person).where(Person.active.is_(True)).order_by(Person.name)
        if municipality_id:
            query = query.where(Person.municipality_id == municipality_id)
        people = list(db.scalars(query).all())
        output = _links_workbook(people, settings)
        suffix = f"_municipio_{municipality_id}" if municipality_id else "_todos"
        return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="enlaces{suffix}.xlsx"'})

    @app.post("/admin/municipios/{municipality_id}/enlaces")
    def municipality_links(municipality_id: int, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        municipality = db.get(Municipality, municipality_id)
        if not municipality:
            raise HTTPException(404)
        people = db.scalars(select(Person).where(Person.municipality_id == municipality_id, Person.active.is_(True)).order_by(Person.name)).all()
        output = _links_workbook(people, settings)
        filename = quote(f"enlaces_{municipality.name}.xlsx")
        return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})

    @app.get("/admin/importar", response_class=HTMLResponse)
    def import_page(request: Request, db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        municipalities = db.scalars(select(Municipality).where(Municipality.active.is_(True)).order_by(Municipality.name)).all()
        batches = db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(10)).all()
        return render(request, "import.html", {"admin": session.admin, "municipalities": municipalities, "batches": batches})

    @app.post("/admin/importar", response_class=HTMLResponse)
    async def import_preview(
        request: Request,
        municipality_id: int = Form(...),
        csrf_token: str = Form(...),
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
    ) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        municipality = db.get(Municipality, municipality_id)
        if not municipality:
            raise HTTPException(404)
        original = Path(file.filename or "archivo").name
        if Path(original).suffix.lower() not in {".csv", ".xlsx"}:
            raise HTTPException(415, "Solo se permiten archivos CSV o XLSX")
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, "El archivo supera 10 MB")
        stored = f"{uuid.uuid4().hex}{Path(original).suffix.lower()}"
        path = settings.data_dir / "imports" / stored
        path.write_bytes(content)
        rows, errors = validate_import(path, original)
        for row in rows:
            existing = db.scalar(select(Person).where(Person.phone == row["phone"]))
            if existing and existing.municipality_id != municipality_id:
                errors.append({"fila": row["row"], "error": "El celular ya pertenece a otro municipio"})
            elif existing and existing.drawing_version > 0:
                changed = existing.reference_lon != row["reference_lon"] or existing.reference_lat != row["reference_lat"]
                if changed:
                    errors.append({"fila": row["row"], "error": "Las coordenadas no pueden cambiar porque ya existe un dibujo"})
        batch = ImportBatch(municipality_id=municipality_id, stored_name=stored, original_name=original, rows_json=json.dumps(rows, ensure_ascii=False), errors_json=json.dumps(errors, ensure_ascii=False), row_count=len(rows) + len(errors))
        db.add(batch)
        _audit(db, request, "IMPORTACION_VALIDADA", session.admin.username, details={"municipality_id": municipality_id, "rows": batch.row_count, "errors": len(errors)})
        db.commit()
        return render(request, "import_preview.html", {"admin": session.admin, "batch": batch, "rows": rows[:100], "errors": errors})

    @app.post("/admin/importar/{batch_id}/confirmar")
    def import_confirm(batch_id: int, request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        batch = db.get(ImportBatch, batch_id)
        if not batch or batch.status != "VALIDADO":
            raise HTTPException(409, "La importación no está disponible")
        errors = json.loads(batch.errors_json)
        if errors:
            raise HTTPException(409, "Corrija todos los errores antes de confirmar")
        rows = json.loads(batch.rows_json)
        for row in rows:
            person = db.scalar(select(Person).where(Person.phone == row["phone"]))
            if person is None:
                person = Person(municipality_id=batch.municipality_id, name=row["name"], phone=row["phone"])
                db.add(person)
                db.flush()
            person.name = row["name"]
            person.observation = row["observation"]
            person.metadata_json = json.dumps(row.get("metadata", {}), ensure_ascii=False)
            if person.drawing_version == 0:
                person.reference_lon = row["reference_lon"]
                person.reference_lat = row["reference_lat"]
            if person.reference_lon is not None and person.reference_lat is not None:
                _job(db, "PREPARE_WINDOW", {"person_id": person.id})
        batch.status = "CONFIRMADO"
        batch.confirmed_at = utcnow()
        _audit(db, request, "IMPORTACION_CONFIRMADA", session.admin.username, details={"batch_id": batch.id, "rows": len(rows)})
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        return RedirectResponse(f"/admin/personas?municipality_id={batch.municipality_id}", status_code=303)

    @app.get("/admin/ortofotos", response_class=HTMLResponse)
    def orthophotos_page(request: Request, db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        sources = db.scalars(select(OrthophotoSource).order_by(OrthophotoSource.relative_path)).all()
        municipalities = db.scalars(select(Municipality).order_by(Municipality.name)).all()
        return render(request, "orthophotos.html", {"admin": session.admin, "sources": sources, "municipalities": municipalities})

    @app.post("/admin/ortofotos/escanear")
    def orthophotos_scan(request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        _job(db, "SCAN_CATALOG", {})
        _audit(db, request, "CATALOGO_SOLICITADO", session.admin.username)
        db.commit()
        return RedirectResponse("/admin/ortofotos", status_code=303)

    @app.post("/admin/ortofotos/{source_id}")
    def orthophoto_assign(source_id: int, request: Request, municipality_id: int | None = Form(None), csrf_token: str = Form(...), db: Session = Depends(get_db)) -> Response:
        session = require_admin(request, db)
        verify_csrf(request, csrf_token, session)
        source = db.get(OrthophotoSource, source_id)
        if not source:
            raise HTTPException(404)
        source.municipality_id = municipality_id or None
        _audit(db, request, "ORTOFOTO_ASIGNADA", session.admin.username, details={"source_id": source_id, "municipality_id": municipality_id})
        db.commit()
        return RedirectResponse("/admin/ortofotos", status_code=303)

    def current_features(db: Session, municipality_id: int | None) -> list[dict]:
        query = select(DrawingVersion, Person, Municipality).join(Person, DrawingVersion.person_id == Person.id).join(Municipality, Person.municipality_id == Municipality.id).where(DrawingVersion.is_current.is_(True), Person.active.is_(True))
        if municipality_id:
            query = query.where(Person.municipality_id == municipality_id)
        return [
            {"type": "Feature", "geometry": json.loads(drawing.geometry_json), "properties": {"person_id": person.id, "nombre": person.name, "celular": person.phone, "municipio": municipality.name, "version": drawing.version, "area_m2": drawing.area_m2, "fecha": drawing.created_at.isoformat()}}
            for drawing, person, municipality in db.execute(query).all()
        ]

    @app.get("/admin/reportes/{format_name}")
    def report_download(format_name: str, request: Request, municipality_id: int | None = None, db: Session = Depends(get_db)) -> Response:
        require_admin(request, db)
        if format_name not in {"geojson", "xlsx", "gpkg", "shp"}:
            raise HTTPException(404)
        features = current_features(db, municipality_id)
        identifier = uuid.uuid4().hex
        export_dir = settings.data_dir / "exports" / identifier
        export_dir.mkdir(parents=True, exist_ok=False)
        geojson_path = export_dir / "predios.geojson"
        geojson_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
        if format_name == "geojson":
            return FileResponse(geojson_path, media_type="application/geo+json", filename="predios.geojson")
        if format_name == "xlsx":
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Predios"
            sheet.append(["PERSONA_ID", "NOMBRE", "CELULAR", "MUNICIPIO", "VERSION", "AREA_M2", "GEOJSON"])
            for feature in features:
                p = feature["properties"]
                sheet.append([p["person_id"], p["nombre"], p["celular"], p["municipio"], p["version"], p["area_m2"], json.dumps(feature["geometry"], separators=(",", ":"))])
            path = export_dir / "predios.xlsx"
            workbook.save(path)
            return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="predios.xlsx")
        try:
            ogr2ogr = qgis_tool(settings, "ogr2ogr")
        except FileNotFoundError as exc:
            raise HTTPException(503, str(exc))
        if format_name == "gpkg":
            path = export_dir / "predios.gpkg"
            args = [str(ogr2ogr), "-f", "GPKG", str(path), str(geojson_path), "-nln", "predios"]
            result = subprocess.run(args, capture_output=True, text=True, timeout=120, env=qgis_environment(settings))
            if result.returncode != 0:
                raise HTTPException(500, f"GDAL: {result.stderr.strip()}")
            return FileResponse(path, media_type="application/geopackage+sqlite3", filename="predios.gpkg")
        shape_dir = export_dir / "shapefile"
        shape_dir.mkdir()
        args = [str(ogr2ogr), "-f", "ESRI Shapefile", str(shape_dir / "predios.shp"), str(geojson_path)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=120, env=qgis_environment(settings))
        if result.returncode != 0:
            raise HTTPException(500, f"GDAL: {result.stderr.strip()}")
        archive = shutil.make_archive(str(export_dir / "predios_shapefile"), "zip", shape_dir)
        return FileResponse(archive, media_type="application/zip", filename="predios_shapefile.zip")

    @app.get("/p/{token}", response_class=HTMLResponse)
    def public_map(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
        rate_limiter.check(f"open:{ip_fingerprint(request, settings)}", 120, 60)
        link = find_public_link(db, token, settings)
        if not link or not link.person.active:
            return render(request, "public_invalid.html", status_code=404)
        person = link.person
        if person.reference_lon is None or person.reference_lat is None:
            return render(request, "public_invalid.html", {"message": "Este registro todavía no tiene una coordenada válida."}, 409)
        if person.status == "PENDIENTE":
            person.status = "ABRIO"
        person.last_opened_at = utcnow()
        _audit(db, request, "ENLACE_ABIERTO", "PUBLICO", person.id)
        db.commit()
        window = db.get(TileWindow, person.id)
        current = db.scalar(select(DrawingVersion).where(DrawingVersion.person_id == person.id, DrawingVersion.is_current.is_(True)))
        return render(request, "public_map.html", {"person": person, "token": token, "public_csrf": public_csrf(token, settings), "window": window, "current_geometry": current.geometry_json if current else "null"})

    @app.post("/p/{token}/submit")
    async def public_submit(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
        rate_limiter.check(f"submit:{ip_fingerprint(request, settings)}", 20, 300)
        link = find_public_link(db, token, settings)
        if not link or not link.person.active:
            raise HTTPException(404, "Enlace inválido, vencido o revocado")
        body = await request.json()
        verify_public_csrf(token, str(body.get("csrf", "")), settings)
        person = link.person
        if person.reference_lon is None or person.reference_lat is None:
            raise HTTPException(409, "No hay coordenada de referencia")
        try:
            geometry, area = validate_polygon(body.get("geometry") or {}, person.reference_lon, person.reference_lat)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        db.execute(update(DrawingVersion).where(DrawingVersion.person_id == person.id, DrawingVersion.is_current.is_(True)).values(is_current=False))
        version = person.drawing_version + 1
        db.add(DrawingVersion(person_id=person.id, version=version, geometry_json=json.dumps(geometry, separators=(",", ":")), area_m2=area, is_current=True))
        person.drawing_version = version
        person.responded_at = utcnow()
        person.status = "RESPONDIO"
        _audit(db, request, "DIBUJO_ENVIADO", "PUBLICO", person.id, {"version": version, "area_m2": area})
        db.commit()
        return JSONResponse({"ok": True, "version": version, "area_m2": area, "message": "Su predio quedó guardado correctamente.", "redirect_url": f"/p/{token}/recibido"})

    @app.get("/p/{token}/recibido", response_class=HTMLResponse)
    def public_success(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
        link = find_public_link(db, token, settings)
        if not link or not link.person.active:
            return render(request, "public_invalid.html", status_code=404)
        return render(request, "public_success.html", {"person": link.person, "version": link.person.drawing_version or None})

    @app.get("/p/{token}/tiles/{z}/{x}/{y}.png")
    def private_tile(token: str, z: int, x: int, y: int, request: Request, db: Session = Depends(get_db)) -> Response:
        link = find_public_link(db, token, settings)
        if not link or not link.person.active:
            raise HTTPException(404)
        window = db.get(TileWindow, link.person_id)
        if not window or window.status != "LISTO" or not window.relative_dir:
            raise HTTPException(404)
        if window.min_zoom is None or window.max_zoom is None or not window.min_zoom <= z <= window.max_zoom:
            raise HTTPException(404)
        base = (settings.data_dir / "tiles" / window.relative_dir).resolve()
        tile = (base / str(z) / str(x) / f"{y}.png").resolve()
        try:
            tile.relative_to(base)
        except ValueError:
            raise HTTPException(404)
        if not tile.is_file():
            raise HTTPException(404)
        return FileResponse(tile, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    return app


app = create_app()
