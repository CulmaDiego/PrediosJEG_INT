from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "si"}


@dataclass(slots=True)
class Settings:
    environment: str
    data_dir: Path
    orthophoto_root: Path
    database_url: str
    secret_key: str
    public_base_url: str
    cookie_secure: bool
    qgis_root: Path
    window_radius_m: int
    tile_min_zoom: int
    tile_max_zoom: int
    asset_mode: str = "cdn"
    max_upload_bytes: int = 10 * 1024 * 1024
    session_hours: int = 8

    @classmethod
    def from_env(cls) -> "Settings":
        env_file = os.getenv("PREDIOS_ENV_FILE")
        load_dotenv(env_file or REPO_ROOT / ".env", override=False)
        environment = os.getenv("PREDIOS_ENV", "development").strip().lower()
        data_value = os.getenv("PREDIOS_DATA_DIR", str(REPO_ROOT.parent / "PrediosRemotos_data_dev"))
        data_dir = Path(data_value).expanduser().resolve()
        ortho_value = os.getenv("PREDIOS_ORTHOPHOTO_ROOT", str(REPO_ROOT / "Imagenes"))
        orthophoto_root = Path(ortho_value).expanduser().resolve()
        database_url = os.getenv("PREDIOS_DATABASE_URL", f"sqlite:///{(data_dir / 'predios.db').as_posix()}")
        secret = os.getenv("PREDIOS_SECRET_KEY", "")
        if not secret:
            if environment == "production":
                raise RuntimeError("PREDIOS_SECRET_KEY es obligatorio en producción")
            secret_file = data_dir / "config" / "dev-secret.txt"
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            if secret_file.is_file():
                secret = secret_file.read_text(encoding="utf-8").strip()
            else:
                secret = secrets.token_urlsafe(48)
                secret_file.write_text(secret, encoding="utf-8")
        return cls(
            environment=environment,
            data_dir=data_dir,
            orthophoto_root=orthophoto_root,
            database_url=database_url,
            secret_key=secret,
            public_base_url=os.getenv("PREDIOS_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            cookie_secure=_env_bool("PREDIOS_COOKIE_SECURE", environment == "production"),
            qgis_root=Path(os.getenv("PREDIOS_QGIS_ROOT", r"C:\Program Files\QGIS 3.44.3")),
            window_radius_m=int(os.getenv("PREDIOS_WINDOW_RADIUS_M", "2000")),
            tile_min_zoom=int(os.getenv("PREDIOS_TILE_MIN_ZOOM", "15")),
            tile_max_zoom=int(os.getenv("PREDIOS_TILE_MAX_ZOOM", "19")),
            asset_mode=os.getenv("PREDIOS_ASSET_MODE", "cdn").strip().lower(),
        )

    def ensure_directories(self) -> None:
        for name in ("imports", "exports", "tiles", "logs", "backups", "run", "tmp"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)
