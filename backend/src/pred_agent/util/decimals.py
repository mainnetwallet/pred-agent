"""Decimal helpers for money, prices and fees.

All money math uses Decimal — never float.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

CENT = Decimal("100")          # 100 cents = $1.00
MIN_OPENING_NOTIONAL = Decimal("0.90")
REDUCE_ONLY_MIN_QTY = Decimal("0.001")
MAX_QTY_DECIMALS = 3


def dec(value: str | int | float | Decimal) -> Decimal:
    """Parse a value to Decimal, rejecting floats that are not exact."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Reject binary floats — force callers to pass strings or ints.
        raise TypeError("float is not allowed; pass a string or int to dec()")
    return Decimal(str(value))


def money_usd(value: Decimal) -> Decimal:
    """Quantize to USD (2 decimal places), rounding down (fail closed)."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def cents_to_share_price(price_cents: Decimal) -> int:
    """Convert cents to EIP-712 price scale: priceCents * 10_000.

    55c -> 550000 (NOT 55 * 1e6).
    """
    return int((price_cents * Decimal(10_000)).to_integral_value(rounding=ROUND_DOWN))


def shares_to_quantity(shares: Decimal) -> int:
    """Convert shares to EIP-712 quantity scale: shares * 1_000_000."""
    return int((shares * Decimal(1_000_000)).to_integral_value(rounding=ROUND_DOWN))


def share_price_to_cents(price_scaled: int) -> Decimal:
    """Convert EIP-712 price scale back to cents."""
    return Decimal(price_scaled) / Decimal(10_000)


def quantity_to_shares(quantity_scaled: int) -> Decimal:
    """Convert EIP-712 quantity scale back to shares."""
    return Decimal(quantity_scaled) / Decimal(1_000_000)


def validate_quantity(shares: Decimal) -> None:
    """Enforce max 3 decimals on quantity."""
    if shares.as_tuple().exponent < -MAX_QTY_DECIMALS:
        raise ValueError(f"quantity exceeds {MAX_QTY_DECIMALS} decimal places")


def validate_opening_notional(amount_usd: Decimal) -> None:
    """Enforce the $0.90 minimum opening notional."""
    if amount_usd < MIN_OPENING_NOTIONAL:
        raise ValueError(f"opening notional below $0.90: {amount_usd}")


def validate_reduce_only_qty(shares: Decimal) -> None:
    """Enforce reduce-only minimum quantity >= 0.001."""
    if shares < REDUCE_ONLY_MIN_QTY:
        raise ValueError(f"reduce-only quantity below 0.001: {shares}")
