"""Phase 8 fixups: orders.amount_usd and proposals.decided_at for persistence."""

revision = "0003_persistence_columns"
down_revision = "0002_chat_rules"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.add_column("orders", sa.Column("amount_usd", sa.String(32), nullable=True))
    op.add_column("proposals", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("proposals", "decided_at")
    op.drop_column("orders", "amount_usd")
