"""Read-only PRED account view (JWT mode): wallet/Safe address and balances.

Uses ONLY the ordinary JWT credentials (``PRED_API_KEY`` + ``PRIVATE_KEY`` login). It is separate
from the CLOB/aggregator client, so JWT and CLOB credentials are never mixed. It places no orders.

States (``snapshot()["state"]``):
  not_configured  PRED_API_KEY and/or PRIVATE_KEY missing -> nothing was requested
  loading         configured, first refresh not finished yet
  ok              login + balance succeeded (``trading_enabled`` may still be False)
  unauthenticated login rejected (bad/missing API key, wrong wallet)
  session_expired access token rejected and refresh/re-login did not recover it
  error           network / PRED 4xx-5xx / unexpected response (reason in ``message``)

Money is only ever a value PRED returned. A failed request leaves cash as ``None``; it is
never replaced by 0. PRED's API reports no total-portfolio-value or unsettled-funds figure, so
those are not computed here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pred_agent.auth.jwt_auth import JwtAuth, JwtAuthError
from pred_agent.transport.jwt_transport import JwtTransport
from pred_agent.transport.protocol import PredTransportError
from pred_agent.util.redaction import redact

NOT_CONFIGURED = "not_configured"
LOADING = "loading"
OK = "ok"
UNAUTHENTICATED = "unauthenticated"
SESSION_EXPIRED = "session_expired"
ERROR = "error"

NOT_CONFIGURED_MESSAGE = (
    "JWT login is not configured. PRED requires PRED_API_KEY (X-API-Key) for login-with-signature, "
    "plus PRIVATE_KEY for the account wallet."
)
TRADING_DISABLED_MESSAGE = (
    "Trading is not enabled for this account's Safe. Complete the Safe approval, then log in again."
)
NOTES = (
    "Available cash is PRED's indexed USDC balance (net of open-order reservations; it can lag). "
    "Positions are shown at cost basis. PRED's API does not report total portfolio value or "
    "unsettled funds, so neither is shown."
)


def initial_snapshot(*, configured: bool) -> dict[str, Any]:
    """Snapshot before the first refresh (or when JWT mode is not configured)."""
    return {
        "state": LOADING if configured else NOT_CONFIGURED,
        "auth_mode": "jwt",
        "code": None if configured else "api_key_missing",
        "message": None if configured else NOT_CONFIGURED_MESSAGE,
        "wallet_address": None,
        "proxy_address": None,
        "trading_enabled": None,
        "cash_available_usd": None,
        "positions_cost_usd": None,
        "positions_payout_usd": None,
        "notes": NOTES,
        "updated_at": None,
    }


def _decimal(value: Any) -> str | None:
    """A finite non-negative decimal string, else None (never coerced to zero)."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d < 0:
        return None
    return format(d, "f")


def _pred_error(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        body = json.loads(raw)
    except ValueError:
        return redact(raw)[:120]
    if isinstance(body, dict):
        err = body.get("error") or body.get("message") or ""
        if isinstance(err, dict):
            err = err.get("code") or err.get("message") or ""
        return redact(str(err))[:120]
    return ""


class AccountService:
    """Blocking (run via ``asyncio.to_thread``); one instance owns one JwtAuth session."""

    def __init__(self, auth: JwtAuth, transport: JwtTransport) -> None:
        self.auth = auth
        self.transport = transport

    def refresh(self) -> dict[str, Any]:
        """Login if needed, fetch balance (+ optional positions totals); return a snapshot."""
        snap = initial_snapshot(configured=True)
        snap["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            balance = self.transport.get_balance()
        except JwtAuthError as exc:
            return self._failed(snap, exc)
        except PredTransportError as exc:
            # 401 here means the retry-after-refresh also failed.
            detail = _pred_error(exc.raw_body)
            state = SESSION_EXPIRED if exc.status in (401, 403) else ERROR
            snap.update(
                state=state,
                code=f"http_{exc.status}",
                message=(
                    "PRED rejected the session (HTTP %d)%s. Re-login failed to recover it."
                    if state == SESSION_EXPIRED
                    else "PRED balance request failed (HTTP %d)%s."
                ) % (exc.status, f": {detail}" if detail else ""),
            )
            self._fill_identity(snap)
            return snap
        except Exception as exc:  # noqa: BLE001 - never leak details; class name only
            snap.update(state=ERROR, code="unexpected", message=f"Balance lookup failed ({type(exc).__name__}).")
            self._fill_identity(snap)
            return snap

        self._fill_identity(snap)
        cash = _decimal(balance.get("usdc_balance")) if balance.get("success") is True else None
        if cash is None:
            err = balance.get("error") or balance.get("message") or "no usable usdc_balance in response"
            snap.update(state=ERROR, code="bad_balance",
                        message=f"PRED did not return a balance: {redact(str(err))[:120]}")
            return snap
        snap.update(state=OK, cash_available_usd=cash)
        if snap["trading_enabled"] is False:
            snap["message"] = TRADING_DISABLED_MESSAGE
            snap["code"] = "trading_disabled"
        self._fill_positions(snap)
        return snap

    def _fill_identity(self, snap: dict[str, Any]) -> None:
        snap.update(self.auth.account())

    def _fill_positions(self, snap: dict[str, Any]) -> None:
        """Optional; a failure here never hides or changes the cash balance."""
        try:
            body = self.transport.get_positions_summary()
        except Exception:  # noqa: BLE001
            return
        if body.get("success") is True:
            snap["positions_cost_usd"] = _decimal(body.get("total_invested"))
            snap["positions_payout_usd"] = _decimal(body.get("total_to_win"))

    def _failed(self, snap: dict[str, Any], exc: JwtAuthError) -> dict[str, Any]:
        if exc.code in ("api_key_missing", "private_key_missing"):
            state = NOT_CONFIGURED
        elif exc.code == "login_rejected":
            state = UNAUTHENTICATED
        else:
            state = ERROR
        snap.update(state=state, code=exc.code, message=exc.message)
        return snap
