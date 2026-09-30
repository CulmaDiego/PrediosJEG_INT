from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Admin, AdminSession, PublicLink, utcnow


SESSION_COOKIE = "predios_session"
CSRF_COOKIE = "predios_csrf"
_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_admin_session(db: Session, admin: Admin, settings: Settings) -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(48)
    raw_csrf = secrets.token_urlsafe(32)
    db.add(
        AdminSession(
            admin_id=admin.id,
            token_hash=sha256(raw_token),
            csrf_hash=sha256(raw_csrf),
            expires_at=utcnow() + timedelta(hours=settings.session_hours),
        )
    )
    db.commit()
    return raw_token, raw_csrf


def current_admin_session(request: Request, db: Session) -> AdminSession | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    session = db.scalar(select(AdminSession).where(AdminSession.token_hash == sha256(raw)))
    if not session or session.expires_at <= utcnow():
        return None
    session.last_seen_at = utcnow()
    return session


def require_admin(request: Request, db: Session) -> AdminSession:
    session = current_admin_session(request, db)
    if not session:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return session


def verify_csrf(request: Request, form_token: str, session: AdminSession) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not form_token or not hmac.compare_digest(form_token, cookie):
        raise HTTPException(status_code=403, detail="Solicitud no válida")
    if not hmac.compare_digest(sha256(form_token), session.csrf_hash):
        raise HTTPException(status_code=403, detail="Solicitud no válida")


def public_csrf(token: str, settings: Settings) -> str:
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def verify_public_csrf(token: str, submitted: str, settings: Settings) -> None:
    if not hmac.compare_digest(public_csrf(token, settings), submitted):
        raise HTTPException(status_code=403, detail="Solicitud no válida")


def find_public_link(db: Session, raw_token: str) -> PublicLink | None:
    link = db.scalar(select(PublicLink).where(PublicLink.token_hash == sha256(raw_token)))
    if not link or link.revoked_at is not None:
        return None
    if link.expires_at and link.expires_at <= utcnow():
        return None
    return link


def ip_fingerprint(request: Request, settings: Settings) -> str:
    ip = request.client.host if request.client else "unknown"
    return hmac.new(settings.secret_key.encode(), ip.encode(), hashlib.sha256).hexdigest()


class RateLimiter:
    def __init__(self) -> None:
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            entries = self._entries[key]
            while entries and entries[0] <= now - window_seconds:
                entries.popleft()
            if len(entries) >= limit:
                raise HTTPException(status_code=429, detail="Demasiados intentos. Espere un momento.")
            entries.append(now)


rate_limiter = RateLimiter()

