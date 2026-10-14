from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

os.environ.setdefault("PREDIOS_DATA_DIR", str(Path(tempfile.gettempdir()) / "predios-remotos-test-bootstrap"))
os.environ.setdefault("PREDIOS_SECRET_KEY", "bootstrap-test-secret-key")

from app.config import Settings
from app.main import create_app
from app.models import Admin, Municipality, Person, PublicLink
from app.security import hash_password, rate_limiter, sha256


@pytest.fixture()
def test_app(tmp_path: Path):
    rate_limiter.reset()
    data = tmp_path / "data"
    orthos = tmp_path / "orthos"
    orthos.mkdir()
    settings = Settings(
        environment="test",
        data_dir=data,
        orthophoto_root=orthos,
        database_url=f"sqlite:///{(data / 'test.db').as_posix()}",
        secret_key="test-secret-key-that-is-long-and-stable",
        public_base_url="http://testserver",
        cookie_secure=False,
        qgis_root=Path(r"C:\QGIS"),
        window_radius_m=350,
        tile_min_zoom=15,
        tile_max_zoom=19,
        asset_mode="cdn",
    )
    app = create_app(settings, create_schema=True)
    raw_token = secrets.token_urlsafe(32)
    with app.state.Session() as db:
        admin = Admin(username="admin", password_hash=hash_password("Correcta-12345"))
        municipality = Municipality(name="San Rafael")
        db.add_all([admin, municipality])
        db.flush()
        person = Person(municipality_id=municipality.id, name="Ana <script>alert(1)</script>", phone="3001234567", reference_lon=-75.031, reference_lat=6.294)
        db.add(person)
        db.flush()
        db.add(PublicLink(person_id=person.id, token_hash=sha256(raw_token)))
        db.commit()
        person_id = person.id
    yield app, TestClient(app), raw_token, person_id
    app.state.tile_renderer.close()


def login(client: TestClient) -> str:
    response = client.post("/admin/login", data={"username": "admin", "password": "Correcta-12345"}, follow_redirects=False)
    assert response.status_code == 303
    return client.cookies.get("predios_csrf")
