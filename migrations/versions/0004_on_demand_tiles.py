"""Prepara ventanas de ortofoto y genera teselas bajo demanda."""

from alembic import op
import sqlalchemy as sa


revision = "0004_on_demand_tiles"
down_revision = "0003_tile_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tile_configurations") as batch:
        batch.drop_constraint("ck_tile_configurations_radius_range", type_="check")
        batch.create_check_constraint(
            "ck_tile_configurations_radius_range",
            "radius_m >= 100 AND radius_m <= 50000",
        )
    op.add_column(
        "tile_windows",
        sa.Column("render_mode", sa.String(length=30), nullable=False, server_default="PREGENERATED"),
    )


def downgrade() -> None:
    op.drop_column("tile_windows", "render_mode")
    with op.batch_alter_table("tile_configurations") as batch:
        batch.drop_constraint("ck_tile_configurations_radius_range", type_="check")
        batch.create_check_constraint(
            "ck_tile_configurations_radius_range",
            "radius_m >= 100 AND radius_m <= 10000",
        )
