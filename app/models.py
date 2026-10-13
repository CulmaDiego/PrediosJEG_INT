from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    admin: Mapped[Admin] = relationship()


class Municipality(Base):
    __tablename__ = "municipalities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    people: Mapped[list[Person]] = relationship(back_populates="municipality")


class OrthophotoSource(Base):
    __tablename__ = "orthophoto_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    relative_path: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    municipality_id: Mapped[int | None] = mapped_column(ForeignKey("municipalities.id"), nullable=True)
    inferred_name: Mapped[str | None] = mapped_column(String(120))
    driver: Mapped[str | None] = mapped_column(String(30))
    crs: Mapped[str | None] = mapped_column(String(100))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    resolution_x: Mapped[float | None] = mapped_column(Float)
    resolution_y: Mapped[float | None] = mapped_column(Float)
    bounds_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="PENDIENTE", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    municipality: Mapped[Municipality | None] = relationship()


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (
        Index("idx_people_municipality_status", "municipality_id", "status"),
        Index("idx_people_name", "name"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    reference_lon: Mapped[float | None] = mapped_column(Float)
    reference_lat: Mapped[float | None] = mapped_column(Float)
    observation: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDIENTE", nullable=False)
    orthophoto_status: Mapped[str] = mapped_column(String(30), default="PENDIENTE", nullable=False)
    orthophoto_error: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime)
    drawing_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    municipality: Mapped[Municipality] = relationship(back_populates="people")
    drawings: Mapped[list[DrawingVersion]] = relationship(back_populates="person")


class PublicLink(Base):
    __tablename__ = "public_links"
    __table_args__ = (Index("idx_public_links_person_active", "person_id", "revoked_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    person: Mapped[Person] = relationship()


class DrawingVersion(Base):
    __tablename__ = "drawing_versions"
    __table_args__ = (
        UniqueConstraint("person_id", "version", name="uq_drawing_person_version"),
        Index("idx_drawing_current", "person_id", "is_current"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry_json: Mapped[str] = mapped_column(Text, nullable=False)
    area_m2: Mapped[float] = mapped_column(Float, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    person: Mapped[Person] = relationship(back_populates="drawings")


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    municipality_id: Mapped[int] = mapped_column(ForeignKey("municipalities.id"), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(100), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="VALIDADO", nullable=False)
    rows_json: Mapped[str] = mapped_column(Text, nullable=False)
    errors_json: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    municipality: Mapped[Municipality] = relationship()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("idx_jobs_status_created", "status", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDIENTE", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class TileConfiguration(Base):
    __tablename__ = "tile_configurations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    radius_m: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class TileWindow(Base):
    __tablename__ = "tile_windows"
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("orthophoto_sources.id"))
    relative_dir: Mapped[str | None] = mapped_column(String(300))
    min_zoom: Mapped[int | None] = mapped_column(Integer)
    max_zoom: Mapped[int | None] = mapped_column(Integer)
    radius_m: Mapped[int] = mapped_column(Integer, default=350, server_default="350", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDIENTE", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    person: Mapped[Person] = relationship()
    source: Mapped[OrthophotoSource | None] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("idx_audit_person_created", "person_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
