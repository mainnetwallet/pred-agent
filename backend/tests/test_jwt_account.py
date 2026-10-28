# ruff: noqa: S105, S106  (dummy test tokens / keys)
"""JWT login + account/balance view against mocked PRED responses. No network, no orders."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx
import pytest

from pred_agent.account import AccountService, initial_snapshot
from pred_agent.auth.jwt_auth import JwtAuth, JwtAuthError
from pred_agent.config import Settings
from pred_agent.control_api.background import build_account_service, refresh_account_once
from pred_agent.control_api.controller import AgentController
from pred_agent.transport.jwt_transport import JwtTransport

PK = "0x" + "11" * 32
EOA = "0x1111111111111111111111111111111111111111"
SAFE = "0x2222222222222222222222222222222222222222"
API_KEY = "test-api-key-123"


def _jwt(exp_in: int = 3600, tag: str = "a") -> str:
    def b64(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': int(time.time()) + exp_in, 't': tag})}.sig"


def _login_body(token: str, enabled: bool = True) -> dict[str, Any]:
    # Documented LoginResponse shape.
    return {
        "success": True,
        "data": {
            "access_token": token,
            "refresh_token": "refresh-1",
            "proxy_wallet_addr": SAFE,
            "wallet_address": EOA,
            "data": {"user_id": "u1", "is_enabled_trading": enabled},
        },
    }


class Pred:
    """Tiny fake PRED server; records requests."""

    def __init__(self, *, enabled: bool = True, balance: dict[str, Any] | None = None,
                 login_status: int = 200) -> None:
        self.enabled = enabled
        self.balance = balance or {"success": True, "usdc_balance": "12.34", "position_balance": "0"}
        self.login_status = login_status
        self.requests: list[httpx.Request] = []
        self.balance_statuses: list[int] = []  # popped first, then 200
        self.logins = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if path == "/api/v1/auth/login-with-signature":
            self.logins += 1
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"success": False, "error": {"code": "invalid_api_key"}})
            return httpx.Response(200, json=_login_body(_jwt(tag=str(self.logins)), self.enabled),
                                  headers={"Set-Cookie": "refresh_token=refresh-1; Path=/"})
        if path == "/api/v1/auth/refresh/token":
            return httpx.Response(401, json={"error": {"code": "REFRESH_TOKEN_ALREADY_USED"}})
        if path == "/api/v1/portfolio/balance":
            if self.balance_statuses:
                st = self.balance_statuses.pop(0)
                if st != 200:
                    return httpx.Response(st, json={"success": False, "usdc_balance": "0", "error": "invalid_auth"})
            return httpx.Response(200, json=self.balance)
        if path == "/api/v1/portfolio/positions":
            return httpx.Response(200, json={"success": True, "total_invested": "5.00", "total_to_win": "9.50"})
        return httpx.Response(404, json={})


def _service(pred: Pred, api_key: str = API_KEY) -> AccountService:
    http = httpx.Client(transport=httpx.MockTransport(pred))
    auth = JwtAuth(api_key=api_key, private_key=PK, eoa_address=EOA, http_client=http)
    return AccountService(auth, JwtTransport(auth, http_client=http))


# ── login ────────────────────────────────────────────────────────────────────

def test_login_sends_api_key_and_documented_body():
    pred = Pred()
    svc = _service(pred)
    tokens = svc.auth.login()
    req = pred.requests[0]
    assert req.headers["X-API-Key"] == API_KEY
    body = json.loads(req.content)["data"]
    assert body["wallet_address"] == EOA and body["chain_type"] == "base"
    assert len(body["signature"]) == 132 and body["signature"].startswith("0x")
    assert isinstance(body["timestamp"], int) and body["nonce"].isdigit()
    assert tokens.proxy_wallet_addr == SAFE and tokens.wallet_address == EOA
    assert tokens.user_id == "u1" and tokens.is_enabled_trading is True


@pytest.mark.parametrize("key", ["", "   ", None])
def test_login_without_api_key_fails_before_any_request(key):
    pred = Pred()
    svc = _service(pred, api_key=key)  # type: ignore[arg-type]
    with pytest.raises(JwtAuthError) as ei:
        svc.auth.login()
    assert ei.value.code == "api_key_missing"
    assert pred.requests == []


def test_login_reads_token_from_header_when_body_lacks_it():
    def handler(req: httpx.Request) -> httpx.Response:
        body = _login_body("ignored")
        del body["data"]["access_token"]
        return httpx.Response(200, json=body, headers={"X-Access-Token": _jwt()})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    auth = JwtAuth(api_key=API_KEY, private_key=PK, eoa_address=EOA, http_client=http)
    assert auth.login().access_token.count(".") == 2


def test_login_malformed_response_is_a_clear_error_not_keyerror():
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"success": True, "data": {}})))
    auth = JwtAuth(api_key=API_KEY, private_key=PK, eoa_address=EOA, http_client=http)
    with pytest.raises(JwtAuthError) as ei:
        auth.login()
    assert ei.value.code == "bad_response"


def test_login_rejected_message_has_no_secrets():
    pred = Pred(login_status=401)
    with pytest.raises(JwtAuthError) as ei:
        _service(pred).auth.login()
    assert ei.value.code == "login_rejected" and ei.value.status == 401
    assert API_KEY not in ei.value.message and PK not in ei.value.message


# ── account snapshot ─────────────────────────────────────────────────────────

def test_refresh_ok_shows_safe_wallet_cash_and_separate_positions():
    snap = _service(Pred()).refresh()
    assert snap["state"] == "ok"
    assert snap["proxy_address"] == SAFE and snap["wallet_address"] == EOA
    assert snap["cash_available_usd"] == "12.34"          # usdc_balance only
    assert snap["positions_cost_usd"] == "5.00"           # separate figures, not added to cash
    assert snap["positions_payout_usd"] == "9.50"
    assert snap["trading_enabled"] is True and snap["message"] is None


def test_balance_request_uses_required_headers():
    pred = Pred()
    _service(pred).refresh()
    bal = next(r for r in pred.requests if r.url.path == "/api/v1/portfolio/balance")
    assert bal.headers["Authorization"].startswith("Bearer ")
    assert bal.headers["X-Wallet-Address"] == EOA and bal.headers["X-Proxy-Address"] == SAFE
    assert "X-API-Key" not in bal.headers  # login-only header
    assert not any(k.lower().startswith("pred_") for k in bal.headers)  # no CLOB (L2) headers


def test_real_zero_balance_is_shown_as_zero():
    snap = _service(Pred(balance={"success": True, "usdc_balance": "0", "position_balance": "0"})).refresh()
    assert snap["state"] == "ok" and snap["cash_available_usd"] == "0"


def test_trading_disabled_still_shows_balance_with_clear_message():
    snap = _service(Pred(enabled=False)).refresh()
    assert snap["state"] == "ok" and snap["cash_available_usd"] == "12.34"
    assert snap["trading_enabled"] is False and snap["code"] == "trading_disabled"
    assert "Safe approval" in snap["message"]


@pytest.mark.parametrize("body", [
    {"success": False, "usdc_balance": "0", "error": "portfolio lookup failed"},
    {"success": True},
    {"success": True, "usdc_balance": "not-a-number"},
])
def test_failed_or_unusable_balance_is_never_zero(body):
    snap = _service(Pred(balance=body)).refresh()
    assert snap["state"] == "error" and snap["cash_available_usd"] is None


def test_missing_api_key_state_is_not_configured():
    snap = _service(Pred(), api_key="").refresh()
    assert snap["state"] == "not_configured" and snap["cash_available_usd"] is None
    assert "PRED_API_KEY" in snap["message"]


def test_login_rejected_state_is_unauthenticated():
    snap = _service(Pred(login_status=401)).refresh()
    assert snap["state"] == "unauthenticated" and snap["cash_available_usd"] is None


def test_expired_access_token_relogs_in_once_and_retries():
    pred = Pred()
    pred.balance_statuses = [401]  # first balance call rejected, retry succeeds
    snap = _service(pred).refresh()
    assert snap["state"] == "ok" and snap["cash_available_usd"] == "12.34"
    assert pred.logins == 2  # refresh token already used (401) -> fresh login


def test_persistent_401_is_session_expired_not_zero():
    pred = Pred()
    pred.balance_statuses = [401, 401]
    snap = _service(pred).refresh()
    assert snap["state"] == "session_expired" and snap["cash_available_usd"] is None
    assert "invalid_auth" in snap["message"]


def test_server_error_state_error_with_status():
    pred = Pred()
    pred.balance_statuses = [500]
    snap = _service(pred).refresh()
    assert snap["state"] == "error" and "500" in snap["message"] and snap["cash_available_usd"] is None


def test_network_failure_is_error_and_leaks_nothing():
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom " + API_KEY)

    http = httpx.Client(transport=httpx.MockTransport(boom))
    auth = JwtAuth(api_key=API_KEY, private_key=PK, eoa_address=EOA, http_client=http)
    snap = AccountService(auth, JwtTransport(auth, http_client=http)).refresh()
    assert snap["state"] == "error" and snap["code"] == "network"
    assert API_KEY not in json.dumps(snap) and PK not in json.dumps(snap)


def test_snapshot_never_contains_tokens_or_keys():
    svc = _service(Pred())
    dumped = json.dumps(svc.refresh())
    for secret in (API_KEY, PK, svc.auth._tokens.access_token, "refresh-1"):  # type: ignore[union-attr]
        assert secret not in dumped


# ── controller / background wiring ───────────────────────────────────────────

def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_status_reports_loading_then_account_and_keeps_live_off():
    s = _settings(pred_api_key=API_KEY, private_key=PK)
    ctl = AgentController(s)
    st = ctl.status()
    assert st["mode"] == "PAPER" and st["balance"] is None
    assert st["account"]["state"] == "loading"
    asyncio.run(refresh_account_once(controller=ctl, service=_service(Pred())))
    st = ctl.status()
    assert st["balance"] == "12.34" and st["account"]["proxy_address"] == SAFE
    assert s.live_trading is False


def test_status_not_configured_without_api_key():
    ctl = AgentController(_settings(private_key=PK))
    acct = ctl.status()["account"]
    assert acct["state"] == "not_configured" and "PRED_API_KEY" in acct["message"]
    assert ctl.status()["balance"] is None


def test_failed_refresh_clears_stale_balance():
    ctl = AgentController(_settings(pred_api_key=API_KEY, private_key=PK))
    asyncio.run(refresh_account_once(controller=ctl, service=_service(Pred())))
    assert ctl.status()["balance"] == "12.34"
    pred = Pred()
    pred.balance_statuses = [500]
    asyncio.run(refresh_account_once(controller=ctl, service=_service(pred)))
    assert ctl.status()["balance"] is None and ctl.status()["account"]["state"] == "error"


def test_build_account_service_requires_both_credentials():
    assert build_account_service(_settings(private_key=PK)) is None
    assert build_account_service(_settings(pred_api_key=API_KEY)) is None
    assert build_account_service(_settings(pred_api_key=API_KEY, private_key=PK)) is not None


def test_initial_snapshot_states():
    assert initial_snapshot(configured=True)["state"] == "loading"
    assert initial_snapshot(configured=False)["state"] == "not_configured"
