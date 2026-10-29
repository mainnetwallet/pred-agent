"""E1 chat write-intent tools — PENDING-only creation, fail-closed pricing, action cards.

Hard rules covered:
  - The LLM tool set contains read tools + PENDING-creation tools only.
    There is no approve / execute / confirm / place_order tool.
  - propose_trade fails closed when no live book exists.
  - create_rule / propose_trade / close_position only create PENDING objects.
  - Action tools (cancel_order / cancel_rule / request_pause / request_panic)
    return confirmation cards and never mutate state themselves.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from pred_agent.book import BookLevel, BookSnapshot
from pred_agent.config import load_settings
from pred_agent.control_api.app import create_app
from pred_agent.control_api.controller import AgentController
from pred_agent.scanner import DiscoveredParent

READ_TOOLS = {
    "list_fixtures", "get_fixture", "get_book", "get_status",
    "get_positions", "get_orders", "get_rules", "get_limits", "get_proposals",
}
WRITE_INTENT_TOOLS = {
    "propose_trade", "create_rule", "close_position",
    "cancel_order", "cancel_rule", "request_pause", "request_resume", "request_panic",
}
FORBIDDEN_TOOLS = {"approve", "execute", "confirm", "place_order", "trade", "cancel_all"}


def _fixture_parent(market_id: str = "mx1") -> DiscoveredParent:
    return DiscoveredParent(
        parent_market_id="pm1",
        type_reference_id=None,
        contract_address=None,
        parent_market_type=None,
        parent_market_family=None,
        canonical_name=None,
        title="Test vs Test",
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


def _snapshot(market_id: str, bid: str, ask: str) -> BookSnapshot:
    bid_d, ask_d = Decimal(bid), Decimal(ask)
    return BookSnapshot(
        market_id=market_id,
        bids=[BookLevel(price_cents=bid_d, quantity=Decimal("10"))],
        asks=[BookLevel(price_cents=ask_d, quantity=Decimal("10"))],
        ltp=None,
        spread_cents=ask_d - bid_d,
        mid_price_cents=(bid_d + ask_d) / 2,
        last_updated_at=None,
        sequence_number=None,
    )


class _FakeBookFeed:
    def __init__(self, snapshots: dict[str, BookSnapshot]) -> None:
        self._snapshots = snapshots

    def get_book(self, market_id: str) -> BookSnapshot | None:
        return self._snapshots.get(market_id)


def _run(fn: Any, **kwargs: Any) -> tuple[Any, list[Any]]:
    return asyncio.run(fn(**kwargs))


# ---------------------------------------------------------------------------
# Tool set surface
# ---------------------------------------------------------------------------

def test_chat_tool_names_cover_read_and_pending_intents():
    app = create_app()
    names = set(app.state.chat_tools)
    assert READ_TOOLS <= names
    assert WRITE_INTENT_TOOLS <= names
    assert len(FORBIDDEN_TOOLS & names) == 0, f"forbidden tools exposed: {FORBIDDEN_TOOLS & names}"


def test_chat_tool_fns_exposed_for_introspection():
    app = create_app()
    fns = app.state.chat_tool_fns
    assert set(fns.keys()) == set(app.state.chat_tools)
    assert callable(fns["propose_trade"])
    assert callable(fns["request_panic"])


# ---------------------------------------------------------------------------
# propose_trade: fail closed without book
# ---------------------------------------------------------------------------

def test_propose_trade_fails_closed_without_book():
    app = create_app()
    app.state.fixture_cache.update([_fixture_parent("mx1")])
    data, cards = _run(
        app.state.chat_tool_fns["propose_trade"],
        market_id="mx1", side="long", amount_usd="5",
    )
    assert "error" in data
    assert "no live price" in data["error"]
    assert cards == []
    assert len(app.state.controller.proposals.list_pending()) == 0


def test_propose_trade_fails_closed_for_unknown_market():
    app = create_app()
    app.state.fixture_cache.update([_fixture_parent("mx1")])
    data, cards = _run(
        app.state.chat_tool_fns["propose_trade"],
        market_id="mx404", side="long", amount_usd="5",
    )
    assert "error" in data
    assert cards == []


# ---------------------------------------------------------------------------
# propose_trade: PENDING proposal with a live book
# ---------------------------------------------------------------------------

def test_propose_trade_creates_pending_proposal():
    app = create_app()
    app.state.fixture_cache.update([_fixture_parent("mx1")])
    app.state.book_feed = _FakeBookFeed({"mx1": _snapshot("mx1", bid="50", ask="54")})

    data, cards = _run(
        app.state.chat_tool_fns["propose_trade"],
        market_id="mx1", side="long", amount_usd="1.5",
    )
    assert data["status"] == "PROPOSED"
    assert len(cards) == 1 and cards[0].type == "proposal"
    card_data = cards[0].data
    assert card_data["market_id"] == "mx1"
    assert card_data["price_cents"] == "54"  # long buys at best ask
    assert card_data["status"] == "PROPOSED"

    pending = app.state.controller.proposals.list_pending()
    assert len(pending) == 1
    assert pending[0].id == data["proposal_id"]


def test_propose_trade_short_uses_best_bid():
    app = create_app()
    app.state.fixture_cache.update([_fixture_parent("mx1")])
    app.state.book_feed = _FakeBookFeed({"mx1": _snapshot("mx1", bid="46", ask="50")})

    data, cards = _run(
        app.state.chat_tool_fns["propose_trade"],
        market_id="mx1", side="short", amount_usd="1.5",
    )
    assert data["status"] == "PROPOSED"
    assert cards[0].data["price_cents"] == "46"  # short sells at best bid


def test_propose_trade_rejects_paused_agent():
    app = create_app()
    app.state.fixture_cache.update([_fixture_parent("mx1")])
    app.state.book_feed = _FakeBookFeed({"mx1": _snapshot("mx1", bid="50", ask="54")})
    app.state.controller.pause()

    data, cards = _run(
        app.state.chat_tool_fns["propose_trade"],
        market_id="mx1", side="long", amount_usd="1.5",
    )
    assert "error" in data and "paused" in data["error"]
    assert cards == []


# ---------------------------------------------------------------------------
# create_rule: PENDING rule for an open position only
# ---------------------------------------------------------------------------

def _controller_with_position() -> AgentController:
    settings = load_settings()
    ctrl = AgentController(settings)
    ctrl.positions.open_position(
        market_id="mx1", parent_id="fx1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"),
        cost_usd=Decimal("5.20"),
    )
    return ctrl


def test_create_rule_tool_creates_pending_rule():
    app = create_app()
    app.state.controller.positions.open_position(
        market_id="mx1", parent_id="fx1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"),
        cost_usd=Decimal("5.20"),
    )
    data, cards = _run(
        app.state.chat_tool_fns["create_rule"],
        market_id="mx1", kind="take_profit", trigger_type="price_cents",
        trigger_value="70",
    )
    assert data["status"] == "PENDING"
    assert len(cards) == 1 and cards[0].type == "rule"
    assert cards[0].data["status"] == "PENDING"
    assert cards[0].data["trigger_type"] == "price_cents"


def test_create_rule_tool_requires_existing_position():
    app = create_app()
    data, cards = _run(
        app.state.chat_tool_fns["create_rule"],
        market_id="mx9", kind="take_profit", trigger_type="price_cents",
        trigger_value="70",
    )
    assert "error" in data and "no open position" in data["error"]
    assert cards == []


def test_create_rule_tool_rejects_insane_trigger():
    app = create_app()
    app.state.controller.positions.open_position(
        market_id="mx1", parent_id="fx1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"),
        cost_usd=Decimal("5.20"),
    )
    # take-profit below entry is nonsense for a long
    data, cards = _run(
        app.state.chat_tool_fns["create_rule"],
        market_id="mx1", kind="take_profit", trigger_type="price_cents",
        trigger_value="40",
    )
    assert "error" in data
    assert cards == []


# ---------------------------------------------------------------------------
# close_position: PENDING proposal, position stays open until human approves
# ---------------------------------------------------------------------------

def test_close_position_tool_creates_pending_proposal():
    app = create_app()
    app.state.controller.positions.open_position(
        market_id="mx1", parent_id="fx1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"),
        cost_usd=Decimal("5.20"),
    )
    data, cards = _run(app.state.chat_tool_fns["close_position"], market_id="mx1")
    assert data["status"] == "PROPOSED"
    assert len(cards) == 1 and cards[0].type == "proposal"
    assert cards[0].data["side"] == "short"  # reduce-only exit of a long

    # Position must still be open — only the human approve closes it
    pos = app.state.controller.positions.get_position("mx1")
    assert pos is not None and pos.status == "open"


# ---------------------------------------------------------------------------
# Action tools: confirmation cards only, never mutate state
# ---------------------------------------------------------------------------

def test_action_tools_return_cards_and_never_execute():
    app = create_app()
    ctrl = app.state.controller

    order = ctrl.executor.place(
        proposal_id="px1", market_id="mx1", side="long",
        price_cents=Decimal("52"), quantity=Decimal("3.8"),
        amount_usd=Decimal("1.98"), order_type="limit",
    )
    rule = ctrl.rules.create(
        market_id="mx1", parent_market_id="fx1", position_ref="mx1",
        kind="take_profit", trigger_type="profit_usd", trigger_value=Decimal("1"),
        size="all", created_via="test", mode="PAPER",
    )

    data, cards = _run(app.state.chat_tool_fns["cancel_order"], order_id=order.id)
    assert data == {"card_created": "cancel_order"}
    assert cards[0].type == "action" and cards[0].data["action"] == "cancel_order"
    assert ctrl.executor.list_orders()[0].status != "cancelled"  # NOT cancelled by the LLM

    data, cards = _run(app.state.chat_tool_fns["cancel_rule"], rule_id=rule.id)
    assert cards[0].data["action"] == "cancel_rule"
    assert ctrl.rules.get(rule.id).status == "PENDING"  # NOT cancelled by the LLM

    data, cards = _run(app.state.chat_tool_fns["request_pause"])
    assert cards[0].data["action"] == "pause"
    assert ctrl.paused is False  # NOT paused by the LLM

    data, cards = _run(app.state.chat_tool_fns["request_panic"])
    assert cards[0].data["action"] == "panic"
    assert ctrl.paused is False  # NOT panicked by the LLM


def test_cancel_order_tool_rejects_unknown_order():
    app = create_app()
    data, cards = _run(app.state.chat_tool_fns["cancel_order"], order_id="nope")
    assert "error" in data
    assert cards == []


# ---------------------------------------------------------------------------
# get_book: REST fallback on cache miss, spread/mid computed
# ---------------------------------------------------------------------------

class _FetchingBookFeed(_FakeBookFeed):
    def __init__(self, fetched: BookSnapshot | None) -> None:
        super().__init__({})
        self._fetched = fetched
        self.calls: list[tuple[str, str]] = []

    def parent_of(self, market_id: str) -> str | None:
        return "0xparent"

    async def fetch_book_rest(self, parent: str, market_id: str) -> BookSnapshot | None:
        self.calls.append((parent, market_id))
        return self._fetched


def test_get_book_falls_back_to_rest_and_computes_spread():
    app = create_app()
    raw = _snapshot("0xm1", "40", "44")
    raw = BookSnapshot(**{**raw.__dict__, "spread_cents": None, "mid_price_cents": None})
    feed = _FetchingBookFeed(raw)
    app.state.book_feed = feed
    out = asyncio.run(app.state.chat_tool_fns["get_book"](market_id="0xm1"))
    assert feed.calls == [("0xparent", "0xm1")]
    assert out["best_bid_cents"] == "40"
    assert out["spread_cents"] == "4"
    assert out["mid_price_cents"] == "42"


def test_get_book_reports_detail_when_rest_fails():
    app = create_app()
    app.state.book_feed = _FetchingBookFeed(None)
    out = asyncio.run(app.state.chat_tool_fns["get_book"](market_id="0xm1"))
    assert out["error"] == "no book data for market 0xm1"
    assert "REST" in out["detail"]
