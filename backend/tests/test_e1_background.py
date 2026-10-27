"""E1 background wiring tests — scanner pass + rules monitor (fail-closed).

Covers:
  - scan_once: discovery → fixture cache → DB upsert → book tracking → event
  - rules monitor: position gone → EXPIRED
  - rules monitor: no book / stale book / no evaluable price → BLOCKED (fail closed)
  - rules monitor: fresh book → unblock + fire (position closed, PnL realized)
  - stop-loss escalation alert after BLOCKED_ESCALATION_SECONDS
  - BookFeed CLOB poller fetches tracked markets via parent map
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from pred_agent.book import BookSnapshot, normalize_book
from pred_agent.config import load_settings
from pred_agent.control_api.app import FixtureCache
from pred_agent.control_api.background import RulesMonitor, scan_once
from pred_agent.control_api.controller import AgentController
from pred_agent.events import EventBus
from pred_agent.realtime import BookFeed
from pred_agent.rules import BLOCKED_ESCALATION_SECONDS
from pred_agent.scanner import DiscoveredParent
from pred_agent.store.models import Fixture
from pred_agent.store.session import create_test_engine


def _parent(market_id: str = "mx1") -> DiscoveredParent:
    return DiscoveredParent(
        parent_market_id="pm1",
        type_reference_id=None,
        contract_address=None,
        parent_market_type=None,
        parent_market_family="moneyline",
        canonical_name=None,
        title="Alpha vs Beta",
        status="active",
        fee_rate_bps=250,
        fixture={
            "fixture_id": "fx1",
            "home_team_id": "t1",
            "away_team_id": "t2",
            "league_name": "Test League",
            "game_start_time": "2099-01-01T12:00:00Z",
        },
        team_data=[
            {"team_id": "t1", "team_name": "Alpha"},
            {"team_id": "t2", "team_name": "Beta"},
        ],
        markets=[{"market_id": market_id, "status": "active"}],
    )


class _FakeScanner:
    def __init__(self, results: list[DiscoveredParent]) -> None:
        self.results = results
        self.upsert_calls = 0
        from pred_agent.scanner import Scanner

        self._real = Scanner()

    def scan_active_moneyline(self) -> list[DiscoveredParent]:
        return self.results

    def upsert_to_db(self, session: Any, results: list[DiscoveredParent]) -> int:
        self.upsert_calls += 1
        return self._real.upsert_to_db(session, results)


class _FakeBookFeed:
    """Book feed fake with the interface RulesMonitor uses."""

    def __init__(self, snapshots: dict[str, BookSnapshot], stale_ids: set[str] | None = None) -> None:
        self._snapshots = snapshots
        self.stale_ids = stale_ids or set()
        self.tracked: dict[str, str | None] = {}
        self.fetch_calls = 0

    def get_book(self, market_id: str) -> BookSnapshot | None:
        return self._snapshots.get(market_id)

    def is_stale(self, market_id: str, max_age_s: int) -> bool:
        return market_id in self.stale_ids

    def parent_of(self, market_id: str) -> str | None:
        return self.tracked.get(market_id)

    def track_market(self, market_id: str, parent_market_id: str | None = None) -> None:
        self.tracked[market_id] = parent_market_id

    async def fetch_book_rest(self, parent_market_id: str, market_id: str) -> BookSnapshot | None:
        self.fetch_calls += 1
        return self._snapshots.get(market_id)


def _snapshot(market_id: str, bid: str, ask: str) -> BookSnapshot:
    return normalize_book(
        {
            "bids": [{"price": bid, "quantity": "10"}],
            "asks": [{"price": ask, "quantity": "10"}],
        },
        market_id,
        ws_dollars=False,
    )


def _controller_with_position(market_id: str = "mx1", side: str = "long",
                              entry: str = "52") -> AgentController:
    ctrl = AgentController(load_settings())
    ctrl.positions.open_position(
        market_id=market_id, parent_id="pm1", side=side,
        quantity=Decimal("10"), avg_price_cents=Decimal(entry),
        cost_usd=Decimal("5.20"),
    )
    return ctrl


# ---------------------------------------------------------------------------
# scan_once
# ---------------------------------------------------------------------------

def test_scan_once_updates_cache_db_tracks_and_publishes():
    scanner = _FakeScanner([_parent("mx1")])
    engine = create_test_engine()
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    cache = FixtureCache()
    feed = _FakeBookFeed({})
    ctrl = AgentController(load_settings())
    bus = EventBus()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    bus.subscribe("fixture_update", queue)

    results = asyncio.run(scan_once(
        scanner=scanner, fixture_cache=cache, session_factory=factory,
        book_feed=feed, controller=ctrl, bus=bus,
    ))

    assert len(results) == 1
    assert scanner.upsert_calls == 1
    assert feed.tracked == {"mx1": "pm1"}
    assert ctrl.feeds_ok is True
    listed = cache.list()
    assert listed[0]["home_team"] == "Alpha"
    assert listed[0]["markets"][0]["market_id"] == "mx1"
    event = queue.get_nowait()
    assert event.type == "fixture_update" and event.payload["count"] == 1

    with factory() as session:
        row = session.query(Fixture).filter_by(pred_fixture_id="fx1").first()
        assert row is not None and row.league_name == "Test League"


def test_scan_once_empty_results_skips_everything():
    scanner = _FakeScanner([])
    engine = create_test_engine()
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    cache = FixtureCache()
    feed = _FakeBookFeed({})
    ctrl = AgentController(load_settings())
    bus = EventBus()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    bus.subscribe("fixture_update", queue)

    results = asyncio.run(scan_once(
        scanner=scanner, fixture_cache=cache, session_factory=factory,
        book_feed=feed, controller=ctrl, bus=bus,
    ))

    assert results == []
    assert scanner.upsert_calls == 0
    assert feed.tracked == {}
    assert queue.empty()
    assert ctrl.feeds_ok is False  # never marked fresh


# ---------------------------------------------------------------------------
# rules monitor
# ---------------------------------------------------------------------------

def _mk_rule(ctrl: AgentController, *, kind: str = "take_profit",
             trigger_type: str = "price_cents", trigger_value: str = "60") -> Any:
    rule = ctrl.rules.create(
        market_id="mx1", parent_market_id="pm1", position_ref="mx1",
        kind=kind, trigger_type=trigger_type, trigger_value=Decimal(trigger_value),
        size="all", created_via="test", mode="PAPER",
    )
    assert ctrl.rules.confirm(rule.id) is not None  # PENDING → ACTIVE
    return rule


def test_monitor_expires_rule_when_position_gone():
    ctrl = _controller_with_position()
    rule = _mk_rule(ctrl)
    ctrl.positions.close_position("mx1", realized_pnl_usd=Decimal("0"))

    stats = asyncio.run(RulesMonitor().run_once(
        controller=ctrl, book_feed=_FakeBookFeed({}),
    ))
    assert stats["expired"] == 1
    assert ctrl.rules.get(rule.id).status == "EXPIRED"


def test_monitor_blocks_rule_without_book_feed():
    ctrl = _controller_with_position()
    rule = _mk_rule(ctrl)
    alerts: list[str] = []

    async def alert(text: str) -> None:
        alerts.append(text)

    stats = asyncio.run(RulesMonitor().run_once(
        controller=ctrl, book_feed=None, alert_fn=alert,
    ))
    assert stats["blocked"] == 1
    assert ctrl.rules.get(rule.id).status == "BLOCKED"
    assert alerts and "BLOCKED" in alerts[0]


def test_monitor_blocks_on_stale_book_then_unblocks_and_fires():
    ctrl = _controller_with_position()  # long @ 52
    rule = _mk_rule(ctrl, trigger_value="60")
    feed = _FakeBookFeed({"mx1": _snapshot("mx1", "64", "66")}, stale_ids={"mx1"})

    stats = asyncio.run(RulesMonitor().run_once(controller=ctrl, book_feed=feed))
    assert stats["blocked"] == 1
    assert ctrl.rules.get(rule.id).status == "BLOCKED"

    # Book freshens and price (mid 65) is above trigger 60 → unblock + fire
    feed.stale_ids.clear()
    stats = asyncio.run(RulesMonitor().run_once(controller=ctrl, book_feed=feed))
    assert stats["unblocked"] == 1
    assert stats["fired"] == 1
    assert ctrl.rules.get(rule.id).status == "DONE"
    assert ctrl.positions.get_position("mx1").status == "closed"
    assert ctrl.positions.total_pnl() > 0  # realized profit


def test_monitor_blocks_when_no_evaluable_price():
    ctrl = _controller_with_position()
    rule = _mk_rule(ctrl)
    empty = normalize_book({"bids": [], "asks": []}, "mx1", ws_dollars=False)
    stats = asyncio.run(RulesMonitor().run_once(
        controller=ctrl, book_feed=_FakeBookFeed({"mx1": empty}),
    ))
    assert stats["blocked"] == 1
    assert ctrl.rules.get(rule.id).status == "BLOCKED"


def test_monitor_publishes_rule_blocked_and_triggered_events():
    ctrl = _controller_with_position()
    rule = _mk_rule(ctrl, trigger_value="60")
    bus = EventBus()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    bus.subscribe("rule_blocked", queue)
    bus.subscribe("rule_triggered", queue)

    # blocked first (no feed), then fresh book → fires
    asyncio.run(RulesMonitor().run_once(
        controller=ctrl, book_feed=None, bus=bus,
    ))
    feed = _FakeBookFeed({"mx1": _snapshot("mx1", "64", "66")})
    asyncio.run(RulesMonitor().run_once(controller=ctrl, book_feed=feed, bus=bus))

    events = [queue.get_nowait().type for _ in range(queue.qsize())]
    assert "rule_blocked" in events
    assert "rule_triggered" in events
    assert ctrl.rules.get(rule.id).status == "DONE"


def test_stop_loss_escalation_after_threshold():
    ctrl = _controller_with_position()
    rule = _mk_rule(ctrl, kind="stop_loss", trigger_type="price_cents", trigger_value="45")
    # Simulate being blocked long ago (e.g. before this process restarted)
    rule.blocked_since = datetime.now(timezone.utc) - timedelta(
        seconds=BLOCKED_ESCALATION_SECONDS + 30
    )
    alerts: list[str] = []

    async def alert(text: str) -> None:
        alerts.append(text)

    asyncio.run(RulesMonitor().run_once(
        controller=ctrl, book_feed=None, alert_fn=alert,
    ))
    assert ctrl.rules.get(rule.id).status == "BLOCKED"
    assert any("ESCALATION" in a for a in alerts)


# ---------------------------------------------------------------------------
# BookFeed CLOB poller (parent map)
# ---------------------------------------------------------------------------

class _RawTransport:
    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self.calls = 0

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls += 1
        return self._raw


@pytest.mark.asyncio
async def test_poll_clob_books_fetches_tracked_markets(monkeypatch):
    raw = {
        "bids": [{"price": "49", "quantity": "10"}],
        "asks": [{"price": "51", "quantity": "10"}],
    }
    transport = _RawTransport(raw)
    feed = BookFeed(transport)
    # Keep the poller hermetic: no real network to pred.app
    monkeypatch.setattr(feed, "_http_get_no_auth", _no_auth_none)
    feed.track_market("mx1", parent_market_id="pm1")

    await feed.start()
    task = asyncio.create_task(feed.poll_clob_books())
    await asyncio.sleep(0.05)
    await feed.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    snap = feed.get_book("mx1")
    assert snap is not None
    assert snap.bids[0].price_cents == Decimal("49")
    assert transport.calls >= 1


async def _no_auth_none(path: str) -> dict[str, Any] | None:
    return None
