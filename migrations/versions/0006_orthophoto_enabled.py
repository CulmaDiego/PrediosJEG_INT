"""Allow administrators to enable or disable imagery without deleting files."""
from alembic import op
import sqlalchemy as sa


revision = "0006_orthophoto_enabled"
down_revision = "0005_clear_recovered_tile_errors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orthophoto_sources", sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    op.drop_column("orthophoto_sources", "enabled")
