"""Order state machine for live execution.

Grounded in kit/03_ARCHITECTURE.md section 4 (Order state machine) and
kit/02_PRED_API_SPEC.md section 8 (Orders).

States:
  PROPOSED -> APPROVED -> REVALIDATED -> SUBMITTED -> (DELAYED) -> OPEN -> PARTIAL -> FILLED
  FILLED -> CLOSING -> CLOSED -> SETTLED
  Any uncertain outcome -> UNKNOWN (reconcile only, never resend)
  Any rejection -> FAILED / REJECTED / EXPIRED / ABORTED

Rules:
- Transitions are persisted before side effects.
- UNKNOWN blocks new orders on that market until resolved.
- order_updated and order_matched_on_chain update the same fill row (key match_id/fill_id).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class OrderState(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REVALIDATED = "REVALIDATED"
    ABORTED = "ABORTED"
    SUBMITTED = "SUBMITTED"
    DELAYED = "DELAYED"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"


# Legal transitions: from -> set of allowed to
LEGAL_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.PROPOSED: {
        OrderState.APPROVED, OrderState.REJECTED, OrderState.EXPIRED, OrderState.ABORTED,
    },
    OrderState.APPROVED: {
        OrderState.REVALIDATED, OrderState.REJECTED, OrderState.EXPIRED, OrderState.ABORTED,
    },
    OrderState.REVALIDATED: {
        OrderState.SUBMITTED, OrderState.ABORTED,
    },
    OrderState.SUBMITTED: {
        OrderState.DELAYED, OrderState.OPEN, OrderState.FAILED, OrderState.UNKNOWN,
    },
    OrderState.DELAYED: {
        OrderState.OPEN, OrderState.FAILED, OrderState.UNKNOWN,
    },
    OrderState.OPEN: {
        OrderState.PARTIAL, OrderState.FILLED, OrderState.FAILED, OrderState.UNKNOWN,
    },
    OrderState.PARTIAL: {
        OrderState.FILLED, OrderState.OPEN, OrderState.FAILED, OrderState.UNKNOWN,
    },
    OrderState.FILLED: {
        OrderState.CLOSING, OrderState.SETTLED,
    },
    OrderState.CLOSING: {
        OrderState.CLOSED, OrderState.FAILED, OrderState.UNKNOWN,
    },
    OrderState.CLOSED: {
        OrderState.SETTLED,
    },
    OrderState.SETTLED: set(),
    OrderState.FAILED: set(),
    OrderState.EXPIRED: set(),
    OrderState.ABORTED: set(),
    OrderState.REJECTED: set(),
    OrderState.UNKNOWN: set(),  # only reconcile can exit UNKNOWN
}


@dataclass
class OrderRecord:
    id: str
    pred_order_id: str | None
    proposal_id: str | None
    state: OrderState
    side: str
    price_cents: int
    quantity: str  # Decimal string in shares
    market_id: str | None = None
    parent_market_id: str | None = None
    filled_qty: str = "0"
    remaining_qty: str | None = None
    salt: str | None = None
    signed_fee_bps: int = 0
    is_delayed: bool = False
    delay_until: int | None = None
    reduce_only: bool = False
    raw_request_redacted: str | None = None
    raw_response: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FillRecord:
    id: str
    order_id: str
    match_id: str | None
    fill_id: str | None
    price_cents: int
    quantity: str
    matched_on_chain: bool = False
    fee_usd: str = "0"
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OrderStateMachine:
    """Persists-before-side-effect state transitions."""

    def __init__(self) -> None:
        self._orders: dict[str, OrderRecord] = {}
        self._fills: dict[str, FillRecord] = {}  # keyed by fill_id
        self._fills_by_match: dict[str, FillRecord] = {}  # keyed by match_id
        self._unknown_markets: set[str] = set()

    # ------------------------------------------------------------------ #
    # Order lifecycle
    # ------------------------------------------------------------------ #

    def create(self, record: OrderRecord) -> None:
        self._orders[record.id] = record

    def transition(self, order_id: str, new_state: OrderState) -> OrderRecord | None:
        """Persist-before-side-effect transition. Returns the record or None."""
        order = self._orders.get(order_id)
        if order is None:
            return None
        if new_state not in LEGAL_TRANSITIONS.get(order.state, set()):
            # Illegal transition — fail closed
            return None
        old_state = order.state
        order.state = new_state
        order.updated_at = datetime.now(timezone.utc)
        # Persist happens here (DB write in production); then side effects.
        return order

    def get(self, order_id: str) -> OrderRecord | None:
        return self._orders.get(order_id)

    def list_orders(self) -> list[OrderRecord]:
        return list(self._orders.values())

    # ------------------------------------------------------------------ #
    # UNKNOWN market blocking
    # ------------------------------------------------------------------ #

    def block_market(self, market_id: str) -> None:
        self._unknown_markets.add(market_id)

    def unblock_market(self, market_id: str) -> None:
        self._unknown_markets.discard(market_id)

    def is_market_blocked(self, market_id: str) -> bool:
        return market_id in self._unknown_markets

    # ------------------------------------------------------------------ #
    # Fill handling — keyed by match_id / fill_id, never double count
    # ------------------------------------------------------------------ #

    def apply_fill(
        self,
        *,
        fill_id: str | None,
        match_id: str | None,
        order_id: str,
        price_cents: int,
        quantity: str,
        matched_on_chain: bool,
        fee_usd: str = "0",
    ) -> FillRecord | None:
        """Apply a fill. Key on match_id/fill_id. order_updated and
        order_matched_on_chain update the same fill row — never count both."""
        # Deduplicate by fill_id
        if fill_id and fill_id in self._fills:
            return self._fills[fill_id]
        # Deduplicate by match_id
        if match_id and match_id in self._fills_by_match:
            return self._fills_by_match[match_id]

        fill = FillRecord(
            id=fill_id or match_id or f"fill-{len(self._fills)+1}",
            order_id=order_id,
            match_id=match_id,
            fill_id=fill_id,
            price_cents=price_cents,
            quantity=quantity,
            matched_on_chain=matched_on_chain,
            fee_usd=fee_usd,
        )
        self._fills[fill.id] = fill
        if match_id:
            self._fills_by_match[match_id] = fill
        return fill

    def get_fill(self, fill_id: str) -> FillRecord | None:
        return self._fills.get(fill_id)

    def list_fills(self) -> list[FillRecord]:
        return list(self._fills.values())
