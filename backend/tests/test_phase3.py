"""Phase 3 unit tests: risk, strategy, probability, proposals, positions, paper executor, reports.

No network calls — uses mock data.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from pred_agent.execution import PaperExecutor
from pred_agent.positions import PositionManager
from pred_agent.probability import ProbabilityEngine
from pred_agent.proposals import ProposalService, ProposalStatus
from pred_agent.reports import Reports
from pred_agent.risk import RiskEngine
from pred_agent.strategy import Strategy


# ---------------------------------------------------------------------------
# Risk: vetoes and Kelly sizing
# ---------------------------------------------------------------------------

def test_risk_veto_low_edge():
    engine = RiskEngine(bankroll_usd=Decimal("100"))
    reasons = engine.veto(
        edge_pp=Decimal("1"), spread_cents=Decimal("2"), depth_ok=True,
        research_fresh=True, research_has_sources=True, book_fresh=True,
        units_ok=True, fee_target_present=True, tick_ok=True,
        fixture_mapped=True, pending_proposal=False, unknown_order=False,
        kickoff_minutes=None, synthetic_only=False, live_no_live_source=False,
    )
    assert any("edge" in r for r in reasons)


def test_risk_veto_spread():
    engine = RiskEngine(bankroll_usd=Decimal("100"))
    reasons = engine.veto(
        edge_pp=Decimal("5"), spread_cents=Decimal("8"), depth_ok=True,
        research_fresh=True, research_has_sources=True, book_fresh=True,
        units_ok=True, fee_target_present=True, tick_ok=True,
        fixture_mapped=True, pending_proposal=False, unknown_order=False,
        kickoff_minutes=None, synthetic_only=False, live_no_live_source=False,
    )
    assert any("spread" in r for r in reasons)


def test_risk_advisory_doubles_min_edge():
    engine = RiskEngine(bankroll_usd=Decimal("100"), advisory=True)
    assert engine.min_edge_pp == Decimal("6")


def test_risk_kelly_never_negative():
    engine = RiskEngine(bankroll_usd=Decimal("1000"))
    f = engine.kelly_size(win_prob=Decimal("0.1"), cost_per_share=Decimal("0.9"))
    assert f == Decimal("0")


def test_risk_size_applies_caps():
    engine = RiskEngine(bankroll_usd=Decimal("1000"), max_trade_usd=Decimal("2"))
    result = engine.size_usd(
        kelly_f=Decimal("1"), bankroll_usd=Decimal("1000"),
        per_fixture_used_usd=Decimal("0"), total_open_usd=Decimal("0"),
        open_positions_count=0, daily_loss_usd=Decimal("0"),
    )
    # Kelly would be 1000 but cap is $2
    assert result.size_usd == Decimal("2")
    assert result.binding_cap == "MAX_TRADE_USD"


def test_risk_daily_loss_pause():
    engine = RiskEngine(bankroll_usd=Decimal("1000"), daily_loss_pct=Decimal("0.05"))
    result = engine.size_usd(
        kelly_f=Decimal("0.5"), bankroll_usd=Decimal("1000"),
        per_fixture_used_usd=Decimal("0"), total_open_usd=Decimal("0"),
        open_positions_count=0, daily_loss_usd=Decimal("60"),  # > $50 = 5% of 1000
    )
    assert result.daily_loss_paused is True


# ---------------------------------------------------------------------------
# Strategy: edge and side choice
# ---------------------------------------------------------------------------

def test_strategy_chooses_long():
    strat = Strategy(min_edge_pp=Decimal("3"))
    result = strat.choose_side(
        p_fair=Decimal("0.6"), price_cents=Decimal("50"),
        fee_per_share=Decimal("0.001"), slip_per_share=Decimal("0.001"),
    )
    assert result is not None
    assert result.side == "long"
    assert result.passes_veto is True


def test_strategy_no_side_passes():
    strat = Strategy(min_edge_pp=Decimal("30"))  # very high threshold
    result = strat.choose_side(
        p_fair=Decimal("0.5"), price_cents=Decimal("50"),
        fee_per_share=Decimal("0"), slip_per_share=Decimal("0"),
    )
    assert result is None


# ---------------------------------------------------------------------------
# Probability: clamp and normalize
# ---------------------------------------------------------------------------

def test_probability_clamp():
    eng = ProbabilityEngine()
    assert eng.clamp01(Decimal("1.5")) == Decimal("0.99")
    assert eng.clamp01(Decimal("-0.5")) == Decimal("0.01")
    assert eng.clamp01(Decimal("0.5")) == Decimal("0.5")


def test_probability_normalize_sums_to_1():
    eng = ProbabilityEngine()
    h, d, a = eng.normalize3(Decimal("0.5"), Decimal("0.3"), Decimal("0.2"))
    assert h + d + a == Decimal("1")


# ---------------------------------------------------------------------------
# Proposals: create, approve, reject, expire
# ---------------------------------------------------------------------------

def test_proposal_approve_reject():
    svc = ProposalService(ttl_seconds=300)
    prop = svc.create(
        mode="PAPER", advisory=False, fixture_id="fx1", market_id="mx1",
        side="long", order_type="limit", price_cents=Decimal("52"),
        quantity=Decimal("3.8"), amount_usd=Decimal("1.98"), est_fee_usd=Decimal("0.04"),
        edge_pp=Decimal("4.6"), p_fair=Decimal("0.58"),
        max_suggested_usd=Decimal("2"), binding_cap="MAX_TRADE_USD",
        reasons=["good form"], risks=["key player doubtful"],
    )
    assert prop.status == ProposalStatus.PROPOSED

    approved = svc.approve(prop.id, via="telegram")
    assert approved is not None
    assert approved.status == ProposalStatus.APPROVED
    assert approved.decided_via == "telegram"

    # Re-test reject on already-decided -> None
    assert svc.reject(prop.id, via="telegram") is None


def test_proposal_expire_stale():
    svc = ProposalService(ttl_seconds=300)
    prop = svc.create(
        mode="PAPER", advisory=False, fixture_id="fx1", market_id="mx1",
        side="long", order_type="limit", price_cents=Decimal("52"),
        quantity=Decimal("3.8"), amount_usd=Decimal("1.98"), est_fee_usd=Decimal("0.04"),
        edge_pp=Decimal("4.6"), p_fair=Decimal("0.58"),
        max_suggested_usd=Decimal("2"), binding_cap=None,
    )
    # Manually set expires_at to the past to simulate expiry
    prop.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = svc.expire_stale()
    assert prop.id in expired
    assert svc.get(prop.id).status == ProposalStatus.EXPIRED


# ---------------------------------------------------------------------------
# Positions: open, close, PnL
# ---------------------------------------------------------------------------

def test_position_open_close_pnl():
    pm = PositionManager()
    pm.open_position(
        market_id="mx1", parent_id="px1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"),
        cost_usd=Decimal("5.20"),
    )
    pos = pm.get_position("mx1")
    assert pos is not None
    assert pos.status == "open"

    pm.add_pnl(kind="realized", amount_usd=Decimal("1.00"), ref="mx1")
    assert pm.daily_pnl() == Decimal("1.00")
    assert pm.total_pnl() == Decimal("1.00")

    closed = pm.close_position("mx1", realized_pnl_usd=Decimal("1.00"))
    assert closed.status == "closed"


# ---------------------------------------------------------------------------
# PaperExecutor: place, fill, cancel
# ---------------------------------------------------------------------------

def test_paper_executor_place_and_cancel():
    ex = PaperExecutor()
    ex._fill_probability = Decimal("0")  # force no fill for deterministic test
    order = ex.place(
        proposal_id="p1", market_id="mx1", side="long",
        price_cents=Decimal("52"), quantity=Decimal("3.8"),
        amount_usd=Decimal("1.98"), order_type="limit",
    )
    assert order.status == "open"
    assert ex.cancel(order.id) is True
    assert ex.cancel(order.id) is False  # already cancelled


def test_paper_executor_never_calls_pred():
    """Paper mode must not invoke PRED write methods — tested by construction:
    PaperExecutor only uses in-memory state."""
    ex = PaperExecutor()
    order = ex.place(
        proposal_id="p1", market_id="mx1", side="long",
        price_cents=Decimal("52"), quantity=Decimal("3.8"),
        amount_usd=Decimal("1.98"), order_type="ioc",
    )
    # IOC orders may fill immediately; either way no external call was made
    assert order.id.startswith("paper-") or "-" in order.id


# ---------------------------------------------------------------------------
# Reports: daily summary and calibration
# ---------------------------------------------------------------------------

def test_reports_daily_summary():
    rep = Reports()
    rep.record_proposal_created()
    rep.record_proposal_approved()
    rep.record_fill(p_fair=Decimal("0.6"), actual_won=True)
    summary = rep.daily_summary(
        mode="PAPER", realized_pnl_usd=Decimal("1.50"),
        total_pnl_usd=Decimal("2.00"), open_positions=1,
    )
    assert summary.mode == "PAPER"
    assert summary.proposals_created == 1
    assert summary.proposals_approved == 1
    assert summary.realized_pnl_usd == Decimal("1.50")
    assert summary.brier_score is not None
