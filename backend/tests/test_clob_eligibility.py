"""CLOB eligibility is fail-closed and never creates a new account."""

# ruff: noqa: S105, S106

from __future__ import annotations

import httpx
import pytest

from pred_agent.auth.clob_auth import ClobAuth, ClobCredentials
from pred_agent.transport.clob_transport import ClobTransport

KEY = "0x" + "11" * 32
EOA = "0x236Fd063A5f03B996745F63D9DbA655bdd55999A"


def _auth(handler: httpx.MockTransport) -> ClobAuth:
    return ClobAuth(
        eoa_address=EOA,
        private_key=KEY,
        http_client=httpx.Client(transport=handler),
    )


def test_aggregator_key_required_never_retries_with_a_partner_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, json={"error": "aggregator_key_required"})

    with pytest.raises(httpx.HTTPStatusError):
        _auth(httpx.MockTransport(handler)).ensure_credentials(0)

    assert [request.url.path for request in seen] == ["/clob/auth/derive-api-key"]
    assert all("x-aggregator-api-key" not in request.headers for request in seen)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_l1"),
        (401, "invalid_auth"),
        (403, "permission_denied"),
        (404, "not_found"),
        (503, "temporary_failure"),
    ],
)
def test_derive_failures_preserve_error_and_never_issue_credentials(
    status: int, code: str
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"error": code})

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _auth(httpx.MockTransport(handler)).ensure_credentials(0)

    assert raised.value.response.status_code == status
    assert raised.value.response.json()["error"] == code
    assert [request.url.path for request in seen] == ["/clob/auth/derive-api-key"]
    assert all("x-aggregator-api-key" not in request.headers for request in seen)


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
    ).rstrip(b"=").decode()
    assert request.headers["PRED_SIGNATURE"] == expected
