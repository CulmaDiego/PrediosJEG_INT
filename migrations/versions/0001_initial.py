"""Esquema inicial de Predios Remotos."""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("admins", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("username", sa.String(80), nullable=False, unique=True), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("municipalities", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(120), nullable=False, unique=True), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("admin_sessions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False), sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("csrf_hash", sa.String(64), nullable=False), sa.Column("expires_at", sa.DateTime(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("last_seen_at", sa.DateTime(), nullable=False))
    op.create_table("orthophoto_sources", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("relative_path", sa.String(500), nullable=False, unique=True), sa.Column("municipality_id", sa.Integer(), sa.ForeignKey("municipalities.id")), sa.Column("inferred_name", sa.String(120)), sa.Column("driver", sa.String(30)), sa.Column("crs", sa.String(100)), sa.Column("width", sa.Integer()), sa.Column("height", sa.Integer()), sa.Column("resolution_x", sa.Float()), sa.Column("resolution_y", sa.Float()), sa.Column("bounds_json", sa.Text()), sa.Column("status", sa.String(30), nullable=False), sa.Column("error", sa.Text()), sa.Column("scanned_at", sa.DateTime()))
    op.create_table("people", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("municipality_id", sa.Integer(), sa.ForeignKey("municipalities.id"), nullable=False), sa.Column("name", sa.String(200), nullable=False), sa.Column("phone", sa.String(20), nullable=False, unique=True), sa.Column("reference_lon", sa.Float()), sa.Column("reference_lat", sa.Float()), sa.Column("observation", sa.Text()), sa.Column("metadata_json", sa.Text(), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("orthophoto_status", sa.String(30), nullable=False), sa.Column("orthophoto_error", sa.Text()), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("last_opened_at", sa.DateTime()), sa.Column("responded_at", sa.DateTime()), sa.Column("drawing_version", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("idx_people_municipality_status", "people", ["municipality_id", "status"])
    op.create_index("idx_people_name", "people", ["name"])
    op.create_table("public_links", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False), sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("expires_at", sa.DateTime()), sa.Column("revoked_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("idx_public_links_person_active", "public_links", ["person_id", "revoked_at"])
    op.create_table("drawing_versions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("geometry_json", sa.Text(), nullable=False), sa.Column("area_m2", sa.Float(), nullable=False), sa.Column("is_current", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("person_id", "version", name="uq_drawing_person_version"))
    op.create_index("idx_drawing_current", "drawing_versions", ["person_id", "is_current"])
    op.create_table("import_batches", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("municipality_id", sa.Integer(), sa.ForeignKey("municipalities.id"), nullable=False), sa.Column("stored_name", sa.String(100), nullable=False), sa.Column("original_name", sa.String(255), nullable=False), sa.Column("status", sa.String(30), nullable=False), sa.Column("rows_json", sa.Text(), nullable=False), sa.Column("errors_json", sa.Text(), nullable=False), sa.Column("row_count", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("confirmed_at", sa.DateTime()))
    op.create_table("jobs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("job_type", sa.String(50), nullable=False), sa.Column("payload_json", sa.Text(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("progress", sa.Integer(), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("locked_by", sa.String(100)), sa.Column("locked_at", sa.DateTime()), sa.Column("error", sa.Text()), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("idx_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_table("tile_windows", sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id", ondelete="CASCADE"), primary_key=True), sa.Column("source_id", sa.Integer(), sa.ForeignKey("orthophoto_sources.id")), sa.Column("relative_dir", sa.String(300)), sa.Column("min_zoom", sa.Integer()), sa.Column("max_zoom", sa.Integer()), sa.Column("status", sa.String(30), nullable=False), sa.Column("error", sa.Text()), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_table("audit_logs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("actor", sa.String(80), nullable=False), sa.Column("action", sa.String(80), nullable=False), sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id")), sa.Column("details_json", sa.Text(), nullable=False), sa.Column("ip_hash", sa.String(64)), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("idx_audit_person_created", "audit_logs", ["person_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_audit_person_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("tile_windows")
    op.drop_index("idx_jobs_status_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("import_batches")
    op.drop_index("idx_drawing_current", table_name="drawing_versions")
    op.drop_table("drawing_versions")
    op.drop_index("idx_public_links_person_active", table_name="public_links")
    op.drop_table("public_links")
    op.drop_index("idx_people_name", table_name="people")
    op.drop_index("idx_people_municipality_status", table_name="people")
    op.drop_table("people")
    op.drop_table("orthophoto_sources")
    op.drop_table("admin_sessions")
    op.drop_table("municipalities")
    op.drop_table("admins")

