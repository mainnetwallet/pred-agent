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

    def provider(parent_market_id: str, market_id: str) -> BookSnapshot | None:
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
        raise LiveSetupError("live trading needs the CLOB transport (no PRED_API_KEY/JWT path wired)")

    eoa = private_to_address(private_key)
    try:
        auth.sync_clock()
        if auth.credentials is None:
            # Existing account: derive (never creates a new account or quota).
            auth.derive_credentials(0)
        status = transport.account_status()
    except Exception as exc:  # noqa: BLE001
        raise LiveSetupError(f"could not authenticate with PRED CLOB: {type(exc).__name__}") from exc

    proxy = str(status.get("proxy_wallet_address") or "").strip()
    if not proxy:
        raise LiveSetupError("account has no Safe proxy wallet yet; finish account setup first")
    if str(status.get("next_step") or "") != "ready":
        raise LiveSetupError(f"account not ready (next_step={status.get('next_step')!r})")
    remote_eoa = str(status.get("eoa") or "").lower()
    if remote_eoa and remote_eoa != eoa.lower():
        raise LiveSetupError("PRIVATE_KEY wallet does not match the PRED account's EOA")

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
