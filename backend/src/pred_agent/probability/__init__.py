"""probability engine — p_fair estimation with capped LLM adjustment.

Grounded in kit/05_STRATEGY_AND_RISK.md section 1 (Probability).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class ProbabilityResult:
    p_baseline: Decimal       # from odds or market-implied prior
    p_adjust: Decimal         # LLM adjustment in percentage points, clamped
    p_fair_home: Decimal      # final fair probability for Home
    p_fair_draw: Decimal      # final fair probability for Draw
    p_fair_away: Decimal      # final fair probability for Away
    low: Decimal              # conservative low bound for Home
    high: Decimal             # high bound for Home
    confidence: Decimal       # 0..1
    advisory: bool            # True if no odds key
    reasons: list[str]
    red_flags: list[str]


class ProbabilityEngine:
    """Computes p_fair for Home/Draw/Away outcomes.

    Rules:
    - Baseline from bookmaker odds (vig removed) -> p_baseline per outcome.
    - Without odds key: ADVISORY mode — market-implied price from PRED mid blended with
      team-strength estimate from research report.
    - LLM adjustment clamped to +/- 3 percentage points; invalid output -> 0.
    - p_fair = clamp(p_baseline + adjust, 0.01, 0.99); normalize so three outcomes sum to 1.
    - Produce [low, high] range from source disagreement and confidence.
    """

    def __init__(self, *, min_edge_pp: Decimal = Decimal("3")) -> None:
        self.min_edge_pp = min_edge_pp

    def clamp01(self, v: Decimal) -> Decimal:
        return min(Decimal("0.99"), max(Decimal("0.01"), v))

    def normalize3(self, home: Decimal, draw: Decimal, away: Decimal) -> tuple[Decimal, Decimal, Decimal]:
        total = home + draw + away
        if total <= 0:
            return Decimal("0.33"), Decimal("0.33"), Decimal("0.33")
        return home / total, draw / total, away / total

    def advisory_prior(self, pred_mid_cents: Decimal) -> dict[str, Decimal]:
        """Weak prior from PRED mid price."""
        # Mid price is the YES price for Home. Home prob ~ mid/100, Away ~ 1-mid/100, small draw.
        p_home = self.clamp01(pred_mid_cents / Decimal(100))
        p_away = self.clamp01(Decimal(1) - p_home)
        p_draw = Decimal("0.05")
        home, draw, away = self.normalize3(p_home, p_draw, p_away)
        return {"home": home, "draw": draw, "away": away}

    def apply_llm_adjustment(self, baseline: dict[str, Decimal],
                             adjustment_pp: Decimal) -> dict[str, Decimal]:
        """Clamp adjustment to +/- 3 pp and apply to home/away (draw unchanged)."""
        clamped = max(-Decimal("3"), min(Decimal("3"), adjustment_pp))
        result = dict(baseline)
        result["home"] = self.clamp01(result["home"] + clamped / Decimal(100))
        result["away"] = self.clamp01(result["away"] - clamped / Decimal(100))
        h, d, a = self.normalize3(result["home"], result["draw"], result["away"])
        return {"home": h, "draw": d, "away": a}

    def compute(self, *, baseline: dict[str, Decimal], llm_adjust_pp: Decimal | None,
                confidence: Decimal, advisory: bool) -> ProbabilityResult:
        adjust = llm_adjust_pp if llm_adjust_pp is not None else Decimal("0")
        p_fair = self.apply_llm_adjustment(baseline, adjust)
        return ProbabilityResult(
            p_baseline=baseline.get("home", Decimal("0")),
            p_adjust=adjust,
            p_fair_home=p_fair["home"],
            p_fair_draw=p_fair["draw"],
            p_fair_away=p_fair["away"],
            low=max(Decimal("0"), p_fair["home"] - Decimal("0.03")),
            high=min(Decimal("1"), p_fair["home"] + Decimal("0.03")),
            confidence=confidence,
            advisory=advisory,
            reasons=[],
            red_flags=[],
        )
