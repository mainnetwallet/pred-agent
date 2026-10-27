"""odds package — optional bookmaker odds with vig removal.

Grounded in kit/05_STRATEGY_AND_RISK.md section 1 (Baseline from bookmaker odds).
Without an odds key, run ADVISORY mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal


@dataclass
class OddsSnapshot:
    fixture_id: str
    home_decimal: Decimal | None
    draw_decimal: Decimal | None
    away_decimal: Decimal | None
    provider: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OddsProvider:
    """Optional odds provider with vig removal. Returns None if no key configured."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def available(self) -> bool:
        return self.api_key is not None

    async def fetch_odds(self, fixture_id: str) -> OddsSnapshot | None:
        if not self.available():
            return None
        # Placeholder: actual odds API call
        return None

    @staticmethod
    def remove_vig(home: Decimal, draw: Decimal | None, away: Decimal) -> dict[str, Decimal]:
        """Proportional vig removal. Returns implied probabilities summing to 1."""
        total = home + draw + away if draw is not None else home + away
        if total <= 0:
            return {"home": Decimal("0"), "draw": Decimal("0"), "away": Decimal("0")}
        probs = {
            "home": Decimal(1) / home,
            "away": Decimal(1) / away,
        }
        if draw is not None:
            probs["draw"] = Decimal(1) / draw
        s = sum(probs.values())
        if s <= 0:
            return {"home": Decimal("0"), "draw": Decimal("0"), "away": Decimal("0")}
        return {k: v / s for k, v in probs.items()}
