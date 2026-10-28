"""JWT (ordinary-login) account access: mocked HTTP only, deterministic signing, no network.

Covers the path that lets an EXISTING PRED account (EOA + Safe) be read and armed for live
trading: login, refresh/rotation, expiry, 401 handling, eligibility from the real reply, balance
failures never shown as zero, existing-account guards, and the read-only diagnostic.
"""
# ruff: noqa: S105, S106

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from pred_agent.auth.jwt_auth import JwtAuth, JwtAuthError
from pred_agent.execution.live_runtime import LiveSetupError, setup_live
from pred_agent.signer.eip712 import private_to_address, sign_create_proxy
from pred_agent.transport.factory import build_transport, resolve_auth_mode
from pred_agent.transport.jwt_transport import JwtTransport
from pred_agent.transport.protocol import PredTransportError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diagnose_pred_access as diag  # noqa: E402

PK = "0x" + "11" * 32
EOA = private_to_address(PK)
SAFE = "0x" + "33" * 20
OTHER_SAFE = "0x" + "44" * 20
FACTORY = "0xA077e7aaBF2e3c0155F83789e95f46602D03F7f0"
API_KEY = "pred-api-key-SECRET"


def _jwt(exp: int) -> str:
    def b(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b({'alg': 'none'})}.{b({'exp': exp})}.sig"


FAR = 4_102_444_800  # 2100
LOGIN_OK = {"success": True, "data": {
    "access_token": _jwt(FAR), "refresh_token": "refresh-SECRET-1",
    "proxy_wallet_addr": SAFE, "wallet_address": EOA,
    "data": {"user_id": "u1", "is_new_user": False, "is_enabled_trading": True,
             "wallet_address": EOA, "proxy_wallet_addr": SAFE},
}}


def _login_body(**nested: Any) -> dict:
    body = json.loads(json.dumps(LOGIN_OK))
    body["data"]["data"].update(nested)
    return body


class Server:
    """Scripted PRED: records requests, replies from per-path queues."""

    def __init__(self, routes: dict[str, list[Any]]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get(request.url.path)
        if not queue:
            return httpx.Response(404, json={"error": "no route"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    def count(self, path: str) -> int:
        return sum(1 for r in self.requests if r.url.path == path)


def _auth(server: Server) -> JwtAuth:
    return JwtAuth(api_key=API_KEY, private_key=PK, eoa_address=EOA,
                   http_client=httpx.Client(transport=httpx.MockTransport(server)))


LOGIN = "/api/v1/auth/login-with-signature"
REFRESH = "/api/v1/auth/refresh/token"
BALANCE = "/api/v1/portfolio/balance"


# ---------------------------------------------------------------- signing (deterministic)

def test_login_signature_is_deterministic_low_s_and_recovers_to_eoa():
    sig = sign_create_proxy(8453, FACTORY, PK)
    assert sig == sign_create_proxy(8453, FACTORY, PK)
    assert len(sig) == 132
    s = int(sig[66:130], 16)
    assert s <= 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0
    typed = {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "chainId", "type": "uint256"},
                             {"name": "verifyingContract", "type": "address"}],
            "CreateProxy": [{"name": "paymentToken", "type": "address"}, {"name": "payment", "type": "uint256"},
                            {"name": "paymentReceiver", "type": "address"}],
        },
        "primaryType": "CreateProxy",
        "domain": {"name": "Pred Contract Proxy Factory", "chainId": 8453, "verifyingContract": FACTORY},
        "message": {"paymentToken": "0x" + "00" * 20, "payment": 0, "paymentReceiver": "0x" + "00" * 20},
    }
    assert Account.recover_message(encode_typed_data(full_message=typed), signature=sig) == EOA


# ---------------------------------------------------------------- login / refresh

def test_login_sends_documented_request_and_parses_reply():
    server = Server({LOGIN: [(200, LOGIN_OK)]})
    tokens = _auth(server).login()
    req = server.requests[0]
    assert req.headers["X-API-Key"] == API_KEY
    data = json.loads(req.content)["data"]
    assert data["wallet_address"] == EOA and data["chain_type"] == "base"
    assert len(data["signature"]) == 132 and data["nonce"].isdigit()
    assert tokens.proxy_wallet_addr == SAFE and tokens.is_enabled_trading is True
    assert tokens.is_new_user is False


def test_tokens_never_appear_in_repr():
    tokens = _auth(Server({LOGIN: [(200, LOGIN_OK)]})).login()
    assert "SECRET" not in repr(tokens) and "access" not in repr(tokens).lower().replace("access_token", "")


def test_login_rejected_raises_safe_error_without_secrets():
    server = Server({LOGIN: [(401, {"error": {"code": "invalid_api_key", "message": "bad"}})]})
    with pytest.raises(JwtAuthError) as ei:
        _auth(server).login()
    msg = str(ei.value)
    assert "401" in msg and "invalid_api_key" in msg
    assert API_KEY not in msg and PK not in msg


@pytest.mark.parametrize("body", [{}, {"data": {}}, {"data": {"access_token": ""}}, [1, 2]])
def test_malformed_login_reply_fails_closed(body):
    with pytest.raises(JwtAuthError):
        _auth(Server({LOGIN: [(200, body)]})).login()


def test_login_network_timeout_is_a_safe_error():
    server = Server({LOGIN: [httpx.ConnectTimeout("t")]})
    with pytest.raises(JwtAuthError, match="ConnectTimeout"):
        _auth(server).login()


def test_missing_is_enabled_trading_means_not_enabled():
    body = json.loads(json.dumps(LOGIN_OK))
    del body["data"]["data"]["is_enabled_trading"]
    assert _auth(Server({LOGIN: [(200, body)]})).login().is_enabled_trading is False


def test_expired_token_is_refreshed_with_rotated_refresh_token():
    server = Server({
        LOGIN: [(200, {**LOGIN_OK, "data": {**LOGIN_OK["data"], "access_token": _jwt(1)}})],
        REFRESH: [(200, {"success": True, "data": {"access_token": _jwt(FAR), "is_enabled_trading": True}})],
    })
    auth = _auth(server)
    auth.login()
    headers = auth.access_headers(SAFE)
    assert server.count(REFRESH) == 1 and server.count(LOGIN) == 1
    assert server.requests[1].headers["X-Refresh-Token"] == "refresh-SECRET-1"
    assert headers["X-Proxy-Address"] == SAFE and headers["Authorization"].startswith("Bearer ")


def test_refresh_401_already_used_falls_back_to_login():
    server = Server({
        LOGIN: [(200, {**LOGIN_OK, "data": {**LOGIN_OK["data"], "access_token": _jwt(1)}}), (200, LOGIN_OK)],
        REFRESH: [(401, {"error": {"code": "REFRESH_TOKEN_ALREADY_USED"}})],
    })
    auth = _auth(server)
    auth.login()
    auth.access_headers(SAFE)
    assert server.count(LOGIN) == 2


# ---------------------------------------------------------------- transport

def _transport(server: Server) -> JwtTransport:
    client = httpx.Client(transport=httpx.MockTransport(server))
    return JwtTransport(JwtAuth(api_key=API_KEY, private_key=PK, eoa_address=EOA, http_client=client),
                        http_client=client)


def test_account_status_reflects_real_reply_not_assumptions():
    t = _transport(Server({LOGIN: [(200, LOGIN_OK)]}))
    st = t.account_status()
    assert st["next_step"] == "ready" and st["proxy_wallet_address"] == SAFE and st["eoa"] == EOA
    t2 = _transport(Server({LOGIN: [(200, _login_body(is_enabled_trading=False))]}))
    assert t2.account_status()["next_step"] == "safe_approval_required"


def test_account_status_login_failure_raises_not_invents():
    t = _transport(Server({LOGIN: [(401, {"error": "nope"})]}))
    with pytest.raises(PredTransportError):
        t.account_status()


def test_get_401_relogs_once_but_write_401_is_never_retried():
    server = Server({LOGIN: [(200, LOGIN_OK)], BALANCE: [(401, {"error": "expired"}), (200, {"success": True, "usdc_balance": "12.5"})],
                     "/api/v1/order/p/place": [(401, {"error": "expired"})]})
    t = _transport(server)
    assert t.get_balance()["usdc_balance"] == "12.5"
    assert server.count(LOGIN) == 2
    with pytest.raises(PredTransportError) as ei:
        t.post("/api/v1/order/p/place", json={"x": 1})
    assert ei.value.status == 401 and server.count("/api/v1/order/p/place") == 1


@pytest.mark.asyncio
async def test_balance_failure_is_unavailable_never_zero():
    from pred_agent.control_api.background import refresh_balance_once

    seen: list[Any] = []
    controller = SimpleNamespace(set_balance=seen.append)
    for reply in [(500, {"success": False, "error": "boom"}), (200, {"success": False})]:
        t = _transport(Server({LOGIN: [(200, LOGIN_OK)], BALANCE: [reply]}))
        await refresh_balance_once(controller=controller, transport=t)
    t = _transport(Server({LOGIN: [(401, {"error": "bad key"})]}))
    await refresh_balance_once(controller=controller, transport=t)
    assert seen == [None, None, None]


# ---------------------------------------------------------------- factory

def _settings(**kw: Any) -> SimpleNamespace:
    base = {
        "private_key": PK, "pred_api_key": None, "pred_partner_key": None, "pred_auth_mode": "auto",
        "host": "https://www.pred.app", "chain_id": 8453, "create_proxy_factory": FACTORY,
        "auth_login_message": "Sign in to PRED Trading Platform", "refresh_threshold_seconds": 300,
        "pred_proxy_address": None, "live_trading": True, "max_spread_cents": 6.0, "max_slippage_pp": 1.5,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_factory_picks_one_path_and_never_mixes_them():
    assert resolve_auth_mode(_settings(pred_api_key="k")) == "jwt"
    assert resolve_auth_mode(_settings()) == "clob"
    assert resolve_auth_mode(_settings(pred_api_key="k", pred_auth_mode="clob")) == "clob"
    assert isinstance(build_transport(_settings(pred_api_key="k")), JwtTransport)
    assert build_transport(_settings()).order_prefix == "/clob"
    with pytest.raises(ValueError, match="PRED_API_KEY"):
        build_transport(_settings(pred_auth_mode="jwt"))
    with pytest.raises(ValueError, match="PRIVATE_KEY"):
        build_transport(_settings(private_key=None))


# ---------------------------------------------------------------- arming (setup_live)

def _arm(monkeypatch, status: dict | Exception, **settings_kw: Any):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("PRIVATE_KEY", PK)

    class T:
        order_prefix = ""
        auth = SimpleNamespace(login=lambda: None)

        def account_status(self):
            if isinstance(status, Exception):
                raise status
            return status

        def get(self, path, params=None):
            return {"orders": []}

    return setup_live(settings=_settings(**settings_kw), transport=T(),
                      market_lookup=lambda _m: None, max_discovery_age_s=60.0)


READY = {"eoa": EOA, "proxy_wallet_address": SAFE, "is_enabled_trading": True, "is_new_user": False, "next_step": "ready"}


def test_arm_blocked_when_trading_not_enabled(monkeypatch):
    with pytest.raises(LiveSetupError, match="is_enabled_trading=false"):
        _arm(monkeypatch, {**READY, "is_enabled_trading": False, "next_step": "safe_approval_required"})


def test_arm_blocked_for_new_user_unless_expected_safe_confirms(monkeypatch):
    with pytest.raises(LiveSetupError, match="NEW user"):
        _arm(monkeypatch, {**READY, "is_new_user": True})


def test_arm_blocked_on_proxy_or_eoa_mismatch(monkeypatch):
    with pytest.raises(LiveSetupError, match="different account"):
        _arm(monkeypatch, READY, pred_proxy_address=OTHER_SAFE)
    with pytest.raises(LiveSetupError, match="does not match"):
        _arm(monkeypatch, {**READY, "eoa": "0x" + "99" * 20})


def test_arm_reports_login_failure_without_secrets(monkeypatch):
    with pytest.raises(LiveSetupError) as ei:
        _arm(monkeypatch, PredTransportError(401, "login rejected: HTTP 401 invalid_api_key"))
    assert "JWT login" in str(ei.value) and PK not in str(ei.value)


def test_arm_succeeds_for_ready_account_matching_expected_safe(monkeypatch):
    rt = _arm(monkeypatch, READY, pred_proxy_address=SAFE)
    assert rt.executor.proxy_address == SAFE and rt.executor.eoa_address == EOA


# ---------------------------------------------------------------- diagnostic (read-only)

class RecordingTransport:
    def __init__(self, status: Any, balance: Any) -> None:
        self.status, self.balance, self.calls = status, balance, []

    def account_status(self):
        self.calls.append("account_status")
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def get_balance(self):
        self.calls.append("get_balance")
        if isinstance(self.balance, Exception):
            raise self.balance
        return self.balance

    def post(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("diagnostic must not write")

    delete = post


def test_diagnostic_is_read_only_and_reports_each_verdict():
    ok = RecordingTransport(READY, {"success": True, "usdc_balance": "3.2"})
    r = diag.diagnose(ok, EOA, SAFE)
    assert r["verdict"] == "ready_to_trade_when_live_trading_enabled" and ok.calls == ["account_status", "get_balance"]
    off = diag.diagnose(RecordingTransport({**READY, "is_enabled_trading": False}, {"success": True, "usdc_balance": "0"}), EOA)
    assert off["verdict"] == "authenticated_but_trading_not_enabled"
    wrong = diag.diagnose(RecordingTransport(READY, {"success": True, "usdc_balance": "1"}), EOA, OTHER_SAFE)
    assert wrong["verdict"] == "wrong_account"
    new = diag.diagnose(RecordingTransport({**READY, "is_new_user": True}, {"success": True, "usdc_balance": "0"}), EOA)
    assert new["verdict"] == "new_user_not_your_existing_account"
    failed = diag.diagnose(RecordingTransport(PredTransportError(401, "x"), None), EOA)
    assert failed["verdict"] == "login_failed"


def test_diagnostic_balance_api_error_is_not_zero():
    r = diag.diagnose(RecordingTransport(READY, PredTransportError(500, "x")), EOA)
    assert r["balance"]["ok"] is False and "usdc_balance" not in r["balance"]


def test_diagnostic_requires_explicit_opt_in(capsys):
    assert diag.main([]) == 2
    assert "--allow-login" in capsys.readouterr().out
