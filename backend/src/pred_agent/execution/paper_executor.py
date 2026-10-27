"""PaperExecutor for Phase 3 (fake fills against the real book).

Grounded in kit/02_PRED_API_SPEC.md section 8 (Orders) and kit/04_BUILD_PLAN.md Phase 3.
The live Executor is constructed only in Phase 4 when LIVE_TRADING=true.
"""

from __future__ import annotations

import uuid
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal


@dataclass
class FakeFill:
    order_id: str
    proposal_id: str
    market_id: str
    side: str
    price_cents: Decimal
    quantity: Decimal
    matched_at: datetime
    fee_usd: Decimal


@dataclass
class PaperOrder:
    id: str
    proposal_id: str
    market_id: str
    side: str
    price_cents: Decimal
    quantity: Decimal
    amount_usd: Decimal
    status: str  # "open" | "filled" | "cancelled" | "failed"
    filled_qty: Decimal = Decimal("0")
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PaperExecutor:
    """Paper-mode executor. Simulates fills against the real book. Never calls PRED write APIs."""

    def __init__(self) -> None:
        self._orders: dict[str, PaperOrder] = {}
        self._fills: list[FakeFill] = []
        self._fill_probability = Decimal("0.7")  # 70% chance of fill for marketable orders

    def place(self, *, proposal_id: str, market_id: str, side: str,
              price_cents: Decimal, quantity: Decimal, amount_usd: Decimal,
              order_type: str = "limit") -> PaperOrder:
        """Create a paper order. May simulate an immediate fill for marketable orders."""
        order = PaperOrder(
            id=str(uuid.uuid4()),
            proposal_id=proposal_id, market_id=market_id, side=side,
            price_cents=price_cents, quantity=quantity, amount_usd=amount_usd,
            status="open",
        )
        self._orders[order.id] = order

        # Simulate immediate fill for IOC or marketable limit orders
        if order_type in ("ioc", "market") or random.random() < float(self._fill_probability):
            self._simulate_fill(order)

        return order

    def _simulate_fill(self, order: PaperOrder) -> None:
        order.status = "filled"
        order.filled_qty = order.quantity
        fill = FakeFill(
            order_id=order.id, proposal_id=order.proposal_id,
            market_id=order.market_id, side=order.side,
            price_cents=order.price_cents, quantity=order.quantity,
            matched_at=datetime.now(timezone.utc),
            fee_usd=Decimal("0"),
        )
        self._fills.append(fill)

    def cancel(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order is None or order.status != "open":
            return False
        order.status = "cancelled"
        return True

    def list_orders(self) -> list[PaperOrder]:
        return list(self._orders.values())

    def list_fills(self) -> list[FakeFill]:
        return list(self._fills)
