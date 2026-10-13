"""Persiste la configuración administrable de las ventanas de teselas."""

from alembic import op
import sqlalchemy as sa


revision = "0003_tile_configuration"
down_revision = "0002_tile_window_radius"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tile_configurations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("radius_m", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_tile_configurations_singleton"),
        sa.CheckConstraint("radius_m >= 100 AND radius_m <= 10000", name="ck_tile_configurations_radius_range"),
    )


def downgrade() -> None:
    op.drop_table("tile_configurations")
