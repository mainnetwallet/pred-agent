"""positions module — paper positions and PnL ledger.

Grounded in kit/02_PRED_API_SPEC.md section 9 (Portfolio) and kit/05_STRATEGY_AND_RISK.md
section 6 (Exits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal


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
    """Tracks paper positions and PnL ledger."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._pnl_ledger: list[PnLEntry] = []

    def open_position(self, *, market_id: str, parent_id: str, side: str,
                      quantity: Decimal, avg_price_cents: Decimal, cost_usd: Decimal) -> None:
        self._positions[market_id] = Position(
            market_id=market_id, parent_id=parent_id, side=side,
            quantity=quantity, avg_price_cents=avg_price_cents, cost_usd=cost_usd,
        )

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
        self._pnl_ledger.append(PnLEntry(
            ts=datetime.now(timezone.utc), kind="realized",
            amount_usd=realized_pnl_usd, ref=market_id,
        ))
        return pos

    def add_pnl(self, *, kind: str, amount_usd: Decimal, ref: str) -> None:
        self._pnl_ledger.append(PnLEntry(
            ts=datetime.now(timezone.utc), kind=kind, amount_usd=amount_usd, ref=ref,
        ))

    def daily_pnl(self) -> Decimal:
        today = datetime.now(timezone.utc).date()
        return sum((e.amount_usd for e in self._pnl_ledger
                    if e.ts.date() == today), Decimal(0))

    def total_pnl(self) -> Decimal:
        return sum((e.amount_usd for e in self._pnl_ledger), Decimal(0))
