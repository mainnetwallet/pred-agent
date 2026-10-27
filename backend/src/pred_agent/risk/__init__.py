"""risk module — hard vetoes, Kelly sizing, caps, daily loss pause.

Grounded in kit/05_STRATEGY_AND_RISK.md sections 3-4 (Sizing and Hard vetoes).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class RiskResult:
    allowed: bool
    veto_reasons: list[str]
    size_usd: Decimal
    binding_cap: str | None
    max_suggested_usd: Decimal
    kelly_fraction: Decimal
    daily_loss_paused: bool


class RiskEngine:
    """Enforces hard vetoes and Kelly sizing with caps."""

    def __init__(
        self,
        *,
        bankroll_usd: Decimal,
        min_edge_pp: Decimal = Decimal("3"),
        kelly_fraction: Decimal = Decimal("0.25"),
        max_trade_usd: Decimal = Decimal("2"),
        per_fixture_pct: Decimal = Decimal("0.04"),
        total_open_exposure_pct: Decimal = Decimal("0.15"),
        daily_loss_pct: Decimal = Decimal("0.05"),
        max_open_positions: int = 8,
        min_opening_notional_usd: Decimal = Decimal("0.90"),
        advisory: bool = False,
    ) -> None:
        self.bankroll_usd = bankroll_usd
        self.min_edge_pp = min_edge_pp * (Decimal(2) if advisory else Decimal(1))
        self.kelly_fraction = kelly_fraction * (Decimal("0.5") if advisory else Decimal(1))
        self.max_trade_usd = max_trade_usd * (Decimal("0.5") if advisory else Decimal(1))
        self.per_fixture_pct = per_fixture_pct
        self.total_open_exposure_pct = total_open_exposure_pct
        self.daily_loss_pct = daily_loss_pct
        self.max_open_positions = max_open_positions
        self.min_opening_notional_usd = min_opening_notional_usd
        self.advisory = advisory
        self.daily_realized_loss_usd = Decimal("0")

    def veto(self, *, edge_pp: Decimal, spread_cents: Decimal | None,
             depth_ok: bool, research_fresh: bool, research_has_sources: bool,
             book_fresh: bool, units_ok: bool, fee_target_present: bool,
             tick_ok: bool, fixture_mapped: bool, pending_proposal: bool,
             unknown_order: bool, kickoff_minutes: int | None,
             synthetic_only: bool, live_no_live_source: bool) -> list[str]:
        reasons: list[str] = []
        if edge_pp < self.min_edge_pp:
            reasons.append(f"edge {edge_pp:.2f}pp below min {self.min_edge_pp:.2f}pp")
        if spread_cents is not None and spread_cents > Decimal("6"):
            reasons.append(f"spread {spread_cents}c above 6c max")
        if not depth_ok:
            reasons.append("depth under size*3")
        if not research_fresh:
            reasons.append("research report stale")
        if not research_has_sources:
            reasons.append("research without sources")
        if not book_fresh:
            reasons.append("book stale")
        if not units_ok:
            reasons.append("unit check failed")
        if not fee_target_present:
            reasons.append("fee target missing")
        if not tick_ok:
            reasons.append("tick size mismatch")
        if not fixture_mapped:
            reasons.append("fixture mapping uncertain")
        if pending_proposal:
            reasons.append("pending proposal exists for this market")
        if unknown_order:
            reasons.append("order in UNKNOWN state on this market")
        if kickoff_minutes is not None and kickoff_minutes < 5:
            reasons.append("kickoff under 5 minutes")
        if synthetic_only:
            reasons.append("synthetic-only depth")
        if live_no_live_source:
            reasons.append("live match with no live-aware probability source")
        return reasons

    def kelly_size(self, *, win_prob: Decimal, cost_per_share: Decimal) -> Decimal:
        """f_kelly = (win_prob - c) / (1 - c); f = KELLY_FRACTION * f_kelly."""
        if cost_per_share >= Decimal(1):
            return Decimal(0)
        f_kelly = (win_prob - cost_per_share) / (Decimal(1) - cost_per_share)
        if f_kelly <= 0:
            return Decimal(0)
        return self.kelly_fraction * f_kelly

    def size_usd(self, *, kelly_f: Decimal, bankroll_usd: Decimal,
                 per_fixture_used_usd: Decimal, total_open_usd: Decimal,
                 open_positions_count: int, daily_loss_usd: Decimal) -> RiskResult:
        """Apply all caps. Returns size and binding cap."""
        veto_reasons: list[str] = []
        if daily_loss_usd >= self.bankroll_usd * self.daily_loss_pct:
            veto_reasons.append("daily loss cap hit")
        if open_positions_count >= self.max_open_positions:
            veto_reasons.append("max open positions reached")

        # Raw Kelly size
        raw_size = kelly_f * bankroll_usd

        # Caps
        cap_per_trade = min(self.max_trade_usd,
                            self.per_fixture_pct * bankroll_usd - per_fixture_used_usd)
        cap_fixture = self.per_fixture_pct * bankroll_usd - per_fixture_used_usd
        cap_exposure = self.total_open_exposure_pct * bankroll_usd - total_open_usd

        size = min(raw_size, cap_per_trade, cap_fixture, cap_exposure, self.max_trade_usd)

        # Determine binding cap
        binding = None
        if size <= 0:
            binding = ("daily_loss_pause" if daily_loss_usd >= self.bankroll_usd * self.daily_loss_pct
                       else "zero_size")
        elif size == self.max_trade_usd:
            binding = "MAX_TRADE_USD"
        elif size == cap_fixture:
            binding = "per_fixture_pct"
        elif size == cap_exposure:
            binding = "total_open_exposure_pct"
        elif size == raw_size:
            binding = "kelly"
        else:
            binding = "MAX_TRADE_USD or per_fixture_pct"

        # Min opening notional
        if size < self.min_opening_notional_usd:
            size = Decimal("0")
            binding = "min_opening_notional"

        allowed = size > 0 and not veto_reasons

        return RiskResult(
            allowed=allowed,
            veto_reasons=veto_reasons,
            size_usd=size,
            binding_cap=binding,
            max_suggested_usd=size,
            kelly_fraction=self.kelly_fraction,
            daily_loss_paused=daily_loss_usd >= self.bankroll_usd * self.daily_loss_pct,
        )
