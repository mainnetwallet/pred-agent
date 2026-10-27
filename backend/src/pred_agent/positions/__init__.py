"""positions module — paper positions and PnL ledger.

Grounded in kit/02_PRED_API_SPEC.md section 9 (Portfolio) and kit/05_STRATEGY_AND_RISK.md
section 6 (Exits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pred_agent.store.repository import PositionRepo


@dataclass
class Position:
    market_id: str
    parent_id: str
    side: str  # "long" or "short"
    quantity: Decimal  # absolute shares
    avg_price_cents: Decimal  # YES-price cents
    cost_usd: Decimal  # invested USD
    status: str = "open"
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None


@dataclass
class PnLEntry:
    ts: datetime
    kind: str  # "realized" | "unrealized" | "fee"
    amount_usd: Decimal
    ref: str


class PositionManager:
    """Tracks paper positions and PnL ledger.

    ``db`` attaches a repository: state is loaded on startup and every
    mutation is written through, so positions survive restarts.
    """

    def __init__(self, db: "PositionRepo | None" = None) -> None:
        self._db = db
        self._positions: dict[str, Position] = db.load_positions() if db else {}
        self._pnl_ledger: list[PnLEntry] = db.load_pnl() if db else []

    def open_position(self, *, market_id: str, parent_id: str, side: str,
                      quantity: Decimal, avg_price_cents: Decimal, cost_usd: Decimal) -> None:
        self._positions[market_id] = Position(
            market_id=market_id, parent_id=parent_id, side=side,
            quantity=quantity, avg_price_cents=avg_price_cents, cost_usd=cost_usd,
        )
        self._persist(self._positions[market_id])

    def get_position(self, market_id: str) -> Position | None:
        return self._positions.get(market_id)

    def list_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.status == "open"]

    def close_position(self, market_id: str, realized_pnl_usd: Decimal) -> Position | None:
        pos = self._positions.get(market_id)
        if pos is None:
            return None
        pos.status = "closed"
        pos.closed_at = datetime.now(timezone.utc)
        entry = PnLEntry(
            ts=datetime.now(timezone.utc), kind="realized",
            amount_usd=realized_pnl_usd, ref=market_id,
        )
        self._pnl_ledger.append(entry)
        self._persist(pos)
        if self._db is not None:
            self._db.add_pnl(entry)
        return pos

    def add_pnl(self, *, kind: str, amount_usd: Decimal, ref: str) -> None:
        entry = PnLEntry(
            ts=datetime.now(timezone.utc), kind=kind, amount_usd=amount_usd, ref=ref,
        )
        self._pnl_ledger.append(entry)
        if self._db is not None:
            self._db.add_pnl(entry)

    def _persist(self, pos: Position) -> None:
        if self._db is not None:
            self._db.save(pos)

    def daily_pnl(self) -> Decimal:
        today = datetime.now(timezone.utc).date()
        return sum((e.amount_usd for e in self._pnl_ledger
                    if e.ts.date() == today), Decimal(0))

    def total_pnl(self) -> Decimal:
        return sum((e.amount_usd for e in self._pnl_ledger), Decimal(0))
