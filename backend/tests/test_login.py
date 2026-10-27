"""Login (env password), website, controller endpoints, Telegram gating."""
# ruff: noqa: S105, S106

from __future__ import annotations

import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pred_agent.config import load_settings
from pred_agent.control_api.app import create_app
from pred_agent.control_api.controller import AgentController
from pred_agent.control_api.session import LoginManager
from pred_agent.telegram.bot import build_application


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret-pass")
    monkeypatch.setenv("SESSION_SECRET", "unit-test-secret")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    return TestClient(create_app())


def _login(client, user="boss", pw="s3cret-pass"):
    return client.post("/v1/login", json={"username": user, "password": pw})


def _hdr(client):
    token = _login(client).json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def test_login_ok_and_wrong(client):
    assert _login(client).status_code == 200
    bad = _login(client, pw="nope")
    assert bad.status_code == 401
    assert bad.json()["ok"] is False
    assert _login(client, user="other").status_code == 401


def test_login_disabled_without_password(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    c = TestClient(create_app())
    assert c.post("/v1/login", json={"username": "admin", "password": ""}).status_code == 503


def test_lockout_after_failures(client):
    for _ in range(5):
        assert _login(client, pw="bad").status_code == 401
    # correct password is now refused while locked
    assert _login(client).status_code == 429


def test_protected_endpoints_need_token(client):
    for path in ["/v1/status", "/v1/proposals", "/v1/orders", "/v1/positions", "/v1/limits"]:
        assert client.get(path).status_code == 401
    for path in ["/v1/pause", "/v1/resume", "/v1/panic"]:
        assert client.post(path).status_code == 401
    assert client.get("/v1/status", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_status_pause_resume(client):
    h = _hdr(client)
    s = client.get("/v1/status", headers=h).json()["data"]
    assert s["mode"] == "PAPER" and s["paused"] is False
    assert client.post("/v1/pause", headers=h).json()["data"]["paused"] is True
    assert client.post("/v1/resume", headers=h).json()["data"]["paused"] is False


def _make_proposal(client):
    ctrl = client.app.state.controller
    return ctrl.proposals.create(
        mode="PAPER", advisory=False, fixture_id="f1", market_id="m1", side="long",
        order_type="limit", price_cents=Decimal("50"), quantity=Decimal("2"),
        amount_usd=Decimal("1.00"), est_fee_usd=Decimal("0.02"), edge_pp=Decimal("4"),
        p_fair=Decimal("0.55"), max_suggested_usd=Decimal("2"), binding_cap=None,
    )


def test_approve_reject_flow(client):
    h = _hdr(client)
    prop = _make_proposal(client)
    r = client.post(f"/v1/proposals/{prop.id}/approve", headers=h, json={})
    assert r.status_code == 200 and r.json()["data"]["status"] == "APPROVED"
    assert len(client.get("/v1/orders", headers=h).json()["data"]) == 1  # paper order placed

    p2 = _make_proposal(client)
    assert client.post(f"/v1/proposals/{p2.id}/reject", headers=h).status_code == 200
    assert client.post(f"/v1/proposals/{p2.id}/reject", headers=h).status_code == 404


def test_approve_blocked_when_paused_or_over_limit(client):
    h = _hdr(client)
    prop = _make_proposal(client)
    over = client.post(f"/v1/proposals/{prop.id}/approve", headers=h, json={"amount_usd": "99"})
    assert over.status_code == 409
    client.post("/v1/pause", headers=h)
    paused = client.post(f"/v1/proposals/{prop.id}/approve", headers=h, json={})
    assert paused.status_code == 409


def test_panic_rejects_pending_and_pauses(client):
    h = _hdr(client)
    prop = _make_proposal(client)
    r = client.post("/v1/panic", headers=h).json()["data"]
    assert r["paused"] is True and r["proposals_rejected"] == 1
    assert client.app.state.controller.proposals.get(prop.id).status.value == "REJECTED"


def test_limits_cannot_exceed_hard_max(client):
    h = _hdr(client)
    assert client.put("/v1/limits", headers=h, json={"max_trade_usd": 1.5}).status_code == 200
    assert client.put("/v1/limits", headers=h, json={"max_trade_usd": 50}).status_code == 400
    assert client.put("/v1/limits", headers=h, json={"live_trading": 1}).status_code == 400


def test_website_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "PRED Agent" in r.text
    assert "s3cret-pass" not in r.text


def test_session_token_tamper_and_expiry():
    m = LoginManager(username="a", password="pw", secret="k", ttl_hours=1)
    tok = m.login("a", "pw")
    assert tok and m.verify(tok) == "a"
    assert m.verify(tok[:-2] + "00") is None
    assert m.verify("garbage") is None
    other = LoginManager(username="a", password="pw", secret="different")
    assert other.verify(tok) is None
    expired = LoginManager(username="a", password="pw", secret="k", ttl_hours=1)
    expired._ttl = -10  # noqa: SLF001
    assert m.verify(expired.login("a", "pw") or "") is None
    assert isinstance(time.time(), float)


def test_websocket_accepts_session_token(client):
    import json

    token = _login(client).json()["data"]["token"]
    with client.websocket_connect("/v1/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        assert "auth_ok" in ws.receive_text()


def test_telegram_disabled_without_env_and_fails_closed(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    s = load_settings()
    assert build_application(s, AgentController(s)) is None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "not-a-number")
    s = load_settings()
    assert build_application(s, AgentController(s)) is None


def test_telegram_builds_with_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCdefGhIJKlmNoPQRsTUVwxyZ")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "42")
    s = load_settings()
    assert build_application(s, AgentController(s)) is not None


def test_logout_revokes_token(client):
    h = _hdr(client)
    assert client.get("/v1/status", headers=h).status_code == 200
    assert client.post("/v1/logout", headers=h).status_code == 200
    # the same token no longer works anywhere
    assert client.get("/v1/status", headers=h).status_code == 401
    assert client.get("/v1/me", headers=h).status_code == 401
    # logging out again / without a token is rejected
    assert client.post("/v1/logout", headers=h).status_code == 401
    assert client.post("/v1/logout").status_code == 401
    # a fresh login still works, and other sessions are unaffected
    assert _login(client).status_code == 200


def test_logout_only_kills_that_session(client):
    h1, h2 = _hdr(client), _hdr(client)
    client.post("/v1/logout", headers=h1)
    assert client.get("/v1/status", headers=h1).status_code == 401
    assert client.get("/v1/status", headers=h2).status_code == 200


def test_login_ignores_surrounding_whitespace(client):
    assert _login(client, user=" boss ", pw="s3cret-pass ").status_code == 200


def test_env_credentials_with_trailing_whitespace(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "boss\n")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret-pass \n")
    monkeypatch.setenv("SESSION_SECRET", "unit-test-secret")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    c = TestClient(create_app())
    assert c.post("/v1/login", json={"username": "boss", "password": "s3cret-pass"}).status_code == 200


def test_blank_env_username_falls_back_to_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "  ")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    c = TestClient(create_app())
    assert c.post("/v1/login", json={"username": "admin", "password": "pw"}).status_code == 200
