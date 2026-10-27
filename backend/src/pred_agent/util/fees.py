"""Signed fee rate for PRED orders (docs: Orders -> Fees). Integer arithmetic only."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def signed_fee_bps(target_bps: int, price_cents: Decimal) -> int:
    """signed = floor(targetBps * max(p, 1 - p)) with p = scaledPrice / 1e6.

    ``target_bps`` is discovery ``parent_market_data.fee_rate_bps``. Target <= 0 means fee-free.
    Never use floats: they disagree with integer floor at several tick prices.
    """
    if target_bps <= 0:
        return 0
    scaled = int((price_cents * Decimal(1_000_000) / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if not 0 < scaled < 1_000_000:
        raise ValueError(f"price out of range (0, 100) cents: {price_cents}")
    m = max(scaled, 1_000_000 - scaled)
    return (target_bps * m) // 1_000_000
