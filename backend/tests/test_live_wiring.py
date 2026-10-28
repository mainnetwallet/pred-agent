"""Live trading wiring: executor revalidation, docs-exact amount/fee, error classification,
reconcile, and the controller's LIVE approve path. No network: transports are fakes."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import pred_agent.execution.live_executor as le_mod
from pred_agent.book import BookLevel, BookSnapshot
from pred_agent.execution import (
    LiveExecutor,
    LiveOrderIntent,
    OrderRecord,
    OrderState,
    OrderStateMachine,
)
from pred_agent.execution.live_executor import ReconcileError
from pred_agent.execution.live_runtime import LiveRuntime, MarketContext
from pred_agent.proposals import ProposalStatus
from pred_agent.transport.protocol import PredTransportError
from pred_agent.util.decimals import order_amount_usd, plain_decimal, quantity_for_amount

MARKET = "0x" + "ab" * 32
PARENT = "0x" + "cd" * 32
CONTRACT = "0x" + "ee" * 20
SAFE = "0x" + "33" * 20


def _book(bid: str = "50", ask: str = "52", qty: str = "100", *, one_sided: bool = False) -> BookSnapshot:
    return BookSnapshot(
        market_id=MARKET,
        bids=[BookLevel(Decimal(bid), Decimal(qty))],
        asks=[] if one_sided else [BookLevel(Decimal(ask), Decimal(qty))],
        ltp=None, spread_cents=None, mid_price_cents=None, last_updated_at=None, sequence_number=None,
    )


class FakeTransport:
    order_prefix = "/clob"

    def __init__(self, *, post: Any = None, get: Any = None, delete: Any = None) -> None:
        self._post = post if post is not None else {"status": "order_created", "order_id": "ord_1",
                                                    "filled_quantity": "0"}
        self._get = get if get is not None else {"orders": []}
        self._delete = delete if delete is not None else {"status": "user_cancelled"}
        self.calls: list[tuple[str, str, Any]] = []

    def _reply(self, value: Any) -> dict[str, Any]:
        if isinstance(value, Exception):
            raise value
        return dict(value)

    def post(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("POST", path, json))
        return self._reply(self._post)

    def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append(("GET", path, None))
        return self._reply(self._get)

    def delete(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("DELETE", path, json))
        return self._reply(self._delete)


def _executor(monkeypatch, transport: FakeTransport, *, book: Any = "ok", market_reason: str | None = None,
              proxy: str | None = SAFE) -> LiveExecutor:
    monkeypatch.setattr(le_mod, "load_settings", lambda: SimpleNamespace(live_trading=True))
    provider = (lambda parent, market: _book()) if book == "ok" else (lambda parent, market: book)
    return LiveExecutor(
        transport=transport, state_machine=OrderStateMachine(),
        eoa_address="0x" + "11" * 20, signer_private_key="0x" + "22" * 32,
        proxy_address=proxy, book_provider=provider, market_check=lambda m: market_reason,
    )


def _intent(**over: Any) -> LiveOrderIntent:
    base: dict[str, Any] = {
        "market_id": MARKET, "parent_market_id": PARENT, "contract_address": CONTRACT,
        "side": "long", "price_cents": Decimal("52"), "quantity": Decimal("3.8"),
        "amount_usd": Decimal("999"), "order_type": "limit", "fee_rate_bps": 0,
        "fee_target_bps": 250,
    }
    base.update(over)
    return LiveOrderIntent(**base)


# ── amount / fee ─────────────────────────────────────────────────────────────


def test_amount_is_docs_formula_not_caller_value(monkeypatch):
    t = FakeTransport()
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent())
    assert order is not None and order.state == OrderState.OPEN
    body = t.calls[0][2]
    assert body["amount"] == "1.976"            # 52 * 3.8 / 100, exact; caller's 999 ignored
    assert body["price"] == "52" and body["quantity"] == "3.8"
    assert body["fee_rate_bps"] == 130          # floor(250 * 0.52): signed, not the raw target
    assert t.calls[0][1] == f"/clob/api/v1/order/{PARENT}/place"


def test_short_amount_uses_complement_price():
    assert order_amount_usd("short", Decimal("52"), Decimal("3.8")) == Decimal("1.824")
    assert plain_decimal(Decimal("1.9760")) == "1.976"
    assert plain_decimal(Decimal("1E+1")) == "10"


def test_quantity_for_amount_rounds_down_and_never_exceeds_budget():
    qty = quantity_for_amount("long", Decimal("52"), Decimal("2"))
    assert qty == Decimal("3.846")
    assert order_amount_usd("long", Decimal("52"), qty) <= Decimal("2")
    qty_s = quantity_for_amount("short", Decimal("52"), Decimal("2"))
    assert order_amount_usd("short", Decimal("52"), qty_s) <= Decimal("2")


# ── revalidation refusals: nothing is sent ───────────────────────────────────


@pytest.mark.parametrize("kwargs,book,market_reason,expect", [
    ({"fee_target_bps": None}, "ok", None, "fee_rate_bps missing"),
    ({"quantity": Decimal("1.2345")}, "ok", None, "at most 3 decimals"),
    ({"quantity": Decimal("1"), "price_cents": Decimal("50")}, "ok", None, "below $0.9"),
    ({"price_cents": Decimal("100")}, "ok", None, "out of range"),
    ({"market_id": "mx1"}, "ok", None, "exact 0x"),
    ({"contract_address": ""}, "ok", None, "contract_address"),
    ({}, None, None, "no fresh order book"),
    ({}, _book(one_sided=True), None, "one-sided"),
    ({}, _book(bid="40", ask="52"), None, "spread"),
    ({"price_cents": Decimal("60")}, "ok", None, "book moved"),
    ({"order_type": "ioc", "quantity": Decimal("60")}, "ok", None, "IOC not safe"),
    ({}, "ok", "market not active", "market not active"),
])
def test_revalidation_refuses_and_sends_nothing(monkeypatch, kwargs, book, market_reason, expect):
    t = FakeTransport()
    ex = _executor(monkeypatch, t, book=book, market_reason=market_reason)
    assert ex.place_from_proposal(proposal_id="p", intent=_intent(**kwargs)) is None
    assert expect in (ex.last_abort_reason or "")
    assert t.calls == []


def test_short_checks_against_best_bid(monkeypatch):
    t = FakeTransport()
    ex = _executor(monkeypatch, t)
    # best bid is 50: selling (short) at 40 is >1.5pp worse than the market
    assert ex.place_from_proposal(proposal_id="p", intent=_intent(side="short", price_cents=Decimal("40"))) is None
    assert "below best bid" in (ex.last_abort_reason or "")


def test_edge_gate_only_for_strategy_trades(monkeypatch):
    t = FakeTransport()
    ex = _executor(monkeypatch, t)
    thin = _intent(p_fair=Decimal("0.53"), min_edge_pp=Decimal("3"))  # fair 53c vs price 52c
    assert ex.place_from_proposal(proposal_id="p", intent=thin) is None
    assert "edge" in (ex.last_abort_reason or "")
    manual = _intent(p_fair=Decimal("0.53"), min_edge_pp=None)       # human-chosen: no edge gate
    assert ex.place_from_proposal(proposal_id="p", intent=manual) is not None


def test_missing_proxy_and_unconfigured_providers_fail_closed(monkeypatch):
    t = FakeTransport()
    assert _executor(monkeypatch, t, proxy=None).place_from_proposal(proposal_id="p", intent=_intent()) is None
    monkeypatch.setattr(le_mod, "load_settings", lambda: SimpleNamespace(live_trading=True))
    bare = LiveExecutor(transport=t, state_machine=OrderStateMachine(), eoa_address="0x" + "11" * 20,
                        signer_private_key="0x" + "22" * 32, proxy_address=SAFE)
    assert bare.place_from_proposal(proposal_id="p", intent=_intent()) is None
    assert t.calls == []


def test_reduce_only_skips_spread_and_notional(monkeypatch):
    t = FakeTransport()
    ex = _executor(monkeypatch, t, book=_book(bid="40", ask="52"))
    out = ex.reduce_only_close(
        proposal_id="p", market_id=MARKET, parent_market_id=PARENT, contract_address=CONTRACT,
        side="short", price_cents=Decimal("40"), quantity=Decimal("0.5"), amount_usd=Decimal("0"),
        fee_target_bps=250,
    )
    assert out is not None and t.calls[0][2]["reduce_only"] is True


# ── error classification ─────────────────────────────────────────────────────


def test_client_rejection_is_failed_and_does_not_block(monkeypatch):
    err = PredTransportError(422, "bad", raw_body='{"status":"failed","order_id":"ord_9","message":"no liquidity"}')
    t = FakeTransport(post=err)
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent())
    assert order is not None and order.state == OrderState.FAILED
    assert order.pred_order_id == "ord_9"
    assert not ex.sm.is_market_blocked(PARENT)


@pytest.mark.parametrize("status", [409, 500, 502, 503])
def test_uncertain_http_status_is_unknown_and_blocks(monkeypatch, status):
    t = FakeTransport(post=PredTransportError(status, "boom", raw_body="{}"))
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent())
    assert order is not None and order.state == OrderState.UNKNOWN
    assert ex.sm.is_market_blocked(PARENT) and ex.sm.is_market_blocked(MARKET)
    assert len(t.calls) == 1
    # A blocked market refuses further orders (never a second salt for the same uncertainty).
    assert ex.place_from_proposal(proposal_id="p2", intent=_intent()) is None
    assert len(t.calls) == 1


def test_unknown_status_reply_fails_closed(monkeypatch):
    t = FakeTransport(post={"status": "weird", "order_id": "o"})
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent())
    assert order is not None and order.state == OrderState.UNKNOWN


# ── cancel uses PRED's order id ──────────────────────────────────────────────


def test_cancel_sends_pred_order_id_not_local_id(monkeypatch):
    t = FakeTransport()
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent())
    assert order is not None
    result = ex.cancel(order.id, MARKET, PARENT)
    assert result["status"] == "user_cancelled"
    assert t.calls[-1] == ("DELETE", f"/clob/api/v1/order/{PARENT}/cancel",
                           {"order_id": "ord_1", "market_id": MARKET})
    assert ex.sm.get(order.id).state == OrderState.CLOSED


def test_cancel_noop_http_400_is_not_cancelled(monkeypatch):
    """Docs: an active delay returns HTTP 400 + status=cancel_noop; the order is NOT cancelled."""
    t = FakeTransport(delete=PredTransportError(
        400, "cancel failed",
        raw_body='{"status":"cancel_noop","order_id":"ord_1","message":"order is delayed"}'))
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent())
    assert order is not None
    result = ex.cancel(order.id, MARKET, PARENT)
    assert result["status"] == "cancel_noop"
    assert ex.sm.get(order.id).state == OrderState.OPEN


def test_cancel_without_pred_id_does_not_call_exchange(monkeypatch):
    t = FakeTransport()
    ex = _executor(monkeypatch, t)
    ex.sm.create(OrderRecord(id="live-x", pred_order_id=None, proposal_id="p", state=OrderState.UNKNOWN,
                             side="long", price_cents=520000, quantity="1"))
    assert ex.cancel("live-x", MARKET, PARENT)["status"] == "error"
    assert t.calls == []


# ── startup reconcile ────────────────────────────────────────────────────────


def test_reconcile_refreshes_adopts_and_flags_unresolved(monkeypatch):
    remote = {"orders": [
        {"order_id": "ord_1", "market_id": MARKET, "parent_market_id": PARENT, "side": "long",
         "price": "52", "quantity": "4", "remaining_quantity": "1"},
        {"order_id": "ord_foreign", "market_id": MARKET, "side": "short", "price": "48",
         "quantity": "2", "remaining_quantity": "2"},
    ]}
    t = FakeTransport(get=remote)
    ex = _executor(monkeypatch, t)
    mine = ex.place_from_proposal(proposal_id="p", intent=_intent())          # ord_1, OPEN
    ex.sm.create(OrderRecord(id="live-gone", pred_order_id="ord_gone", proposal_id="p", state=OrderState.OPEN,
                             side="long", price_cents=520000, quantity="1", market_id="mg", parent_market_id="pg"))
    report = ex.reconcile_startup()
    assert t.calls[-1][1] == "/clob/api/v1/portfolio/open-orders"
    assert report == {"exchange_open_orders": 2, "refreshed": 1, "adopted": 1, "unresolved": ["live-gone"]}
    assert ex.sm.get(mine.id).state == OrderState.PARTIAL
    assert ex.sm.get("live-gone").state == OrderState.UNKNOWN and ex.sm.is_market_blocked("pg")
    assert ex.sm.get("ord_foreign").state == OrderState.OPEN


def test_reconcile_unrecognised_shape_fails_closed(monkeypatch):
    ex = _executor(monkeypatch, FakeTransport(get={"unexpected": 1}))
    with pytest.raises(ReconcileError):
        ex.reconcile_startup()


# ── LiveRuntime ──────────────────────────────────────────────────────────────


def test_runtime_blocks_direct_place_and_cancels_per_parent(monkeypatch):
    t = FakeTransport(delete={"total_found": 1, "total_cancelled": 1, "total_failed": 0, "results": []})
    ex = _executor(monkeypatch, t)
    ex.place_from_proposal(proposal_id="p", intent=_intent())
    rt = LiveRuntime(ex, startup_report={})
    with pytest.raises(RuntimeError):
        rt.place(market_id="m")
    assert rt.cancel_everything() == {"cancelled": 1, "failed": 0, "parents": 1}


# ── controller: LIVE approve path ────────────────────────────────────────────


def _controller(monkeypatch, executor: LiveExecutor, *, fee: int | None = 250, active: bool = True):
    from pred_agent.config import load_settings
    from pred_agent.control_api.controller import AgentController

    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("PRIVATE_KEY", "0x" + "22" * 32)
    ctl = AgentController(load_settings())
    ctl.market_lookup = lambda mid: MarketContext(
        market_id=mid, parent_market_id=PARENT, contract_address=CONTRACT, fee_target_bps=fee,
        active=active, inactive_reason=None if active else "market not active", age_s=1.0,
    )
    ctl.arm_live(LiveRuntime(executor, startup_report={}))
    return ctl


def _proposal(ctl, **over: Any):
    args: dict[str, Any] = {
        "mode": "LIVE", "advisory": False, "fixture_id": "fx1", "market_id": MARKET, "side": "long",
        "order_type": "limit", "price_cents": Decimal("52"), "quantity": Decimal("3.8"),
        "amount_usd": Decimal("1.98"), "est_fee_usd": Decimal("0.04"), "edge_pp": Decimal("6"),
        "p_fair": Decimal("0.58"), "max_suggested_usd": Decimal("2"), "binding_cap": None,
        "reasons": ["requested via chat"],
    }
    args.update(over)
    return ctl.proposals.create(**args)


def test_controller_unarmed_live_refuses(monkeypatch):
    ctl = _controller(monkeypatch, _executor(monkeypatch, FakeTransport()))
    ctl.disarm_live("no Safe proxy wallet yet")
    prop = _proposal(ctl)
    assert ctl.approve(prop.id, "app") is None
    assert "not ready" in (ctl.last_error or "")
    assert prop.status == ProposalStatus.PROPOSED


def test_controller_places_real_order_with_recomputed_quantity(monkeypatch):
    t = FakeTransport(post={"status": "matched", "order_id": "ord_7", "filled_quantity": "3.807"})
    ctl = _controller(monkeypatch, _executor(monkeypatch, t))
    prop = _proposal(ctl)
    out = ctl.approve(prop.id, "app")
    assert out is not None and out.status == ProposalStatus.APPROVED
    body = t.calls[0][2]
    assert body["quantity"] == "3.807"                       # floor(1.98 / 0.52, 3dp), not the stale 3.8
    assert body["amount"] == plain_decimal(order_amount_usd("long", Decimal("52"), Decimal("3.807")))
    assert Decimal(body["amount"]) <= Decimal("1.98")
    pos = ctl.positions.get_position(MARKET)
    assert pos is not None and pos.quantity == Decimal("3.807")


def test_controller_refusal_keeps_proposal_pending_and_sends_nothing(monkeypatch):
    t = FakeTransport()
    ctl = _controller(monkeypatch, _executor(monkeypatch, t, book=_book(bid="40", ask="52")))
    prop = _proposal(ctl)
    assert ctl.approve(prop.id, "app") is None
    assert "spread" in (ctl.last_error or "")
    assert prop.status == ProposalStatus.PROPOSED and t.calls == []


def test_controller_missing_fee_target_refuses(monkeypatch):
    t = FakeTransport()
    ctl = _controller(monkeypatch, _executor(monkeypatch, t), fee=None)
    prop = _proposal(ctl)
    assert ctl.approve(prop.id, "app") is None
    assert "fee_rate_bps" in (ctl.last_error or "") and t.calls == []


def test_controller_exchange_rejection_aborts_proposal(monkeypatch):
    t = FakeTransport(post=PredTransportError(422, "x", raw_body='{"status":"failed","message":"nope"}'))
    ctl = _controller(monkeypatch, _executor(monkeypatch, t))
    prop = _proposal(ctl)
    assert ctl.approve(prop.id, "app") is None
    assert prop.status == ProposalStatus.ABORTED and ctl.positions.list_positions() == []


def test_controller_unknown_outcome_stays_approved_and_warns(monkeypatch):
    t = FakeTransport(post=PredTransportError(503, "x", raw_body="{}"))
    ctl = _controller(monkeypatch, _executor(monkeypatch, t))
    prop = _proposal(ctl)
    out = ctl.approve(prop.id, "app")
    assert out is not None and out.status == ProposalStatus.APPROVED
    assert "UNKNOWN" in (ctl.last_error or "")
    assert ctl.positions.list_positions() == []              # never assume a fill
    again = _proposal(ctl)
    assert ctl.approve(again.id, "app") is None              # market blocked


def test_controller_resting_order_opens_no_position(monkeypatch):
    ctl = _controller(monkeypatch, _executor(monkeypatch, FakeTransport()))
    assert ctl.approve(_proposal(ctl).id, "app") is not None
    assert ctl.positions.list_positions() == []


def test_controller_strategy_trade_needs_edge(monkeypatch):
    t = FakeTransport()
    ctl = _controller(monkeypatch, _executor(monkeypatch, t))
    prop = _proposal(ctl, reasons=["model edge"], p_fair=Decimal("0.53"))    # not manual
    assert ctl.approve(prop.id, "app") is None
    assert "edge" in (ctl.last_error or "") and t.calls == []


def test_controller_amount_cap_and_position_limits(monkeypatch):
    t = FakeTransport()
    ctl = _controller(monkeypatch, _executor(monkeypatch, t))
    prop = _proposal(ctl)
    assert ctl.approve(prop.id, "app", amount=Decimal("500")) is None        # over max_trade_usd
    ctl.limits["max_open_positions"] = 0
    assert ctl.approve(prop.id, "app") is None
    assert "max_open_positions" in (ctl.last_error or "") and t.calls == []


def test_controller_exit_uses_position_quantity_and_closes_on_fill(monkeypatch):
    t = FakeTransport(post={"status": "matched", "order_id": "ord_x", "filled_quantity": "2"})
    ctl = _controller(monkeypatch, _executor(monkeypatch, t, book=_book(bid="55", ask="57")))
    ctl.positions.open_position(market_id=MARKET, parent_id="fx1", side="long", quantity=Decimal("2"),
                                avg_price_cents=Decimal("52"), cost_usd=Decimal("1.04"))
    prop, err = ctl.close_position_proposal(MARKET, via="chat")
    assert err is None and prop is not None
    prop.mode = "LIVE"
    prop.price_cents = Decimal("55")
    assert ctl.approve(prop.id, "app") is not None
    body = t.calls[0][2]
    assert body["reduce_only"] is True and body["side"] == "short" and body["quantity"] == "2"
    assert ctl.positions.get_position(MARKET).status != "open"


def test_panic_cancels_live_orders(monkeypatch):
    t = FakeTransport(delete={"total_found": 1, "total_cancelled": 1, "total_failed": 0, "results": []})
    ctl = _controller(monkeypatch, _executor(monkeypatch, t))
    assert ctl.approve(_proposal(ctl).id, "app") is not None
    result = ctl.panic()
    assert result["orders_cancelled"] == 1 and result["live_cancel_failed"] == 0 and ctl.paused


# ── fill sync / positions / reconcile false-positive ─────────────────────────


def _trade(order_id: str, tid: str, qty: str, price: str = "52") -> dict[str, Any]:
    return {"trade_history_id": tid, "order_id": order_id, "market_id": MARKET, "side": "long",
            "price": price, "quantity": qty, "fee_amount": "0.01", "transaction_hash": "0xabc"}


def test_sync_fills_applies_once_and_marks_filled(monkeypatch):
    t = FakeTransport()
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent(quantity=Decimal("4")))
    assert order.state == OrderState.OPEN
    t._get = {"success": True, "trade_history": [_trade("ord_1", "t1", "1.5"), _trade("ord_1", "t2", "2.5")]}
    assert ex.sync_fills() == {"new_fills": 2, "resolved_unknown": 0}
    assert order.state == OrderState.FILLED and order.filled_qty == "4"
    assert ex.sync_fills()["new_fills"] == 0                  # idempotent: no double counting
    assert len(ex.sm.list_fills()) == 2


def test_partial_fill_then_unknown_order_recovers_and_unblocks(monkeypatch):
    t = FakeTransport(post=PredTransportError(503, "x", raw_body='{"order_id":"ord_u"}'))
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent(quantity=Decimal("4")))
    assert order.state == OrderState.UNKNOWN and ex.sm.is_market_blocked(PARENT)
    t._get = {"success": True, "trade_history": [_trade("ord_u", "t9", "4")]}
    assert ex.sync_fills() == {"new_fills": 1, "resolved_unknown": 1}
    assert order.state == OrderState.FILLED
    assert not ex.sm.is_market_blocked(PARENT) and not ex.sm.is_market_blocked(MARKET)


def test_filled_resting_order_is_not_flagged_unknown(monkeypatch):
    """The false positive: a resting order that fully fills vanishes from open-orders."""
    t = FakeTransport()
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent(quantity=Decimal("4")))
    t._get = {"success": True, "trade_history": [_trade("ord_1", "t1", "4")], "orders": []}
    report = ex.sync()
    assert order.state == OrderState.FILLED and report["unresolved"] == []
    assert not ex.sm.is_market_blocked(PARENT)


def test_vanished_without_fills_stays_unknown_then_recovers_when_visible(monkeypatch):
    t = FakeTransport(get={"success": True, "trade_history": [], "orders": []})
    ex = _executor(monkeypatch, t)
    order = ex.place_from_proposal(proposal_id="p", intent=_intent())
    assert ex.sync()["unresolved"] == [order.id] and ex.sm.is_market_blocked(PARENT)
    t._get = {"success": True, "trade_history": [], "orders": [
        {"order_id": "ord_1", "market_id": MARKET, "price": "52", "quantity": "3.8", "remaining_quantity": "3.8"}]}
    assert ex.sync()["unresolved"] == [] and order.state == OrderState.OPEN
    assert not ex.sm.is_market_blocked(PARENT)               # delayed portfolio listing resolved


def test_sync_fills_bad_shape_fails_closed(monkeypatch):
    ex = _executor(monkeypatch, FakeTransport(get={"success": False}))
    with pytest.raises(ReconcileError):
        ex.sync_fills()


def test_fetch_positions_normalises_and_skips_ambiguous(monkeypatch):
    rows = [
        {"market_id": MARKET, "parent_market_id": PARENT, "side": "long", "quantity": "3.8",
         "average_price": "52", "amount": "1.976"},
        {"market_id": "m2", "side": "long", "quantity": "1", "average_price": "0.52", "amount": "0.52"},  # dollars?
        {"market_id": "m3", "side": "sideways", "quantity": "1", "average_price": "50", "amount": "0.5"},
        {"market_id": "m4", "side": "short", "quantity": "0", "average_price": "50", "amount": "0"},
    ]
    ex = _executor(monkeypatch, FakeTransport(get={"success": True, "positions": rows}))
    out = ex.fetch_positions()
    assert [p["market_id"] for p in out] == [MARKET]
    assert out[0]["avg_price_cents"] == Decimal("52") and out[0]["cost_usd"] == Decimal("1.976")


def test_controller_sync_mirrors_positions_and_blocks_on_failure(monkeypatch):
    t = FakeTransport(get={"success": True, "trade_history": [], "orders": [], "positions": [
        {"market_id": MARKET, "parent_market_id": PARENT, "side": "long", "quantity": "2",
         "average_price": "55", "amount": "1.1"}]})
    ctl = _controller(monkeypatch, _executor(monkeypatch, t))
    report = ctl.sync_live()
    assert report is not None and report["positions"] == 1
    pos = ctl.positions.get_position(MARKET)
    assert pos.quantity == Decimal("2") and pos.avg_price_cents == Decimal("55")
    t._get = {"success": False}
    assert ctl.sync_live() is None and ctl.live_sync_error
    prop = _proposal(ctl)
    assert ctl.approve(prop.id, "app") is None and "sync" in (ctl.last_error or "")
    assert prop.status == ProposalStatus.PROPOSED


# ── docs conformance: open-orders pagination / omitted empty list / settlement rows ──


class _PagedTransport(FakeTransport):
    def __init__(self, pages: list[dict]) -> None:
        super().__init__()
        self._pages = list(pages)
        self.params_seen: list[dict | None] = []

    def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append(("GET", path, None))
        self.params_seen.append(params)
        return dict(self._pages.pop(0))


def test_reconcile_reads_all_open_order_pages(monkeypatch):
    row = lambda i: {"order_id": f"ord_{i}", "market_id": MARKET, "parent_market_id": PARENT,  # noqa: E731
                     "side": "long", "price": "50", "quantity": "1", "remaining_quantity": "1"}
    t = _PagedTransport([
        {"success": True, "orders": [row(1)], "pagination": {"has_more": True, "limit": 1, "offset": 0}},
        {"success": True, "orders": [row(2)], "pagination": {"has_more": False, "limit": 1, "offset": 1}},
    ])
    ex = _executor(monkeypatch, t)
    report = ex.reconcile_startup()
    assert report["exchange_open_orders"] == 2
    assert [p["offset"] for p in t.params_seen] == [0, 1]


def test_reconcile_accepts_omitted_empty_orders(monkeypatch):
    t = _PagedTransport([{"success": True, "pagination": {"has_more": False, "limit": 50, "offset": 0}}])
    ex = _executor(monkeypatch, t)
    assert ex.reconcile_startup()["exchange_open_orders"] == 0


def test_reconcile_fails_closed_on_success_false(monkeypatch):
    import pytest

    from pred_agent.execution.live_executor import ReconcileError

    t = _PagedTransport([{"success": False, "error": "boom", "pagination": {"has_more": False}}])
    with pytest.raises(ReconcileError):
        _executor(monkeypatch, t).reconcile_startup()
