"""SQLAlchemy models for pred-agent.

Tables created in Phase 2: fixtures, markets, kv, decisions, audit_log.
Remaining tables (proposals, orders, fills, positions, pnl_ledger, research_snapshots,
odds_snapshots, probabilities, devices) are added in later phases.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
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
