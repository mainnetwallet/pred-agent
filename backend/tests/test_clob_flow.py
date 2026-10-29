"""CLOB auth + order flow against a FAKE PRED server (no real network, never a real order).

The fake enforces PRED's documented rules independently of the client's own helpers:
  L1  ClobAuth EIP-712 signature must recover to PRED_ADDRESS, timestamp within 30 s of server time
  L2  HMAC over timestamp + METHOD + canonicalPath(no /clob, no query) + exact raw body
  order  EIP-712 Order signature must recover to the EOA (signer) with the Safe as maker,
         signed fee == floor(target * max(p, 1 - p)), amount == side-aware notional, typed payload
Sources: PRED docs "Builder and Aggregator Onboarding" and "Orders".

Unit-level coverage of ensure_credentials()/create_credentials()/ClobAccessBlocked branching
lives in test_clob_eligibility.py; this file covers the end-to-end flow (auth -> transport ->
executor -> reconcile) and the CLOB-vs-JWT / live-arming / enablement-CLI integration points.
"""

from __future__ import annotations

import json
import re
import socket
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from eth_account import Account
from eth_account.messages import encode_typed_data

import pred_agent.execution.live_executor as le_mod
from pred_agent.auth.clob_auth import ClobAccessBlocked, ClobAuth
from pred_agent.auth.mode import resolve_auth_mode
from pred_agent.book import BookLevel, BookSnapshot
from pred_agent.config import Settings
from pred_agent.execution import LiveExecutor, LiveOrderIntent, OrderState, OrderStateMachine
from pred_agent.execution.live_runtime import LiveSetupError, describe_auth_error, setup_live
from pred_agent.transport.clob_transport import ClobTransport
from pred_agent.transport.protocol import PredTransportError

KEY = "0x" + "11" * 32
EOA = Account.from_key(KEY).address
SAFE = "0x" + "33" * 20
CONTRACT = "0x" + "ee" * 20
MARKET = "0x" + "ab" * 32
PARENT = "0x" + "cd" * 32
SECRET_BYTES = b"super-secret-hmac-key-material-01"
CURVE_HALF = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0
ZERO = "0x0000000000000000000000000000000000000000"


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(*_a: Any, **_k: Any) -> None:
        raise AssertionError("real network access attempted in a mocked-only test")

    monkeypatch.setattr(socket.socket, "connect", _deny)


class FakePred:
    """Minimal PRED CLOB server that validates what the docs say PRED validates."""

    def __init__(self, *, known_eoa: str | None = EOA, pad: bool = False, skew_s: int = 0) -> None:
        self.known_eoa = known_eoa.lower() if known_eoa else None
        # Which base64url form the "server" accepts for PRED_SIGNATURE. False (unpadded) is the
        # client's own default (pad_signature=False), so a fresh ClobAuth needs no retry against
        # a default FakePred; pad=True exercises the one-shot GET retry in ClobTransport.get().
        self.pad = pad
        self.skew_s = skew_s        # server clock minus local clock
        self.issued = False
        self.secret = urlsafe_b64encode(SECRET_BYTES).decode()
        self.requests: list[httpx.Request] = []
        self.violations: list[str] = []
        self.accepted_orders: list[dict[str, Any]] = []
        self.account_creations = 0

    # -- helpers --------------------------------------------------------- #

    def now(self) -> int:
        return int(time.time()) + self.skew_s

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    @staticmethod
    def _err(status: int, code: str) -> httpx.Response:
        return httpx.Response(status, json={"error": code})

    def _fresh(self, ts: str) -> bool:
        return ts.isdigit() and abs(int(ts) - self.now()) <= 30

    # -- routing --------------------------------------------------------- #

    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        self.requests.append(request)
        path = request.url.path
        if path == "/clob/time":
            return httpx.Response(200, json=self.now())
        if path in ("/clob/auth/derive-api-key", "/clob/auth/api-key"):
            return self._l1(request, path)
        bad = self._l2(request)
        if bad is not None:
            return bad
        if path == "/clob/api/v1/user/account-status":
            return httpx.Response(200, json={
                "user_id": "u1", "eoa": EOA, "proxy_wallet_address": SAFE, "next_step": "ready"})
        if path == "/clob/api/v1/portfolio/open-orders":
            body: dict[str, Any] = {"success": True,
                                    "pagination": {"has_more": False, "limit": 100, "offset": 0}}
            if self.accepted_orders:
                body["orders"] = [{
                    "order_id": f"ord-{i + 1}", "market_id": o["market_id"], "parent_market_id": PARENT,
                    "side": o["side"], "price": o["price"], "quantity": o["quantity"],
                    "remaining_quantity": o["quantity"],
                } for i, o in enumerate(self.accepted_orders)]
            return httpx.Response(200, json=body)
        if path == "/clob/api/v1/portfolio/trade-history":
            return httpx.Response(200, json={"success": True, "trade_history": [], "pagination": {
                "total": 0, "limit": 100, "offset": 0, "has_next": False}})
        if path == f"/clob/api/v1/order/{PARENT}/place" and request.method == "POST":
            return self._place(request)
        return httpx.Response(404, json={"error": "not_found"})

    # -- L1 -------------------------------------------------------------- #

    def _l1(self, request: httpx.Request, path: str) -> httpx.Response:  # noqa: PLR0911
        h = request.headers
        try:
            address, sig, ts, nonce = h["PRED_ADDRESS"], h["PRED_SIGNATURE"], h["PRED_TIMESTAMP"], h["PRED_NONCE"]
        except KeyError:
            return self._err(401, "invalid_l1")
        if not self._fresh(ts):
            return self._err(401, "invalid_l1")
        full = {
            "types": {
                "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                 {"name": "chainId", "type": "uint256"}],
                "ClobAuth": [{"name": "address", "type": "address"}, {"name": "timestamp", "type": "string"},
                             {"name": "nonce", "type": "uint256"}, {"name": "message", "type": "string"}],
            },
            "primaryType": "ClobAuth",
            "domain": {"name": "ClobAuthDomain", "version": "1", "chainId": 8453},
            "message": {"address": address, "timestamp": ts, "nonce": int(nonce),
                        "message": "This message attests that I control the given wallet"},
        }
        recovered = Account.recover_message(encode_typed_data(full_message=full), signature=sig)
        if recovered.lower() != address.lower():
            return self._err(401, "invalid_l1")
        known = self.known_eoa is not None and address.lower() == self.known_eoa
        creds = {"apiKey": "key-1", "secret": self.secret, "passphrase": "pp-1"}
        if request.method == "POST":
            if not known:
                if "X-Aggregator-API-Key" in h:
                    self.account_creations += 1  # a partner key would have opened a NEW account
                return self._err(401, "aggregator_key_required")
            self.issued = True
            return httpx.Response(200, json=creds)
        if not known or not self.issued:
            return self._err(401, "invalid_auth")
        return httpx.Response(200, json=creds)

    # -- L2 -------------------------------------------------------------- #

    def _l2(self, request: httpx.Request) -> httpx.Response | None:
        h = request.headers
        if h.get("PRED_API_KEY") != "key-1" or h.get("PRED_PASSPHRASE") != "pp-1":
            return self._err(401, "invalid_l2")
        if h.get("PRED_ADDRESS", "").lower() != (self.known_eoa or ""):
            return self._err(401, "invalid_l2")
        if not self._fresh(h.get("PRED_TIMESTAMP", "")):
            return self._err(401, "invalid_l2")
        canonical = request.url.path[len("/clob"):]  # excludes /clob and the query string
        message = h["PRED_TIMESTAMP"] + request.method.upper() + canonical + request.content.decode()
        mac = urlsafe_b64encode(
            __import__("hmac").new(urlsafe_b64decode(self.secret), message.encode(), "sha256").digest()
        ).decode()
        expected = mac if self.pad else mac.rstrip("=")
        if h.get("PRED_SIGNATURE") != expected:
            return self._err(401, "invalid_l2")
        return None

    # -- order placement ------------------------------------------------- #

    def _place(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0912
        try:
            b = json.loads(request.content)
        except ValueError:
            self.violations.append("body is not JSON")
            return self._err(400, "bad_json")
        v = self.violations
        for field in ("salt", "market_id", "side", "price", "quantity", "signature", "type", "timestamp"):
            if field not in b:
                v.append(f"missing {field}")
        if v:
            return self._err(400, "validation")
        if not (isinstance(b["salt"], str) and b["salt"].isdigit()):
            v.append("salt must be a decimal string")
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", b["market_id"]):
            v.append("market_id must be 0x + 64 hex")
        if b["side"] not in ("long", "short"):
            v.append("side must be long|short")
        if b["type"] not in ("limit", "market", "ioc"):
            v.append("type must be limit|market|ioc")
        if not (isinstance(b["price"], str) and isinstance(b["quantity"], str)):
            v.append("price and quantity must be decimal strings")
            return self._err(400, "validation")
        price, qty = Decimal(b["price"]), Decimal(b["quantity"])
        if not Decimal(0) < price < Decimal(100):
            v.append("price must be in (0,100) cents")
        if qty.as_tuple().exponent < -3:  # type: ignore[operator]
            v.append("quantity has more than 3 decimals")
        if not (isinstance(b["timestamp"], int) and abs(b["timestamp"] - self.now()) <= 30):
            v.append("timestamp must be int unix seconds within 30 s")
        expected_amount = (price * qty / 100) if b["side"] == "long" else ((100 - price) * qty / 100)
        if not b.get("reduce_only") and Decimal(b.get("amount", "-1")) != expected_amount:
            v.append(f"amount {b.get('amount')} != notional {expected_amount}")
        scaled = int(price * 10_000)
        expected_fee = 250 * max(scaled, 1_000_000 - scaled) // 1_000_000
        if b.get("fee_rate_bps") != expected_fee:
            v.append(f"fee_rate_bps {b.get('fee_rate_bps')} != signed rate {expected_fee}")
        sig = b["signature"]
        if not re.fullmatch(r"0x[0-9a-f]{130}", sig) or int(sig[66:130], 16) > CURVE_HALF:
            v.append("signature must be 0x + 130 hex, low-s")
        else:
            full = {
                "types": {
                    "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                     {"name": "chainId", "type": "uint256"},
                                     {"name": "verifyingContract", "type": "address"}],
                    "Order": [{"name": n, "type": t} for n, t in (
                        ("salt", "uint256"), ("maker", "address"), ("signer", "address"), ("taker", "address"),
                        ("price", "uint256"), ("quantity", "uint256"), ("expiration", "uint256"),
                        ("nonce", "uint256"), ("questionId", "bytes32"), ("feeRateBps", "uint256"),
                        ("intent", "uint8"), ("signatureType", "uint8"))],
                },
                "primaryType": "Order",
                "domain": {"name": "Pred CTF Exchange", "version": "1", "chainId": 8453,
                           "verifyingContract": CONTRACT},
                "message": {
                    "salt": int(b["salt"]), "maker": SAFE, "signer": EOA, "taker": ZERO,
                    "price": scaled, "quantity": int(qty * 1_000_000), "expiration": b.get("expiration", 0),
                    "nonce": 0, "questionId": b["market_id"], "feeRateBps": b.get("fee_rate_bps"),
                    "intent": 0 if b["side"] == "long" else 1, "signatureType": 2,
                },
            }
            recovered = Account.recover_message(encode_typed_data(full_message=full), signature=sig)
            if recovered != EOA:
                v.append(f"order signature recovers to {recovered}, expected signer {EOA} (maker = Safe)")
        if v:
            return self._err(400, "validation")
        self.accepted_orders.append(b)
        return httpx.Response(202, json={"status": "order_created",
                                         "order_id": f"ord-{len(self.accepted_orders)}",
                                         "message": "ok", "filled_quantity": "0"})


# ── helpers ──────────────────────────────────────────────────────────────────


def _book() -> BookSnapshot:
    return BookSnapshot(
        market_id=MARKET, bids=[BookLevel(Decimal("50"), Decimal("100"))],
        asks=[BookLevel(Decimal("52"), Decimal("100"))], ltp=None, spread_cents=None,
        mid_price_cents=None, last_updated_at=None, sequence_number=None,
    )


def _stack(fake: FakePred) -> tuple[ClobAuth, ClobTransport]:
    auth = ClobAuth(eoa_address=EOA, private_key=KEY, http_client=fake.client())
    return auth, ClobTransport(auth, http_client=fake.client())


def _settings(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "live_trading": True, "private_key": KEY, "chain_id": 8453, "host": "https://www.pred.app",
        "max_spread_cents": 6.0, "max_slippage_pp": 1.5, "pred_auth_mode": "clob", "pred_api_key": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _arm(fake: FakePred, monkeypatch: pytest.MonkeyPatch, **settings_over: Any) -> Any:
    monkeypatch.setattr(le_mod, "load_settings", lambda: SimpleNamespace(live_trading=True))
    _, transport = _stack(fake)
    runtime = setup_live(settings=_settings(**settings_over), transport=transport,
                         market_lookup=lambda m: None, max_discovery_age_s=60.0)
    runtime.executor.book_provider = lambda parent, market: _book()
    runtime.executor.market_check = lambda market: None
    return runtime


def _intent(**over: Any) -> LiveOrderIntent:
    base: dict[str, Any] = {
        "market_id": MARKET, "parent_market_id": PARENT, "contract_address": CONTRACT, "side": "long",
        "price_cents": Decimal("52"), "quantity": Decimal("3.8"), "amount_usd": Decimal("999"),
        "order_type": "limit", "fee_rate_bps": 0, "fee_target_bps": 250,
    }
    base.update(over)
    return LiveOrderIntent(**base)


# ── the happy path: existing eligible account, end to end ────────────────────


def test_eligible_account_authenticates_and_places_a_correct_mocked_order(monkeypatch):
    fake = FakePred()
    runtime = _arm(fake, monkeypatch)

    order = runtime.executor.place_from_proposal(proposal_id="p1", intent=_intent())

    assert fake.violations == []  # L1, L2, EIP-712 order signature, fee, amount all valid
    assert order is not None and order.state == OrderState.OPEN and order.pred_order_id == "ord-1"
    body = fake.accepted_orders[0]
    assert body["side"] == "long" and body["type"] == "limit" and body["fee_rate_bps"] == 130
    assert Decimal(body["amount"]) == Decimal("1.976")  # 52c * 3.8 shares / 100, NOT the caller's 999
    assert isinstance(body["timestamp"], int) and isinstance(body["salt"], str)
    # the ordinary-login key and any partner key are never involved
    assert all("X-Aggregator-API-Key" not in r.headers and "X-API-Key" not in r.headers for r in fake.requests)
    assert fake.account_creations == 0
    # exactly one placement request was sent
    assert sum(1 for r in fake.requests if r.method == "POST" and r.url.path.endswith("/place")) == 1


def test_reconciliation_sees_the_resting_order_and_keeps_it_open(monkeypatch):
    fake = FakePred()
    runtime = _arm(fake, monkeypatch)
    order = runtime.executor.place_from_proposal(proposal_id="p1", intent=_intent())
    assert order is not None

    report = runtime.executor.sync()

    assert fake.violations == []
    assert report["unresolved"] == [] and report["exchange_open_orders"] == 1
    assert order.state == OrderState.OPEN and order.remaining_qty == "3.8"
    assert not runtime.executor.sm.is_market_blocked(MARKET)


# ── L1 clock handling ───────────────────────────────────────────────────────


def test_l1_timestamp_is_synced_to_pred_clock_before_signing():
    """PRED rejects L1 timestamps more than 30 s from server time; an hour of skew must be fixed."""
    fake = FakePred(skew_s=3600)
    auth, _ = _stack(fake)
    creds = auth.ensure_credentials(0)  # no explicit sync_clock(): the auth must do it itself
    assert creds.api_key == "key-1"
    assert fake.requests[0].url.path == "/clob/time"


# ── the PRED-side eligibility blocker (integration layer, not auth internals) ──


def test_setup_live_reports_the_exact_blocker_and_stays_unarmed(monkeypatch):
    fake = FakePred(known_eoa=None)
    monkeypatch.setattr(le_mod, "load_settings", lambda: SimpleNamespace(live_trading=True))
    _, transport = _stack(fake)
    with pytest.raises(LiveSetupError) as info:
        setup_live(settings=_settings(), transport=transport, market_lookup=lambda m: None,
                   max_discovery_age_s=60.0)
    msg = str(info.value)
    assert "CLOB access blocked" in msg and "aggregator_key_required" in msg and EOA in msg
    assert not any(r.url.path.endswith("/place") for r in fake.requests)


def test_partner_key_cannot_be_configured_or_sent(monkeypatch):
    monkeypatch.setenv("PRED_PARTNER_KEY", "partner-secret")
    assert not hasattr(Settings(_env_file=None), "pred_partner_key")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ClobAuth(eoa_address=EOA, private_key=KEY, aggregator_api_key="x")  # type: ignore[call-arg]
    fake = FakePred(known_eoa=None)
    auth, _ = _stack(fake)
    with pytest.raises(ClobAccessBlocked):
        auth.ensure_credentials(0)
    assert fake.account_creations == 0


# ── transport regressions ───────────────────────────────────────────────────


def test_account_status_raises_on_http_error_instead_of_returning_the_error_body():
    fake = FakePred(pad=True)  # server only accepts padded; force a definite L2 failure below
    auth, transport = _stack(fake)
    auth.ensure_credentials(0)
    transport._l2_verified = True  # fallback disabled: a plain 401 must surface as an exception
    with pytest.raises(PredTransportError) as info:
        transport.account_status()
    assert info.value.status == 401
    assert "HTTP 401 invalid_l2" in describe_auth_error(info.value)


def test_health_check_and_clob_time_work_before_any_credentials_exist():
    fake = FakePred()
    auth, transport = _stack(fake)
    assert auth.credentials is None
    assert transport.health_check() is True
    assert isinstance(transport.clob_time(), int)
    assert all("PRED_SIGNATURE" not in r.headers for r in fake.requests)  # /clob/time is unauthenticated


def test_l2_signature_unpadded_by_default_and_needs_no_retry():
    """pad_signature defaults to False (the client's historical, still-default behavior)."""
    fake = FakePred(pad=False)
    auth, transport = _stack(fake)
    auth.ensure_credentials(0)
    assert transport.account_status()["proxy_wallet_address"] == SAFE
    assert auth.pad_signature is False
    assert sum(r.url.path.endswith("account-status") for r in fake.requests) == 1


def test_l2_padding_mismatch_is_recovered_once_on_a_read_only_get():
    """The docs do not state padding. If PRED answers invalid_l2 to the first read, retry once
    with the other encoding (here: PRED only accepts padded, the client defaults to unpadded)."""
    fake = FakePred(pad=True)
    auth, transport = _stack(fake)
    auth.ensure_credentials(0)

    assert transport.account_status()["next_step"] == "ready"
    assert auth.pad_signature is True
    assert sum(r.url.path.endswith("account-status") for r in fake.requests) == 2

    transport.get("/clob/api/v1/portfolio/open-orders")  # calibrated: no further flip/retry
    assert sum(r.url.path.endswith("open-orders") for r in fake.requests) == 1


def test_orders_are_never_retried_on_an_l2_failure():
    fake = FakePred(pad=True)
    auth, transport = _stack(fake)
    auth.ensure_credentials(0)
    with pytest.raises(PredTransportError):
        transport.post(f"/clob/api/v1/order/{PARENT}/place", json={"salt": "1"})
    posts = [r for r in fake.requests if r.method == "POST" and r.url.path.endswith("/place")]
    assert len(posts) == 1 and auth.pad_signature is False  # no retry, no encoding change


def test_rejected_order_is_failed_not_unknown_and_sent_once(monkeypatch):
    fake = FakePred(pad=True)
    runtime = _arm(fake, monkeypatch)            # account-status calibrates pad_signature -> True
    assert runtime.executor.transport.auth.pad_signature is True
    runtime.executor.transport.auth.pad_signature = False  # force a bad L2 on the order itself
    runtime.executor.transport._l2_verified = True
    order = runtime.executor.place_from_proposal(proposal_id="p1", intent=_intent())
    assert order is not None and order.state == OrderState.FAILED  # 401 = definitely not placed
    assert sum(1 for r in fake.requests if r.url.path.endswith("/place")) == 1
    assert not runtime.executor.sm.is_market_blocked(MARKET)


# ── CLOB-vs-JWT selection ───────────────────────────────────────────────────


def test_pred_api_key_never_switches_to_jwt(monkeypatch):
    monkeypatch.delenv("PRED_AUTH_MODE", raising=False)
    monkeypatch.setenv("PRED_API_KEY", "ordinary-login-key")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.pred_api_key == "ordinary-login-key"
    assert resolve_auth_mode(s) == "clob"


@pytest.mark.parametrize(("raw", "expected"), [("jwt", "jwt"), (" JWT ", "jwt"), ("", "clob"), ("CLOB", "clob")])
def test_auth_mode_setting_is_normalised(monkeypatch, raw, expected):
    monkeypatch.setenv("PRED_AUTH_MODE", raw)
    assert resolve_auth_mode(Settings(_env_file=None)) == expected  # type: ignore[call-arg]


def test_live_arming_refuses_jwt_mode(monkeypatch):
    fake = FakePred()
    monkeypatch.setattr(le_mod, "load_settings", lambda: SimpleNamespace(live_trading=True))
    _, transport = _stack(fake)
    with pytest.raises(LiveSetupError, match="PRED_AUTH_MODE=clob"):
        setup_live(settings=_settings(pred_auth_mode="jwt"), transport=transport,
                   market_lookup=lambda m: None, max_discovery_age_s=60.0)
    assert fake.requests == []  # refused before any network call


def test_status_reports_the_real_auth_mode(monkeypatch):
    from pred_agent.control_api.controller import AgentController

    monkeypatch.setenv("PRED_API_KEY", "ordinary-login-key")
    monkeypatch.delenv("PRED_AUTH_MODE", raising=False)
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    assert AgentController(Settings(_env_file=None)).status()["auth_mode"] == "clob"  # type: ignore[call-arg]


# ── enablement CLI gets CLOB credentials ────────────────────────────────────


def _patch_cli(monkeypatch, fake: FakePred) -> None:
    from pred_agent.enablement import cli

    monkeypatch.setenv("PRIVATE_KEY", KEY)
    monkeypatch.delenv("PRED_AUTH_MODE", raising=False)
    monkeypatch.setenv("PRED_API_KEY", "ordinary-login-key")  # present, but must not select JWT
    monkeypatch.setattr(
        cli, "ClobAuth", lambda **kw: ClobAuth(**kw, http_client=fake.client()))
    monkeypatch.setattr(
        cli, "ClobTransport", lambda auth, base_url: ClobTransport(auth, base_url, http_client=fake.client()))


def test_enablement_cli_obtains_clob_credentials_before_calling_pred(monkeypatch):
    from pred_agent.enablement import cli

    fake = FakePred()
    _patch_cli(monkeypatch, fake)
    _, transport = cli._load_transport()
    assert isinstance(transport, ClobTransport) and transport.auth.credentials is not None
    assert transport.account_status()["next_step"] == "ready"  # a valid L2 request
    assert fake.violations == []


def test_enablement_cli_reports_the_blocker_instead_of_crashing(monkeypatch, capsys, tmp_path):
    from pred_agent.enablement import cli

    fake = FakePred(known_eoa=None)
    _patch_cli(monkeypatch, fake)
    assert cli.main(["--prepare", "--state-file", str(tmp_path / "s.json")]) == 1
    assert "CLOB access blocked" in capsys.readouterr().err
    assert fake.account_creations == 0


# ── safety defaults ─────────────────────────────────────────────────────────


def test_live_trading_is_off_by_default_and_executor_refuses_to_exist(monkeypatch):
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    assert Settings(_env_file=None).live_trading is False  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="LIVE_TRADING=true"):
        LiveExecutor(transport=_stack(FakePred())[1], state_machine=OrderStateMachine(),
                     eoa_address=EOA, signer_private_key=KEY)
    with pytest.raises(LiveSetupError, match="LIVE_TRADING is not true"):
        setup_live(settings=_settings(live_trading=False), transport=None,
                   market_lookup=lambda m: None, max_discovery_age_s=60.0)


def test_render_blueprint_keeps_live_off_and_has_no_partner_key():
    text = (Path(__file__).resolve().parents[2] / "render.yaml").read_text()
    env = {e["key"]: e for e in yaml.safe_load(text)["services"][0]["envVars"]}
    assert env["LIVE_TRADING"]["value"] == "false"
    assert env["PRED_AUTH_MODE"]["value"] == "clob"
    assert "PRED_PARTNER_KEY" not in env
    assert env["PRED_API_KEY"].get("sync") is False  # optional, entered by hand, never committed
