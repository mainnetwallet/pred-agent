"""book.py — order book analysis: spread, depth, slippage walk, synthetic flag.

Grounded in kit/02_PRED_API_SPEC.md section 7 (Order book and realtime).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class BookLevel:
    price_cents: Decimal
    quantity: Decimal
    order_count: int | None = None
    total_usd: Decimal | None = None


@dataclass(frozen=True)
class BookSnapshot:
    market_id: str
    bids: list[BookLevel]   # descending price
    asks: list[BookLevel]   # ascending price
    ltp: Decimal | None
    spread_cents: Decimal | None
    mid_price_cents: Decimal | None
    last_updated_at: str | None
    sequence_number: int | None
    synthetic: bool = False
    synthetic_reason: str | None = None


@dataclass(frozen=True)
class SlippageWalk:
    side: str          # "buy" or "sell"
    target_shares: Decimal
    avg_price_cents: Decimal
    slippage_pp: Decimal
    filled_shares: Decimal
    usable: bool       # True if depth >= size * 3 at acceptable price


class BookAnalyzer:
    """Analyzes a normalized book snapshot."""

    def __init__(self, *, max_spread_cents: Decimal = Decimal("6"),
                 max_slippage_pp: Decimal = Decimal("1.5")) -> None:
        self.max_spread_cents = max_spread_cents
        self.max_slippage_pp = max_slippage_pp

    def analyze(self, snapshot: BookSnapshot) -> BookSnapshot:
        """Return snapshot with spread/mid computed and synthetic flagged if suspected."""
        spread = None
        mid = None
        if snapshot.bids and snapshot.asks:
            best_bid = snapshot.bids[0].price_cents
            best_ask = snapshot.asks[0].price_cents
            spread = best_ask - best_bid
            mid = (best_bid + best_ask) / Decimal(2)
        return BookSnapshot(
            market_id=snapshot.market_id,
            bids=snapshot.bids,
            asks=snapshot.asks,
            ltp=snapshot.ltp,
            spread_cents=spread,
            mid_price_cents=mid,
            last_updated_at=snapshot.last_updated_at,
            sequence_number=snapshot.sequence_number,
            synthetic=snapshot.synthetic,
            synthetic_reason=snapshot.synthetic_reason,
        )

    def slippage_walk(self, snapshot: BookSnapshot, side: str,
                      target_shares: Decimal) -> SlippageWalk:
        """Walk the book for a given side and size. Returns avg price, slippage, usable flag."""
        levels = snapshot.asks if side == "buy" else snapshot.bids
        remaining = target_shares
        weighted_cost = Decimal(0)
        filled = Decimal(0)
        total_depth = Decimal(0)
        for level in levels:
            total_depth += level.quantity
            if remaining <= 0:
                continue
            take = min(remaining, level.quantity)
            weighted_cost += level.price_cents * take
            filled += take
            remaining -= take
        if filled == 0:
            return SlippageWalk(
                side=side, target_shares=target_shares,
                avg_price_cents=Decimal(0), slippage_pp=Decimal(0),
                filled_shares=Decimal(0), usable=False,
            )
        avg_price = weighted_cost / filled
        best_price = levels[0].price_cents if levels else avg_price
        # Slippage in percentage points: how far from best price we had to go
        if side == "buy":
            slippage_pp = (avg_price - best_price) / best_price * Decimal(100)
        else:
            slippage_pp = (best_price - avg_price) / best_price * Decimal(100)
        # Veto: depth under size times 3 at acceptable price (kit/05_STRATEGY_AND_RISK.md)
        usable = (total_depth >= target_shares * Decimal(3)) and (
            slippage_pp <= self.max_slippage_pp
        )
        return SlippageWalk(
            side=side, target_shares=target_shares,
            avg_price_cents=avg_price, slippage_pp=slippage_pp,
            filled_shares=filled, usable=usable,
        )

    def check_spread(self, snapshot: BookSnapshot) -> bool:
        """True if spread is within acceptable limit."""
        if snapshot.spread_cents is None:
            return False
        return snapshot.spread_cents <= self.max_spread_cents


def normalize_book(raw: dict[str, Any], market_id: str, *, ws_dollars: bool = False) -> BookSnapshot:
    """Normalize a raw orderbook response (REST cents or WS dollars) into cents.

    ws_dollars=True indicates the source is the Ably WS (prices like "0.62" = 62¢).
    REST prices are already in cents (e.g. "62").
    """
    bids_raw = raw.get("bids", [])
    asks_raw = raw.get("asks", [])

    def to_cents(price_str: Any) -> Decimal:
        p = Decimal(str(price_str))
        if ws_dollars:
            return p * Decimal(100)  # 0.62 -> 62 cents
        return p  # already cents

    bids = [
        BookLevel(
            price_cents=to_cents(lvl.get("price")),
            quantity=Decimal(str(lvl.get("quantity", "0"))),
            order_count=lvl.get("order_count"),
            total_usd=Decimal(str(lvl["total_usd"])) if lvl.get("total_usd") is not None else None,
        )
        for lvl in bids_raw
    ]
    asks = [
        BookLevel(
            price_cents=to_cents(lvl.get("price")),
            quantity=Decimal(str(lvl.get("quantity", "0"))),
            order_count=lvl.get("order_count"),
            total_usd=Decimal(str(lvl["total_usd"])) if lvl.get("total_usd") is not None else None,
        )
        for lvl in asks_raw
    ]
    # REST: bids desc, asks asc. WS may not be sorted — sort defensively.
    bids.sort(key=lambda lvl: lvl.price_cents, reverse=True)
    asks.sort(key=lambda lvl: lvl.price_cents)

    ltp = None
    if raw.get("ltp") is not None:
        ltp = to_cents(raw["ltp"])

    metadata = raw.get("metadata") or {}
    return BookSnapshot(
        market_id=market_id,
        bids=bids,
        asks=asks,
        ltp=ltp,
        spread_cents=None,  # computed by analyzer
        mid_price_cents=None,
        last_updated_at=raw.get("last_updated_at") or metadata.get("last_calculated"),
        sequence_number=raw.get("sequence_number") or raw.get("last_update_id"),
        synthetic=False,
    )
