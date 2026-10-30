"""Chat autopilot — the chat assistant acts directly and reports the REAL outcome.

What changes versus the old confirm-card flow
---------------------------------------------
* ``trade`` / ``exit_now`` / ``cancel_*`` execute immediately (no approve tap).
* Every execution returns an outcome report whose ``status`` is taken from the exchange/executor
  state, never from the model's wishes: FILLED, PARTIAL, RESTING (limit order on the book),
  NOT_FILLED, FAILED, UNKNOWN, ERROR. The chat must repeat that status verbatim.
* Exits are automated in LIVE too: percent/price/time triggers fire a real reduce-only sell
  (market = marketable IOC with a slippage cap; limit = a resting sell at the target price).

What does NOT change
--------------------
* Hard caps (max_trade_usd, daily loss, max open positions) still apply in the controller.
* The existing live guards still apply (fresh book, fee target, UNKNOWN-order market block, one
  live placement at a time). An UNKNOWN outcome is never retried or resent.
* Withdrawals are not here: they always need one human confirmation (see ``withdraw.py``).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Any

from pred_agent.book import BookSnapshot
from pred_agent.rules import (
    EXEC_LIMIT,
    EXEC_MARKET,
    KIND_STOP_LOSS,
    KIND_TAKE_PROFIT,
    KIND_TIME_EXIT,
    STATUS_DONE,
    STATUS_FAILED,
    TRIGGER_DEADLINE_TS,
    TRIGGER_LOSS_PCT,
    TRIGGER_PRICE_CENTS,
    TRIGGER_PROFIT_PCT,
    Rule,
)

if TYPE_CHECKING:
    from pred_agent.control_api.controller import AgentController
    from pred_agent.positions import Position

log = logging.getLogger(__name__)

# Order-record states (live OrderState values / paper statuses, upper-cased).
_FILLED = {"FILLED", "CLOSED", "SETTLED"}
_RESTING = {"OPEN", "DELAYED", "SUBMITTED", "APPROVED", "REVALIDATED"}
_DEAD = {"FAILED", "REJECTED", "ABORTED", "EXPIRED", "CANCELLED", "CANCELED"}

PLAN_TTL = timedelta(hours=24)
MAX_EXIT_ATTEMPTS = 10
MAX_RULES = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ExitPlan:
    """What to do once an entry fills. Rules are only created when a position exists."""

    market_id: str
    tp_pct: Decimal | None = None
    tp_price_cents: Decimal | None = None
    sl_pct: Decimal | None = None
    sl_price_cents: Decimal | None = None
    deadline_ts: Decimal | None = None
    created_at: datetime = field(default_factory=_now)

    def is_empty(self) -> bool:
        return not any((self.tp_pct, self.tp_price_cents, self.sl_pct, self.sl_price_cents,
                        self.deadline_ts))

    def describe(self) -> list[str]:
        out: list[str] = []
        if self.tp_pct:
            out.append(f"take-profit +{self.tp_pct}% -> market sell")
        if self.tp_price_cents:
            out.append(f"take-profit: resting limit sell at {self.tp_price_cents}c")
        if self.sl_pct:
            out.append(f"stop-loss -{self.sl_pct}% -> market sell")
        if self.sl_price_cents:
            out.append(f"stop-loss at {self.sl_price_cents}c -> market sell")
        if self.deadline_ts:
            when = datetime.fromtimestamp(float(self.deadline_ts), tz=timezone.utc)
            out.append(f"time exit: market sell at {when.isoformat(timespec='minutes')}")
        return out


def market_order_price(side: str, book: BookSnapshot, tol: Decimal) -> Decimal | None:
    """Marketable IOC limit price: top of book plus/minus the slippage cap, whole cents.

    Matches fill at the resting prices, so the cap only bounds the worst price accepted.
    """
    if side == "long":
        if not book.asks:
            return None
        return min(Decimal(99), (book.asks[0].price_cents + tol).to_integral_value(ROUND_CEILING))
    if not book.bids:
        return None
    return max(Decimal(1), (book.bids[0].price_cents - tol).to_integral_value(ROUND_FLOOR))


def norm_state(order: Any) -> str:
    st = getattr(order, "state", None)
    if st is not None:
        return str(getattr(st, "value", st)).upper()
    return str(getattr(order, "status", "") or "").upper()


class Autopilot:
    def __init__(self, controller: "AgentController") -> None:
        self.c = controller
        self.pending_plans: dict[str, ExitPlan] = {}   # proposal_id -> plan (resting entries)
        self._attempts: dict[str, int] = {}

    # ── helpers ───────────────────────────────────────────────

    @property
    def _tol(self) -> Decimal:
        return Decimal(str(self.c.settings.max_slippage_pp))

    def _order_for(self, proposal_id: str) -> Any | None:
        for o in self.c.executor.list_orders():
            if getattr(o, "proposal_id", None) == proposal_id:
                return o
        return None

    def _report(  # noqa: PLR0911
        self, *, prop_id: str, approved: Any, order_type: str, side: str, market_id: str,
        price: Decimal, requested_usd: Decimal | None = None, kind: str = "entry",
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "kind": kind, "mode": self.c.mode, "market_id": market_id, "side": side,
            "order_type": "market" if order_type == "ioc" else order_type,
            "price_cents": str(price), "proposal_id": prop_id,
        }
        if requested_usd is not None:
            base["requested_usd"] = str(requested_usd)
        if approved is None:
            return {**base, "status": "FAILED", "ok": False,
                    "reason": self.c.last_error or "refused before sending", "order_id": None}
        order = self._order_for(prop_id)
        if order is None:
            return {**base, "status": "ERROR", "ok": False, "order_id": None,
                    "reason": "no order record after approval; check open orders before retrying"}
        state = norm_state(order)
        filled = Decimal(str(getattr(order, "filled_qty", "0") or "0"))
        base["order_id"] = getattr(order, "pred_order_id", None) or getattr(order, "id", None)
        base["filled_shares"] = str(filled)
        if state == "UNKNOWN":
            return {**base, "status": "UNKNOWN", "ok": False,
                    "reason": self.c.last_error or
                    "outcome unknown; do NOT resend. Check open orders / positions."}
        if state in _FILLED:
            return {**base, "status": "FILLED", "ok": True}
        if state == "PARTIAL" or (filled > 0 and state in _RESTING):
            return {**base, "status": "PARTIAL", "ok": True,
                    "note": "part filled; the rest is "
                    + ("resting on the book" if order_type == "limit" else "cancelled (IOC)")}
        if state in _RESTING:
            if order_type == "ioc":
                return {**base, "status": "NOT_FILLED", "ok": False,
                        "reason": "no liquidity within the slippage cap; nothing was bought/sold"}
            return {**base, "status": "RESTING", "ok": True,
                    "note": "limit order is on the book, not filled yet"}
        if state in _DEAD:
            return {**base, "status": "FAILED", "ok": False,
                    "reason": str(getattr(order, "raw_response", "") or state)[:300]}
        return {**base, "status": "ERROR", "ok": False, "reason": f"unexpected order state {state}"}

    # ── entry ─────────────────────────────────────────────────

    def trade(
        self, *, market_id: str, parent_market_id: str, fixture_id: str, side: str,
        amount_usd: Decimal, order_type: str, limit_price_cents: Decimal | None,
        book: BookSnapshot | None, plan: ExitPlan | None = None, via: str = "chat-auto",
    ) -> dict[str, Any]:
        """Place an entry and return the real outcome. ``order_type``: market | limit."""
        c = self.c
        if order_type == "limit":
            if limit_price_cents is None or not Decimal(0) < limit_price_cents < Decimal(100):
                return {"status": "FAILED", "ok": False,
                        "reason": "limit order needs limit_price_cents between 0 and 100"}
            price, otype, user_priced = limit_price_cents, "limit", True
        else:
            if book is None:
                return {"status": "FAILED", "ok": False,
                        "reason": "no live book for this market; cannot price a market order"}
            price_or_none = market_order_price(side, book, self._tol)
            if price_or_none is None:
                return {"status": "FAILED", "ok": False,
                        "reason": "order book has no liquidity on the needed side"}
            price, otype, user_priced = price_or_none, "ioc", False

        prop, err = c.propose_trade_from_chat(
            market_id=market_id, parent_market_id=parent_market_id, fixture_id=fixture_id,
            side=side, amount_usd=amount_usd, price_cents=price, created_via=via,
            order_type=otype, user_priced=user_priced,
        )
        if prop is None:
            return {"status": "FAILED", "ok": False, "reason": err or "proposal rejected",
                    "mode": c.mode, "market_id": market_id}
        approved = c.approve(prop.id, via=via)
        report = self._report(
            prop_id=prop.id, approved=approved, order_type=otype, side=side,
            market_id=market_id, price=price, requested_usd=amount_usd,
        )
        if c.live is not None and approved is not None:
            c.sync_live()   # position average/cost now come from the exchange, not our cap price
        pos_after = c.positions.get_position(market_id)
        if pos_after is not None and pos_after.status == "open":
            report["position"] = {
                "shares": str(pos_after.quantity), "avg_price_cents": str(pos_after.avg_price_cents),
                "cost_usd": str(pos_after.cost_usd),
            }
        if plan is not None and not plan.is_empty() and report["status"] in (
            "FILLED", "PARTIAL", "RESTING",
        ):
            report["exit_plan"] = self._attach_plan(plan, prop.id, report["status"])
        return report

    def _attach_plan(self, plan: ExitPlan, prop_id: str, status: str) -> dict[str, Any]:
        pos = self.c.positions.get_position(plan.market_id)
        if pos is not None and pos.status == "open" and status in ("FILLED", "PARTIAL"):
            rules = self.arm_exits(pos, plan)
            return {"armed": True, "rules": rules, "plan": plan.describe()}
        self.pending_plans[prop_id] = plan
        return {"armed": False, "plan": plan.describe(),
                "note": "will be armed automatically once the entry fills (lost on restart)"}

    def apply_pending_plans(self) -> list[dict[str, Any]]:
        """Arm plans whose entry has filled; drop stale ones. Called by the monitor."""
        done: list[dict[str, Any]] = []
        for pid, plan in list(self.pending_plans.items()):
            pos = self.c.positions.get_position(plan.market_id)
            if pos is not None and pos.status == "open":
                done.append({"market_id": plan.market_id, "rules": self.arm_exits(pos, plan)})
                del self.pending_plans[pid]
            elif _now() - plan.created_at > PLAN_TTL:
                del self.pending_plans[pid]
        return done

    def arm_exits(self, pos: "Position", plan: ExitPlan) -> list[dict[str, Any]]:
        """Create ACTIVE exit rules for an open position (no confirmation: autopilot)."""
        specs: list[tuple[str, str, Decimal, str]] = []
        if plan.tp_pct:
            specs.append((KIND_TAKE_PROFIT, TRIGGER_PROFIT_PCT, plan.tp_pct, EXEC_MARKET))
        if plan.tp_price_cents:
            specs.append((KIND_TAKE_PROFIT, TRIGGER_PRICE_CENTS, plan.tp_price_cents, EXEC_LIMIT))
        if plan.sl_pct:
            specs.append((KIND_STOP_LOSS, TRIGGER_LOSS_PCT, plan.sl_pct, EXEC_MARKET))
        if plan.sl_price_cents:
            specs.append((KIND_STOP_LOSS, TRIGGER_PRICE_CENTS, plan.sl_price_cents, EXEC_MARKET))
        if plan.deadline_ts:
            specs.append((KIND_TIME_EXIT, TRIGGER_DEADLINE_TS, plan.deadline_ts, EXEC_MARKET))
        out: list[dict[str, Any]] = []
        for kind, ttype, value, exec_mode in specs:
            rule, err = self.add_rule(pos.market_id, kind, ttype, value, exec_mode)
            out.append({"rule_id": rule.id, "status": rule.status, "kind": kind,
                        "trigger": f"{ttype}={value}", "exec": exec_mode} if rule
                       else {"error": err, "kind": kind, "trigger": f"{ttype}={value}"})
        return out

    def add_rule(
        self, market_id: str, kind: str, trigger_type: str, value: Decimal, exec_mode: str,
    ) -> tuple[Rule | None, str | None]:
        """Create + immediately activate an exit rule (autopilot: no confirm step)."""
        c = self.c
        if len(c.rules.list_active()) + len(c.rules.list_pending()) >= MAX_RULES:
            return None, f"max {MAX_RULES} rules reached"
        if exec_mode == EXEC_LIMIT and (
            trigger_type != TRIGGER_PRICE_CENTS or kind != KIND_TAKE_PROFIT
        ):
            return None, "limit exits only work as a take-profit at a price"
        rule, err = c.create_rule_from_chat(
            market_id=market_id, kind=kind, trigger_type=trigger_type, trigger_value=value,
            created_via="chat-auto", exec_mode=exec_mode,
        )
        if rule is None:
            return None, err
        c.rules.confirm(rule.id)
        return rule, None

    # ── exits ─────────────────────────────────────────────────

    def cancel_resting_exits(self, market_id: str) -> int:
        """Cancel resting reduce-only orders on a market so their shares are free to sell."""
        c = self.c
        cancelled = 0
        ids: set[str] = set()
        for rule in c.rules.list_all():
            if rule.market_id == market_id and rule.resting_order_id:
                ids.add(rule.resting_order_id)
        for o in c.executor.list_orders():
            if (getattr(o, "market_id", None) == market_id and getattr(o, "reduce_only", False)
                    and norm_state(o) in (_RESTING | {"PARTIAL"})):
                ids.add(o.id)
        for oid in ids:
            try:
                if c.executor.cancel(oid):
                    cancelled += 1
            except Exception:  # noqa: BLE001
                log.warning("cancel of resting exit %s failed", oid, exc_info=True)
        for rule in c.rules.list_all():
            if rule.market_id == market_id and rule.resting_order_id in ids:
                rule.resting_order_id = None
                c.rules._persist(rule)  # noqa: SLF001
        return cancelled

    def exit_now(
        self, *, market_id: str, book: BookSnapshot | None, mode: str = "market",
        limit_price_cents: Decimal | None = None, via: str = "chat-auto",
        cancel_resting: bool = True,
    ) -> dict[str, Any]:
        """Sell the position now. mode market = marketable IOC; limit = resting reduce-only sell."""
        c = self.c
        pos = c.positions.get_position(market_id)
        if pos is None or pos.status != "open":
            return {"status": "FAILED", "ok": False, "kind": "exit",
                    "reason": f"no open position for market {market_id}"}
        exit_side = "short" if pos.side == "long" else "long"
        if mode == "limit":
            if limit_price_cents is None or not Decimal(0) < limit_price_cents < Decimal(100):
                return {"status": "FAILED", "ok": False, "kind": "exit",
                        "reason": "limit exit needs limit_price_cents between 0 and 100"}
            price, otype, user_priced = limit_price_cents, "limit", True
        else:
            if book is None:
                return {"status": "FAILED", "ok": False, "kind": "exit",
                        "reason": "no live book to price a market exit"}
            p = market_order_price(exit_side, book, self._tol)
            if p is None:
                return {"status": "FAILED", "ok": False, "kind": "exit",
                        "reason": "order book has no liquidity on the needed side"}
            price, otype, user_priced = p, "ioc", False
        if cancel_resting and otype == "ioc":
            self.cancel_resting_exits(market_id)
        prop, err = c.exit_proposal(
            market_id, order_type=otype, price_cents=price, via=via, user_priced=user_priced,
        )
        if prop is None:
            return {"status": "FAILED", "ok": False, "kind": "exit", "reason": err}
        approved = c.approve(prop.id, via=via)
        report = self._report(
            prop_id=prop.id, approved=approved, order_type=otype, side=exit_side,
            market_id=market_id, price=price, kind="exit",
        )
        if c.live is not None and approved is not None:
            c.sync_live()
        after = c.positions.get_position(market_id)
        report["position_closed"] = after is None or after.status != "open"
        if after is not None and after.status == "open":
            report["remaining_shares"] = str(after.quantity)
        return report

    # ── monitor integration (LIVE) ────────────────────────────

    def live_rule_step(
        self, rule: Rule, pos: "Position", book: BookSnapshot, price: Decimal,
    ) -> dict[str, Any] | None:
        """One evaluation of an ACTIVE rule in LIVE. Returns an event dict when something
        happened ({"event": armed|done|retry|failed, "text": ...}), else None."""
        c = self.c
        if rule.exec_mode == EXEC_LIMIT:
            return self._step_limit_exit(rule, pos, book)

        fire, reason = c.rules.should_trigger(rule, position=pos, current_price_cents=price)
        if not fire:
            rule.last_eval_at = _now()
            rule.last_eval_note = f"no trigger: {reason}"
            c.rules._persist(rule)  # noqa: SLF001
            return None
        rule.triggered_at = _now()
        rule.last_eval_note = f"triggered: {reason}"
        report = self.exit_now(market_id=rule.market_id, book=book, mode="market",
                               via=f"rule:{rule.id[:8]}")
        if report.get("position_closed"):
            return self._finish(rule, STATUS_DONE, report, f"exit done ({reason})")
        n = self._attempts[rule.id] = self._attempts.get(rule.id, 0) + 1
        if n >= MAX_EXIT_ATTEMPTS or report.get("status") == "UNKNOWN":
            return self._finish(rule, STATUS_FAILED, report, f"exit failed after {n} tries")
        rule.last_eval_note = f"exit attempt {n} not completed ({report.get('status')}): " \
                              f"{report.get('reason') or report.get('note')}; will retry"
        c.rules._persist(rule)  # noqa: SLF001
        return {"event": "retry", "rule_id": rule.id, "report": report,
                "text": f"⚠️ exit rule {rule.id[:8]} not done yet ({report.get('status')}); retrying"}

    def _step_limit_exit(  # noqa: PLR0911
        self, rule: Rule, pos: "Position", book: BookSnapshot,
    ) -> dict[str, Any] | None:
        c = self.c
        if rule.resting_order_id is None:
            report = self.exit_now(
                market_id=rule.market_id, book=book, mode="limit",
                limit_price_cents=rule.trigger_value, via=f"rule:{rule.id[:8]}",
                cancel_resting=False,
            )
            if report.get("position_closed"):
                return self._finish(rule, STATUS_DONE, report, "limit exit filled at once")
            if report.get("status") in ("RESTING", "PARTIAL"):
                rule.resting_order_id = self._order_ref(report)
                rule.last_eval_note = f"resting sell placed at {rule.trigger_value}c"
                rule.last_eval_at = _now()
                c.rules._persist(rule)  # noqa: SLF001
                return {"event": "armed", "rule_id": rule.id, "report": report,
                        "text": f"📌 limit sell resting at {rule.trigger_value}c "
                                f"({pos.quantity} shares)"}
            n = self._attempts[rule.id] = self._attempts.get(rule.id, 0) + 1
            rule.last_eval_note = f"could not place resting sell: {report.get('reason')}"
            c.rules._persist(rule)  # noqa: SLF001
            if n >= 3 or report.get("status") == "UNKNOWN":
                return self._finish(rule, STATUS_FAILED, report, "could not place limit exit")
            return None
        order = next((o for o in c.executor.list_orders()
                      if rule.resting_order_id in (getattr(o, "pred_order_id", None),
                                                   getattr(o, "id", None))), None)
        if order is None:
            return None
        st = norm_state(order)
        if st in _FILLED:
            self._close_after_fill(pos, rule.trigger_value)
            return self._finish(rule, STATUS_DONE, {"status": "FILLED", "price_cents": str(rule.trigger_value)},
                                f"resting sell filled at {rule.trigger_value}c")
        if st in _DEAD:
            rule.resting_order_id = None
            rule.last_eval_note = f"resting sell ended as {st}; re-arming"
            c.rules._persist(rule)  # noqa: SLF001
        return None

    @staticmethod
    def _order_ref(report: dict[str, Any]) -> str | None:
        ref = report.get("order_id")
        return str(ref) if ref else None

    def _close_after_fill(self, pos: "Position", price_cents: Decimal) -> None:
        pnl = self.c.rules.compute_pnl(position=pos, current_price_cents=price_cents,
                                       fee_rate_bps=int(self.c._fee_target_bps(pos.market_id)))  # noqa: SLF001
        self.c.positions.close_position(pos.market_id, realized_pnl_usd=pnl["profit_usd"])

    def _finish(self, rule: Rule, status: str, report: dict[str, Any], note: str) -> dict[str, Any]:
        rule.status = status
        rule.order_id = self._order_ref(report) or rule.order_id
        rule.last_eval_at = _now()
        rule.last_eval_note = note
        self.c.rules._persist(rule)  # noqa: SLF001
        self._attempts.pop(rule.id, None)
        ok = status == STATUS_DONE
        return {"event": "done" if ok else "failed", "rule_id": rule.id, "report": report,
                "text": ("✅ " if ok else "🚨 ") + f"{rule.kind} {rule.id[:8]}: {note}"}

    # ── orders ────────────────────────────────────────────────

    def resolve_order_id(self, ref: str) -> str | None:
        ref = ref.strip().lower()
        ids: list[tuple[str, str]] = []
        for o in self.c.executor.list_orders():
            for cand in (getattr(o, "id", None), getattr(o, "pred_order_id", None)):
                if cand:
                    ids.append((str(cand), str(o.id)))
        exact = [oid for cand, oid in ids if cand.lower() == ref]
        if exact:
            return exact[0]
        hits = {oid for cand, oid in ids if cand.lower().startswith(ref)}
        return next(iter(hits)) if len(hits) == 1 else None

    def cancel_order(self, ref: str) -> dict[str, Any]:
        oid = self.resolve_order_id(ref)
        if oid is None:
            return {"status": "FAILED", "ok": False, "reason": f"no unique order matching {ref!r}"}
        try:
            ok = bool(self.c.executor.cancel(oid))
        except Exception as exc:  # noqa: BLE001
            return {"status": "ERROR", "ok": False, "order_id": oid, "reason": type(exc).__name__}
        if not ok:
            return {"status": "FAILED", "ok": False, "order_id": oid,
                    "reason": "order not cancellable (already filled, cancelled or in the delay queue)"}
        if self.c.live is not None:
            self.c.sync_live()
        return {"status": "CANCELLED", "ok": True, "order_id": oid}

    def cancel_all_orders(self) -> dict[str, Any]:
        c = self.c
        if c.live is not None:
            res = c.live.cancel_everything()
            return {"status": "CANCELLED" if not res["failed"] else "PARTIAL", "ok": not res["failed"],
                    **res}
        n = sum(1 for o in c.executor.list_orders() if c.executor.cancel(o.id))
        return {"status": "CANCELLED", "ok": True, "cancelled": n, "failed": 0}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def deadline_from(minutes: float | None = None, at_iso: str | None = None) -> Decimal | None:
    """Unix-seconds deadline from 'minutes from now' or an ISO timestamp (UTC if naive)."""
    if at_iso:
        dt = datetime.fromisoformat(at_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return Decimal(str(int(dt.timestamp())))
    if minutes is not None:
        return Decimal(str(int(time.time() + float(minutes) * 60)))
    return None
