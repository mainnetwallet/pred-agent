"""Background task loop — scanner wiring + rules monitor (fail-closed).

Grounded in ARCHITECTURE.md hard rules:
- The scanner refreshes the fixture cache, upserts to the DB, and tracks
  discovered markets in the book feed (CLOB poller).
- ACTIVE rules are evaluated against the book feed. Fail closed: a missing,
  stale or unreadable book never fires a rule — the rule goes BLOCKED
  (with operator alert) until fresh data returns.
- A BLOCKED stop-loss escalates after BLOCKED_ESCALATION_SECONDS.
- Events are published after state changes for the WS bridge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import sessionmaker

from pred_agent.book import BookSnapshot
from pred_agent.control_api.controller import AgentController
from pred_agent.events import Event, EventBus
from pred_agent.realtime import BookFeed
from pred_agent.rules import (
    BLOCKED_ESCALATION_SECONDS,
    STATUS_ACTIVE,
    STATUS_BLOCKED,
)
from pred_agent.scanner import DiscoveredParent, Scanner
from pred_agent.transport.protocol import PredTransportError
from pred_agent.util.redaction import redact

log = logging.getLogger(__name__)

AlertFn = Callable[[str], Awaitable[None]]


class _NullTransport:
    """Placeholder PredTransport used when no credentials are configured.

    BookFeed.fetch_book_rest tries an unauthenticated GET first and only falls
    back to transport.get() — this transport makes that fallback fail cleanly.
    """

    caps: Any = None

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raise RuntimeError("no PRED transport configured")

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        raise RuntimeError("no PRED transport configured")

    def delete(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        raise RuntimeError("no PRED transport configured")

    def raw_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, str, dict[str, Any]]:
        raise RuntimeError("no PRED transport configured")

    def health_check(self) -> bool:
        return False


def build_book_feed(settings: Any) -> BookFeed:
    """Build a BookFeed with the best available transport."""
    if getattr(settings, "private_key", None):
        try:
            from pred_agent.auth.clob_auth import ClobAuth
            from pred_agent.signer.eip712 import private_to_address
            from pred_agent.transport.clob_transport import ClobTransport

            auth = ClobAuth(
                eoa_address=private_to_address(settings.private_key),
                private_key=settings.private_key,
                chain_id=settings.chain_id,
                base_url=settings.host,
            )
            return BookFeed(ClobTransport(auth, base_url=settings.host))
        except Exception:
            log.warning("could not build CLOB transport; book feed runs unauthenticated", exc_info=True)
    return BookFeed(_NullTransport())


# ─────────────────────────────────────────────────────────────
# Scanner pass
# ─────────────────────────────────────────────────────────────

async def scan_once(
    *,
    scanner: Scanner,
    fixture_cache: Any,  # FixtureCache (lives in control_api.app)
    session_factory: sessionmaker[Any],
    book_feed: BookFeed | None,
    controller: AgentController,
    bus: EventBus | None = None,
) -> list[DiscoveredParent]:
    """One discovery pass: scan → cache → DB → book tracking → event."""
    try:
        results = await asyncio.to_thread(scanner.scan_active_moneyline)
    except Exception:
        controller.set_feed_diag(getattr(scanner, "last_scan", None) or {"error": "scan failed"})
        raise
    controller.set_feed_diag(getattr(scanner, "last_scan", None) or {})
    if not results:
        # Fail closed: an empty scan does not count as a healthy feed.
        log.warning(
            "scan_once found 0 active moneyline fixtures; feeds not marked fresh; stats=%s",
            getattr(scanner, "last_scan", {}),
        )
        return results

    fixture_cache.update(results)

    try:
        with session_factory() as session:
            scanner.upsert_to_db(session, results)
    except Exception:
        log.warning("fixture DB upsert failed; continuing with in-memory cache", exc_info=True)

    controller.mark_feed_updated()
    for parent in results:
        for m in parent.markets:
            mid = m.get("market_id")
            if mid and book_feed is not None:
                book_feed.track_market(mid, parent_market_id=parent.parent_market_id)

    if bus is not None:
        await bus.publish(Event("fixture_update", {
            "count": len(results),
            "parent_market_ids": [p.parent_market_id for p in results[:20]],
        }))
    log.info("scan_once discovered=%d", len(results))
    return results


def _error_from_raw(raw: str | None) -> str:
    """Best-effort error text from a PRED error body (redacted, truncated)."""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return redact(raw)[:200]
    if isinstance(data, dict):
        err = data.get("error") or data.get("message") or ""
        if isinstance(err, dict):
            err = err.get("code") or err.get("message") or ""
        return redact(str(err))[:200]
    return redact(raw)[:200]


_AUTH_RETRY_BACKOFF_S = 300  # after a refused CLOB auth, wait before asking PRED again


async def refresh_balance_once(*, controller: AgentController, transport: Any) -> None:
    """Fetch available USDC via the transport; unavailable stays None (never faked)."""
    getter = getattr(transport, "get_balance", None)
    if getter is None:
        return
    auth = getattr(transport, "auth", None)
    # CLOB: recover existing L2 credentials (derive never creates an account/quota).
    if auth is not None and hasattr(auth, "derive_credentials") and getattr(auth, "credentials", "n/a") is None:
        if getattr(auth, "_auth_retry_after", 0.0) > time.monotonic():
            controller.set_balance(None)  # recently refused; do not hammer PRED
            return
        try:
            await asyncio.to_thread(auth.sync_clock)
            await asyncio.to_thread(getattr(auth, "ensure_credentials", auth.derive_credentials), 0)
        except Exception as exc:  # noqa: BLE001
            from pred_agent.execution.live_runtime import describe_auth_error

            log.warning("balance unavailable: CLOB auth failed (retrying in %ss): %s",
                        _AUTH_RETRY_BACKOFF_S, describe_auth_error(exc))
            auth._auth_retry_after = time.monotonic() + _AUTH_RETRY_BACKOFF_S
            controller.set_balance(None)
            return
    try:
        body = await asyncio.to_thread(getter)
    except PredTransportError as exc:
        # Docs: 401/500 balance replies carry success=false + error in the body, which the
        # transport turns into an exception. Surface status and PRED's error, never a fake 0.
        log.warning("balance unavailable: HTTP %s error=%r", exc.status, _error_from_raw(exc.raw_body))
        controller.set_balance(None)
        return
    if body.get("success") is True and body.get("usdc_balance") is not None:
        controller.set_balance(str(body["usdc_balance"]))
    else:
        log.warning("balance unavailable: success=%r keys=%s error=%r",
                    body.get("success"), sorted(body.keys()), body.get("error") or body.get("message"))
        controller.set_balance(None)


# ─────────────────────────────────────────────────────────────
# Rules monitor (fail closed)
# ─────────────────────────────────────────────────────────────

def _eval_price(snapshot: BookSnapshot) -> Decimal | None:
    """Best evaluable price from a snapshot: mid, else LTP, else top level."""
    if snapshot.mid_price_cents is not None:
        return snapshot.mid_price_cents
    if snapshot.ltp is not None:
        return snapshot.ltp
    if snapshot.bids:
        return snapshot.bids[0].price_cents
    if snapshot.asks:
        return snapshot.asks[0].price_cents
    return None


class RulesMonitor:
    """Evaluates ACTIVE rules against the book feed once per cycle.

    Stateful only for stop-loss escalation bookkeeping (in-memory; a restart
    restarts the escalation clock, which is acceptable for PAPER).
    """

    def __init__(self) -> None:
        self._escalated: set[str] = set()

    async def run_once(
        self,
        *,
        controller: AgentController,
        book_feed: BookFeed | None,
        bus: EventBus | None = None,
        alert_fn: AlertFn | None = None,
    ) -> dict[str, int]:
        stats = {"evaluated": 0, "fired": 0, "blocked": 0, "unblocked": 0, "expired": 0}
        for rule in list(controller.rules.list_all()):
            if rule.status not in (STATUS_ACTIVE, STATUS_BLOCKED):
                continue
            stats["evaluated"] += 1

            pos = controller.positions.get_position(rule.market_id)
            if pos is None or pos.status != "open":
                if controller.rules.mark_expired(rule, "position no longer open"):
                    stats["expired"] += 1
                    await self._publish(bus, "rule_updated", {"rule_id": rule.id, "status": "EXPIRED"})
                continue

            if controller.mode == "LIVE":
                # Rules fire through the paper executor, which must never "close" a real
                # position. Automated live exits are not enabled: fail closed and say so.
                if controller.rules.mark_blocked(
                    rule, "LIVE mode: automated exits are not enabled; close the position manually"
                ):
                    stats["blocked"] += 1
                    await self._on_blocked(rule, bus, alert_fn)
                continue

            snapshot = await self._fresh_snapshot(
                book_feed=book_feed,
                market_id=rule.market_id,
                max_age_s=controller.settings.book_max_age_s,
            )
            if snapshot is None:
                if controller.rules.mark_blocked(rule, "book stale or unavailable (fail closed)"):
                    stats["blocked"] += 1
                    await self._on_blocked(rule, bus, alert_fn)
                continue

            if rule.status == STATUS_BLOCKED:
                controller.rules.mark_unblocked(rule)
                stats["unblocked"] += 1

            price = _eval_price(snapshot)
            if price is None:
                if controller.rules.mark_blocked(rule, "book has no evaluable price (fail closed)"):
                    stats["blocked"] += 1
                    await self._on_blocked(rule, bus, alert_fn)
                continue

            fired = await controller.rules.try_fire(
                rule,
                position=pos,
                current_price_cents=price,
                executor=controller.executor,
                positions=controller.positions,
            )
            if fired:
                stats["fired"] += 1
                await self._publish(bus, "rule_triggered", {
                    "rule_id": rule.id,
                    "kind": rule.kind,
                    "market_id": rule.market_id,
                    "price_cents": str(price),
                })
                self._escalated.discard(rule.id)
        return stats

    # ── internals ─────────────────────────────────────────────

    @staticmethod
    async def _fresh_snapshot(
        *,
        book_feed: BookFeed | None,
        market_id: str,
        max_age_s: int,
    ) -> BookSnapshot | None:
        if book_feed is None:
            return None
        snapshot = book_feed.get_book(market_id)
        if snapshot is None:
            parent = book_feed.parent_of(market_id)
            if parent:
                snapshot = await book_feed.fetch_book_rest(parent, market_id)
        if snapshot is None:
            return None
        if book_feed.is_stale(market_id, max_age_s):
            return None
        return snapshot

    async def _on_blocked(
        self,
        rule: Any,
        bus: EventBus | None,
        alert_fn: AlertFn | None,
    ) -> None:
        await self._publish(bus, "rule_blocked", {
            "rule_id": rule.id,
            "kind": rule.kind,
            "market_id": rule.market_id,
            "note": rule.last_eval_note,
        })
        if alert_fn is None:
            return
        if rule.kind == "stop_loss":
            blocked_for = 0.0
            if rule.blocked_since is not None:
                blocked_for = time.time() - rule.blocked_since.timestamp()
            if blocked_for >= BLOCKED_ESCALATION_SECONDS and rule.id not in self._escalated:
                self._escalated.add(rule.id)
                await alert_fn(
                    f"🚨 ESCALATION: stop-loss rule {rule.id[:8]} has been BLOCKED for "
                    f"> {BLOCKED_ESCALATION_SECONDS}s — check feeds! ({rule.last_eval_note})"
                )
                return
        await alert_fn(f"⚠️ rule {rule.id[:8]} ({rule.kind}) BLOCKED: {rule.last_eval_note}")

    @staticmethod
    async def _publish(bus: EventBus | None, etype: str, payload: dict[str, Any]) -> None:
        if bus is not None:
            await bus.publish(Event(etype, payload))


# ─────────────────────────────────────────────────────────────
# Background loop runner (used by app lifespan)
# ─────────────────────────────────────────────────────────────

async def run_background_tasks(
    *,
    scanner: Scanner,
    fixture_cache: Any,
    session_factory: sessionmaker[Any],
    book_feed: BookFeed | None,
    controller: AgentController,
    bus: EventBus | None = None,
    monitor: RulesMonitor | None = None,
    alert_fn: AlertFn | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Run scanner + rules monitor + CLOB poller until stop is set."""
    stop_ev = stop or asyncio.Event()
    monitor = monitor or RulesMonitor()

    async def scan_loop() -> None:
        controller.set_feed_diag(scanner.last_scan)  # live reference, mutated during scans
        while not stop_ev.is_set():
            try:
                await scan_once(
                    scanner=scanner,
                    fixture_cache=fixture_cache,
                    session_factory=session_factory,
                    book_feed=book_feed,
                    controller=controller,
                    bus=bus,
                )
            except Exception:
                log.warning("scan pass failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_ev.wait(), timeout=controller.settings.scan_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def monitor_loop() -> None:
        while not stop_ev.is_set():
            try:
                await monitor.run_once(
                    controller=controller,
                    book_feed=book_feed,
                    bus=bus,
                    alert_fn=alert_fn,
                )
            except Exception:
                log.warning("rules monitor pass failed", exc_info=True)
            await asyncio.sleep(max(1, controller.settings.scan_interval_seconds))

    async def balance_loop() -> None:
        transport = getattr(book_feed, "transport", None)
        while not stop_ev.is_set():
            try:
                await refresh_balance_once(controller=controller, transport=transport)
            except Exception:
                controller.set_balance(None)
                log.warning("balance refresh failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_ev.wait(), timeout=max(15, controller.settings.scan_interval_seconds))
            except asyncio.TimeoutError:
                pass

    tasks = [asyncio.create_task(scan_loop()), asyncio.create_task(monitor_loop())]
    if book_feed is not None:
        tasks.append(asyncio.create_task(balance_loop()))
    if book_feed is not None:
        await book_feed.start()
        tasks.append(asyncio.create_task(book_feed.poll_clob_books()))
    try:
        await stop_ev.wait()
    finally:
        for t in tasks:
            t.cancel()
        if book_feed is not None:
            await book_feed.stop()
