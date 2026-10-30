"""Autopilot exits: rules.exec_mode (market|limit) and rules.resting_order_id."""

revision = "0004_rule_exec_mode"
down_revision = "0003_persistence_columns"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402
from alembic import op  # noqa: E402


def upgrade() -> None:
    op.add_column("rules", sa.Column("exec_mode", sa.String(8), nullable=False, server_default="market"))
    op.add_column("rules", sa.Column("resting_order_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("rules", "resting_order_id")
    op.drop_column("rules", "exec_mode")
