import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from diagnose_clob_auth import diagnose, render  # noqa: E402

from pred_agent.auth.clob_auth import ClobAuth  # noqa: E402

KEY = "0x" + "11" * 32


def _auth(handler) -> ClobAuth:
    from pred_agent.signer.eip712 import private_to_address

    return ClobAuth(eoa_address=private_to_address(KEY), private_key=KEY,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_unknown_eoa_report_and_no_partner_key_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(401, json={"error": "aggregator_key_required"})

    auth = _auth(handler)
    report = diagnose(auth, auth._eoa_address)
    out = render(report)
    assert "aggregator_key_required" in out and "existing CLOB credentials were not recovered" in out
    assert all("x-aggregator-api-key" not in {k.lower() for k in r.headers} for r in seen)
    assert KEY not in out
    assert [r.url.path for r in seen] == ["/clob/time", "/clob/auth/derive-api-key"]


def test_eligible_account_reported_ok() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(200, json={"apiKey": "k", "secret": "c2Vj", "passphrase": "p"})

    auth = _auth(handler)
    assert "recoverable existing CLOB credentials" in render(diagnose(auth, auth._eoa_address))
