"""Phase 4 unit tests: live executor, state machine, cancels — no network (mock transport)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pred_agent.execution import (
    LiveExecutor,
    LiveOrderIntent,
    OrderRecord,
    OrderState,
    OrderStateMachine,
)
from pred_agent.execution.state_machine import LEGAL_TRANSITIONS


class MockTransport:
    """Mock PRED transport for Phase 4 tests — no network."""

    def __init__(self, *, place_response: dict | None = None,
                 cancel_response: dict | None = None,
                 place_error: Exception | None = None) -> None:
        self.place_response = place_response or {"status": "order_created", "order_id": "ord_1"}
        self.cancel_response = cancel_response or {"status": "user_cancelled"}
        self.place_error = place_error
        self.calls: list[str] = []

    def post(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("POST", path, redact_json(json) if json else None))
        if self.place_error:
            raise self.place_error
        return dict(self.place_response)

    def delete(self, path: str, json: dict | None = None) -> dict:
        self.calls.append(("DELETE", path, redact_json(json) if json else None))
        return dict(self.cancel_response)

    def get(self, path: str, params: dict | None = None) -> dict:
        return {}

    def raw_get(self, path: str, params: dict | None = None):
        return 200, "{}", {}

    def health_check(self) -> bool:
        return True

    @property
    def caps(self):
        from pred_agent.transport.protocol import TransportCaps
        return TransportCaps()


def redact_json(d: dict) -> dict:
    """Redact signature for test output."""
    out = dict(d)
    if "signature" in out:
        out["signature"] = "[REDACTED]"
    return out


# ---------------------------------------------------------------------------
# State machine legal transitions
# ---------------------------------------------------------------------------

def test_state_machine_legal_transition():
    sm = OrderStateMachine()
    rec = OrderRecord(
        id="o1", pred_order_id=None, proposal_id="p1", state=OrderState.PROPOSED,
        side="long", price_cents=520000, quantity="3.8",
    )
    sm.create(rec)
    updated = sm.transition("o1", OrderState.APPROVED)
    assert updated is not None
    assert updated.state == OrderState.APPROVED


def test_state_machine_illegal_transition_blocked():
    sm = OrderStateMachine()
    rec = OrderRecord(
        id="o1", pred_order_id=None, proposal_id="p1", state=OrderState.PROPOSED,
        side="long", price_cents=520000, quantity="3.8",
    )
    sm.create(rec)
    # PROPOSED -> FILLED is illegal
    updated = sm.transition("o1", OrderState.FILLED)
    assert updated is None


def test_unknown_market_blocked():
    sm = OrderStateMachine()
    assert sm.is_market_blocked("mx1") is False
    sm.block_market("mx1")
    assert sm.is_market_blocked("mx1") is True
    sm.unblock_market("mx1")
    assert sm.is_market_blocked("mx1") is False


# ---------------------------------------------------------------------------
# Fill dedup: order_updated and order_matched_on_chain never double count
# ---------------------------------------------------------------------------

def test_fill_dedup_by_match_id():
    sm = OrderStateMachine()
    f1 = sm.apply_fill(
        fill_id=None, match_id="m1", order_id="o1",
        price_cents=520000, quantity="1.0", matched_on_chain=False,
    )
    f2 = sm.apply_fill(
        fill_id=None, match_id="m1", order_id="o1",
        price_cents=520000, quantity="1.0", matched_on_chain=True,
    )
    # Same match_id -> same fill object returned, not duplicated
    assert f1 is f2
    assert len(sm.list_fills()) == 1


def test_fill_dedup_by_fill_id():
    sm = OrderStateMachine()
    f1 = sm.apply_fill(
        fill_id="f1", match_id=None, order_id="o1",
        price_cents=520000, quantity="1.0", matched_on_chain=False,
    )
    f2 = sm.apply_fill(
        fill_id="f1", match_id=None, order_id="o1",
        price_cents=520000, quantity="1.0", matched_on_chain=True,
    )
    assert f1 is f2
    assert len(sm.list_fills()) == 1


# ---------------------------------------------------------------------------
# LiveExecutor: gated by LIVE_TRADING=true
# ---------------------------------------------------------------------------

def test_live_executor_requires_live_trading(monkeypatch):
    import pred_agent.execution.live_executor as le_mod

    # Force load_settings to return live_trading=False
    class FakeSettings:
        live_trading = False

    monkeypatch.setattr(le_mod, "load_settings", lambda: FakeSettings())

    transport = MockTransport()
    sm = OrderStateMachine()
    with pytest.raises(RuntimeError, match="LIVE_TRADING=true"):
        LiveExecutor(
            transport=transport, state_machine=sm,
            eoa_address="0x" + "11" * 20,
            signer_private_key="0x" + "22" * 32,
        )


# ---------------------------------------------------------------------------
# LiveExecutor place -> success path (mocked transport)
# ---------------------------------------------------------------------------

def test_live_executor_place_success(monkeypatch):
    import pred_agent.execution.live_executor as le_mod

    class FakeSettings:
        live_trading = True

    monkeypatch.setattr(le_mod, "load_settings", lambda: FakeSettings())

    transport = MockTransport()
    sm = OrderStateMachine()
    exec_ = LiveExecutor(
        transport=transport, state_machine=sm,
        eoa_address="0x" + "11" * 20,
        signer_private_key="0x" + "22" * 32,
        proxy_address="0x" + "33" * 20,
    )

    intent = LiveOrderIntent(
        market_id="0x" + "ab" * 32,
        parent_market_id="0x" + "cd" * 32,
        contract_address="0x" + "ee" * 20,
        side="long",
        price_cents=Decimal("52"),
        quantity=Decimal("3.8"),
        amount_usd=Decimal("1.98"),
        order_type="ioc",
        fee_rate_bps=125,
    )
    order = exec_.place_from_proposal(proposal_id="p1", intent=intent)

    assert order is not None
    assert order.state in (OrderState.OPEN, OrderState.FILLED, OrderState.DELAYED)
    assert order.salt is not None
    assert len(transport.calls) == 1
    # Signature redacted in audit
    assert transport.calls[0][2]["signature"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# LiveExecutor place -> transport error => UNKNOWN, no resend
# ---------------------------------------------------------------------------

def test_live_executor_place_error_unknown(monkeypatch):
    import pred_agent.execution.live_executor as le_mod

    class FakeSettings:
        live_trading = True

    monkeypatch.setattr(le_mod, "load_settings", lambda: FakeSettings())

    transport = MockTransport(place_error=ConnectionError("network down"))
    sm = OrderStateMachine()
    exec_ = LiveExecutor(
        transport=transport, state_machine=sm,
        eoa_address="0x" + "11" * 20,
        signer_private_key="0x" + "22" * 32,
        proxy_address="0x" + "33" * 20,
    )

    intent = LiveOrderIntent(
        market_id="0x" + "ab" * 32,
        parent_market_id="0x" + "cd" * 32,
        contract_address="0x" + "ee" * 20,
        side="long",
        price_cents=Decimal("52"),
        quantity=Decimal("3.8"),
        amount_usd=Decimal("1.98"),
        order_type="ioc",
        fee_rate_bps=125,
    )
    order = exec_.place_from_proposal(proposal_id="p1", intent=intent)

    assert order is not None
    assert order.state == OrderState.UNKNOWN
    assert sm.is_market_blocked(intent.parent_market_id)
    # Only ONE call — no resend with new salt
    assert len(transport.calls) == 1


# ---------------------------------------------------------------------------
# Cancel: cancel_noop means NOT cancelled
# ---------------------------------------------------------------------------

def test_cancel_cancel_noop_not_cancelled(monkeypatch):
    import pred_agent.execution.live_executor as le_mod

    class FakeSettings:
        live_trading = True

    monkeypatch.setattr(le_mod, "load_settings", lambda: FakeSettings())

    transport = MockTransport(cancel_response={"status": "cancel_noop"})
    sm = OrderStateMachine()
    exec_ = LiveExecutor(
        transport=transport, state_machine=sm,
        eoa_address="0x" + "11" * 20,
        signer_private_key="0x" + "22" * 32,
    )

    # Pre-create an open order
    order = OrderRecord(
        id="o1", pred_order_id="ord_1", proposal_id="p1", state=OrderState.OPEN,
        side="long", price_cents=520000, quantity="3.8",
    )
    sm.create(order)

    result = exec_.cancel(order_id="o1", market_id="mx1", parent_market_id="px1")
    assert result["status"] == "cancel_noop"
    # Order should still be OPEN (not cancelled)
    updated = sm.get("o1")
    assert updated.state == OrderState.OPEN


# ---------------------------------------------------------------------------
# Legal transitions cover all kit states
# ---------------------------------------------------------------------------

def test_kit_states_covered():
    expected = {
        OrderState.PROPOSED, OrderState.APPROVED, OrderState.REVALIDATED,
        OrderState.ABORTED, OrderState.SUBMITTED, OrderState.DELAYED,
        OrderState.OPEN, OrderState.PARTIAL, OrderState.FILLED,
        OrderState.CLOSING, OrderState.CLOSED, OrderState.SETTLED,
        OrderState.FAILED, OrderState.EXPIRED, OrderState.UNKNOWN,
        OrderState.REJECTED,
    }
    assert set(LEGAL_TRANSITIONS.keys()) == expected


def test_live_executor_fails_closed_without_proxy(monkeypatch):
    import pred_agent.execution.live_executor as le_mod

    class FakeSettings:
        live_trading = True

    monkeypatch.setattr(le_mod, "load_settings", lambda: FakeSettings())
    transport = MockTransport()
    exec_ = LiveExecutor(
        transport=transport, state_machine=OrderStateMachine(),
        eoa_address="0x" + "11" * 20, signer_private_key="0x" + "22" * 32,
    )
    intent = LiveOrderIntent(
        market_id="0x" + "ab" * 32, parent_market_id="0x" + "cd" * 32,
        contract_address="0x" + "ee" * 20, side="long", price_cents=Decimal("68"),
        quantity=Decimal("2"), amount_usd=Decimal("1.36"), order_type="limit",
        fee_rate_bps=0, fee_target_bps=250,
    )
    assert exec_.place_from_proposal(proposal_id="p", intent=intent) is None
    assert intent.fee_rate_bps == 170  # signed rate recomputed from target
    assert transport.calls == []
