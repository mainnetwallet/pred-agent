"""SQLAlchemy models for pred-agent.

Tables created in Phase 2: fixtures, markets, kv, decisions, audit_log.
Added in Phase 8 (Chat + Rules): proposals, orders, positions, pnl_ledger,
  rules, chat_sessions, chat_messages.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Fixture(Base):
    """A live/upcoming football fixture discovered from PRED."""

    __tablename__ = "fixtures"

    pred_fixture_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_market_id: Mapped[str] = mapped_column(String(66), index=True)
    type_reference_id: Mapped[str | None] = mapped_column(String(128), index=True)
    league_id: Mapped[str | None] = mapped_column(String(64))
    league_name: Mapped[str | None] = mapped_column(String(128))
    home_team: Mapped[str | None] = mapped_column(String(128))
    away_team: Mapped[str | None] = mapped_column(String(128))
    kickoff: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(32))
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    game_status: Mapped[str | None] = mapped_column(String(32))
    last_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Market(Base):
    """A child outcome market under a parent fixture."""

    __tablename__ = "markets"

    market_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    parent_market_id: Mapped[str] = mapped_column(String(66), index=True)
    outcome_name: Mapped[str] = mapped_column(String(128))
    team_id: Mapped[str | None] = mapped_column(String(64))
    tick_size: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    family: Mapped[str | None] = mapped_column(String(32))
    contract_address: Mapped[str | None] = mapped_column(String(42))
    fee_target_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ltp: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mid_price: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KV(Base):
    """Generic key-value store for cursors, session tokens (encrypted), settings."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Decision(Base):
    """Audit of agent decisions per fixture/market (proposed/skipped/vetoed)."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fixture_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    market_id: Mapped[str | None] = mapped_column(String(66), index=True, nullable=True)
    outcome: Mapped[str] = mapped_column(String(32))  # proposed|skipped|vetoed
    reason_codes: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(Base):
    """Redacted audit log of agent/telegram/app actions."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(64))  # agent|telegram|app:<device>
    action: Mapped[str] = mapped_column(String(128))
    payload_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)


# ============================================================
# Phase 8 additions: proposals, orders, positions, pnl_ledger,
#                    rules, chat_sessions, chat_messages
# ============================================================


class ProposalRow(Base):
    """Persisted trade proposal."""

    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8))          # PAPER | LIVE
    advisory: Mapped[bool] = mapped_column(Boolean, default=False)
    fixture_id: Mapped[str] = mapped_column(String(64), index=True)
    market_id: Mapped[str] = mapped_column(String(66), index=True)
    side: Mapped[str] = mapped_column(String(8))           # long | short
    order_type: Mapped[str] = mapped_column(String(8))     # limit | ioc
    price_cents: Mapped[str] = mapped_column(String(32))   # Decimal string
    quantity: Mapped[str] = mapped_column(String(32))      # Decimal string
    amount_usd: Mapped[str] = mapped_column(String(32))    # Decimal string
    est_fee_usd: Mapped[str] = mapped_column(String(32))   # Decimal string
    edge_pp: Mapped[str] = mapped_column(String(32))       # Decimal string
    p_fair: Mapped[str] = mapped_column(String(32))        # Decimal string
    max_suggested_usd: Mapped[str] = mapped_column(String(32))
    binding_cap: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reasons: Mapped[str] = mapped_column(Text, default="[]")   # JSON list
    risks: Mapped[str] = mapped_column(Text, default="[]")     # JSON list
    status: Mapped[str] = mapped_column(String(16), index=True, default="PROPOSED")
    via: Mapped[str | None] = mapped_column(String(32), nullable=True)  # app|telegram|chat
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderRow(Base):
    """Persisted paper/live order record."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pred_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))
    price_cents: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[str] = mapped_column(String(32))
    amount_usd: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market_id: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)
    parent_market_id: Mapped[str | None] = mapped_column(String(66), nullable=True)
    filled_qty: Mapped[str] = mapped_column(String(32), default="0")
    remaining_qty: Mapped[str | None] = mapped_column(String(32), nullable=True)
    salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_fee_bps: Mapped[int] = mapped_column(Integer, default=0)
    is_delayed: Mapped[bool] = mapped_column(Boolean, default=False)
    reduce_only: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_request_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PositionRow(Base):
    """Persisted paper position."""

    __tablename__ = "positions"

    market_id: Mapped[str] = mapped_column(String(66), primary_key=True)
    parent_id: Mapped[str] = mapped_column(String(66))
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[str] = mapped_column(String(32))       # Decimal
    avg_price_cents: Mapped[str] = mapped_column(String(32))  # Decimal
    cost_usd: Mapped[str] = mapped_column(String(32))       # Decimal
    status: Mapped[str] = mapped_column(String(16), default="open")
    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PnLLedgerRow(Base):
    """Persisted PnL ledger entry."""

    __tablename__ = "pnl_ledger"

    # Integer (not BigInteger): SQLite only autoincrements INTEGER primary keys
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    kind: Mapped[str] = mapped_column(String(16))    # realized | unrealized | fee
    amount_usd: Mapped[str] = mapped_column(String(32))  # Decimal string
    ref: Mapped[str] = mapped_column(String(128))


class RuleRow(Base):
    """Persisted conditional exit rule (take-profit / stop-loss)."""

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    market_id: Mapped[str] = mapped_column(String(66), index=True)
    parent_market_id: Mapped[str] = mapped_column(String(66))
    position_ref: Mapped[str | None] = mapped_column(String(66), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))       # take_profit | stop_loss
    trigger_type: Mapped[str] = mapped_column(String(16))  # profit_usd | loss_usd | price_cents | profit_pct
    trigger_value: Mapped[str] = mapped_column(String(32))  # Decimal string
    size: Mapped[str] = mapped_column(String(32), default="all")  # all | fixed_usd:X | fixed_shares:X
    status: Mapped[str] = mapped_column(String(16), index=True, default="PENDING")
    # PENDING | ACTIVE | TRIGGERED | EXECUTING | DONE | CANCELLED | EXPIRED | BLOCKED | FAILED
    created_via: Mapped[str] = mapped_column(String(16), default="chat")  # chat | app | telegram
    mode: Mapped[str] = mapped_column(String(8), default="PAPER")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_eval_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_eval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Escalation tracking for blocked stop-loss rules
    blocked_since: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Exit execution: "market" (marketable sell on trigger) | "limit" (resting reduce-only sell)
    exec_mode: Mapped[str] = mapped_column(String(8), default="market")
    resting_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ChatSession(Base):
    """A user chat session."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)  # app:<device>|telegram:<uid>
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_active: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    """A single turn in a chat session."""

    __tablename__ = "chat_messages"

    # Integer (not BigInteger): SQLite only autoincrements INTEGER primary keys
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))    # user | assistant | tool
    content: Mapped[str] = mapped_column(Text)
    cards: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of card dicts
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
