"""Registra el radio usado para generar cada ventana de teselas."""

from alembic import op
import sqlalchemy as sa


revision = "0002_tile_window_radius"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tile_windows",
        sa.Column("radius_m", sa.Integer(), nullable=False, server_default="350"),
    )


def downgrade() -> None:
    op.drop_column("tile_windows", "radius_m")
