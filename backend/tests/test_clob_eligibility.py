"""CLOB eligibility: derive-then-create (no partner key) only, never creates a new account."""

# ruff: noqa: S105, S106

from __future__ import annotations

import httpx
import pytest

from pred_agent.auth.clob_auth import ClobAccessBlocked, ClobAuth, ClobCredentials
from pred_agent.transport.clob_transport import ClobTransport

KEY = "0x" + "11" * 32
EOA = "0x236Fd063A5f03B996745F63D9DbA655bdd55999A"


def _auth(handler: httpx.MockTransport) -> ClobAuth:
    return ClobAuth(
        eoa_address=EOA,
        private_key=KEY,
        http_client=httpx.Client(transport=handler),
    )


def test_eligible_existing_account_can_derive_without_any_create_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(
            200, json={"apiKey": "clob-key", "secret": "c2VjcmV0", "passphrase": "pass"}
        )

    creds = _auth(httpx.MockTransport(handler)).ensure_credentials(0)
    assert creds.api_key == "clob-key"
    paths = [r.url.path for r in seen if r.url.path != "/clob/time"]
    assert paths == ["/clob/auth/derive-api-key"]


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "invalid_l1"), (401, "invalid_auth"), (403, "permission_denied"), (404, "not_found")],
)
def test_eligible_account_without_credentials_yet_falls_through_to_create(
    status: int, code: str
) -> None:
    """Docs: derive only recovers; an account made on the PRED site has none yet. An existing
    eligible user can issue them with L1 headers + a fresh nonce, with no partner key and no
    quota consumed — so the fallthrough must happen for every documented 'not found' style code."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        if request.url.path.endswith("derive-api-key"):
            return httpx.Response(status, json={"error": code})
        assert "x-aggregator-api-key" not in {k.lower() for k in request.headers}
        return httpx.Response(200, json={"apiKey": "k", "secret": "c2VjcmV0", "passphrase": "p"})

    creds = _auth(httpx.MockTransport(handler)).ensure_credentials(0)
    assert creds.api_key == "k"
    paths = [r.url.path for r in seen if r.url.path != "/clob/time"]
    assert paths == ["/clob/auth/derive-api-key", "/clob/auth/api-key"]


def test_derive_server_error_does_not_fall_through_to_create() -> None:
    """A 503 means "ask again later", not "no credentials yet" — don't mask it with a create call."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(503, json={"error": "temporary_failure"})

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _auth(httpx.MockTransport(handler)).ensure_credentials(0)

    assert raised.value.response.status_code == 503
    paths = [r.url.path for r in seen if r.url.path != "/clob/time"]
    assert paths == ["/clob/auth/derive-api-key"]


def test_unknown_eoa_raises_clob_access_blocked_and_never_sends_a_partner_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(401, json={"error": "aggregator_key_required"})

    with pytest.raises(ClobAccessBlocked) as info:
        _auth(httpx.MockTransport(handler)).ensure_credentials(0)

    paths = [r.url.path for r in seen if r.url.path != "/clob/time"]
    assert paths == ["/clob/auth/derive-api-key", "/clob/auth/api-key"]
    assert all("x-aggregator-api-key" not in {k.lower() for k in r.headers} for r in seen)
    text = info.value.explain()
    assert "derive-api-key -> HTTP 401" in text and "create-api-key (no partner key) -> HTTP 401" in text
    assert "aggregator_key_required" in text and "PRIMARY wallet" in text
    assert "No new account or Safe" in text
    assert isinstance(info.value, httpx.HTTPStatusError)  # existing except-clauses keep working


def test_explicit_credential_creation_for_eligible_account_has_no_partner_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/clob/auth/api-key"
        assert "x-aggregator-api-key" not in request.headers
        return httpx.Response(
            200, json={"apiKey": "clob-key", "secret": "c2VjcmV0", "passphrase": "pass"}
        )

    credentials = _auth(httpx.MockTransport(handler)).create_credentials(0)
    assert credentials.api_key == "clob-key"


def test_l2_signature_uses_exact_body_and_clob_canonical_path() -> None:
    auth = _auth(httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    auth._credentials = ClobCredentials(
        api_key="clob-key", secret="c2VjcmV0", passphrase="pass"
    )
    auth._now = lambda: 1_700_000_000  # type: ignore[method-assign]

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = ClobTransport(auth, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = transport.post("/clob/api/v1/order/parent/place?ignored=query", json={"b": 2, "a": 1})

    assert result == {"ok": True}
    request = captured[0]
    assert request.content == b'{"b":2,"a":1}'
    message = "1700000000POST/api/v1/order/parent/place" + '{"b":2,"a":1}'
    import base64
    import hashlib
    import hmac

    expected = base64.urlsafe_b64encode(
        hmac.new(b"secret", message.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()  # default pad_signature=False
    assert request.headers["PRED_SIGNATURE"] == expected
