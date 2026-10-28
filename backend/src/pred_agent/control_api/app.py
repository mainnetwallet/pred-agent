"""Control API FastAPI app — Phase 8 (Chat + Rules).

Grounded in kit/07_CONTROL_API.md.
New in Phase 8:
  - POST /v1/chat, GET /v1/chat/{session_id}, DELETE /v1/chat/{session_id}
  - GET /v1/rules, POST /v1/rules/{id}/confirm, POST /v1/rules/{id}/cancel
  - GET /v1/fixtures?state=live|upcoming|all (was always returning [])
  - WS: broadcasts events to authenticated sockets via ConnectionManager
  - Per-IP chat rate limiting (10 msg/min), max message length 4000 chars
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from pred_agent.book import BookAnalyzer
from pred_agent.config import load_settings
from pred_agent.control_api.auth import DeviceAuth
from pred_agent.control_api.controller import AgentController, to_jsonable
from pred_agent.control_api.session import LoginManager
from pred_agent.events import Event, EventBus
from pred_agent.execution.live_runtime import MarketContext
from pred_agent.llm import LLMRequest, OpenAICompatClient, build_llm_client
from pred_agent.llm.chat import ChatMessage, build_chat_llm
from pred_agent.realtime import BookFeed
from pred_agent.scanner import DiscoveredParent
from pred_agent.store.repository import ChatRepo
from pred_agent.store.session import create_session_manager

log = logging.getLogger(__name__)

_WEB_INDEX = Path(__file__).parent / "web" / "index.html"

# ─────────────────────────────────────────────────────────────
# game_status: known live signals (fail-closed — unknown = not live)
# ─────────────────────────────────────────────────────────────
# Values are compared after normalisation (lowercase, no spaces/underscores/hyphens).
# Observed in the live PRED feed: Scheduled/scheduled, Fixture, InProgress, Final.
_LIVE_STATUSES = {
    "inprogress", "inplay", "live", "1h", "2h", "ht", "et", "p",
    "halftime", "firsthalf", "secondhalf", "extratime", "penalties",
}
_UPCOMING_STATUSES = {"scheduled", "fixture", "notstarted", "ns", "upcoming", "prematch", "tbd"}
_FINISHED_STATUSES = {"final", "finished", "ft", "aet", "pen", "ended", "completed", "closed"}
_UNKNOWN_GS_LOG: set[str] = set()   # log each unknown value once


def _norm_gs(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch not in " _-")


def _first(*vals: Any) -> Any:
    """First value that is not None (0 is a valid score)."""
    return next((v for v in vals if v is not None), None)


def _fixture_state(game_status: str | None, kickoff: datetime | None) -> str:
    """Derive live/upcoming/other from game_status + kickoff heuristic.
    Fail-closed: unknown game_status → 'other' (not treated as live).
    """
    if game_status is not None:
        gs = _norm_gs(game_status)
        if gs in _LIVE_STATUSES:
            return "live"
        if gs in _UPCOMING_STATUSES:
            return "upcoming"
        if gs in _FINISHED_STATUSES:
            return "other"
        if gs not in _UNKNOWN_GS_LOG:
            _UNKNOWN_GS_LOG.add(gs)
            log.info("unknown game_status=%r — treated as 'other' (fail closed)", game_status)
        return "other"
    # None: use kickoff heuristic
    if kickoff is None:
        return "upcoming"
    now = datetime.now(timezone.utc)
    return "live" if kickoff < now else "upcoming"


# ─────────────────────────────────────────────────────────────
# WS ConnectionManager (A5)
# ─────────────────────────────────────────────────────────────

class ConnectionManager:
    """Broadcast events to authenticated WebSocket clients."""

    def __init__(self) -> None:
        self._sockets: dict[str, WebSocket] = {}       # device_id → socket
        self._subscriptions: dict[str, set[str]] = defaultdict(set)  # device_id → market_ids

    async def connect(self, device_id: str, ws: WebSocket) -> None:
        self._sockets[device_id] = ws

    def disconnect(self, device_id: str) -> None:
        self._sockets.pop(device_id, None)
        self._subscriptions.pop(device_id, None)

    def subscribe(self, device_id: str, markets: list[str]) -> None:
        self._subscriptions[device_id] = set(markets)

    async def broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        msg = json.dumps({"type": event_type, "data": payload})
        dead = []
        for did, ws in list(self._sockets.items()):
            try:
                await ws.send_text(msg)
            except Exception:  # noqa: BLE001
                dead.append(did)
        for did in dead:
            self.disconnect(did)

    async def broadcast_market(self, market_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast only to subscribers of a specific market."""
        msg = json.dumps({"type": event_type, "data": payload})
        for did, ws in list(self._sockets.items()):
            if market_id in self._subscriptions.get(did, set()):
                try:
                    await ws.send_text(msg)
                except Exception:  # noqa: BLE001
                    self.disconnect(did)


# ─────────────────────────────────────────────────────────────
# Chat rate limiter
# ─────────────────────────────────────────────────────────────

class ChatRateLimiter:
    """Simple sliding-window rate limiter: 10 messages per 60 seconds per key."""

    def __init__(self, max_per_window: int = 10, window_s: int = 60) -> None:
        self._max = max_per_window
        self._window = window_s
        self._history: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        times = self._history[key]
        times[:] = [t for t in times if now - t < self._window]
        if len(times) >= self._max:
            return False
        times.append(now)
        return True


# ─────────────────────────────────────────────────────────────
# In-memory chat sessions
# ─────────────────────────────────────────────────────────────

class ChatSessionStore:
    """Chat session store — memory + optional DB persistence (Phase 8)."""

    def __init__(self, db: ChatRepo | None = None) -> None:
        self._db = db
        self._sessions: dict[str, list[ChatMessage]] = db.load_all() if db else {}

    def get_or_create(self, session_id: str | None, actor: str) -> tuple[str, list[ChatMessage]]:
        if session_id and session_id in self._sessions:
            return session_id, self._sessions[session_id]
        sid = str(uuid.uuid4())
        self._sessions[sid] = []
        if self._db is not None:
            self._db.save_session(sid, actor)
        return sid, []

    def append(self, session_id: str, message: ChatMessage) -> None:
        self._sessions.setdefault(session_id, []).append(message)
        if self._db is not None:
            self._db.save_message(session_id, message)

    def get_history(self, session_id: str) -> list[ChatMessage]:
        return self._sessions.get(session_id, [])

    def clear(self, session_id: str) -> bool:
        if session_id in self._sessions:
            self._sessions.pop(session_id)
            if self._db is not None:
                self._db.clear(session_id)
            return True
        return False


# ─────────────────────────────────────────────────────────────
# Response envelope helpers
# ─────────────────────────────────────────────────────────────

def envelope(*, ok: bool, data: Any = None, error: dict[str, Any] | None = None,
             audit_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": ok}
    if data is not None:
        body["data"] = data
    if error is not None:
        body["error"] = error
    if audit_id is not None:
        body["audit_id"] = audit_id
    return body


def error_response(code: str, message: str, status_code: int = 400,
                   audit_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=envelope(ok=False, error={"code": code, "message": message}, audit_id=audit_id),
    )


# ─────────────────────────────────────────────────────────────
# Fixtures in-memory cache (populated by scanner loop)
# ─────────────────────────────────────────────────────────────

class FixtureCache:
    """Thread-safe in-memory fixture cache. Updated by scanner loop."""

    def __init__(self) -> None:
        self._fixtures: list[DiscoveredParent] = []
        self._updated_at: float | None = None

    def update(self, fixtures: list[DiscoveredParent]) -> None:
        self._fixtures = list(fixtures)
        self._updated_at = time.monotonic()

    def market_context(self, market_id: str) -> MarketContext | None:
        """Discovery facts for one child market (parent id, contract, fee target, status)."""
        if self._updated_at is None:
            return None
        for f in self._fixtures:
            for m in f.markets or []:
                if m.get("market_id") != market_id:
                    continue
                active = f.status == "active" and m.get("status") == "active"
                return MarketContext(
                    market_id=market_id,
                    parent_market_id=f.parent_market_id,
                    contract_address=f.contract_address,
                    fee_target_bps=f.fee_rate_bps,
                    active=active,
                    inactive_reason=None if active else (
                        f"market not active (parent={f.status}, child={m.get('status')})"
                    ),
                    age_s=time.monotonic() - self._updated_at,
                )
        return None

    def list(self, state: str | None = None) -> list[dict[str, Any]]:
        result = []
        for f in self._fixtures:
            fix = f.fixture or {}
            kickoff_str = fix.get("game_start_time")
            kickoff: datetime | None = None
            if kickoff_str:
                try:
                    kickoff = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
            gs = fix.get("game_status") or fix.get("sports_game_status")
            fstate = _fixture_state(gs, kickoff)

            if state and fstate != state:
                continue

            # Dhaka = UTC+6
            dhaka_tz = timezone(timedelta(hours=6))
            kickoff_dhaka = kickoff.astimezone(dhaka_tz).isoformat() if kickoff else None

            team_names = {
                t.get("team_id"): t.get("team_name") or t.get("name")
                for t in (f.team_data or []) if t.get("team_id")
            }
            home_id = fix.get("home_team_id")
            away_id = fix.get("away_team_id")

            result.append({
                "id": fix.get("fixture_id") or f.parent_market_id,
                "parent_market_id": f.parent_market_id,
                "home_team": team_names.get(home_id) or home_id,
                "away_team": team_names.get(away_id) or away_id,
                "league": fix.get("league_name"),        # may be absent in discovery feed
                "kickoff": kickoff.isoformat() if kickoff else None,
                "kickoff_dhaka": kickoff_dhaka,
                "state": fstate,
                "game_status": gs,
                "home_score": _first(fix.get("home_team_score"), fix.get("sports_home_score"), fix.get("home_score")),
                "away_score": _first(fix.get("away_team_score"), fix.get("sports_away_score"), fix.get("away_score")),
                "minute": fix.get("minute"),
                "markets": [
                    {
                        "market_id": m.get("market_id"),
                        "status": m.get("status"),
                        "best_bid_cents": None,
                        "best_ask_cents": None,
                    }
                    for m in (f.markets or [])
                ],
            })
        return result


# ─────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────

def create_app(*, background: bool = False) -> FastAPI:  # noqa: PLR0915
    settings = load_settings()
    bus = EventBus()
    auth = DeviceAuth()
    login_mgr = LoginManager(
        username=settings.admin_username,
        password=settings.admin_password,
        secret=settings.session_secret,
        ttl_hours=settings.session_ttl_hours,
    )
    engine, session_factory = create_session_manager(settings.database_url)
    controller = AgentController(settings, db_session_factory=session_factory)
    ws_manager = ConnectionManager()
    chat_sessions = ChatSessionStore(db=ChatRepo(session_factory))
    chat_rl = ChatRateLimiter(max_per_window=10, window_s=60)
    fixture_cache = FixtureCache()
    controller.market_lookup = fixture_cache.market_context

    app = FastAPI(title="PRED Control API")
    app.state.settings = settings
    app.state.auth = auth
    app.state.bus = bus
    app.state.login = login_mgr
    app.state.controller = controller
    app.state.ws_manager = ws_manager
    app.state.fixture_cache = fixture_cache
    app.state.session_factory = session_factory
    app.state.book_feed = None  # set below when background tasks are enabled

    # ── event bus → WS broadcast task ────────────────────────

    async def _bus_to_ws() -> None:
        """Drain the event bus and broadcast to all authenticated WS clients."""
        import asyncio
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        # Subscribe to all broadcast events
        for etype in (
            "fixture_update", "proposal_created", "proposal_updated",
            "rule_created", "rule_updated", "rule_triggered", "rule_blocked",
            "order_update", "position_update", "status_update",
        ):
            bus.subscribe(etype, queue)
        while True:
            event = await bus.drain(queue)
            await ws_manager.broadcast(event.type, event.payload)

    # ── lifespan ──────────────────────────────────────────────

    if background:
        from pred_agent.control_api.background import build_book_feed

        app.state.book_feed = build_book_feed(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        from pred_agent.telegram.bot import send_alert, start_bot, stop_bot

        tg_app = await start_bot(settings, controller, engine=engine)
        app.state.telegram = tg_app
        bus_task = asyncio.create_task(_bus_to_ws())

        async def _alert(text: str) -> None:
            await send_alert(tg_app, settings, text)

        bg_task: asyncio.Task[Any] | None = None
        stop_ev: asyncio.Event | None = None
        live_task: asyncio.Task[Any] | None = None

        async def _arm_live_loop() -> None:
            """Arm LIVE trading (retrying) — until armed, LIVE approvals fail closed."""
            from pred_agent.execution.live_runtime import LiveSetupError, setup_live

            transport = getattr(app.state.book_feed, "transport", None)
            delay = 60.0
            alerted_setup: str | None = None
            while True:
                try:
                    runtime = await asyncio.to_thread(
                        setup_live,
                        settings=settings,
                        transport=transport,
                        market_lookup=fixture_cache.market_context,
                        max_discovery_age_s=float(settings.scan_interval_seconds * 3 + 30),
                    )
                    controller.arm_live(runtime)
                    await _alert(
                        "🔴 LIVE trading ARMED — approvals now place REAL orders. "
                        f"Exchange open orders: {runtime.startup_report['exchange_open_orders']}, "
                        f"unresolved: {len(runtime.startup_report['unresolved'])}."
                    )
                    break
                except LiveSetupError as exc:
                    reason = str(exc)
                    controller.disarm_live(reason)
                    if "aggregator_key_required" in reason:
                        # PRED has not enabled CLOB access for this EOA: retrying every minute
                        # cannot help and only hammers PRED. Back off, and tell the operator once.
                        delay = min(delay * 2, 900.0)
                        if alerted_setup != "aggregator_key_required":
                            alerted_setup = "aggregator_key_required"
                            await _alert(
                                "⚠️ LIVE is ON but PRED has not enabled CLOB access for this wallet "
                                "(401 aggregator_key_required). No real orders can be placed. Check that "
                                "PRIVATE_KEY is the primary wallet of an eligible existing PRED account; "
                                "no new account will be created automatically; retrying "
                                f"every {int(delay // 60)}+ min."
                            )
                except Exception as exc:  # noqa: BLE001
                    controller.disarm_live(f"unexpected setup failure: {type(exc).__name__}")
                await asyncio.sleep(delay)
            # Armed: keep mirroring fills / open orders / positions from PRED.
            last_unresolved = 0
            alerted_error: str | None = None
            while True:
                await asyncio.sleep(30)
                report = await asyncio.to_thread(controller.sync_live)
                if report is None:
                    err = controller.live_sync_error
                    if err and err != alerted_error:  # alert on change, not every 30 s
                        alerted_error = err
                        await _alert(f"⚠️ LIVE sync failing — new orders blocked: {err}")
                    continue
                alerted_error = None
                unresolved = len(report.get("unresolved", []))
                if unresolved > last_unresolved:
                    await _alert(f"⚠️ {unresolved} LIVE order(s) UNKNOWN — markets blocked until reconciled.")
                last_unresolved = unresolved

        if background and settings.live_trading:
            live_task = asyncio.create_task(_arm_live_loop())
        if background:
            from pred_agent.control_api.background import run_background_tasks
            from pred_agent.scanner import Scanner

            stop_ev = asyncio.Event()
            bg_task = asyncio.create_task(run_background_tasks(
                scanner=Scanner(),
                fixture_cache=fixture_cache,
                session_factory=session_factory,
                book_feed=app.state.book_feed,
                controller=controller,
                bus=bus,
                alert_fn=_alert,
                stop=stop_ev,
            ))
        try:
            yield
        finally:
            bus_task.cancel()
            if live_task is not None:
                live_task.cancel()
            if bg_task is not None and stop_ev is not None:
                stop_ev.set()
                bg_task.cancel()
            await stop_bot(tg_app)

    app.router.lifespan_context = lifespan

    # ── auth helpers ──────────────────────────────────────────

    def _verify_token(token: str) -> str | None:
        user = login_mgr.verify(token)
        if user is not None:
            return f"session:{user}"
        return auth.verify_token(token)

    def _verify_auth(authorization: str | None) -> str | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        return _verify_token(authorization[len("Bearer "):])

    def _unauth() -> JSONResponse:
        return error_response("unauthorized", "invalid or missing token", 401)

    def _require_xconfirm(x_confirm: str | None) -> JSONResponse | None:
        """LIVE mode: risky actions require the X-Confirm header — proof the
        human passed the client's confirmation gate (biometric on mobile,
        confirm() dialog on the website). PAPER mode never requires it."""
        if controller.mode != "LIVE":
            return None
        if x_confirm:
            return None
        return error_response("xconfirm_required", "LIVE mode: X-Confirm header required", 403)

    def _client_key(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[-1].strip()
        return request.client.host if request.client else "?"

    # ── build chat LLM with read-only + PENDING-only write tools ──

    def _make_chat_llm():  # type: ignore[no-untyped-def]
        from pred_agent.llm.chat import Card, Tool

        def _book() -> "BookFeed | None":
            return getattr(app.state, "book_feed", None)

        def _find_market(market_id: str) -> dict[str, Any] | None:
            for fx in fixture_cache.list():
                for m in fx.get("markets", []):
                    if m.get("market_id") == market_id:
                        return {"fixture": fx, "market": m}
            return None

        def _price_for(side: str, market_id: str) -> Decimal | None:
            bf = _book()
            if bf is None:
                return None
            snap = bf.get_book(market_id)
            if snap is None:
                return None
            if side == "long" and snap.asks:
                return snap.asks[0].price_cents
            if side == "short" and snap.bids:
                return snap.bids[0].price_cents
            return snap.mid_price_cents

        # ── read-only tools ─────────────────────────────────

        async def _list_fixtures(state: str = "all") -> dict[str, Any]:
            items = fixture_cache.list(state if state != "all" else None)
            return {"fixtures": items, "count": len(items), "state_filter": state}

        async def _get_fixture(fixture_id: str) -> dict[str, Any]:
            match = next(
                (
                    f for f in fixture_cache.list()
                    if f.get("id") == fixture_id or f.get("parent_market_id") == fixture_id
                ),
                None,
            )
            if match is None:
                return {"error": f"fixture {fixture_id} not found"}
            return {"fixture": match}

        async def _get_book(market_id: str) -> dict[str, Any]:
            bf = _book()
            if bf is None:
                return {"error": "book feed is not running"}
            snap = bf.get_book(market_id)
            if snap is None:
                # Cache miss: fetch once over REST instead of failing (the rules
                # monitor already does this). Parent comes from the feed's
                # tracking table, else from the fixture cache.
                parent = getattr(bf, "parent_of", lambda _m: None)(market_id)
                if parent is None:
                    found = _find_market(market_id)
                    if found is not None:
                        parent = found["fixture"].get("parent_market_id")
                fetch = getattr(bf, "fetch_book_rest", None)
                if parent and fetch is not None:
                    snap = await fetch(parent, market_id)
                if snap is None:
                    why = getattr(bf, "last_fetch_error", lambda _m: None)(market_id)
                    detail = (
                        "REST orderbook fetch failed or returned nothing"
                        if parent
                        else "market is not in the fixture cache (unknown parent market)"
                    )
                    out: dict[str, Any] = {
                        "error": f"no book data for market {market_id}",
                        "detail": detail,
                    }
                    if why:
                        out["pred_response"] = why  # PRED's own answer, e.g. 503 market not found
                    if parent and parent == market_id:
                        out["note"] = (
                            "discovery lists this market with parent_market_id == market_id; "
                            "PRED's orderbook has no entry for that pair (market may be closed)"
                        )
                    return out
            snap = BookAnalyzer().analyze(snap)
            return {
                "market_id": market_id,
                "best_bid_cents": str(snap.bids[0].price_cents) if snap.bids else None,
                "best_ask_cents": str(snap.asks[0].price_cents) if snap.asks else None,
                "mid_price_cents": str(snap.mid_price_cents) if snap.mid_price_cents is not None else None,
                "spread_cents": str(snap.spread_cents) if snap.spread_cents is not None else None,
            }

        async def _get_status() -> dict[str, Any]:
            return controller.status()

        async def _get_positions() -> dict[str, Any]:
            return {"positions": controller.positions_list()}

        async def _get_orders() -> dict[str, Any]:
            return {"orders": controller.orders()}

        async def _get_rules() -> dict[str, Any]:
            rules = controller.list_rules()
            return {"rules": [r.to_dict() for r in rules]}

        async def _get_limits() -> dict[str, Any]:
            return {"limits": controller.limits_list()}

        # ── write-intent tools (create PENDING objects or confirmation cards ONLY) ──

        async def _propose_trade(market_id: str, side: str, amount_usd: str) -> tuple[dict[str, Any], list[Card]]:
            found = _find_market(market_id)
            if found is None:
                return {"error": f"market {market_id} is not in the current fixture feed"}, []
            fixture = found["fixture"]
            side_n = str(side).lower()
            if side_n in ("buy", "yes", "long"):
                side_n = "long"
            elif side_n in ("sell", "no", "short"):
                side_n = "short"
            else:
                return {"error": "side must be long (buy) or short (sell)"}, []
            try:
                amount = Decimal(str(amount_usd))
            except InvalidOperation:
                return {"error": "amount_usd must be a decimal number"}, []
            price = _price_for(side_n, market_id)
            if price is None or price <= 0:
                return {"error": "no live price for this market — cannot price a proposal (fail closed)"}, []
            prop, err = controller.propose_trade_from_chat(
                market_id=market_id,
                parent_market_id=fixture.get("parent_market_id", ""),
                fixture_id=fixture.get("id", ""),
                side=side_n,
                amount_usd=amount,
                price_cents=price,
            )
            if prop is None:
                return {"error": err or "proposal rejected"}, []
            return {"proposal_id": prop.id, "status": prop.status.value}, [Card("proposal", to_jsonable(prop))]

        async def _create_rule(
            market_id: str, kind: str, trigger_type: str, trigger_value: str,
            size: str = "all",
        ) -> tuple[dict[str, Any], list[Card]]:
            kind_n = str(kind).lower()
            if kind_n not in ("take_profit", "stop_loss"):
                return {"error": "kind must be take_profit or stop_loss"}, []
            tt = str(trigger_type).lower()
            if tt not in ("profit_usd", "loss_usd", "price_cents", "profit_pct"):
                return {"error": "trigger_type must be profit_usd, loss_usd, price_cents or profit_pct"}, []
            try:
                value = Decimal(str(trigger_value))
            except InvalidOperation:
                return {"error": "trigger_value must be a decimal number"}, []
            rule, err = controller.create_rule_from_chat(
                market_id=market_id, kind=kind_n, trigger_type=tt,
                trigger_value=value, size=size,
            )
            if rule is None:
                return {"error": err or "rule rejected"}, []
            return {"rule_id": rule.id, "status": rule.status}, [Card("rule", rule.to_dict())]

        async def _close_position(market_id: str) -> tuple[dict[str, Any], list[Card]]:
            prop, err = controller.close_position_proposal(market_id, via="chat")
            if prop is None:
                return {"error": err or "cannot close position"}, []
            return {"proposal_id": prop.id, "status": prop.status.value}, [Card("proposal", to_jsonable(prop))]

        async def _cancel_order(order_id: str) -> tuple[dict[str, Any], list[Card]]:
            order = next((o for o in controller.executor.list_orders() if o.id == order_id), None)
            if order is None:
                return {"error": f"order {order_id} not found"}, []
            card = Card("action", {
                "action": "cancel_order",
                "order_id": order_id,
                "label": f"Cancel order {order_id} ({order.side} @ {order.price_cents}c)?",
            })
            return {"card_created": "cancel_order"}, [card]

        async def _cancel_rule(rule_id: str) -> tuple[dict[str, Any], list[Card]]:
            rule = controller.rules.get(rule_id)
            if rule is None:
                return {"error": f"rule {rule_id} not found"}, []
            card = Card("action", {
                "action": "cancel_rule",
                "rule_id": rule_id,
                "label": f"Cancel rule {rule_id}?",
            })
            return {"card_created": "cancel_rule"}, [card]

        async def _request_pause() -> tuple[dict[str, Any], list[Card]]:
            card = Card("action", {"action": "pause", "label": "Pause the agent (block new entries)?"})
            return {"card_created": "pause"}, [card]

        async def _request_panic() -> tuple[dict[str, Any], list[Card]]:
            card = Card("action", {
                "action": "panic",
                "label": "PANIC: pause + reject proposals + cancel orders and ALL rules?",
            })
            return {"card_created": "panic"}, [card]

        tools = [
            Tool(
                name="list_fixtures",
                description=(
                    "List live or upcoming football fixtures. "
                    "state='live' for in-play, 'upcoming' for future, 'all' for both. "
                    "Returns market prices if available. Kickoff times are in UTC; "
                    "convert to Asia/Dhaka (UTC+6) for replies."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "state": {
                            "type": "string",
                            "enum": ["live", "upcoming", "all"],
                            "description": "Filter fixtures by state",
                        }
                    },
                    "required": [],
                },
                fn=_list_fixtures,
            ),
            Tool(
                name="get_fixture",
                description="Get one fixture's details by id or parent_market_id.",
                parameters={
                    "type": "object",
                    "properties": {"fixture_id": {"type": "string"}},
                    "required": ["fixture_id"],
                },
                fn=_get_fixture,
            ),
            Tool(
                name="get_book",
                description="Get the current order book (best bid/ask) for a market.",
                parameters={
                    "type": "object",
                    "properties": {"market_id": {"type": "string"}},
                    "required": ["market_id"],
                },
                fn=_get_book,
            ),
            Tool(
                name="get_status",
                description=(
                    "Get the current agent status: mode, feeds_ok, exposure, PnL, paused, feed_diag. "
                    "When feeds_ok is false, always report feed_diag verbatim (error, cutoff, pages, total, "
                    "kept, families, moneyline_statuses, dropped_*)."
                ),
                parameters={"type": "object", "properties": {}, "required": []},
                fn=_get_status,
            ),
            Tool(
                name="get_positions",
                description="Get all open paper positions with cost, side, quantity.",
                parameters={"type": "object", "properties": {}, "required": []},
                fn=_get_positions,
            ),
            Tool(
                name="get_orders",
                description="Get all paper orders and their current states.",
                parameters={"type": "object", "properties": {}, "required": []},
                fn=_get_orders,
            ),
            Tool(
                name="get_rules",
                description="Get all exit rules (take-profit/stop-loss) with their status.",
                parameters={"type": "object", "properties": {}, "required": []},
                fn=_get_rules,
            ),
            Tool(
                name="get_limits",
                description="Get current trading limits: max_trade_usd, daily_loss_usd, max_open_positions.",
                parameters={"type": "object", "properties": {}, "required": []},
                fn=_get_limits,
            ),
            Tool(
                name="propose_trade",
                description=(
                    "Create a PENDING trade proposal card for the human to approve. "
                    "NEVER executes a trade. side='long' (buy YES) or 'short' (sell/buy NO). "
                    "Restate market, side and amount to the user before calling."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "market_id": {"type": "string"},
                        "side": {"type": "string", "enum": ["long", "short"]},
                        "amount_usd": {"type": "string", "description": "USD amount as a decimal string"},
                    },
                    "required": ["market_id", "side", "amount_usd"],
                },
                fn=_propose_trade,
            ),
            Tool(
                name="create_rule",
                description=(
                    "Create a PENDING exit rule card for the human to confirm. NEVER executes. "
                    "kind: take_profit or stop_loss. trigger_type: profit_usd, loss_usd, "
                    "price_cents or profit_pct. Requires an existing open position on the market."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "market_id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["take_profit", "stop_loss"]},
                        "trigger_type": {
                            "type": "string",
                            "enum": ["profit_usd", "loss_usd", "price_cents", "profit_pct"],
                        },
                        "trigger_value": {"type": "string", "description": "Decimal trigger value"},
                        "size": {"type": "string", "description": "all (default)"},
                    },
                    "required": ["market_id", "kind", "trigger_type", "trigger_value"],
                },
                fn=_create_rule,
            ),
            Tool(
                name="close_position",
                description=(
                    "Create a PENDING reduce-only exit proposal card for an open position. "
                    "The human must approve it."
                ),
                parameters={
                    "type": "object",
                    "properties": {"market_id": {"type": "string"}},
                    "required": ["market_id"],
                },
                fn=_close_position,
            ),
            Tool(
                name="cancel_order",
                description="Create a confirmation card to cancel an open order. Never cancels directly.",
                parameters={
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
                fn=_cancel_order,
            ),
            Tool(
                name="cancel_rule",
                description="Create a confirmation card to cancel a rule. Never cancels directly.",
                parameters={
                    "type": "object",
                    "properties": {"rule_id": {"type": "string"}},
                    "required": ["rule_id"],
                },
                fn=_cancel_rule,
            ),
            Tool(
                name="request_pause",
                description="Create a confirmation card to pause the agent. Never pauses directly.",
                parameters={"type": "object", "properties": {}, "required": []},
                fn=_request_pause,
            ),
            Tool(
                name="request_panic",
                description="Create a confirmation card to trigger PANIC. Never triggers it directly.",
                parameters={"type": "object", "properties": {}, "required": []},
                fn=_request_panic,
            ),
        ]
        app.state.chat_tools = [t.name for t in tools]
        app.state.chat_tool_fns = {t.name: t.fn for t in tools}
        return build_chat_llm(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            tools=tools,
            mode_provider=lambda: controller.mode,
        )

    _chat_llm = _make_chat_llm()  # type: ignore[no-untyped-call]

    # ── Health ────────────────────────────────────────────────

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health() -> Any:
        return {"ok": True, "status": "healthy"}

    @app.get("/ready")
    async def ready() -> Any:
        return {"ok": True, "ready": True}

    # ── Pairing ───────────────────────────────────────────────

    @app.post("/v1/pair")
    async def pair(body: dict[str, Any]) -> Any:
        code = body.get("code")
        device_name = body.get("device_name", "unknown")
        device_public_key = body.get("device_public_key", "")
        if not code:
            return error_response("missing_code", "pairing code required", 400)
        token = auth.pair(code, device_name, device_public_key)
        if token is None:
            return error_response("invalid_code", "pairing code invalid or expired", 400)
        return envelope(ok=True, data={"device_token": token})

    @app.get("/v1/devices")
    async def list_devices(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data={"devices": auth.list_devices()})

    @app.delete("/v1/devices/{device_id}")
    async def revoke_device(device_id: str, authorization: str | None = Header(None)) -> Any:
        caller = _verify_auth(authorization)
        if caller is None:
            return _unauth()
        if caller != device_id and not caller.startswith("session:"):
            return error_response("forbidden", "cannot revoke another device", 403)
        ok = auth.revoke_device(device_id)
        if not ok:
            return error_response("not_found", "device not found", 404)
        return envelope(ok=True, data={"revoked": True})

    # ── Website + login ───────────────────────────────────────

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def index() -> Any:
        return HTMLResponse(_WEB_INDEX.read_text(encoding="utf-8"))

    @app.post("/v1/login")
    async def login(body: dict[str, Any], request: Request) -> Any:
        if not login_mgr.enabled:
            return error_response("login_disabled", "ADMIN_PASSWORD is not set on the server", 503)
        client = _client_key(request)
        wait = login_mgr.locked_for(client)
        if wait:
            return error_response("locked", f"too many attempts, retry in {wait}s", 429)
        token = login_mgr.login(str(body.get("username", "")), str(body.get("password", "")), client)
        if token is None:
            return error_response("invalid_credentials", "wrong username or password", 401)
        return envelope(ok=True, data={"token": token, "expires_in": settings.session_ttl_hours * 3600})

    @app.post("/v1/logout")
    async def logout(authorization: str | None = Header(None)) -> Any:
        if not authorization or not authorization.startswith("Bearer "):
            return _unauth()
        token = authorization[len("Bearer "):]
        if login_mgr.logout(token):
            return envelope(ok=True, data={"logged_out": True})
        device_id = auth.verify_token(token)
        if device_id is None:
            return _unauth()
        auth.revoke_device(device_id)
        return envelope(ok=True, data={"logged_out": True})

    @app.get("/v1/me")
    async def me(authorization: str | None = Header(None)) -> Any:
        who = _verify_auth(authorization)
        if who is None:
            return _unauth()
        return envelope(ok=True, data={"user": who})

    # ── Control ───────────────────────────────────────────────

    @app.get("/v1/status")
    async def status(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=controller.status())

    @app.post("/v1/pause")
    async def pause(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        controller.pause()
        return envelope(ok=True, data=controller.status())

    @app.post("/v1/resume")
    async def resume(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        controller.resume()
        return envelope(ok=True, data=controller.status())

    @app.post("/v1/panic")
    async def panic(authorization: str | None = Header(None),
                    x_confirm: str | None = Header(None, alias="X-Confirm")) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        gate = _require_xconfirm(x_confirm)
        if gate is not None:
            return gate
        result = controller.panic()
        await bus.publish(Event("status_update", {"paused": True, **result}))
        return envelope(ok=True, data={**controller.status(), **result})

    # ── Proposals ─────────────────────────────────────────────

    @app.get("/v1/proposals")
    async def proposals(status: str | None = None, authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=to_jsonable(controller.list_proposals(status)))

    @app.post("/v1/proposals/{proposal_id}/approve")
    async def approve(proposal_id: str, body: dict[str, Any] | None = None,
                      authorization: str | None = Header(None),
                      x_confirm: str | None = Header(None, alias="X-Confirm")) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        gate = _require_xconfirm(x_confirm)
        if gate is not None:
            return gate
        amount: Decimal | None = None
        raw = (body or {}).get("amount_usd")
        if raw not in (None, ""):
            try:
                amount = Decimal(str(raw))
            except InvalidOperation:
                return error_response("bad_amount", "amount_usd must be a decimal string", 400)
        # Blocking HTTP (book fetch + order placement) must not stall the event loop.
        prop = await asyncio.to_thread(controller.approve, proposal_id, "app", amount)
        if prop is None:
            return error_response(
                "not_approvable",
                controller.last_error
                or "proposal missing/expired, agent paused, or amount outside limits",
                409,
            )
        await bus.publish(Event("proposal_updated", to_jsonable(prop)))
        return envelope(ok=True, data=to_jsonable(prop))

    @app.post("/v1/proposals/{proposal_id}/reject")
    async def reject(proposal_id: str, authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        prop = controller.reject(proposal_id, via="app")
        if prop is None:
            return error_response("not_found", "proposal missing or already decided", 404)
        await bus.publish(Event("proposal_updated", to_jsonable(prop)))
        return envelope(ok=True, data=to_jsonable(prop))

    # ── Orders / Positions / Limits ───────────────────────────

    @app.get("/v1/orders")
    async def orders(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=controller.orders())

    @app.post("/v1/orders/{order_id}/cancel")
    async def cancel_order(order_id: str, authorization: str | None = Header(None),
                           x_confirm: str | None = Header(None, alias="X-Confirm")) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        gate = _require_xconfirm(x_confirm)
        if gate is not None:
            return gate
        ok = controller.executor.cancel(order_id)
        if not ok:
            return error_response("not_found", "order not found or not cancellable", 404)
        return envelope(ok=True, data={"cancelled": True, "order_id": order_id})

    @app.get("/v1/positions")
    async def positions(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=controller.positions_list())

    @app.post("/v1/positions/{market_id}/close")
    async def close_position(market_id: str, authorization: str | None = Header(None),
                             x_confirm: str | None = Header(None, alias="X-Confirm")) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        gate = _require_xconfirm(x_confirm)
        if gate is not None:
            return gate
        prop, err = controller.close_position_proposal(market_id, via="app")
        if prop is None:
            return error_response("not_found", err or "position not found", 404)
        await bus.publish(Event("proposal_created", to_jsonable(prop)))
        return envelope(ok=True, data=to_jsonable(prop))

    @app.get("/v1/limits")
    async def get_limits(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=controller.limits_list())

    @app.put("/v1/limits")
    async def put_limits(body: dict[str, Any], authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        err = controller.set_limits(body)
        if err:
            return error_response("bad_limit", err, 400)
        return envelope(ok=True, data=controller.limits_list())

    # ── LLM ───────────────────────────────────────────────────

    @app.get("/v1/llm")
    async def llm_info(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        llm = build_llm_client(settings)
        configured = isinstance(llm, OpenAICompatClient)
        return envelope(ok=True, data={
            "configured": configured,
            "model": settings.llm_model if configured else None,
            "base_url": settings.llm_base_url if configured else None,
        })

    @app.post("/v1/llm/test")
    async def llm_test(authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        llm = build_llm_client(settings)
        if not isinstance(llm, OpenAICompatClient):
            return error_response("llm_not_configured", "set LLM_API_KEY and LLM_MODEL", 400)
        res = await llm.complete(LLMRequest(
            system_prompt="Answer by calling the provided function.",
            user_prompt="Call the function with ok=true.",
            response_schema={"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
        ))
        if not res.valid:
            why = f": {res.error}" if res.error else ""
            return error_response("llm_failed", f"LLM call failed{why}", 502)
        return envelope(ok=True, data={"model": llm.model, "reply": res.parsed})

    # ── Fixtures (A4) ─────────────────────────────────────────

    @app.get("/v1/fixtures")
    async def fixtures(state: str | None = None, authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        valid_states = {"live", "upcoming", "all", None}
        if state not in valid_states:
            return error_response("bad_state", "state must be live, upcoming, or all", 400)
        items = fixture_cache.list(state if state != "all" else None)
        return envelope(ok=True, data=items)

    @app.get("/v1/fixtures/{fixture_id}")
    async def fixture_detail(fixture_id: str, authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        all_items = fixture_cache.list()
        match = next(
            (f for f in all_items if f.get("id") == fixture_id or f.get("parent_market_id") == fixture_id),
            None,
        )
        if match is None:
            return error_response("not_found", "fixture not found", 404)
        return envelope(ok=True, data=match)

    # ── Rules (C3) ────────────────────────────────────────────

    @app.get("/v1/rules")
    async def list_rules(status: str | None = None, authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        rules = controller.list_rules(status)
        return envelope(ok=True, data=[r.to_dict() for r in rules])

    @app.post("/v1/rules/{rule_id}/confirm")
    async def confirm_rule(rule_id: str, authorization: str | None = Header(None),
                           x_confirm: str | None = Header(None, alias="X-Confirm")) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        gate = _require_xconfirm(x_confirm)
        if gate is not None:
            return gate
        rule = controller.confirm_rule(rule_id)
        if rule is None:
            return error_response("not_confirmable", "rule not found, not PENDING, or max active rules reached", 409)
        await bus.publish(Event("rule_updated", rule.to_dict()))
        return envelope(ok=True, data=rule.to_dict())

    @app.post("/v1/rules/{rule_id}/cancel")
    async def cancel_rule(rule_id: str, authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        rule = controller.cancel_rule(rule_id)
        if rule is None:
            return error_response("not_found", "rule not found or already terminal", 404)
        await bus.publish(Event("rule_updated", rule.to_dict()))
        return envelope(ok=True, data=rule.to_dict())

    # ── Chat (B2) ─────────────────────────────────────────────

    @app.post("/v1/chat")
    async def chat(body: dict[str, Any], request: Request,  # noqa: PLR0911
                   authorization: str | None = Header(None)) -> Any:
        actor = _verify_auth(authorization)
        if actor is None:
            return _unauth()

        # Rate limit per actor
        if not chat_rl.is_allowed(actor):
            return error_response("rate_limited", "too many chat messages, slow down", 429)

        message = str(body.get("message", "")).strip()
        if not message:
            return error_response("empty_message", "message is required", 400)
        if len(message) > 4000:
            return error_response("message_too_long", "message must be <= 4000 chars", 400)

        session_id = body.get("session_id")
        sid, history = chat_sessions.get_or_create(session_id, actor)

        try:
            response = await _chat_llm.chat(history, message)
        except Exception as exc:  # noqa: BLE001
            log.error("chat error: %s", exc)
            return error_response("llm_error", "Chat service error. Try again.", 502)

        # Store turn in session history
        chat_sessions.append(sid, ChatMessage(role="user", content=message))
        chat_sessions.append(sid, ChatMessage(role="assistant", content=response.reply))

        data: dict[str, Any] = {
            "session_id": sid,
            "reply": response.reply,
            "cards": [{"type": c.type, "data": c.data} for c in response.cards],
        }
        if response.error == "llm_not_configured":
            return JSONResponse(
                status_code=200,
                content=envelope(
                    ok=False,
                    data=data,
                    error={"code": "llm_not_configured", "message": response.reply},
                ),
            )
        return envelope(ok=True, data=data)

    @app.post("/v1/chat/stream")
    async def chat_stream(body: dict[str, Any], request: Request,  # noqa: PLR0911
                          authorization: str | None = Header(None)) -> Any:
        """Same as POST /v1/chat but streamed as Server-Sent Events.

        Events: ``session`` {session_id}, ``delta`` {text}, ``tool`` {name},
        ``cards`` {cards}, ``reset``, ``done`` {session_id, reply, cards, error}.
        Validation errors are returned as normal JSON error responses (before
        the stream starts).
        """
        actor = _verify_auth(authorization)
        if actor is None:
            return _unauth()
        if not chat_rl.is_allowed(actor):
            return error_response("rate_limited", "too many chat messages, slow down", 429)

        message = str(body.get("message", "")).strip()
        if not message:
            return error_response("empty_message", "message is required", 400)
        if len(message) > 4000:
            return error_response("message_too_long", "message must be <= 4000 chars", 400)

        sid, history = chat_sessions.get_or_create(body.get("session_id"), actor)

        def _sse(event: str, data: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

        async def _gen() -> Any:
            yield _sse("session", {"session_id": sid})
            final: dict[str, Any] | None = None
            try:
                async for ev in _chat_llm.chat_stream(history, message):
                    etype = ev.get("type", "")
                    if etype == "done":
                        final = ev
                        break
                    yield _sse(etype, {k: v for k, v in ev.items() if k != "type"})
            except Exception as exc:  # noqa: BLE001
                log.error("chat stream error: %s", exc)
                yield _sse("done", {"session_id": sid, "reply": "Chat service error. Try again.",
                                    "cards": [], "error": "llm_error"})
                return
            if final is None:
                final = {"reply": "", "cards": [], "error": "llm_error"}
            # Persist the turn only when the model produced a real reply.
            if final.get("error") is None:
                chat_sessions.append(sid, ChatMessage(role="user", content=message))
                chat_sessions.append(sid, ChatMessage(role="assistant", content=final.get("reply", "")))
            yield _sse("done", {
                "session_id": sid,
                "reply": final.get("reply", ""),
                "cards": final.get("cards", []),
                "error": final.get("error"),
            })

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/chat/{session_id}")
    async def chat_history(session_id: str, authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        history = chat_sessions.get_history(session_id)
        return envelope(ok=True, data={
            "session_id": session_id,
            "messages": [{"role": m.role, "content": m.content} for m in history],
        })

    @app.delete("/v1/chat/{session_id}")
    async def clear_chat(session_id: str, authorization: str | None = Header(None)) -> Any:
        if _verify_auth(authorization) is None:
            return _unauth()
        cleared = chat_sessions.clear(session_id)
        if not cleared:
            return error_response("not_found", "session not found", 404)
        return envelope(ok=True, data={"cleared": True})

    # ── WebSocket (A5) ────────────────────────────────────────

    @app.websocket("/v1/ws")
    async def ws(websocket: WebSocket) -> Any:
        await websocket.accept()
        device_id: str | None = None
        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                mtype = msg.get("type")

                if mtype == "auth":
                    token = msg.get("token", "")
                    device_id = _verify_token(token)
                    if device_id is None:
                        await websocket.send_text(json.dumps({"type": "error", "error": "invalid_token"}))
                        await websocket.close(code=4001)
                        return
                    await ws_manager.connect(device_id, websocket)
                    await websocket.send_text(json.dumps({"type": "auth_ok", "device_id": device_id}))

                elif mtype == "subscribe":
                    if device_id is None:
                        await websocket.send_text(json.dumps({"type": "error", "error": "not_authenticated"}))
                        continue
                    markets = msg.get("markets", [])
                    ws_manager.subscribe(device_id, markets)
                    await websocket.send_text(json.dumps({"type": "subscribed", "markets": markets}))

                elif mtype == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

                else:
                    await websocket.send_text(json.dumps({"type": "error", "error": "unknown_type"}))

        except WebSocketDisconnect:
            pass
        finally:
            if device_id:
                ws_manager.disconnect(device_id)

    return app


# ─────────────────────────────────────────────────────────────
# Lazy singleton
# ─────────────────────────────────────────────────────────────

_app_instance: FastAPI | None = None


def get_app() -> FastAPI:
    global _app_instance  # noqa: PLW0603
    if _app_instance is None:
        _app_instance = create_app()
    return _app_instance
