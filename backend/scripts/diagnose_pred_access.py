"""scripts/diagnose_pred_access.py - read-only JWT account-access check (redacted report).

Does exactly: login-with-signature -> read balance. It NEVER places/cancels an order, never
calls any /clob account-creation endpoint, never sends a partner key, and never prints keys,
tokens, signatures or full responses.

Login is an authentication call that PRED answers with ``is_new_user``; because the docs do not
say whether an unknown EOA is auto-provisioned, you must opt in explicitly with --allow-login.

Usage:  PYTHONPATH=src PRIVATE_KEY=0x... PRED_API_KEY=... [PRED_PROXY_ADDRESS=0x...] \\
            python scripts/diagnose_pred_access.py --allow-login
"""

from __future__ import annotations

import sys
from typing import Any

from pred_agent.config import load_settings
from pred_agent.signer.eip712 import private_to_address
from pred_agent.transport.protocol import PredTransportError


def diagnose(transport: Any, eoa: str, expected_proxy: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {"eoa": eoa, "auth_path": "jwt (ordinary login, X-API-Key)"}
    try:
        status = transport.account_status()
    except PredTransportError as exc:
        report["login"] = {"ok": False, "error": str(exc)[:160]}
        report["verdict"] = "login_failed"
        return report
    report["login"] = {"ok": True}
    proxy = str(status.get("proxy_wallet_address") or "")
    report["proxy_wallet_address"] = proxy or None
    report["is_enabled_trading"] = status.get("is_enabled_trading")
    report["is_new_user"] = status.get("is_new_user")
    report["eoa_matches"] = str(status.get("eoa") or "").lower() == eoa.lower()
    if expected_proxy:
        report["proxy_matches_expected"] = proxy.lower() == expected_proxy.lower()
    try:
        body = transport.get_balance()
        if body.get("success") is True and body.get("usdc_balance") is not None:
            report["balance"] = {"ok": True, "usdc_balance": str(body["usdc_balance"])}
        else:
            report["balance"] = {"ok": False, "error": str(body.get("error") or body.get("message") or "no balance in reply")[:120]}
    except PredTransportError as exc:
        # An API failure is reported as a failure, never as a zero balance.
        report["balance"] = {"ok": False, "http": exc.status}
    if report.get("proxy_matches_expected") is False or report["eoa_matches"] is False:
        report["verdict"] = "wrong_account"
    elif report["is_new_user"] is True and not expected_proxy:
        report["verdict"] = "new_user_not_your_existing_account"
    elif not proxy:
        report["verdict"] = "no_proxy_wallet"
    elif report["is_enabled_trading"] is not True:
        report["verdict"] = "authenticated_but_trading_not_enabled"
    else:
        report["verdict"] = "ready_to_trade_when_live_trading_enabled"
    return report


_VERDICTS = {
    "login_failed": "Login was rejected. Check PRED_API_KEY (ordinary-login key) and that PRIVATE_KEY is your account's EOA.",
    "wrong_account": "PRED returned a different EOA/Safe than expected. Do NOT trade; check PRIVATE_KEY / PRED_PROXY_ADDRESS.",
    "new_user_not_your_existing_account": "PRED flagged this EOA as a new user. Do NOT trade; check PRIVATE_KEY.",
    "no_proxy_wallet": "Account has no Safe yet; finish account setup on PRED first.",
    "authenticated_but_trading_not_enabled": "Authentication works but PRED reports is_enabled_trading=false: complete Safe approval, then re-login.",
    "ready_to_trade_when_live_trading_enabled": "Account can trade. LIVE_TRADING is still controlled only by you.",
}


def render(report: dict[str, Any]) -> str:
    lines = [f"EOA: {report['eoa']}   path: {report['auth_path']}"]
    for key in ("login", "proxy_wallet_address", "is_enabled_trading", "is_new_user", "eoa_matches",
                "proxy_matches_expected", "balance"):
        if key in report:
            lines.append(f"- {key}: {report[key]}")
    lines.append(f"\nRESULT: {_VERDICTS[report['verdict']]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--allow-login" not in argv:
        print("Refusing to run without --allow-login (login may be answered with is_new_user).")
        return 2
    settings = load_settings()
    if not settings.private_key or not settings.pred_api_key:
        print("PRIVATE_KEY and PRED_API_KEY must both be set")
        return 1
    from pred_agent.transport.factory import build_transport

    settings_mode = settings.pred_auth_mode
    if settings_mode == "clob":
        print("PRED_AUTH_MODE=clob: this diagnostic only covers the JWT path")
        return 1
    transport = build_transport(settings)
    eoa = private_to_address(settings.private_key)
    print(render(diagnose(transport, eoa, settings.pred_proxy_address)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
