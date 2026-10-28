"""E1 X-Confirm gate tests.

LIVE mode: risky endpoints (panic, approve, confirm_rule, cancel_order,
close_position) must return 403 xconfirm_required without the X-Confirm
header. PAPER mode never requires it.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from pred_agent.control_api.app import create_app

LIVE_ENV = {"LIVE_TRADING": "true", "PRIVATE_KEY": "0x" + "11" * 32}


def _authed(client: TestClient) -> dict[str, str]:
    auth = client.app.state.auth
    code = auth.generate_pairing_code("xc-device")
    resp = client.post("/v1/pair", json={"code": code, "device_name": "xc-device"})
    token = resp.json()["data"]["device_token"]
    return {"Authorization": f"Bearer {token}"}


def _live_app(monkeypatch):
    for k, v in LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    app = create_app()
    assert app.state.controller.mode == "LIVE"
    return app


# ── LIVE mode: gate enforced ─────────────────────────────────

def test_live_panic_requires_xconfirm(monkeypatch):
    app = _live_app(monkeypatch)
    client = TestClient(app)
    headers = _authed(client)

    resp = client.post("/v1/panic", headers=headers, json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "xconfirm_required"
    assert app.state.controller.paused is False  # gate blocked the action

    resp = client.post("/v1/panic", headers={**headers, "X-Confirm": "yes"}, json={})
    assert resp.status_code == 200
    assert app.state.controller.paused is True


def _arm_fake_live(controller):
    from pred_agent.control_api.controller import AgentController  # noqa: F401
    from pred_agent.execution.live_runtime import MarketContext
    from pred_agent.execution.state_machine import OrderRecord, OrderState

    class _Exec:
        last_abort_reason = None

        def precheck(self, intent):
            return None

        def place_from_proposal(self, *, proposal_id, intent):
            return OrderRecord(
                id="live-1", pred_order_id="o1", proposal_id=proposal_id, state=OrderState.OPEN,
                side=intent.side, price_cents=520000, quantity=str(intent.quantity),
            )

    class _Runtime:
        executor = _Exec()

        def list_orders(self):
            return []

        def cancel(self, order_id):
            return False

        def cancel_everything(self):
            return {"cancelled": 0, "failed": 0, "parents": 0}

    controller.market_lookup = lambda mid: MarketContext(
        market_id=mid, parent_market_id="0x" + "cd" * 32, contract_address="0x" + "ee" * 20,
        fee_target_bps=250, active=True, inactive_reason=None, age_s=1.0,
    )
    controller.arm_live(_Runtime())


def test_live_approve_requires_xconfirm(monkeypatch):
    app = _live_app(monkeypatch)
    # This test covers the X-Confirm gate only; arm the controller with a fake live runtime.
    _arm_fake_live(app.state.controller)
    client = TestClient(app)
    headers = _authed(client)
    prop = app.state.controller.proposals.create(
        mode="LIVE", advisory=False, fixture_id="fx1", market_id="mx1",
        side="long", order_type="limit", price_cents=Decimal("52"),
        quantity=Decimal("3.8"), amount_usd=Decimal("1.98"), est_fee_usd=Decimal("0.04"),
        edge_pp=Decimal("5"), p_fair=Decimal("0.58"),
        max_suggested_usd=Decimal("2"), binding_cap=None,
    )

    resp = client.post(f"/v1/proposals/{prop.id}/approve", headers=headers, json={})
    assert resp.status_code == 403
    assert prop.status.value == "PROPOSED"  # still pending

    resp = client.post(
        f"/v1/proposals/{prop.id}/approve",
        headers={**headers, "X-Confirm": "yes"}, json={},
    )
    assert resp.status_code == 200


def test_live_confirm_rule_requires_xconfirm(monkeypatch):
    app = _live_app(monkeypatch)
    client = TestClient(app)
    headers = _authed(client)
    app.state.controller.positions.open_position(
        market_id="mx1", parent_id="fx1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"),
        cost_usd=Decimal("5.20"),
    )
    rule = app.state.controller.rules.create(
        market_id="mx1", parent_market_id="fx1", position_ref="mx1",
        kind="take_profit", trigger_type="price_cents", trigger_value=Decimal("70"),
        size="all", created_via="test", mode="LIVE",
    )

    resp = client.post(f"/v1/rules/{rule.id}/confirm", headers=headers, json={})
    assert resp.status_code == 403
    assert rule.status == "PENDING"

    resp = client.post(
        f"/v1/rules/{rule.id}/confirm",
        headers={**headers, "X-Confirm": "yes"}, json={},
    )
    assert resp.status_code == 200
    assert rule.status == "ACTIVE"


def test_live_cancel_order_requires_xconfirm(monkeypatch):
    app = _live_app(monkeypatch)
    client = TestClient(app)
    headers = _authed(client)

    resp = client.post("/v1/orders/any/cancel", headers=headers, json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "xconfirm_required"

    # With the header the gate passes; unknown order → 404, not 403
    resp = client.post(
        "/v1/orders/any/cancel", headers={**headers, "X-Confirm": "yes"}, json={},
    )
    assert resp.status_code == 404


def test_live_close_position_requires_xconfirm(monkeypatch):
    app = _live_app(monkeypatch)
    client = TestClient(app)
    headers = _authed(client)

    resp = client.post("/v1/positions/mx1/close", headers=headers, json={})
    assert resp.status_code == 403

    resp = client.post(
        "/v1/positions/mx1/close", headers={**headers, "X-Confirm": "yes"}, json={},
    )
    assert resp.status_code == 404  # gate passed; no position


# ── PAPER mode: gate never required ──────────────────────────

def test_paper_mode_panic_without_xconfirm_allowed():
    app = create_app()
    assert app.state.controller.mode == "PAPER"
    client = TestClient(app)
    headers = _authed(client)

    resp = client.post("/v1/panic", headers=headers, json={})
    assert resp.status_code == 200
    assert app.state.controller.paused is True


def test_live_approve_fails_closed_while_live_execution_unwired(monkeypatch):
    """LIVE + no live executor wired: approve is refused (409), proposal stays pending."""
    app = _live_app(monkeypatch)
    assert app.state.controller.live_execution_ready is False
    client = TestClient(app)
    headers = _authed(client)
    prop = app.state.controller.proposals.create(
        mode="LIVE", advisory=False, fixture_id="fx1", market_id="mx1",
        side="long", order_type="limit", price_cents=Decimal("52"),
        quantity=Decimal("3.8"), amount_usd=Decimal("1.98"), est_fee_usd=Decimal("0.04"),
        edge_pp=Decimal("5"), p_fair=Decimal("0.58"),
        max_suggested_usd=Decimal("2"), binding_cap=None,
    )
    resp = client.post(
        f"/v1/proposals/{prop.id}/approve",
        headers={**headers, "X-Confirm": "yes"}, json={},
    )
    assert resp.status_code == 409
    assert prop.status.value == "PROPOSED"
    assert app.state.controller.positions.list_positions() == []
