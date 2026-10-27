"""strategy module — edge after fee and slippage, side choice.

Grounded in kit/05_STRATEGY_AND_RISK.md section 2 (Edge).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class EdgeResult:
    side: str                    # "long" or "short"
    price_cents: Decimal
    win_prob: Decimal
    cost_per_share: Decimal
    fee_per_share: Decimal
    slip_per_share: Decimal
    edge_pp: Decimal             # edge in percentage points
    passes_veto: bool


class Strategy:
    """Computes edge per outcome/side and chooses Long or Short."""

    def __init__(self, *, min_edge_pp: Decimal = Decimal("3")) -> None:
        self.min_edge_pp = min_edge_pp

    def edge_for_side(self, *, p_fair: Decimal, price_cents: Decimal,
                      fee_per_share: Decimal, slip_per_share: Decimal,
                      side: str) -> EdgeResult:
        if side == "long":
            cost = price_cents / Decimal(100)
            win_prob = p_fair
        else:
            cost = (Decimal(100) - price_cents) / Decimal(100)
            win_prob = Decimal(1) - p_fair

        edge = win_prob - cost - fee_per_share - slip_per_share
        edge_pp = edge * Decimal(100)
        passes = edge_pp >= self.min_edge_pp

        return EdgeResult(
            side=side, price_cents=price_cents, win_prob=win_prob,
            cost_per_share=cost, fee_per_share=fee_per_share,
            slip_per_share=slip_per_share, edge_pp=edge_pp, passes_veto=passes,
        )

    def choose_side(self, *, p_fair: Decimal, price_cents: Decimal,
                    fee_per_share: Decimal, slip_per_share: Decimal) -> EdgeResult | None:
        """Pick the side with positive edge above min_edge_pp. Returns None if neither passes."""
        long_edge = self.edge_for_side(
            p_fair=p_fair, price_cents=price_cents, fee_per_share=fee_per_share,
            slip_per_share=slip_per_share, side="long",
        )
        short_edge = self.edge_for_side(
            p_fair=p_fair, price_cents=price_cents, fee_per_share=fee_per_share,
            slip_per_share=slip_per_share, side="short",
        )
        candidates = [e for e in (long_edge, short_edge) if e.passes_veto]
        if not candidates:
            return None
        # Choose the side with higher edge
        return max(candidates, key=lambda e: e.edge_pp)
