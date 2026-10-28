import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from issue_clob_credentials import CONFIRMATION, issue_credentials  # noqa: E402

from pred_agent.auth.clob_auth import ClobAuth  # noqa: E402

KEY = "0x" + "11" * 32


def _auth(handler) -> ClobAuth:
    from pred_agent.signer.eip712 import private_to_address

    return ClobAuth(
        eoa_address=private_to_address(KEY),
        private_key=KEY,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_explicit_issue_requires_confirmation_without_sending_request() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    try:
        issue_credentials(_auth(handler), "no")
    except ValueError as exc:
        assert "ISSUE EXISTING ACCOUNT CREDENTIALS" in str(exc)
    else:
        raise AssertionError("confirmation should be required")
    assert calls == 0


def test_explicit_issue_sends_only_l1_headers_for_existing_account() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(200, json={"apiKey": "k", "secret": "c2Vj", "passphrase": "p"})

    assert issue_credentials(_auth(handler), CONFIRMATION) == {"ok": True}
    assert [request.url.path for request in seen] == ["/clob/time", "/clob/auth/api-key"]
    assert "x-aggregator-api-key" not in seen[1].headers
    assert "PRED_SIGNATURE" in seen[1].headers


@pytest.mark.parametrize("status, code", [(400, "invalid_l1"), (401, "invalid_auth"), (403, "permission_denied"), (503, "auth_unavailable")])
def test_explicit_issue_preserves_rejected_authentication(status: int, code: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(status, json={"error": code})

    with pytest.raises(httpx.HTTPStatusError) as raised:
        issue_credentials(_auth(handler), CONFIRMATION)

    assert raised.value.response.status_code == status
    assert raised.value.response.json()["error"] == code
    assert [request.url.path for request in seen] == ["/clob/time", "/clob/auth/api-key"]
