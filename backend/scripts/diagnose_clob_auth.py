"""scripts/diagnose_clob_auth.py — read-only CLOB L1 check; prints a redacted report for PRED support.

Tries ONLY what cannot create an account: derive-api-key, then create-api-key WITHOUT a partner
key. Never sends X-Aggregator-API-Key, never prints private keys or credentials.
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


def _outcome(call: Any) -> dict[str, Any]:
    try:
        call()
        return {"ok": True}
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:200]
        try:
            data = json.loads(exc.response.text)
            err = data.get("error", "") if isinstance(data, dict) else ""
            code = err.get("code", "") if isinstance(err, dict) else str(err)
        except ValueError:
            code = ""
        return {"ok": False, "http": exc.response.status_code, "code": code, "body": body}
    except Exception as exc:  # noqa: BLE001 - diagnostic must not crash
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


def diagnose(auth: ClobAuth, eoa: str) -> dict[str, Any]:
    report: dict[str, Any] = {"eoa": eoa, "checked_at_utc": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())}
    report["clock"] = _outcome(auth.sync_clock)
    report["derive"] = _outcome(lambda: auth.derive_credentials(0))
    report["create_without_partner_key"] = _outcome(lambda: auth.create_credentials(0))
    return report


def render(report: dict[str, Any]) -> str:
    lines = [f"EOA: {report['eoa']}   time: {report['checked_at_utc']}"]
    for step in ("clock", "derive", "create_without_partner_key"):
        r = report[step]
        lines.append(f"- {step}: " + ("OK" if r["ok"] else f"FAILED {r.get('http', '')} {r.get('code') or r.get('error', '')}".strip()))
    if report["derive"]["ok"] or report["create_without_partner_key"]["ok"]:
        lines.append("\nRESULT: this EOA is an eligible existing account. CLOB auth works.")
    else:
        lines.append(
            "\nRESULT: PRED does not treat this EOA as an existing eligible account's primary wallet.\n"
            "Send the lines above to PRED support and ask them to enable CLOB access for this EOA\n"
            "(or tell you the account's primary wallet). Do NOT include keys or secrets."
        )
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    if not settings.private_key:
        print("PRIVATE_KEY not set")
        return 1
    eoa = private_to_address(settings.private_key)
    auth = ClobAuth(eoa_address=eoa, private_key=settings.private_key, chain_id=settings.chain_id)
    print(render(diagnose(auth, eoa)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
