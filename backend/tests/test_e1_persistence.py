"""E1 persistence tests — proposals, positions, orders, rules, chat survive restarts.

Uses an in-memory SQLite engine shared across service instances to simulate
a process restart (new service objects, same database).
"""
from __future__ import annotations

from decimal import Decimal

from pred_agent.execution import PaperExecutor
from pred_agent.llm.chat import ChatMessage
from pred_agent.positions import PositionManager
from pred_agent.proposals import ProposalService, ProposalStatus
from pred_agent.rules import RulesService
from pred_agent.store.repository import (
    ChatRepo,
    OrderRepo,
    PositionRepo,
    ProposalRepo,
    RuleRepo,
)
from pred_agent.store.session import create_test_engine


def _engine_factory():
    engine = create_test_engine()
    return engine


def _sf(engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_proposals_survive_restart():
    engine = create_test_engine()
    sf = _sf(engine)

    svc = ProposalService(ttl_seconds=300, db=ProposalRepo(sf))
    prop = svc.create(
        mode="PAPER", advisory=False, fixture_id="fx1", market_id="mx1",
        side="long", order_type="limit", price_cents=Decimal("52"),
        quantity=Decimal("3.8"), amount_usd=Decimal("1.98"), est_fee_usd=Decimal("0.04"),
        edge_pp=Decimal("5"), p_fair=Decimal("0.58"),
        max_suggested_usd=Decimal("2"), binding_cap=None,
    )
    svc.approve(prop.id, via="app")

    # "Restart": fresh service over the same DB
    svc2 = ProposalService(ttl_seconds=300, db=ProposalRepo(sf))
    restored = svc2.get(prop.id)
    assert restored is not None
    assert restored.status == ProposalStatus.APPROVED
    assert restored.market_id == "mx1"
    assert restored.amount_usd == Decimal("1.98")
    assert restored.decided_via == "app"


def test_positions_and_pnl_survive_restart():
    engine = create_test_engine()
    sf = _sf(engine)

    pm = PositionManager(db=PositionRepo(sf))
    pm.open_position(
        market_id="mx1", parent_id="px1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"),
        cost_usd=Decimal("5.20"),
    )
    pm.close_position("mx1", realized_pnl_usd=Decimal("1.50"))

    pm2 = PositionManager(db=PositionRepo(sf))
    closed = pm2.get_position("mx1")
    assert closed is not None
    assert closed.status == "closed"
    assert pm2.total_pnl() == Decimal("1.50")
    assert pm2.list_positions() == []  # no open positions


def test_orders_survive_restart():
    engine = create_test_engine()
    sf = _sf(engine)

    ex = PaperExecutor(db=OrderRepo(sf))
    order = ex.place(
        proposal_id="p1", market_id="mx1", side="long",
        price_cents=Decimal("52"), quantity=Decimal("3.8"),
        amount_usd=Decimal("1.98"), order_type="ioc",  # IOC fills immediately
    )
    assert order.status == "filled"

    ex2 = PaperExecutor(db=OrderRepo(sf))
    orders = ex2.list_orders()
    assert len(orders) == 1
    assert orders[0].id == order.id
    assert orders[0].status == "filled"
    assert orders[0].quantity == Decimal("3.8")


def test_rules_survive_restart_and_keep_state():
    engine = create_test_engine()
    sf = _sf(engine)

    rules = RulesService(db=RuleRepo(sf))
    rule = rules.create(
        market_id="mx1", parent_market_id="px1", position_ref="mx1",
        kind="take_profit", trigger_type="profit_usd", trigger_value=Decimal("3"),
    )
    rules.confirm(rule.id)

    rules2 = RulesService(db=RuleRepo(sf))
    restored = rules2.get(rule.id)
    assert restored is not None
    assert restored.status == "ACTIVE"
    assert restored.trigger_value == Decimal("3")
    assert restored.kind == "take_profit"

    # A fired rule must stay fired after restart
    import asyncio

    pm = PositionManager()
    pm.open_position(
        market_id="mx1", parent_id="px1", side="long",
        quantity=Decimal("10"), avg_price_cents=Decimal("52"),
        cost_usd=Decimal("5.20"),
    )
    asyncio.run(rules2.try_fire(
        restored,
        position=pm.get_position("mx1"),  # type: ignore[arg-type]
        current_price_cents=Decimal("95"),
        executor=PaperExecutor(),
    ))
    assert restored.status == "DONE"

    rules3 = RulesService(db=RuleRepo(sf))
    assert rules3.get(rule.id).status == "DONE"  # type: ignore[union-attr]


def test_chat_sessions_survive_restart():
    engine = create_test_engine()
    sf = _sf(engine)

    repo = ChatRepo(sf)
    from pred_agent.control_api.app import ChatSessionStore

    store = ChatSessionStore(db=repo)
    sid, _ = store.get_or_create(None, "app:device1")
    store.append(sid, ChatMessage(role="user", content="ekhon live match ki?"))
    store.append(sid, ChatMessage(role="assistant", content="Arsenal vs Chelsea"))

    store2 = ChatSessionStore(db=ChatRepo(sf))
    history = store2.get_history(sid)
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[1].content == "Arsenal vs Chelsea"

    # Clear deletes rows
    assert store2.clear(sid) is True
    store3 = ChatSessionStore(db=ChatRepo(sf))
    assert store3.get_history(sid) == []
