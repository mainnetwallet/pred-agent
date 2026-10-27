"""Unit conversions: cents <-> 1e6 wei, price/quantity scaling.

Grounded in kit/02_PRED_API_SPEC.md section 8 (Orders → Units).
"""

from __future__ import annotations

from decimal import Decimal

from .decimals import (
    cents_to_share_price,
    quantity_to_shares,
    share_price_to_cents,
    shares_to_quantity,
)


def price_cents_to_wei(price_cents: Decimal) -> int:
    """Cents -> EIP-712 price (wei-scaled): cents * 10_000.

    Example: 55 cents -> 550000.
    """
    return cents_to_share_price(price_cents)


def wei_to_price_cents(price_wei: int) -> Decimal:
    """EIP-712 price (wei-scaled) -> cents."""
    return share_price_to_cents(price_wei)


def shares_to_wei(shares: Decimal) -> int:
    """Shares -> EIP-712 quantity (wei-scaled): shares * 1_000_000."""
    return shares_to_quantity(shares)


def wei_to_shares(quantity_wei: int) -> Decimal:
    """EIP-712 quantity (wei-scaled) -> shares."""
    return quantity_to_shares(quantity_wei)
