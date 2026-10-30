"""DB repositories — persistence for proposals, orders, positions, PnL, rules, chat.

Each service keeps its in-memory structures as the source of truth and
write-through to the DB when a repository is attached. On startup the
services load rows back into memory, so state survives restarts.

If the tables do not exist yet (dev without ``alembic upgrade head``), the
repositories degrade to in-memory only and log a single warning.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from pred_agent.execution.paper_executor import PaperOrder
from pred_agent.llm.chat import ChatMessage
from pred_agent.positions import PnLEntry, Position
from pred_agent.proposals import Proposal, ProposalStatus
from pred_agent.rules import Rule
from pred_agent.store.models import (
    ChatMessage as ChatMessageRow,
)
from pred_agent.store.models import (
    ChatSession as ChatSessionRow,
)
from pred_agent.store.models import (
    OrderRow,
    PnLLedgerRow,
    PositionRow,
    ProposalRow,
    RuleRow,
)

log = logging.getLogger(__name__)

_TABLE_MISSING_WARNED: set[str] = set()


def _warn_missing_tables(exc: Exception) -> None:
    name = type(exc).__name__
    if name not in _TABLE_MISSING_WARNED:
        _TABLE_MISSING_WARNED.add(name)
        log.warning(
            "DB tables missing (%s) — running in-memory only. Run `alembic upgrade head`.",
            name,
        )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _dec(value: str | None) -> Decimal:
    return Decimal(value) if value else Decimal("0")


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _session(session_factory: sessionmaker[Session]) -> Session:
    return session_factory()


class ProposalRepo:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def load_all(self) -> dict[str, Proposal]:
        try:
            with _session(self._sf) as sess:
                rows = sess.query(ProposalRow).all()
                return {
                    r.id: Proposal(
                        id=r.id,
                        mode=r.mode,
                        advisory=bool(r.advisory),
                        fixture_id=r.fixture_id,
                        market_id=r.market_id,
                        side=r.side,
                        order_type=r.order_type,
                        price_cents=_dec(r.price_cents),
                        quantity=_dec(r.quantity),
                        amount_usd=_dec(r.amount_usd),
                        est_fee_usd=_dec(r.est_fee_usd),
                        edge_pp=_dec(r.edge_pp),
                        p_fair=_dec(r.p_fair),
                        max_suggested_usd=_dec(r.max_suggested_usd),
                        binding_cap=r.binding_cap,
                        reasons=_json_list(r.reasons),
                        risks=_json_list(r.risks),
                        status=ProposalStatus(r.status),
                        created_at=_aware(r.created_at) or datetime.now(timezone.utc),
                        expires_at=_aware(r.expires_at),
                        decided_at=_aware(r.decided_at),
                        decided_via=r.via,
                    )
                    for r in rows
                }
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)
            return {}

    def save(self, prop: Proposal) -> None:
        try:
            with _session(self._sf) as sess:
                row = sess.get(ProposalRow, prop.id)
                if row is None:
                    row = ProposalRow(id=prop.id)
                    sess.add(row)
                row.mode = prop.mode
                row.advisory = prop.advisory
                row.fixture_id = prop.fixture_id
                row.market_id = prop.market_id
                row.side = prop.side
                row.order_type = prop.order_type
                row.price_cents = str(prop.price_cents)
                row.quantity = str(prop.quantity)
                row.amount_usd = str(prop.amount_usd)
                row.est_fee_usd = str(prop.est_fee_usd)
                row.edge_pp = str(prop.edge_pp)
                row.p_fair = str(prop.p_fair)
                row.max_suggested_usd = str(prop.max_suggested_usd)
                row.binding_cap = prop.binding_cap
                row.reasons = json.dumps(prop.reasons)
                row.risks = json.dumps(prop.risks)
                row.status = prop.status.value
                row.via = prop.decided_via
                row.created_at = prop.created_at
                row.expires_at = prop.expires_at
                row.decided_at = prop.decided_at
                sess.commit()
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)


class OrderRepo:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def load_all(self) -> dict[str, PaperOrder]:
        try:
            with _session(self._sf) as sess:
                rows = sess.query(OrderRow).all()
                return {
                    r.id: PaperOrder(
                        id=r.id,
                        proposal_id=r.proposal_id or "",
                        market_id=r.market_id or "",
                        side=r.side,
                        price_cents=Decimal(r.price_cents),
                        quantity=_dec(r.quantity),
                        amount_usd=_dec(r.amount_usd),
                        status=r.state,
                        filled_qty=_dec(r.filled_qty),
                        created_at=_aware(r.created_at) or datetime.now(timezone.utc),
                    )
                    for r in rows
                }
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)
            return {}

    def save(self, order: PaperOrder) -> None:
        try:
            with _session(self._sf) as sess:
                row = sess.get(OrderRow, order.id)
                if row is None:
                    row = OrderRow(id=order.id)
                    sess.add(row)
                row.proposal_id = order.proposal_id or None
                row.state = order.status
                row.side = order.side
                row.price_cents = int(order.price_cents)
                row.quantity = str(order.quantity)
                row.amount_usd = str(order.amount_usd)
                row.market_id = order.market_id
                row.filled_qty = str(order.filled_qty)
                row.created_at = order.created_at
                sess.commit()
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)


class PositionRepo:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def load_positions(self) -> dict[str, Position]:
        try:
            with _session(self._sf) as sess:
                rows = sess.query(PositionRow).all()
                return {
                    r.market_id: Position(
                        market_id=r.market_id,
                        parent_id=r.parent_id,
                        side=r.side,
                        quantity=_dec(r.quantity),
                        avg_price_cents=_dec(r.avg_price_cents),
                        cost_usd=_dec(r.cost_usd),
                        status=r.status,
                        opened_at=_aware(r.opened_at) or datetime.now(timezone.utc),
                        closed_at=_aware(r.closed_at),
                    )
                    for r in rows
                }
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)
            return {}

    def save(self, position: Position) -> None:
        try:
            with _session(self._sf) as sess:
                row = sess.get(PositionRow, position.market_id)
                if row is None:
                    row = PositionRow(market_id=position.market_id)
                    sess.add(row)
                row.parent_id = position.parent_id
                row.side = position.side
                row.quantity = str(position.quantity)
                row.avg_price_cents = str(position.avg_price_cents)
                row.cost_usd = str(position.cost_usd)
                row.status = position.status
                row.opened_at = position.opened_at
                row.closed_at = position.closed_at
                sess.commit()
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)

    def load_pnl(self) -> list[PnLEntry]:
        try:
            with _session(self._sf) as sess:
                rows = sess.query(PnLLedgerRow).order_by(PnLLedgerRow.id).all()
                return [
                    PnLEntry(
                        ts=_aware(r.ts) or datetime.now(timezone.utc),
                        kind=r.kind,
                        amount_usd=_dec(r.amount_usd),
                        ref=r.ref,
                    )
                    for r in rows
                ]
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)
            return []

    def add_pnl(self, entry: PnLEntry) -> None:
        try:
            with _session(self._sf) as sess:
                sess.add(PnLLedgerRow(ts=entry.ts, kind=entry.kind,
                                      amount_usd=str(entry.amount_usd), ref=entry.ref))
                sess.commit()
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)


class RuleRepo:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def load_all(self) -> dict[str, Rule]:
        try:
            with _session(self._sf) as sess:
                rows = sess.query(RuleRow).all()
                return {
                    r.id: Rule(
                        id=r.id,
                        market_id=r.market_id,
                        parent_market_id=r.parent_market_id,
                        position_ref=r.position_ref,
                        kind=r.kind,
                        trigger_type=r.trigger_type,
                        trigger_value=_dec(r.trigger_value),
                        size=r.size,
                        status=r.status,
                        created_via=r.created_via,
                        mode=r.mode,
                        expires_at=_aware(r.expires_at),
                        confirmed_at=_aware(r.confirmed_at),
                        triggered_at=_aware(r.triggered_at),
                        last_eval_at=_aware(r.last_eval_at),
                        last_eval_note=r.last_eval_note,
                        order_id=r.order_id,
                        blocked_since=_aware(r.blocked_since),
                        exec_mode=r.exec_mode or "market",
                        resting_order_id=r.resting_order_id,
                    )
                    for r in rows
                }
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)
            return {}

    def save(self, rule: Rule) -> None:
        try:
            with _session(self._sf) as sess:
                row = sess.get(RuleRow, rule.id)
                if row is None:
                    row = RuleRow(id=rule.id)
                    sess.add(row)
                row.market_id = rule.market_id
                row.parent_market_id = rule.parent_market_id
                row.position_ref = rule.position_ref
                row.kind = rule.kind
                row.trigger_type = rule.trigger_type
                row.trigger_value = str(rule.trigger_value)
                row.size = rule.size
                row.status = rule.status
                row.created_via = rule.created_via
                row.mode = rule.mode
                row.confirmed_at = rule.confirmed_at
                row.triggered_at = rule.triggered_at
                row.expires_at = rule.expires_at
                row.last_eval_at = rule.last_eval_at
                row.last_eval_note = rule.last_eval_note
                row.order_id = rule.order_id
                row.blocked_since = rule.blocked_since
                row.exec_mode = rule.exec_mode
                row.resting_order_id = rule.resting_order_id
                sess.commit()
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)


class ChatRepo:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def load_all(self) -> dict[str, list[ChatMessage]]:
        try:
            with _session(self._sf) as sess:
                sessions: dict[str, list[ChatMessage]] = {}
                rows = (
                    sess.query(ChatMessageRow)
                    .order_by(ChatMessageRow.id)
                    .all()
                )
                for r in rows:
                    sessions.setdefault(r.session_id, []).append(
                        ChatMessage(role=r.role, content=r.content)
                    )
                return sessions
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)
            return {}

    def save_session(self, session_id: str, actor: str) -> None:
        try:
            with _session(self._sf) as sess:
                sess.add(ChatSessionRow(id=session_id, actor=actor))
                sess.commit()
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)

    def save_message(self, session_id: str, message: ChatMessage) -> None:
        try:
            with _session(self._sf) as sess:
                sess.add(ChatMessageRow(session_id=session_id, role=message.role,
                                        content=message.content))
                sess.commit()
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)

    def clear(self, session_id: str) -> None:
        try:
            with _session(self._sf) as sess:
                sess.query(ChatMessageRow).filter(
                    ChatMessageRow.session_id == session_id
                ).delete()
                sess.query(ChatSessionRow).filter(
                    ChatSessionRow.id == session_id
                ).delete()
                sess.commit()
        except (OperationalError, ProgrammingError) as exc:
            _warn_missing_tables(exc)


__all__ = [
    "ChatRepo",
    "OrderRepo",
    "PositionRepo",
    "ProposalRepo",
    "RuleRepo",
]
