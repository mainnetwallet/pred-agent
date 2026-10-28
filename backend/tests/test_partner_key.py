"""PRED_PARTNER_KEY is strictly opt-in: only used to create a NEW account after derive and
create-without-key were both refused with aggregator_key_required."""

from __future__ import annotations

import httpx
import pytest

from pred_agent.auth.clob_auth import ClobAuth
from pred_agent.config import Settings

KEY = "0x" + "11" * 32
EOA = "0x236Fd063A5f03B996745F63D9DbA655bdd55999A"
PARTNER = "partner-secret-123"
CREDS = {"apiKey": "k", "secret": "c2VjcmV0", "passphrase": "p"}


def _auth(handler, partner: str | None) -> ClobAuth:
    return ClobAuth(
        eoa_address=EOA,
        private_key=KEY,
        aggregator_api_key=partner,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _refuse(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": "aggregator_key_required"})


def test_without_partner_key_never_sends_it_and_raises() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _refuse(request)

    auth = _auth(handler, partner=None)
    with pytest.raises(httpx.HTTPStatusError):
        auth.ensure_credentials(0)
    assert [r.url.path for r in seen] == ["/clob/auth/derive-api-key", "/clob/auth/api-key"]
    assert all("X-Aggregator-API-Key" not in r.headers for r in seen)


def test_partner_key_used_only_for_create_after_both_refusals() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/clob/auth/api-key" and "X-Aggregator-API-Key" in request.headers:
            return httpx.Response(200, json=CREDS)
        return _refuse(request)

    auth = _auth(handler, partner=PARTNER)
    creds = auth.ensure_credentials(0)
    assert creds.api_key == "k"
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/clob/auth/derive-api-key"),
        ("POST", "/clob/auth/api-key"),
        ("POST", "/clob/auth/api-key"),
    ]
    # the key is never sent on derive or on the first create attempt
    assert "X-Aggregator-API-Key" not in seen[0].headers
    assert "X-Aggregator-API-Key" not in seen[1].headers
    assert seen[2].headers["X-Aggregator-API-Key"] == PARTNER


def test_existing_account_derive_success_never_uses_partner_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CREDS)

    auth = _auth(handler, partner=PARTNER)
    auth.ensure_credentials(0)
    assert [r.url.path for r in seen] == ["/clob/auth/derive-api-key"]


def test_other_create_errors_do_not_trigger_partner_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("derive-api-key"):
            return httpx.Response(401, json={"error": "invalid_auth"})
        return httpx.Response(401, json={"error": "invalid_l1"})

    auth = _auth(handler, partner=PARTNER)
    with pytest.raises(httpx.HTTPStatusError):
        auth.ensure_credentials(0)
    assert all("X-Aggregator-API-Key" not in r.headers for r in seen)


def test_partner_key_setting_is_separate_and_blank_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRED_PARTNER_KEY", "   ")
    monkeypatch.setenv("PRED_API_KEY", "login-key")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.pred_partner_key is None
    assert s.pred_api_key == "login-key"
    monkeypatch.setenv("PRED_PARTNER_KEY", " abc ")
    assert Settings(_env_file=None).pred_partner_key == "abc"  # type: ignore[call-arg]
