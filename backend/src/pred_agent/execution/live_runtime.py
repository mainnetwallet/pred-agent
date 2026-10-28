"""Live runtime — builds the LiveExecutor stack and exposes what the controller needs.

Everything here fails closed: if any setup step cannot be confirmed (credentials, Safe
address, account readiness, startup reconcile) ``setup_live`` raises ``LiveSetupError`` and
the controller keeps refusing LIVE approvals. Nothing here can turn LIVE_TRADING on; the
mode comes from frozen settings only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from pred_agent.book import BookSnapshot, normalize_book
from pred_agent.execution.live_executor import LiveExecutor
from pred_agent.execution.state_machine import OrderRecord, OrderState, OrderStateMachine

log = logging.getLogger(__name__)

# Cancelling is only meaningful for orders that may still rest on the book.
_CANCELLABLE = frozenset({
    OrderState.SUBMITTED, OrderState.DELAYED, OrderState.OPEN, OrderState.PARTIAL,
})


class LiveSetupError(RuntimeError):
    """Live trading could not be armed; the reason is safe to show to the operator."""


@dataclass(frozen=True)
class MarketContext:
    """Discovery facts needed to sign and place an order on one child market."""

    market_id: str
    parent_market_id: str
    contract_address: str | None
    fee_target_bps: int | None
    active: bool
    inactive_reason: str | None
    age_s: float  # seconds since the discovery snapshot this came from


MarketLookup = Callable[[str], MarketContext | None]


def make_book_provider(host: str, *, timeout_s: float = 8.0) -> Callable[[str, str], BookSnapshot | None]:
    """Fresh, synchronous, unauthenticated REST order book (docs: orderbooks are public).

    Prices are cents in REST. Anything outside (0, 100] cents means the units are not what
    we think, so the book is rejected rather than used.
    """
    base = host.rstrip("/")

    def provider(parent_market_id: str, market_id: str) -> BookSnapshot | None:  # noqa: PLR0911
        url = f"{base}/api/v1/order/{parent_market_id}/orderbook/{market_id}"
        try:
            resp = httpx.get(url, params={"depth": 10, "include_metadata": "true"}, timeout=timeout_s)
        except httpx.HTTPError:
            log.warning("live book fetch failed for %s", market_id, exc_info=True)
            return None
        if resp.status_code >= 400:
            return None
        try:
            raw: Any = resp.json()
        except ValueError:
            return None
        if isinstance(raw, dict) and "bids" not in raw and isinstance(raw.get("data"), dict):
            raw = raw["data"]
        if not isinstance(raw, dict) or "bids" not in raw or "asks" not in raw:
            return None
        try:
            snap = normalize_book(raw, market_id, ws_dollars=False)
        except (ArithmeticError, ValueError, TypeError):
            return None
        for lvl in (*snap.bids, *snap.asks):
            if not Decimal(0) < lvl.price_cents <= Decimal(100):
                return None
        return snap

    return provider


class LiveRuntime:
    """Controller-facing wrapper: PaperExecutor-like list/cancel surface over LiveExecutor."""

    def __init__(self, executor: LiveExecutor, *, startup_report: dict[str, Any]) -> None:
        self.executor = executor
        self.startup_report = startup_report

    # --- PaperExecutor-compatible surface (used by the API for listing/cancelling) ---

    def list_orders(self) -> list[OrderRecord]:
        return self.executor.sm.list_orders()

    def cancel(self, order_id: str) -> bool:
        rec = self.executor.sm.get(order_id)
        if rec is None or rec.state not in _CANCELLABLE:
            return False
        if not rec.market_id or not rec.parent_market_id:
            return False
        result = self.executor.cancel(order_id, rec.market_id, rec.parent_market_id)
        return result.get("status") == "user_cancelled"

    def place(self, **_: Any) -> Any:
        """Live orders are only placed through an approved proposal, never directly."""
        raise RuntimeError("direct order placement is disabled in LIVE mode")

    def cancel_everything(self) -> dict[str, int]:
        """Panic: cancel all open orders in every parent we know about. Never raises."""
        parents = {
            o.parent_market_id for o in self.executor.sm.list_orders()
            if o.parent_market_id and o.state in (_CANCELLABLE | {OrderState.UNKNOWN})
        }
        cancelled = failed = 0
        for parent in sorted(parents):
            try:
                res = self.executor.cancel_all_per_parent(parent)
            except Exception:  # noqa: BLE001 - panic must keep going
                log.error("panic cancel_all failed for parent %s", parent, exc_info=True)
                failed += 1
                continue
            cancelled += int(res.get("total_cancelled", 0) or 0)
            failed += int(res.get("total_failed", 0) or 0)
            if res.get("status") == "error":
                failed += 1
        return {"cancelled": cancelled, "failed": failed, "parents": len(parents)}


# PRED CLOB auth error codes (docs: Builder / Aggregator Onboarding -> Errors and retries).
_AUTH_HINTS = {
    "invalid_l1": "check ClobAuth signature: chain ID, timestamp (within 30s), nonce, and that PRIVATE_KEY is the account EOA",
    "invalid_l2": "check L2 key/passphrase/secret, uppercase method, canonical path and body",
    "invalid_auth": "credential revoked, account not eligible, or this EOA is not the account's primary wallet",
    "aggregator_key_required": (
        "PRED CLOB does not recognise this EOA as an existing eligible account (derive and "
        "create-without-partner-key were both refused). Check that PRIVATE_KEY is the primary wallet "
        "of your PRED account; a PRED-issued PRED_PARTNER_KEY would only create a NEW empty account "
        "with a different Safe"
    ),
    "nonce_already_used": "credentials already issued for nonce 0; derive with fresh L1 headers",
}


def describe_auth_error(exc: BaseException) -> str:
    """Human-readable cause for a CLOB auth failure: HTTP status + PRED error code + hint.

    ``httpx.HTTPStatusError`` alone hides everything useful, so read the response body.
    """
    import json as _json

    response = getattr(exc, "response", None)
    if response is None:
        return f"{type(exc).__name__}: {exc}"[:200]
    status = getattr(response, "status_code", "?")
    code = ""
    try:
        data = _json.loads(response.text)
        if isinstance(data, dict):
            err = data.get("error") or data.get("code") or data.get("message") or ""
            if isinstance(err, dict):
                err = err.get("code") or err.get("message") or ""
            code = str(err)[:120]
    except ValueError:
        pass
    hint = next((h for k, h in _AUTH_HINTS.items() if k in code), "")
    if not hint and status == 429:
        hint = "rate limited; retry later"
    elif not hint and isinstance(status, int) and status >= 500:
        hint = "PRED server error; retry later"
    elif not hint and status in (401, 403):
        hint = "authentication rejected; verify PRIVATE_KEY, partner key, and that the account exists"
    out = f"HTTP {status}"
    if code:
        out += f" {code}"
    if hint:
        out += f" ({hint})"
    return out


def setup_live(
    *,
    settings: Any,
    transport: Any,
    market_lookup: MarketLookup,
    max_discovery_age_s: float,
) -> LiveRuntime:
    """Arm live trading or raise ``LiveSetupError`` explaining what is missing."""
    from pred_agent.signer.eip712 import private_to_address

    if not getattr(settings, "live_trading", False):
        raise LiveSetupError("LIVE_TRADING is not true")
    private_key = getattr(settings, "private_key", None)
    if not private_key:
        raise LiveSetupError("PRIVATE_KEY is not set")
    auth = getattr(transport, "auth", None)
    if auth is None or not hasattr(transport, "account_status"):
        raise LiveSetupError("no authenticated PRED transport (set PRIVATE_KEY and PRED_API_KEY or CLOB access)")

    eoa = private_to_address(private_key)
    is_jwt = hasattr(auth, "login") and not hasattr(auth, "derive_credentials")
    try:
        if not is_jwt:
            # CLOB (aggregator) path: derive, else create L2 creds for an EXISTING account
            # (never a new account/quota).
            auth.sync_clock()
            if auth.credentials is None:
                (getattr(auth, "ensure_credentials", None) or auth.derive_credentials)(0)
        # JWT path: account_status() performs the real login-with-signature itself.
        status = transport.account_status()
    except Exception as exc:  # noqa: BLE001
        path = "JWT login" if is_jwt else "CLOB"
        raise LiveSetupError(
            f"could not authenticate with PRED ({path}): {describe_auth_error(exc)} [EOA {eoa}]"
        ) from exc

    proxy = str(status.get("proxy_wallet_address") or "").strip()
    if not proxy:
        raise LiveSetupError("account has no Safe proxy wallet yet; finish account setup first")
    remote_eoa = str(status.get("eoa") or "").lower()
    if remote_eoa and remote_eoa != eoa.lower():
        raise LiveSetupError("PRIVATE_KEY wallet does not match the PRED account's EOA")
    expected_proxy = str(getattr(settings, "pred_proxy_address", None) or "").strip().lower()
    if expected_proxy and expected_proxy != proxy.lower():
        raise LiveSetupError(
            f"PRED reports Safe {proxy} for this EOA but PRED_PROXY_ADDRESS is {expected_proxy}; "
            "refusing to trade from a different account"
        )
    if status.get("is_new_user") is True and not expected_proxy:
        raise LiveSetupError(
            "PRED reports this EOA as a NEW user (not your existing account). Refusing to trade; "
            "check PRIVATE_KEY, or set PRED_PROXY_ADDRESS to your existing Safe to confirm it"
        )
    if str(status.get("next_step") or "") != "ready":
        hint = (
            " (PRED reports is_enabled_trading=false: complete Safe approval, then re-login)"
            if is_jwt else ""
        )
        raise LiveSetupError(f"account not ready (next_step={status.get('next_step')!r}){hint}")

    def market_check(market_id: str) -> str | None:
        ctx = market_lookup(market_id)
        if ctx is None:
            return "market not found in the latest discovery scan"
        if ctx.age_s > max_discovery_age_s:
            return f"discovery data is stale ({ctx.age_s:.0f}s old)"
        if not ctx.active:
            return ctx.inactive_reason or "market is no longer active"
        return None

    executor = LiveExecutor(
        transport=transport,
        state_machine=OrderStateMachine(),
        eoa_address=eoa,
        signer_private_key=private_key,
        chain_id=settings.chain_id,
        proxy_address=proxy,
        book_provider=make_book_provider(settings.host),
        market_check=market_check,
        max_spread_cents=Decimal(str(settings.max_spread_cents)),
        max_slippage_pp=Decimal(str(settings.max_slippage_pp)),
    )
    try:
        report = executor.reconcile_startup()
    except Exception as exc:  # noqa: BLE001
        raise LiveSetupError(f"startup reconcile failed: {type(exc).__name__}: {exc}") from exc
    log.warning(
        "LIVE trading armed: maker=%s open_orders=%s adopted=%s",
        proxy, report["exchange_open_orders"], report["adopted"],
    )
    return LiveRuntime(executor, startup_report=report)
