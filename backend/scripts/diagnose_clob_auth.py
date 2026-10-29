"""scripts/diagnose_clob_auth.py — CLOB access check; prints a redacted report for PRED support.

Tries exactly what production CLOB auth tries, in the same order: GET derive-api-key, then
POST api-key with L1 headers and NO partner key (PRED issues credentials that way only to an
existing eligible account, and this never opens a new one). It never sends X-Aggregator-API-Key
and never prints private keys or credentials, only PRED's own (redacted) error codes. If both
are refused, it also retries once with a lowercase PRED_ADDRESS header to rule out an
address-format problem.
Usage:  PYTHONPATH=src PRIVATE_KEY=0x... python scripts/diagnose_clob_auth.py
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx

from pred_agent.auth.clob_auth import ClobAuth
from pred_agent.config import load_settings
from pred_agent.signer.eip712 import private_to_address
from pred_agent.util.redaction import redact


def _outcome(call: Any) -> dict[str, Any]:
    try:
        call()
        return {"ok": True}
    except httpx.HTTPStatusError as exc:
        try:
            data = json.loads(exc.response.text)
            err = data.get("error", "") if isinstance(data, dict) else ""
            code = err.get("code", "") if isinstance(err, dict) else str(err)
        except ValueError:
            code = ""
        return {"ok": False, "http": exc.response.status_code, "code": redact(code)[:120]}
    except Exception as exc:  # noqa: BLE001 - diagnostic must not crash
        return {"ok": False, "error": type(exc).__name__}


def _probe(auth: ClobAuth) -> dict[str, Any]:
    return {
        "derive": _outcome(lambda: auth.derive_credentials(0)),
        "create": _outcome(lambda: auth.create_credentials(0)),
    }


def _any_ok(probe: dict[str, Any]) -> bool:
    return bool(probe["derive"]["ok"] or probe["create"]["ok"])


def diagnose(auth: ClobAuth, eoa: str, lowercase_auth: ClobAuth | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {"eoa": eoa, "checked_at_utc": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())}
    report["clock"] = _outcome(auth.sync_clock)
    report.update(_probe(auth))
    if lowercase_auth is not None and not _any_ok(report):
        report["lowercase_address"] = _probe(lowercase_auth)
    return report


def _fmt(r: dict[str, Any]) -> str:
    return "OK" if r["ok"] else f"FAILED {r.get('http', '')} {r.get('code') or r.get('error', '')}".strip()


def render(report: dict[str, Any]) -> str:
    lines = [f"EOA: {report['eoa']}   time: {report['checked_at_utc']}"]
    lines.append(f"- clock: {_fmt(report['clock'])}")
    lines.append(f"- derive-api-key: {_fmt(report['derive'])}")
    lines.append(f"- create-api-key (no partner key): {_fmt(report['create'])}")
    lower = report.get("lowercase_address")
    if lower is not None:
        lines.append(f"- lowercase PRED_ADDRESS derive: {_fmt(lower['derive'])}")
        lines.append(f"- lowercase PRED_ADDRESS create: {_fmt(lower['create'])}")
    if _any_ok(report):
        lines.append("\nRESULT: this EOA is an eligible existing account. CLOB auth works.")
    elif lower is not None and _any_ok(lower):
        lines.append(
            "\nRESULT: PRED accepted the LOWERCASE address but not the checksummed one: its account "
            "lookup is case-sensitive. Report this to PRED."
        )
    else:
        lines.append(
            "\nRESULT: PRED does not treat this EOA as an existing eligible account's primary wallet "
            "(server-side decision; not a signing or clock problem).\n"
            "Per PRED's docs: the EOA must be the account's PRIMARY wallet, and market-maker /\n"
            "administrator accounts are not eligible. Send the lines above to PRED support and ask\n"
            "them to enable CLOB access for this EOA (or tell you the account's primary wallet).\n"
            "This agent never creates a new account. Do NOT include keys or secrets."
        )
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    if not settings.private_key:
        print("PRIVATE_KEY not set")
        return 1
    eoa = private_to_address(settings.private_key)
    kwargs: dict[str, Any] = {"private_key": settings.private_key, "chain_id": settings.chain_id,
                              "base_url": settings.host}
    auth = ClobAuth(eoa_address=eoa, **kwargs)
    lower = ClobAuth(eoa_address=eoa.lower(), **kwargs) if eoa != eoa.lower() else None
    print(render(diagnose(auth, eoa, lower)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
