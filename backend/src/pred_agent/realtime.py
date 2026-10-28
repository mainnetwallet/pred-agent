"""realtime.py — REST book fetch + Ably feed (JWT) / poller (CLOB) + local book cache.

Grounded in kit/02_PRED_API_SPEC.md section 7 and kit/03_ARCHITECTURE.md section 5.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from pred_agent.book import BookSnapshot, normalize_book
from pred_agent.config import load_settings
from pred_agent.transport.protocol import PredTransport

logger = logging.getLogger(__name__)


@dataclass
class BookCacheEntry:
    snapshot: BookSnapshot
    received_at: float


class BookFeed:
    """Local order book cache with sequence guard and unit normalization.

    JWT mode: Ably feed (market:{type_reference_id} channels).
    CLOB mode: REST poller for tracked markets.
    """

    def __init__(self, transport: PredTransport) -> None:
        settings = load_settings()
        self.transport = transport
        self._cache: dict[str, BookCacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_seq: dict[str, int] = {}
        self._poll_interval = settings.scan_interval_seconds
        self._tracked_markets: set[str] = set()
        self._parents: dict[str, str] = {}
        self._running = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def get_book(self, market_id: str) -> BookSnapshot | None:
        entry = self._cache.get(market_id)
        return entry.snapshot if entry else None

    def book_age_seconds(self, market_id: str) -> float | None:
        entry = self._cache.get(market_id)
        if entry is None:
            return None
        return time.time() - entry.received_at

    def is_stale(self, market_id: str, max_age_s: int) -> bool:
        age = self.book_age_seconds(market_id)
        if age is None:
            return True
        return age > max_age_s

    def track_market(self, market_id: str, parent_market_id: str | None = None) -> None:
        self._tracked_markets.add(market_id)
        if parent_market_id:
            self._parents[market_id] = parent_market_id

    def untrack_market(self, market_id: str) -> None:
        self._tracked_markets.discard(market_id)
        self._parents.pop(market_id, None)

    def parent_of(self, market_id: str) -> str | None:
        return self._parents.get(market_id)

    # ------------------------------------------------------------------ #
    # REST book fetch (used by both modes)
    # ------------------------------------------------------------------ #

    async def fetch_book_rest(self, parent_market_id: str, market_id: str) -> BookSnapshot | None:
        """Fetch a single orderbook via REST. Try without auth first, fall back to auth."""
        path = f"/api/v1/order/{parent_market_id}/orderbook/{market_id}?depth=10&include_metadata=true"
        # Try without auth
        try:
            raw = await self._http_get_no_auth(path)
            if raw:
                return self._normalize_and_cache(market_id, raw, ws_dollars=False)
        except Exception:
            logger.warning("unauthenticated book fetch failed for %s; trying authenticated transport",
                           market_id, exc_info=True)
        # Fall back to authenticated transport
        try:
            raw = self.transport.get(path)
            return self._normalize_and_cache(market_id, raw, ws_dollars=False)
        except Exception as exc:
            logger.warning("book fetch failed for market=%s parent=%s: %s", market_id, parent_market_id, exc)
            return None

    async def _http_get_no_auth(self, path: str) -> dict[str, Any] | None:

        settings = load_settings()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.host}{path}")
            if resp.status_code >= 400:
                logger.warning("orderbook GET %s -> HTTP %s: %.200s", path, resp.status_code, resp.text)
                return None
            data: dict[str, Any] = resp.json()
            return data

    def _normalize_and_cache(self, market_id: str, raw: dict[str, Any],
                             ws_dollars: bool) -> BookSnapshot:
        snapshot = normalize_book(raw, market_id, ws_dollars=ws_dollars)
        # Sequence guard
        seq = snapshot.sequence_number
        if seq is not None:
            last = self._last_seq.get(market_id)
            if last is not None and seq <= last:
                return self._cache[market_id].snapshot  # stale, ignore
            self._last_seq[market_id] = seq
        self._cache[market_id] = BookCacheEntry(
            snapshot=snapshot, received_at=time.time()
        )
        return snapshot

    # ------------------------------------------------------------------ #
    # Ably feed (JWT mode only)
    # ------------------------------------------------------------------ #

    async def start_ably_feed(self, ably_token_details: dict[str, Any],
                              type_reference_ids: list[str]) -> None:
        """Connect to Ably and subscribe to market:{type_reference_id} channels."""
        # Ably SDK is sync; run in executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._ably_run, ably_token_details, type_reference_ids
        )

    def _ably_run(self, token_details: dict[str, Any], type_reference_ids: list[str]) -> None:
        """Blocking Ably connection (runs in executor)."""
        from ably import AblyRealtime

        ably = AblyRealtime(token_details=token_details)
        connection = ably.connection

        def on_connected(_: Any) -> None:
            for trid in type_reference_ids:
                channel_name = f"market:{trid}"
                channel = ably.channels.get(channel_name)
                channel.subscribe(self._on_orderbook_message)

        def on_failed(_: Any) -> None:
            pass

        connection.on("connected", on_connected)
        connection.on("failed", on_failed)

        ably.connect()
        # Block until disconnected
        while self._running:
            time.sleep(1)
        ably.close()

    def _on_orderbook_message(self, message: Any) -> None:
        """Handle an Ably orderbook snapshot message."""
        try:
            data = message.data if hasattr(message, "data") else message
            market_id = data.get("market_id")
            if not market_id:
                return  # drop messages without market_id
            self._normalize_and_cache(market_id, data, ws_dollars=True)
        except Exception:
            # never crash the feed on a bad message
            logger.debug("dropped malformed feed message", exc_info=True)

    # ------------------------------------------------------------------ #
    # CLOB poller
    # ------------------------------------------------------------------ #

    async def poll_clob_books(self) -> None:
        """Poll REST books for tracked markets (CLOB mode)."""
        while self._running:
            for market_id in list(self._tracked_markets):
                parent_id = self._parents.get(market_id)
                if not parent_id:
                    continue
                try:
                    await self.fetch_book_rest(parent_id, market_id)
                except Exception:
                    logger.debug("poll fetch failed for %s", market_id, exc_info=True)
            await asyncio.sleep(self._poll_interval)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ #
    # Unit check (startup)
    # ------------------------------------------------------------------ #

    def startup_unit_check(self, rest_snapshot: BookSnapshot,
                           ws_snapshot: BookSnapshot,
                           market_id: str) -> bool:
        """Compare REST vs WS snapshots of the same market. If units can't be
        established, halt trading."""
        rest_best_bid = rest_snapshot.bids[0].price_cents if rest_snapshot.bids else None
        ws_best_bid = ws_snapshot.bids[0].price_cents if ws_snapshot.bids else None
        if rest_best_bid is None or ws_best_bid is None:
            return False
        # REST uses cents (e.g. 62), WS uses dollars scaled to cents (e.g. 0.62 -> 62).
        # If they differ by a factor of 100, WS is in raw dollars and wasn't converted.
        ratio = rest_best_bid / ws_best_bid if ws_best_bid else Decimal(0)
        if ratio not in (Decimal(1), Decimal(100)):
            return False  # units cannot be established -> halt trading
        return True
