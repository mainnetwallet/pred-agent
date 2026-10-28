"""scripts/diagnose_clob_auth.py — read-only CLOB L1 check; prints a redacted report for PRED support.

Tries only ``derive-api-key``. Credential issuance is intentionally a separate,
confirmation-gated operator action in ``issue_clob_credentials.py``.
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


def diagnose(auth: ClobAuth, eoa: str) -> dict[str, Any]:
    report: dict[str, Any] = {"eoa": eoa, "checked_at_utc": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())}
    report["clock"] = _outcome(auth.sync_clock)
    report["derive"] = _outcome(lambda: auth.derive_credentials(0))
    return report


def render(report: dict[str, Any]) -> str:
    lines = [f"EOA: {report['eoa']}   time: {report['checked_at_utc']}"]
    for step in ("clock", "derive"):
        r = report[step]
        lines.append(f"- {step}: " + ("OK" if r["ok"] else f"FAILED {r.get('http', '')} {r.get('code') or r.get('error', '')}".strip()))
    if report["derive"]["ok"]:
        lines.append("\nRESULT: this EOA has recoverable existing CLOB credentials.")
    else:
        lines.append(
            "\nRESULT: existing CLOB credentials were not recovered. This diagnostic did not request issuance.\n"
            "Send the lines above to PRED support and ask them to confirm eligibility and the primary wallet.\n"
            "Do NOT include keys or secrets."
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
