# ruff: noqa: S105, S106  (dummy test tokens)
"""Docs alignment: CLOB order prefix, JWT refresh envelope, balance fetch."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from pred_agent.auth.jwt_auth import JwtAuth, JwtTokens
from pred_agent.control_api.background import refresh_balance_once
from pred_agent.execution.live_executor import LiveExecutor
from pred_agent.transport.clob_transport import ClobTransport
from pred_agent.transport.jwt_transport import JwtTransport
from pred_agent.transport.protocol import PredTransportError


def test_order_prefix_per_transport():
    assert ClobTransport.order_prefix == "/clob"
    assert JwtTransport.order_prefix == ""


def test_live_executor_uses_transport_prefix():
    ex = LiveExecutor.__new__(LiveExecutor)
    ex.transport = SimpleNamespace(order_prefix="/clob")
    assert ex._order_prefix() == "/clob"
    ex.transport = SimpleNamespace()
    assert ex._order_prefix() == ""


def _jwt(handler) -> JwtAuth:
    auth = JwtAuth(
        api_key="k", private_key="0x" + "11" * 32, eoa_address="0xabc",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    auth._tokens = JwtTokens(
        access_token="old", refresh_token="r1", proxy_wallet_addr="0xproxy",
        wallet_address="0xabc", user_id="u1", is_enabled_trading=True,
    )
    return auth


def test_refresh_reads_nested_data_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"success": True, "data": {
                "access_token": "new", "user_id": "u1",
                "proxy_wallet_address": "0xproxy2", "is_enabled_trading": True,
                "data": {"user_id": "u1"},
            }},
            headers={"Set-Cookie": "refresh_token=r2; Path=/; HttpOnly"},
        )

    tokens = _jwt(handler).refresh()
    assert tokens.access_token == "new"
    assert tokens.refresh_token == "r2"
    assert tokens.proxy_wallet_addr == "0xproxy2"
    assert tokens.is_enabled_trading is True


class _Ctl:
    def __init__(self) -> None:
        self.balance: Any = "unset"

    def set_balance(self, v: Any) -> None:
        self.balance = v


class _Tr:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def get_balance(self) -> dict[str, Any]:
        return self._body


def test_balance_top_level_fields():
    ctl = _Ctl()
    asyncio.run(refresh_balance_once(
        controller=ctl, transport=_Tr({"success": True, "usdc_balance": "10.5", "position_balance": "0"})))
    assert ctl.balance == "10.5"


def test_balance_failure_stays_unavailable():
    ctl = _Ctl()
    asyncio.run(refresh_balance_once(
        controller=ctl, transport=_Tr({"success": False, "usdc_balance": "0", "error": "x"})))
    assert ctl.balance is None


def test_balance_http_error_stays_unavailable_and_logs_pred_error(caplog):
    """Docs: 401/500 balance replies carry success=false + error; transport raises on >=400."""
    class _Raising:
        def get_balance(self) -> dict[str, Any]:
            raise PredTransportError(401, "GET failed", raw_body='{"success":false,"usdc_balance":"0","error":"invalid_l2"}')

    ctl = _Ctl()
    with caplog.at_level("WARNING"):
        asyncio.run(refresh_balance_once(controller=ctl, transport=_Raising()))
    assert ctl.balance is None
    assert "HTTP 401" in caplog.text and "invalid_l2" in caplog.text


def test_describe_auth_error_shows_status_code_and_hint():
    from pred_agent.execution.live_runtime import describe_auth_error

    req = httpx.Request("GET", "https://www.pred.app/clob/auth/derive-api-key")
    resp = httpx.Response(401, request=req, json={"error": "invalid_auth"})
    exc = httpx.HTTPStatusError("401", request=req, response=resp)
    msg = describe_auth_error(exc)
    assert "HTTP 401" in msg and "invalid_auth" in msg and "primary wallet" in msg
    assert describe_auth_error(RuntimeError("boom")).startswith("RuntimeError")


def _clob_auth(handler):
    from pred_agent.auth.clob_auth import ClobAuth

    key = "0x" + "11" * 32
    from pred_agent.signer.eip712 import private_to_address

    return ClobAuth(
        eoa_address=private_to_address(key), private_key=key,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_ensure_credentials_only_recovers_existing_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("derive-api-key")
        return httpx.Response(200, json={"apiKey": "k", "secret": "c2VjcmV0", "passphrase": "p"})

    auth = _clob_auth(handler)
    creds = auth.ensure_credentials(0)
    assert creds.api_key == "k"


def test_create_nonce_already_used_recovers_by_deriving_same_nonce():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/auth/api-key"):
            return httpx.Response(400, json={"error": "nonce_already_used"})
        return httpx.Response(200, json={"apiKey": "k", "secret": "c2VjcmV0", "passphrase": "p"})

    creds = _clob_auth(handler).create_credentials(0)
    assert creds.api_key == "k"
    assert seen == ["/clob/auth/api-key", "/clob/auth/derive-api-key"]


def test_ensure_credentials_does_not_mask_server_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    with pytest.raises(httpx.HTTPStatusError):
        _clob_auth(handler).ensure_credentials(0)


def test_aggregator_key_required_hint_explains_unknown_account():
    from pred_agent.execution.live_runtime import describe_auth_error

    req = httpx.Request("POST", "https://www.pred.app/clob/auth/api-key")
    exc = httpx.HTTPStatusError(
        "401", request=req, response=httpx.Response(401, request=req, json={"error": "aggregator_key_required"}))
    msg = describe_auth_error(exc)
    assert "aggregator_key_required" in msg and "primary wallet" in msg
    assert "no new account or Safe" in msg


def test_clob_auth_failure_backs_off_instead_of_hammering_pred():
    calls = {"n": 0}

    class _Auth:
        credentials = None

        def sync_clock(self) -> None: ...

        def derive_credentials(self, nonce: int) -> None:
            calls["n"] += 1
            raise RuntimeError("401")

    class _T(_Tr):
        auth = _Auth()

    ctl = _Ctl()
    t = _T({"success": True, "usdc_balance": "5"})
    for _ in range(3):
        asyncio.run(refresh_balance_once(controller=ctl, transport=t))
    assert calls["n"] == 1 and ctl.balance is None


def test_telegram_conflict_is_one_quiet_line(caplog):
    from telegram.error import Conflict

    from pred_agent.telegram import bot as tg

    tg._conflict_state["last"] = None
    with caplog.at_level("WARNING"):
        tg._polling_error(Conflict("terminated by other getUpdates request"))
        tg._polling_error(Conflict("terminated by other getUpdates request"))
    assert caplog.text.count("another instance is polling") == 1
    assert "Traceback" not in caplog.text


def test_telegram_polls_only_after_becoming_leader():
    from pred_agent.telegram import bot as tg

    class _Lock:
        def __init__(self) -> None:
            self.tries = 0

        def try_acquire(self) -> bool:
            self.tries += 1
            return self.tries >= 3  # old instance still holds it for the first 2 tries

    class _Updater:
        started = 0

        async def start_polling(self, **_: Any) -> None:
            self.started += 1

    class _Bot:
        sent = 0

        async def send_message(self, **_: Any) -> None:
            self.sent += 1

    app = SimpleNamespace(updater=_Updater(), bot=_Bot())
    settings = SimpleNamespace(telegram_allowed_user_id="1")
    ctl = SimpleNamespace(mode="live")
    lock = _Lock()

    async def run() -> None:
        orig = asyncio.sleep
        tg.asyncio.sleep = lambda _s: orig(0)  # type: ignore[assignment]
        try:
            await tg._poll_when_leader(app, settings, ctl, lock)  # type: ignore[arg-type]
        finally:
            tg.asyncio.sleep = orig  # type: ignore[assignment]

    asyncio.run(run())
    assert lock.tries == 3 and app.updater.started == 1 and app.bot.sent == 1


def test_leader_lock_fails_open_without_postgres():
    from sqlalchemy import create_engine

    from pred_agent.telegram.bot import _LeaderLock

    assert _LeaderLock(None, "t").try_acquire() is True
    assert _LeaderLock(create_engine("sqlite://"), "t").try_acquire() is True


def test_ptb_conflict_error_line_is_filtered() -> None:
    import logging

    from pred_agent.telegram.bot import _QuietConflictFilter

    f = _QuietConflictFilter()

    def rec(msg: str) -> logging.LogRecord:
        return logging.LogRecord("telegram.ext.Updater", logging.ERROR, __file__, 1, msg, None, None)

    assert not f.filter(rec("Error while getting Updates: Conflict: terminated by other getUpdates request"))
    assert f.filter(rec("Error while getting Updates: Bad Gateway"))
