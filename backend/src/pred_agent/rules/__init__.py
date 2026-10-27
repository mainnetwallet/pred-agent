"""Rules engine — conditional exit rules (take-profit / stop-loss).

Grounded in PLAN.md Phase C and ARCHITECTURE.md hard rules:
- Rules fire AT MOST ONCE (atomic status compare-and-set).
- PAPER vs LIVE: LiveExecutor only constructed if settings.live_trading=True.
- Fail closed: stale book / veto / feed-down → BLOCKED (retryable), never bypass.
- Pause: exits still run. Panic: all ACTIVE/PENDING rules cancelled.
- Money is Decimal throughout, never float.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pred_agent.execution.paper_executor import PaperExecutor
from pred_agent.positions import Position

if TYPE_CHECKING:
    from pred_agent.positions import PositionManager
    from pred_agent.store.repository import RuleRepo

log = logging.getLogger(__name__)

# Rule status constants
STATUS_PENDING = "PENDING"
STATUS_ACTIVE = "ACTIVE"
STATUS_TRIGGERED = "TRIGGERED"
STATUS_EXECUTING = "EXECUTING"
STATUS_DONE = "DONE"
STATUS_CANCELLED = "CANCELLED"
STATUS_EXPIRED = "EXPIRED"
STATUS_BLOCKED = "BLOCKED"
STATUS_FAILED = "FAILED"

# Trigger types
TRIGGER_PROFIT_USD = "profit_usd"
TRIGGER_LOSS_USD = "loss_usd"
TRIGGER_PRICE_CENTS = "price_cents"
TRIGGER_PROFIT_PCT = "profit_pct"

# How long a PENDING rule survives without confirmation
RULE_PENDING_TTL_HOURS = 24
# Escalate BLOCKED stop-loss alerts after this many seconds
BLOCKED_ESCALATION_SECONDS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Rule:
    """In-memory rule object (backed by DB via RulesService)."""

    def __init__(
        self,
        *,
        id: str,
        market_id: str,
        parent_market_id: str,
        position_ref: str | None,
        kind: str,             # take_profit | stop_loss
        trigger_type: str,
        trigger_value: Decimal,
        size: str = "all",
        status: str = STATUS_PENDING,
        created_via: str = "chat",
        mode: str = "PAPER",
        expires_at: datetime | None = None,
        confirmed_at: datetime | None = None,
        triggered_at: datetime | None = None,
        last_eval_at: datetime | None = None,
        last_eval_note: str | None = None,
        order_id: str | None = None,
        blocked_since: datetime | None = None,
    ) -> None:
        self.id = id
        self.market_id = market_id
        self.parent_market_id = parent_market_id
        self.position_ref = position_ref
        self.kind = kind
        self.trigger_type = trigger_type
        self.trigger_value = trigger_value
        self.size = size
        self.status = status
        self.created_via = created_via
        self.mode = mode
        self.expires_at = expires_at or (_now() + timedelta(hours=RULE_PENDING_TTL_HOURS))
        self.confirmed_at = confirmed_at
        self.triggered_at = triggered_at
        self.last_eval_at = last_eval_at
        self.last_eval_note = last_eval_note
        self.order_id = order_id
        self.blocked_since = blocked_since
        self._lock: asyncio.Lock = asyncio.Lock()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "market_id": self.market_id,
            "parent_market_id": self.parent_market_id,
            "position_ref": self.position_ref,
            "kind": self.kind,
            "trigger_type": self.trigger_type,
            "trigger_value": str(self.trigger_value),
            "size": self.size,
            "status": self.status,
            "created_via": self.created_via,
            "mode": self.mode,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "last_eval_at": self.last_eval_at.isoformat() if self.last_eval_at else None,
            "last_eval_note": self.last_eval_note,
            "order_id": self.order_id,
        }


class RulesService:
    """Rules store with lifecycle management.

    ``db`` attaches a repository: rules are loaded on startup and every
    mutation is written through, so they survive restarts.
    """

    def __init__(self, db: "RuleRepo | None" = None) -> None:
        self._db = db
        self._rules: dict[str, Rule] = db.load_all() if db else {}

    def _persist(self, rule: Rule) -> None:
        if self._db is not None:
            self._db.save(rule)

    def create(
        self,
        *,
        market_id: str,
        parent_market_id: str,
        position_ref: str | None,
        kind: str,
        trigger_type: str,
        trigger_value: Decimal,
        size: str = "all",
        created_via: str = "chat",
        mode: str = "PAPER",
    ) -> Rule:
        rule = Rule(
            id=str(uuid.uuid4()),
            market_id=market_id,
            parent_market_id=parent_market_id,
            position_ref=position_ref,
            kind=kind,
            trigger_type=trigger_type,
            trigger_value=trigger_value,
            size=size,
            status=STATUS_PENDING,
            created_via=created_via,
            mode=mode,
        )
        self._rules[rule.id] = rule
        self._persist(rule)
        log.info("rule.created id=%s kind=%s trigger=%s@%s", rule.id, kind, trigger_type, trigger_value)
        return rule

    def get(self, rule_id: str) -> Rule | None:
        return self._rules.get(rule_id)

    def list_all(self) -> list[Rule]:
        return list(self._rules.values())

    def list_active(self) -> list[Rule]:
        return [r for r in self._rules.values() if r.status == STATUS_ACTIVE]

    def list_pending(self) -> list[Rule]:
        return [r for r in self._rules.values() if r.status == STATUS_PENDING]

    def confirm(self, rule_id: str) -> Rule | None:
        rule = self._rules.get(rule_id)
        if rule is None or rule.status != STATUS_PENDING:
            return None
        rule.status = STATUS_ACTIVE
        rule.confirmed_at = _now()
        self._persist(rule)
        log.info("rule.confirmed id=%s", rule_id)
        return rule

    def cancel(self, rule_id: str) -> Rule | None:
        rule = self._rules.get(rule_id)
        if rule is None or rule.status in (STATUS_DONE, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_FAILED):
            return None
        rule.status = STATUS_CANCELLED
        self._persist(rule)
        log.info("rule.cancelled id=%s", rule_id)
        return rule

    def cancel_all(self) -> int:
        """Cancel all ACTIVE and PENDING rules (used on panic)."""
        count = 0
        for rule in self._rules.values():
            if rule.status in (STATUS_ACTIVE, STATUS_PENDING):
                rule.status = STATUS_CANCELLED
                self._persist(rule)
                count += 1
        if count:
            log.info("rules.cancel_all cancelled=%d", count)
        return count

    def expire_stale(self) -> int:
        """Expire PENDING rules past their TTL."""
        now = _now()
        count = 0
        for rule in self._rules.values():
            if rule.status == STATUS_PENDING and rule.expires_at and now > rule.expires_at:
                rule.status = STATUS_EXPIRED
                self._persist(rule)
                count += 1
        return count

    # ── monitor state transitions (fail-closed) ──────────────

    def mark_blocked(self, rule: Rule, note: str) -> bool:
        """Move an ACTIVE rule to BLOCKED (fail closed). Returns True on transition."""
        if rule.status != STATUS_ACTIVE:
            return False
        rule.status = STATUS_BLOCKED
        if rule.blocked_since is None:
            rule.blocked_since = _now()
        rule.last_eval_at = _now()
        rule.last_eval_note = note
        self._persist(rule)
        log.warning("rule.blocked id=%s note=%s", rule.id, note)
        return True

    def mark_unblocked(self, rule: Rule) -> bool:
        """Move a BLOCKED rule back to ACTIVE when data is fresh again."""
        if rule.status != STATUS_BLOCKED:
            return False
        rule.status = STATUS_ACTIVE
        rule.blocked_since = None
        self._persist(rule)
        log.info("rule.unblocked id=%s", rule.id)
        return True

    def mark_expired(self, rule: Rule, note: str) -> bool:
        """Expire a rule whose position is gone or whose market resolved."""
        if rule.status not in (STATUS_ACTIVE, STATUS_BLOCKED, STATUS_PENDING):
            return False
        rule.status = STATUS_EXPIRED
        rule.last_eval_at = _now()
        rule.last_eval_note = note
        self._persist(rule)
        log.info("rule.expired id=%s note=%s", rule.id, note)
        return True

    # ── evaluation (called by monitor) ───────────────────────

    def compute_pnl(
        self,
        *,
        position: Position,
        current_price_cents: Decimal,
        fee_rate_bps: int = 250,
    ) -> dict[str, Decimal]:
        """Compute net PnL for a position at current_price_cents.

        Uses Decimal math throughout. Fees applied to exit revenue.
        """
        # entry = position.avg_price_cents  # available if needed for long/short per-entry PnL
        qty = position.quantity
        cost = position.cost_usd

        if position.side == "long":
            revenue = current_price_cents / 100 * qty
        else:
            # short: revenue when price drops
            revenue = (100 - current_price_cents) / 100 * qty

        fee_usd = revenue * Decimal(fee_rate_bps) / Decimal(10000)
        net_revenue = revenue - fee_usd
        profit_usd = net_revenue - cost
        profit_pct = (profit_usd / cost * 100) if cost > 0 else Decimal(0)

        return {
            "profit_usd": profit_usd,
            "loss_usd": -profit_usd,
            "profit_pct": profit_pct,
            "net_revenue": net_revenue,
            "fee_usd": fee_usd,
        }

    def should_trigger(
        self,
        rule: Rule,
        *,
        position: Position,
        current_price_cents: Decimal,
        fee_rate_bps: int = 250,
    ) -> tuple[bool, str]:
        """Check if rule should trigger. Returns (should_fire, reason)."""
        pnl = self.compute_pnl(
            position=position,
            current_price_cents=current_price_cents,
            fee_rate_bps=fee_rate_bps,
        )
        tt = rule.trigger_type
        tv = rule.trigger_value

        if tt == TRIGGER_PROFIT_USD:
            fire = pnl["profit_usd"] >= tv
            return fire, f"profit_usd={pnl['profit_usd']:.4f} >= {tv}"
        elif tt == TRIGGER_LOSS_USD:
            fire = pnl["loss_usd"] >= tv  # loss_usd is positive when losing
            return fire, f"loss_usd={pnl['loss_usd']:.4f} >= {tv}"
        elif tt == TRIGGER_PRICE_CENTS:
            if rule.kind == "take_profit":
                fire = current_price_cents >= tv
                return fire, f"price={current_price_cents} >= {tv}"
            else:  # stop_loss
                fire = current_price_cents <= tv
                return fire, f"price={current_price_cents} <= {tv}"
        elif tt == TRIGGER_PROFIT_PCT:
            fire = pnl["profit_pct"] >= tv
            return fire, f"profit_pct={pnl['profit_pct']:.2f}% >= {tv}%"
        return False, f"unknown trigger_type={tt}"

    async def try_fire(
        self,
        rule: Rule,
        *,
        position: Position,
        current_price_cents: Decimal,
        executor: PaperExecutor,
        fee_rate_bps: int = 250,
        positions: "PositionManager | None" = None,
    ) -> bool:
        """Atomically fire a rule. Returns True if fired, False if already fired.

        On success the exit order is placed and (when ``positions`` is given)
        the position is closed with its realized net PnL.
        """
        async with rule._lock:
            if rule.status != STATUS_ACTIVE:
                return False  # already fired or cancelled

            should, reason = self.should_trigger(
                rule, position=position,
                current_price_cents=current_price_cents,
                fee_rate_bps=fee_rate_bps,
            )
            if not should:
                rule.last_eval_at = _now()
                rule.last_eval_note = f"no trigger: {reason}"
                self._persist(rule)
                return False

            # Atomic: ACTIVE → TRIGGERED → EXECUTING
            rule.status = STATUS_TRIGGERED
            rule.triggered_at = _now()
            rule.last_eval_note = f"triggered: {reason}"
            self._persist(rule)
            log.info("rule.triggered id=%s reason=%s", rule.id, reason)

            rule.status = STATUS_EXECUTING
            self._persist(rule)

        # Execute outside lock — paper executor
        try:
            side = "short" if position.side == "long" else "long"
            qty = position.quantity  # full position for now (size="all")
            amount_usd = current_price_cents / 100 * qty

            order = executor.place(
                proposal_id=f"rule-{rule.id}",
                market_id=rule.market_id,
                side=side,
                price_cents=current_price_cents,
                quantity=qty,
                amount_usd=amount_usd,
                order_type="ioc",
            )
            rule.order_id = order.id
            rule.status = STATUS_DONE
            rule.last_eval_at = _now()

            realized = Decimal("0")
            if positions is not None:
                pnl = self.compute_pnl(
                    position=position,
                    current_price_cents=current_price_cents,
                    fee_rate_bps=fee_rate_bps,
                )
                realized = pnl["profit_usd"]
                positions.close_position(position.market_id, realized_pnl_usd=realized)
                rule.last_eval_note = (
                    f"executed at {current_price_cents}c — net PnL ${realized:.4f}"
                )

            self._persist(rule)
            log.info("rule.done id=%s order=%s pnl=%s", rule.id, order.id, realized)
            return True
        except Exception as exc:  # noqa: BLE001
            rule.status = STATUS_FAILED
            rule.last_eval_note = f"execution failed: {type(exc).__name__}"
            self._persist(rule)
            log.error("rule.failed id=%s exc=%s", rule.id, exc)
            return False
