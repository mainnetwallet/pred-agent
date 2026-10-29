import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from diagnose_clob_auth import diagnose, render  # noqa: E402

from pred_agent.auth.clob_auth import ClobAuth  # noqa: E402

KEY = "0x" + "11" * 32


def _auth(handler, address: str | None = None) -> ClobAuth:
    from pred_agent.signer.eip712 import private_to_address

    return ClobAuth(eoa_address=address or private_to_address(KEY), private_key=KEY,
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
    assert "aggregator_key_required" in out and "does not treat this EOA" in out
    assert all("x-aggregator-api-key" not in {k.lower() for k in r.headers} for r in seen)
    assert KEY not in out
    paths = [r.url.path for r in seen if r.url.path != "/clob/time"]
    assert paths == ["/clob/auth/derive-api-key", "/clob/auth/api-key"]


def test_eligible_account_reported_ok() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(200, json={"apiKey": "k", "secret": "c2Vj", "passphrase": "p"})

    auth = _auth(handler)
    assert "CLOB auth works" in render(diagnose(auth, auth._eoa_address))


def test_lowercase_fallback_probe_runs_only_when_both_fail_and_reports_case_sensitivity() -> None:
    eoa = "0xABCDEF1234567890ABCDEF1234567890ABCDEF12"
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        if req.headers.get("PRED_ADDRESS") == eoa.lower():
            return httpx.Response(200, json={"apiKey": "k", "secret": "c2Vj", "passphrase": "p"})
        return httpx.Response(401, json={"error": "aggregator_key_required"})

    checksummed = _auth(handler, eoa)
    lower = _auth(handler, eoa.lower())
    report = diagnose(checksummed, eoa, lower)
    out = render(report)
    assert "case-sensitive" in out
    assert any(r.headers.get("PRED_ADDRESS") == eoa.lower() for r in seen)


def test_lowercase_probe_is_skipped_when_the_checksummed_address_already_works() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/clob/time":
            return httpx.Response(200, json=1_800_000_000)
        return httpx.Response(200, json={"apiKey": "k", "secret": "c2Vj", "passphrase": "p"})

    eoa = "0xABCDEF1234567890ABCDEF1234567890ABCDEF12"
    report = diagnose(_auth(handler, eoa), eoa, _auth(handler, eoa.lower()))
    assert "lowercase_address" not in report
