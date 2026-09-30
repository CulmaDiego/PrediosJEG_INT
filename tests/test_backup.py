from __future__ import annotations

import sqlite3
from pathlib import Path

from app.cli import backup, restore, verify_backup
from app.config import Settings


def test_full_backup_verify_and_restore(tmp_path: Path):
    data = tmp_path / "production-data"
    settings = Settings(
        environment="production",
        data_dir=data,
        orthophoto_root=tmp_path / "orthos",
        database_url=f"sqlite:///{(data / 'predios.db').as_posix()}",
        secret_key="production-test-secret",
        public_base_url="https://predios.example",
        cookie_secure=True,
        qgis_root=Path(r"C:\QGIS"),
        window_radius_m=350,
        tile_min_zoom=15,
        tile_max_zoom=19,
    )
    settings.ensure_directories()
    with sqlite3.connect(data / "predios.db") as connection:
        connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES ('original')")
    tile = data / "tiles" / "sample" / "18" / "1" / "2.png"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"tile-original")
    destination = data / "backups" / "test-backup"
    assert backup(settings, destination) == 0
    assert verify_backup(destination) == 0

    with sqlite3.connect(data / "predios.db") as connection:
        connection.execute("UPDATE evidence SET value='changed'")
    tile.write_bytes(b"tile-changed")
    assert restore(settings, destination, "RESTAURAR_RESPALDO_COMPLETO") == 0
    with sqlite3.connect(data / "predios.db") as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "original"
    assert tile.read_bytes() == b"tile-original"

