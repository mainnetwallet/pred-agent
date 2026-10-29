"""Slash commands + click endpoints: everything the UI can click can be typed.

Covers the deterministic /command handler (no LLM), the new REST endpoints the
buttons call (POST /v1/proposals, POST /v1/rules), and the LIVE-mode confirm
gate for money-moving commands.
"""
from __future__ import annotations

import json
from decimal import Decimal

from fastapi.testclient import TestClient

from pred_agent.control_api.app import create_app
from tests.test_e1_chat_tools import _FakeBookFeed, _fixture_parent, _snapshot

LIVE_ENV = {"LIVE_TRADING": "true", "PRIVATE_KEY": "0x" + "11" * 32}


def _setup(app=None):
    app = app or create_app()
    app.state.fixture_cache.update([_fixture_parent("mx1")])
    app.state.book_feed = _FakeBookFeed({"mx1": _snapshot("mx1", bid="50", ask="54")})
    client = TestClient(app)
    code = app.state.auth.generate_pairing_code("slash-device")
    tok = client.post("/v1/pair", json={"code": code, "device_name": "slash-device"}).json()["data"]["device_token"]
    return app, client, {"Authorization": f"Bearer {tok}"}


def _say(client, headers, text, **extra):
    r = client.post("/v1/chat", headers={**headers, **extra}, json={"message": text})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _open_position(app):
    app.state.controller.positions.open_position(
        market_id="mx1", parent_id="fx1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"), cost_usd=Decimal("5.20"))


# ── read commands ───────────────────────────────────────────


def test_help_lists_commands_without_llm():
    _, client, h = _setup()
    d = _say(client, h, "/help")
    for cmd in ("/buy", "/sell", "/approve", "/tp", "/sl", "/pause", "/resume", "/panic", "/limit"):
        assert cmd in d["reply"]


def test_unknown_command_is_friendly():
    _, client, h = _setup()
    assert "unknown command" in _say(client, h, "/frobnicate")["reply"]


def test_status_and_lists():
    app, client, h = _setup()
    assert "Mode" in _say(client, h, "/status")["reply"]
    assert "No open positions" in _say(client, h, "/positions")["reply"]
    assert "No pending proposals" in _say(client, h, "/proposals")["reply"]
    assert "No exit rules" in _say(client, h, "/rules")["reply"]
    assert "max_trade_usd" in _say(client, h, "/limits")["reply"]
    d = _say(client, h, "/upcoming")
    assert "Alpha" in d["reply"] and d["cards"][0]["type"] == "fixture_list"


def test_match_shows_market_ids_and_quotes():
    _, client, h = _setup()
    d = _say(client, h, "/match fx1")
    assert "mx1" in d["reply"] and "54" in d["reply"]


# ── trade + approve / reject ────────────────────────────────


def test_buy_creates_pending_proposal_then_approve_and_reject():
    app, client, h = _setup()
    d = _say(client, h, "/buy mx1 2")
    assert d["cards"][0]["type"] == "proposal" and d["cards"][0]["data"]["side"] == "long"
    pid = d["cards"][0]["data"]["id"]
    assert len(app.state.controller.proposals.list_pending()) == 1

    # unique prefix is enough
    assert "approved" in _say(client, h, f"/approve {pid[:6]}")["reply"].lower()
    assert len(app.state.controller.proposals.list_pending()) == 0

    d2 = _say(client, h, "/sell mx1 1")
    assert d2["cards"][0]["data"]["side"] == "short"
    assert "rejected" in _say(client, h, f"/reject {d2['cards'][0]['data']['id'][:6]}")["reply"]


def test_buy_validates_amount_and_cap():
    _, client, h = _setup()
    assert "❌" in _say(client, h, "/buy mx1 abc")["reply"]
    assert "❌" in _say(client, h, "/buy mx1 -5")["reply"]
    assert "❌" in _say(client, h, "/buy mx1 999999")["reply"]  # above max_trade_usd
    assert "❌" in _say(client, h, "/buy nope 5")["reply"]
    assert "usage" in _say(client, h, "/buy mx1")["reply"]


def test_buy_fails_closed_without_price():
    app, client, h = _setup()
    app.state.book_feed = _FakeBookFeed({})
    assert "no live price" in _say(client, h, "/buy mx1 2")["reply"]


def test_approve_unknown_id_errors():
    _, client, h = _setup()
    assert "❌" in _say(client, h, "/approve zzzz")["reply"]


# ── close / TP / SL / confirm / cancel ──────────────────────


def test_close_creates_pending_exit_proposal():
    app, client, h = _setup()
    assert "❌" in _say(client, h, "/close mx1")["reply"]  # no position yet
    _open_position(app)
    d = _say(client, h, "/close mx1")
    assert d["cards"][0]["type"] == "proposal" and d["cards"][0]["data"]["side"] == "short"


def test_tp_sl_parse_units_and_confirm_cancel_flow():
    app, client, h = _setup()
    _open_position(app)

    d = _say(client, h, "/tp mx1 70c")
    r = d["cards"][0]["data"]
    assert (r["kind"], r["trigger_type"], r["status"]) == ("take_profit", "price_cents", "PENDING")
    assert _say(client, h, "/tp mx1 5")["cards"][0]["data"]["trigger_type"] == "profit_usd"
    assert _say(client, h, "/tp mx1 20%")["cards"][0]["data"]["trigger_type"] == "profit_pct"
    sl = _say(client, h, "/sl mx1 3")["cards"][0]["data"]
    assert (sl["kind"], sl["trigger_type"]) == ("stop_loss", "loss_usd")
    assert "❌" in _say(client, h, "/sl mx1 20%")["reply"]   # no pct stop-loss
    assert "❌" in _say(client, h, "/tp mx1 40c")["reply"]   # TP below entry for a long

    assert "ACTIVE" in _say(client, h, f"/confirm {r['id'][:6]}")["reply"]
    assert "cancelled" in _say(client, h, f"/cancel {sl['id'][:6]}")["reply"]


def test_cancel_order():
    app, client, h = _setup()
    d = _say(client, h, "/buy mx1 2")
    assert "approved" in _say(client, h, f"/approve {d['cards'][0]['data']['id'][:6]}")["reply"].lower()
    assert "❌" in _say(client, h, "/cancel nosuchid")["reply"]


# ── control ─────────────────────────────────────────────────


def test_pause_resume_and_limit():
    app, client, h = _setup()
    _say(client, h, "/pause")
    assert app.state.controller.paused is True
    assert "paused" in _say(client, h, "/buy mx1 1")["reply"]   # blocked while paused
    _say(client, h, "/resume")
    assert app.state.controller.paused is False

    assert "set to 1.5" in _say(client, h, "/limit max_trade_usd 1.5")["reply"]
    assert app.state.controller.limits["max_trade_usd"] == 1.5
    assert "❌" in _say(client, h, "/limit max_trade_usd 99999999")["reply"]
    assert "❌" in _say(client, h, "/limit bogus 1")["reply"]


def test_panic_always_returns_confirm_card_and_does_nothing():
    app, client, h = _setup()
    d = _say(client, h, "/panic")
    assert d["cards"][0]["type"] == "action" and d["cards"][0]["data"]["action"] == "panic"
    assert app.state.controller.paused is False


# ── LIVE mode gate ──────────────────────────────────────────


def test_live_approve_and_confirm_return_cards_without_xconfirm(monkeypatch):
    for k, v in LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    app, client, h = _setup(create_app())
    assert app.state.controller.mode == "LIVE"
    pid = app.state.controller.proposals.create(
        mode="LIVE", advisory=False, fixture_id="fx1", market_id="mx1", side="long",
        order_type="limit", price_cents=Decimal("54"), quantity=Decimal("1"),
        amount_usd=Decimal("1"), est_fee_usd=Decimal("0"), edge_pp=Decimal("0"),
        p_fair=Decimal("0.54"), max_suggested_usd=Decimal("5"), binding_cap=None,
        reasons=[], risks=None).id
    d = _say(client, h, f"/approve {pid[:6]}")
    assert "LIVE" in d["reply"] and d["cards"][0]["type"] == "proposal"
    assert d["cards"][0]["data"]["status"] == "PROPOSED"  # nothing executed

    _open_position(app)
    rid = _say(client, h, "/tp mx1 70c")["cards"][0]["data"]["id"]
    d = _say(client, h, f"/confirm {rid[:6]}")
    assert d["cards"][0]["type"] == "rule" and d["cards"][0]["data"]["status"] == "PENDING"


# ── streaming path ──────────────────────────────────────────


def test_slash_command_works_over_sse_stream():
    _, client, h = _setup()
    r = client.post("/v1/chat/stream", headers=h, json={"message": "/status"})
    assert r.status_code == 200
    body = r.text
    assert "event: delta" in body and "event: done" in body
    done = [json.loads(b.split("data:", 1)[1]) for b in body.split("\n\n") if b.startswith("event: done")][0]
    assert "Mode" in done["reply"] and done["error"] is None


# ── click endpoints ─────────────────────────────────────────


def test_rest_create_proposal_by_click_is_pending_and_validated():
    app, client, h = _setup()
    r = client.post("/v1/proposals", headers=h, json={"market_id": "mx1", "side": "long", "amount_usd": "2"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "PROPOSED"
    assert client.post("/v1/proposals", headers=h, json={"market_id": "mx1", "side": "long", "amount_usd": "x"}).status_code == 409
    assert client.post("/v1/proposals", headers=h, json={"amount_usd": "2"}).status_code == 400
    assert client.post("/v1/proposals", json={"market_id": "mx1"}).status_code == 401


def test_rest_create_rule_by_click():
    app, client, h = _setup()
    _open_position(app)
    r = client.post("/v1/rules", headers=h, json={"market_id": "mx1", "kind": "stop_loss", "trigger_type": "loss_usd", "trigger_value": "2"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "PENDING"
    assert client.post("/v1/rules", headers=h, json={"market_id": "mx1", "kind": "x", "trigger_type": "loss_usd", "trigger_value": "2"}).status_code == 400
    assert client.post("/v1/rules", json={}).status_code == 401


def test_status_pending_alias_lists_proposed_proposals():
    """Website + app call /v1/proposals?status=pending — it must return PROPOSED ones."""
    app, client, h = _setup()
    _say(client, h, "/buy mx1 1")
    for q in ("pending", "PENDING", "PROPOSED"):
        data = client.get(f"/v1/proposals?status={q}", headers=h).json()["data"]
        assert len(data) == 1 and data[0]["status"] == "PROPOSED", q
    assert client.get("/v1/proposals?status=APPROVED", headers=h).json()["data"] == []
