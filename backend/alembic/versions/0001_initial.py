"""Phase 2 database migration: fixtures, markets, kv, decisions, audit_log."""

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


import sqlalchemy as sa  # noqa: E402
from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "fixtures",
        sa.Column("pred_fixture_id", sa.String(64), primary_key=True),
        sa.Column("parent_market_id", sa.String(66), index=True, nullable=False),
        sa.Column("type_reference_id", sa.String(128), index=True, nullable=True),
        sa.Column("league_id", sa.String(64), nullable=True),
        sa.Column("league_name", sa.String(128), nullable=True),
        sa.Column("home_team", sa.String(128), nullable=True),
        sa.Column("away_team", sa.String(128), nullable=True),
        sa.Column("kickoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("minute", sa.Integer(), nullable=True),
        sa.Column("game_status", sa.String(32), nullable=True),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "markets",
        sa.Column("market_id", sa.String(66), primary_key=True),
        sa.Column("parent_market_id", sa.String(66), index=True, nullable=False),
        sa.Column("outcome_name", sa.String(128), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=True),
        sa.Column("tick_size", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("family", sa.String(32), nullable=True),
        sa.Column("contract_address", sa.String(42), nullable=True),
        sa.Column("fee_target_bps", sa.Integer(), nullable=True),
        sa.Column("ltp", sa.String(32), nullable=True),
        sa.Column("mid_price", sa.String(32), nullable=True),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "kv",
        sa.Column("key", sa.String(256), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("fixture_id", sa.String(64), index=True, nullable=True),
        sa.Column("market_id", sa.String(66), index=True, nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_codes", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("payload_redacted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("decisions")
    op.drop_table("kv")
    op.drop_table("markets")
    op.drop_table("fixtures")
