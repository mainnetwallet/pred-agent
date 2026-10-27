"""Phase 8 migration: proposals, orders, positions, pnl_ledger, rules, chat_sessions, chat_messages."""

revision = "0002_chat_rules"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("advisory", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fixture_id", sa.String(64), index=True, nullable=False),
        sa.Column("market_id", sa.String(66), index=True, nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("price_cents", sa.String(32), nullable=False),
        sa.Column("quantity", sa.String(32), nullable=False),
        sa.Column("amount_usd", sa.String(32), nullable=False),
        sa.Column("est_fee_usd", sa.String(32), nullable=False),
        sa.Column("edge_pp", sa.String(32), nullable=False),
        sa.Column("p_fair", sa.String(32), nullable=False),
        sa.Column("max_suggested_usd", sa.String(32), nullable=False),
        sa.Column("binding_cap", sa.String(32), nullable=True),
        sa.Column("reasons", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("risks", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), index=True, nullable=False, server_default="PROPOSED"),
        sa.Column("via", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("pred_order_id", sa.String(128), nullable=True),
        sa.Column("proposal_id", sa.String(64), index=True, nullable=True),
        sa.Column("state", sa.String(16), index=True, nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.String(32), nullable=False),
        sa.Column("market_id", sa.String(66), index=True, nullable=True),
        sa.Column("parent_market_id", sa.String(66), nullable=True),
        sa.Column("filled_qty", sa.String(32), nullable=False, server_default="0"),
        sa.Column("remaining_qty", sa.String(32), nullable=True),
        sa.Column("salt", sa.String(64), nullable=True),
        sa.Column("signed_fee_bps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_delayed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("raw_request_redacted", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    op.create_table(
        "positions",
        sa.Column("market_id", sa.String(66), primary_key=True),
        sa.Column("parent_id", sa.String(66), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.String(32), nullable=False),
        sa.Column("avg_price_cents", sa.String(32), nullable=False),
        sa.Column("cost_usd", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "pnl_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount_usd", sa.String(32), nullable=False),
        sa.Column("ref", sa.String(128), nullable=False),
    )

    op.create_table(
        "rules",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("market_id", sa.String(66), index=True, nullable=False),
        sa.Column("parent_market_id", sa.String(66), nullable=False),
        sa.Column("position_ref", sa.String(66), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("trigger_value", sa.String(32), nullable=False),
        sa.Column("size", sa.String(32), nullable=False, server_default="all"),
        sa.Column("status", sa.String(16), index=True, nullable=False, server_default="PENDING"),
        sa.Column("created_via", sa.String(16), nullable=False, server_default="chat"),
        sa.Column("mode", sa.String(8), nullable=False, server_default="PAPER"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_eval_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_eval_note", sa.Text(), nullable=True),
        sa.Column("order_id", sa.String(64), nullable=True),
        sa.Column("blocked_since", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor", sa.String(64), index=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_active", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(64), index=True, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("cards", sa.Text(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("rules")
    op.drop_table("pnl_ledger")
    op.drop_table("positions")
    op.drop_table("orders")
    op.drop_table("proposals")
