from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from starlette.testclient import TestClient
from sqlalchemy import func, select

from app.models import AuditLog, Municipality, PublicLink, utcnow
from app.security import sha256
from tests.conftest import login


def test_sql_injection_does_not_authenticate_or_damage_database(test_app):
    app, client, _, _ = test_app
    response = client.post("/admin/login", data={"username": "' OR 1=1 --", "password": "x"})
    assert response.status_code == 401
    with app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(Municipality)) == 1


def test_stored_xss_is_escaped(test_app):
    _, client, _, _ = test_app
    login(client)
    response = client.get("/admin/personas")
    assert response.status_code == 200
    assert "Ana &lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "Ana <script>alert(1)</script>" not in response.text


def test_invalid_upload_is_rejected(test_app):
    _, client, _, _ = test_app
    csrf = login(client)
    response = client.post("/admin/importar", data={"municipality_id": "1", "csrf_token": csrf}, files={"file": ("malware.exe", b"MZ", "application/octet-stream")})
    assert response.status_code == 415


def test_revoked_token_is_rejected(test_app):
    app, client, token, _ = test_app
    with app.state.Session() as db:
        link = db.scalar(select(PublicLink).where(PublicLink.token_hash == sha256(token)))
        link.revoked_at = utcnow()
        db.commit()
    assert client.get(f"/p/{token}").status_code == 404


def test_csrf_is_required_for_admin_write(test_app):
    _, client, _, _ = test_app
    login(client)
    response = client.post("/admin/municipios", data={"name": "Nuevo", "csrf_token": "incorrecto"})
    assert response.status_code == 403


def test_admin_surfaces_render(test_app):
    _, client, _, person_id = test_app
    login(client)
    for path in ("/admin", "/admin/municipios", "/admin/personas", f"/admin/personas/{person_id}", "/admin/importar", "/admin/ortofotos"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_concurrent_public_reads_and_audit_writes_under_wal(test_app):
    app, client, token, person_id = test_app

    def read_public(_: int) -> int:
        with TestClient(app) as local:
            return local.get(f"/p/{token}").status_code

    def write_audit(index: int) -> int:
        with app.state.Session() as db:
            db.add(AuditLog(actor="TEST", action="CONCURRENT", person_id=person_id, details_json=f'{{"i":{index}}}'))
            db.commit()
            return 1

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda i: read_public(i) if i % 2 == 0 else write_audit(i), range(24)))
    assert all(result in {1, 409} for result in results)
