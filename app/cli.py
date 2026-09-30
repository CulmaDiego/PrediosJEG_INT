from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from .config import Settings
from .db import build_engine, build_session_factory
from .models import Admin
from .security import hash_password


def init_admin(settings: Settings, username: str) -> int:
    password = os.getenv("ADMIN_INITIAL_PASSWORD", "")
    if len(password) < 12:
        print("ADMIN_INITIAL_PASSWORD debe tener al menos 12 caracteres", file=sys.stderr)
        return 2
    factory = build_session_factory(build_engine(settings.database_url))
    with factory() as db:
        if db.scalar(select(func.count()).select_from(Admin)):
            print("Ya existe el administrador único; no se realizó ningún cambio.")
            return 0
        db.add(Admin(username=username.strip(), password_hash=hash_password(password)))
        db.commit()
    print("Administrador creado.")
    return 0


def backup(settings: Settings, destination: Path) -> int:
    if settings.environment != "production":
        print("El respaldo de actualización solo se permite con PREDIOS_ENV=production", file=sys.stderr)
        return 2
    destination.mkdir(parents=True, exist_ok=False)
    database_path = Path(settings.database_url.removeprefix("sqlite:///"))
    with sqlite3.connect(database_path) as source, sqlite3.connect(destination / "predios.db") as target:
        source.backup(target)
    for name in ("tiles", "imports", "exports"):
        source_dir = settings.data_dir / name
        if source_dir.exists():
            shutil.copytree(source_dir, destination / name)
    manifest = {"created_at": datetime.now().isoformat(), "environment": settings.environment, "database": "predios.db"}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(destination)
    return 0


def verify_backup(destination: Path) -> int:
    manifest = destination / "manifest.json"
    database = destination / "predios.db"
    if not manifest.is_file() or not database.is_file():
        print("Faltan el manifiesto o la base en el respaldo", file=sys.stderr)
        return 2
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        print(f"integrity_check: {result}", file=sys.stderr)
        return 3
    print("Respaldo verificado: SQLite íntegra y manifiesto presente.")
    return 0


def restore(settings: Settings, source: Path, confirm: str) -> int:
    if settings.environment != "production" or confirm != "RESTAURAR_RESPALDO_COMPLETO":
        print("Restauración rechazada: entorno o confirmación inválidos", file=sys.stderr)
        return 2
    if verify_backup(source) != 0:
        return 3
    database_path = Path(settings.database_url.removeprefix("sqlite:///"))
    Path(f"{database_path}-wal").unlink(missing_ok=True)
    Path(f"{database_path}-shm").unlink(missing_ok=True)
    shutil.copy2(source / "predios.db", database_path)
    for name in ("tiles", "imports", "exports"):
        backup_dir = source / name
        target = settings.data_dir / name
        if backup_dir.exists():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(backup_dir, target)
    print("Respaldo completo restaurado.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-admin")
    init.add_argument("--username", default="admin")
    sub.add_parser("has-admin")
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--destination", type=Path, required=True)
    verify = sub.add_parser("verify-backup")
    verify.add_argument("--source", type=Path, required=True)
    restore_parser = sub.add_parser("restore-backup")
    restore_parser.add_argument("--source", type=Path, required=True)
    restore_parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "init-admin":
        return init_admin(settings, args.username)
    if args.command == "has-admin":
        factory = build_session_factory(build_engine(settings.database_url))
        with factory() as db:
            return 0 if db.scalar(select(func.count()).select_from(Admin)) else 1
    if args.command == "backup":
        return backup(settings, args.destination.resolve())
    if args.command == "verify-backup":
        return verify_backup(args.source.resolve())
    return restore(settings, args.source.resolve(), args.confirm)


if __name__ == "__main__":
    raise SystemExit(main())
