"""Explicitly issue L2 credentials for an existing eligible CLOB account.

This command never sends a partner key and never activates an account or creates a
Safe. It must be invoked with ``--confirm-existing-account`` after verifying that
the EOA is the account's existing primary wallet.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

from pred_agent.auth.clob_auth import ClobAuth
from pred_agent.config import load_settings
from pred_agent.signer.eip712 import private_to_address
from pred_agent.util.redaction import redact

CONFIRMATION = "ISSUE EXISTING ACCOUNT CREDENTIALS"


def issue_credentials(auth: ClobAuth, confirmation: str) -> dict[str, Any]:
    """Issue credentials only after an exact operator confirmation."""
    if confirmation != CONFIRMATION:
        raise ValueError(f" type {CONFIRMATION!r} to confirm this operation")
    auth.sync_clock()
    auth.create_credentials(nonce=0)
    return {"ok": True}


def _safe_failure(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        code = ""
        try:
            data = response.json()
            error = data.get("error") or data.get("code") if isinstance(data, dict) else ""
            code = error.get("code", "") if isinstance(error, dict) else str(error)
        except (ValueError, TypeError):
            pass
        return {"ok": False, "http": response.status_code, "code": redact(code)[:120]}
    return {"ok": False, "error": type(exc).__name__}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-existing-account", action="store_true")
    args = parser.parse_args(argv)
    print(
        "WARNING: this sends one L1 credential-issuance request for the existing EOA.\n"
        "It does not create a Safe or activate an account, but PRED may reject an ineligible EOA.\n"
        "No partner key is sent. Live trading remains unchanged."
    )
    if not args.confirm_existing_account:
        print("Re-run with --confirm-existing-account to confirm this operation.")
        return 2
    settings = load_settings()
    if not settings.private_key:
        print("PRIVATE_KEY not set")
        return 1
    eoa = private_to_address(settings.private_key)
    auth = ClobAuth(eoa_address=eoa, private_key=settings.private_key, chain_id=settings.chain_id)
    try:
        result = issue_credentials(auth, CONFIRMATION)
    except Exception as exc:  # noqa: BLE001 - CLI reports only sanitized diagnostics
        result = _safe_failure(exc)
    print(f"EOA: {eoa}")
    print("RESULT: " + ("credentials issued" if result["ok"] else f"FAILED {result}"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
