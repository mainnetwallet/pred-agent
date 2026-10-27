"""Shared agent state used by the Control API, the website, and the Telegram bot.

One instance per process, so a pause from Telegram is seen by the app and the
website immediately. This layer can never enable LIVE_TRADING: the mode is read
from frozen settings only.

Phase 8 additions:
- RulesService integration (create, confirm, cancel rules)
- panic() now also cancels all ACTIVE/PENDING rules
- pause() blocks new entries but does NOT stop exit rules
- feeds_ok derived from real feed freshness timestamp
- balance: null when unavailable (never fake "0.00")
"""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from pred_agent.config import Settings
from pred_agent.execution.paper_executor import PaperExecutor
from pred_agent.positions import Position, PositionManager
from pred_agent.proposals import Proposal, ProposalService, ProposalStatus
from pred_agent.rules import Rule, RulesService
from pred_agent.store.repository import OrderRepo, PositionRepo, ProposalRepo, RuleRepo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


def to_jsonable(obj: Any) -> Any:  # noqa: PLR0911
    """Money stays a decimal string; datetimes become ISO 8601."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj


log = logging.getLogger(__name__)


class AgentController:
    def __init__(self, settings: Settings,
                 db_session_factory: "sessionmaker[Session] | None" = None) -> None:
        self.settings = settings
        self.paused = False
        self.proposals = ProposalService(
            ttl_seconds=settings.proposal_ttl_seconds,
            db=ProposalRepo(db_session_factory) if db_session_factory else None,
        )
        # LiveExecutor is not wired into this controller yet (no transport / contract
        # address / fee lookup / reconcile). Until it is, LIVE approvals fail closed
        # instead of marking a proposal APPROVED without ever placing an order.
        self.live_execution_ready = False
        self.executor = PaperExecutor(
            db=OrderRepo(db_session_factory) if db_session_factory else None
        )
        self.positions = PositionManager(
            db=PositionRepo(db_session_factory) if db_session_factory else None
        )
        self.rules = RulesService(
            db=RuleRepo(db_session_factory) if db_session_factory else None
        )
        self.limits: dict[str, float] = {
            "max_trade_usd": float(settings.limits.max_trade_usd),
            "daily_loss_usd": float(settings.limits.daily_loss_usd),
            "max_open_positions": float(settings.limits.max_open_positions),
        }
        # Hard upper bounds: config can lower these, nothing can raise them.
        self._hard_max = dict(self.limits)
        # Feed freshness: set by scanner/book-feed tasks
        self._last_feed_update: datetime | None = None
        self._balance_usd: str | None = None   # None = unavailable (never "0.00" fake)

    # ── status ────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return "LIVE" if self.settings.live_trading else "PAPER"

    @property
    def feeds_ok(self) -> bool:
        """True iff feed data arrived within the configured max-age window."""
        if self._last_feed_update is None:
            return False
        age = (datetime.now(timezone.utc) - self._last_feed_update).total_seconds()
        return age <= self.settings.book_max_age_s * 3  # 3× book_max_age for scanner freshness

    def mark_feed_updated(self) -> None:
        self._last_feed_update = datetime.now(timezone.utc)

    def status(self) -> dict[str, Any]:
        exposure = sum((p.cost_usd for p in self.positions.list_positions()), Decimal("0"))
        active_rules = len(self.rules.list_active())
        return {
            "mode": self.mode,
            "auth_mode": "jwt" if self.settings.pred_api_key else "clob",
            "feeds_ok": self.feeds_ok,
            "balance": self._balance_usd,   # null = not available
            "exposure": str(exposure),
            "daily_pnl": str(self.positions.daily_pnl()),
            "paused": self.paused,
            "pending_proposals": len(self.proposals.list_pending()),
            "active_rules": active_rules,
        }

    # ── pause / resume / panic ────────────────────────────────

    def pause(self) -> None:
        """Pause new entries. Exit rules (ACTIVE) still run to reduce risk."""
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def panic(self) -> dict[str, int]:
        """Pause + reject all pending proposals + cancel all open orders + cancel all rules."""
        self.paused = True
        rejected = 0
        for prop in list(self.proposals.list_pending()):
            if self.proposals.reject(prop.id, via="panic"):
                rejected += 1
        cancelled = sum(1 for o in self.executor.list_orders() if self.executor.cancel(o.id))
        rules_cancelled = self.rules.cancel_all()
        return {
            "proposals_rejected": rejected,
            "orders_cancelled": cancelled,
            "rules_cancelled": rules_cancelled,
        }

    # ── proposals ─────────────────────────────────────────────

    def list_proposals(self, status: str | None = None) -> list[Proposal]:
        self.proposals.expire_stale()
        items = list(self.proposals._proposals.values())  # noqa: SLF001
        if status:
            items = [p for p in items if p.status.value == status.upper()]
        return sorted(items, key=lambda p: p.created_at, reverse=True)

    def approve(self, proposal_id: str, via: str, amount: Decimal | None = None) -> Proposal | None:
        if self.paused:
            return None
        cap = Decimal(str(self.limits["max_trade_usd"]))
        if amount is not None and (amount <= 0 or amount > cap):
            return None
        if self.mode == "LIVE" and not self.live_execution_ready:
            log.error("approve blocked: LIVE_TRADING=true but live execution is not wired")
            return None
        prop = self.proposals.approve(proposal_id, via=via, new_amount_usd=amount)
        if prop is None:
            return None
        if self.mode == "PAPER":
            self.executor.place(
                proposal_id=prop.id, market_id=prop.market_id, side=prop.side,
                price_cents=prop.price_cents, quantity=prop.quantity,
                amount_usd=prop.amount_usd, order_type=prop.order_type,
            )
            # Paper fills are assumed immediate at the proposal price, so the
            # position exists for rule monitoring right away.
            self.positions.open_position(
                market_id=prop.market_id, parent_id=prop.fixture_id, side=prop.side,
                quantity=prop.quantity, avg_price_cents=prop.price_cents,
                cost_usd=prop.amount_usd,
            )
        return prop

    def reject(self, proposal_id: str, via: str) -> Proposal | None:
        return self.proposals.reject(proposal_id, via=via)

    # ── chat write intents (create PENDING only, never execute) ──

    def propose_trade_from_chat(
        self,
        *,
        market_id: str,
        parent_market_id: str,
        fixture_id: str,
        side: str,
        amount_usd: Decimal,
        price_cents: Decimal,
        created_via: str = "chat",
    ) -> tuple[Proposal | None, str | None]:
        """Create a PENDING trade proposal from a chat intent. Returns (proposal, error)."""
        if self.paused:
            return None, "agent is paused — new proposals are blocked"
        cap = Decimal(str(self.limits["max_trade_usd"]))
        if amount_usd <= 0 or amount_usd > cap:
            return None, f"amount_usd must be > 0 and <= {cap}"
        if side not in ("long", "short"):
            return None, "side must be long or short"
        if price_cents <= 0:
            return None, "no live price for this market — book unavailable"
        qty = amount_usd / (price_cents / 100)
        est_fee = amount_usd * Decimal(250) / Decimal(10000)
        prop = self.proposals.create(
            mode=self.mode, advisory=False, fixture_id=fixture_id, market_id=market_id,
            side=side, order_type="limit", price_cents=price_cents, quantity=qty,
            amount_usd=amount_usd, est_fee_usd=est_fee, edge_pp=Decimal("0"),
            p_fair=price_cents / 100,
            max_suggested_usd=cap, binding_cap=None,
            reasons=[f"requested via {created_via}"], risks=None,
        )
        return prop, None

    def close_position_proposal(
        self, market_id: str, via: str = "chat"
    ) -> tuple[Proposal | None, str | None]:
        """Create a PENDING reduce-only exit proposal for an open position."""
        pos = self.positions.get_position(market_id)
        if pos is None:
            return None, f"no open position for market {market_id}"
        exit_side = "short" if pos.side == "long" else "long"
        price = pos.avg_price_cents
        est_fee = pos.cost_usd * Decimal(250) / Decimal(10000)
        prop = self.proposals.create(
            mode=self.mode, advisory=False, fixture_id=pos.parent_id, market_id=market_id,
            side=exit_side, order_type="limit", price_cents=price, quantity=pos.quantity,
            amount_usd=pos.cost_usd, est_fee_usd=est_fee, edge_pp=Decimal("0"),
            p_fair=price / 100,
            max_suggested_usd=Decimal(str(self.limits["max_trade_usd"])),
            binding_cap=None, reasons=[f"exit requested via {via}"], risks=None,
        )
        return prop, None

    def create_rule_from_chat(  # noqa: PLR0911
        self,
        *,
        market_id: str,
        kind: str,
        trigger_type: str,
        trigger_value: Decimal,
        size: str = "all",
        created_via: str = "chat",
    ) -> tuple[Rule | None, str | None]:
        """Create a PENDING exit rule from a chat intent. Returns (rule, error)."""
        pos = self.positions.get_position(market_id)
        if pos is None:
            return None, f"no open position for market {market_id}"
        if kind not in ("take_profit", "stop_loss"):
            return None, f"kind must be take_profit or stop_loss (got {kind})"
        if trigger_type not in ("profit_usd", "loss_usd", "price_cents", "profit_pct"):
            return None, f"unsupported trigger_type {trigger_type}"
        if trigger_value <= 0:
            return None, "trigger_value must be > 0"
        if trigger_type == "price_cents":
            err = self._sane_price_trigger(pos, kind, trigger_value)
            if err:
                return None, err
        if len(self.rules.list_active()) + len(self.rules.list_pending()) >= 10:
            return None, "max 10 rules reached"
        rule = self.rules.create(
            market_id=market_id,
            parent_market_id=pos.parent_id,
            position_ref=market_id,
            kind=kind,
            trigger_type=trigger_type,
            trigger_value=trigger_value,
            size=size,
            created_via=created_via,
            mode=self.mode,
        )
        return rule, None

    @staticmethod
    def _sane_price_trigger(pos: Position, kind: str, trigger_value: Decimal) -> str | None:  # noqa: PLR0911
        entry = pos.avg_price_cents
        if pos.side == "long" and kind == "take_profit" and trigger_value <= entry:
            return f"take-profit price {trigger_value} must be above entry {entry} for a long"
        if pos.side == "long" and kind == "stop_loss" and trigger_value >= entry:
            return f"stop-loss price {trigger_value} must be below entry {entry} for a long"
        if pos.side == "short" and kind == "take_profit" and trigger_value >= entry:
            return f"take-profit price {trigger_value} must be below entry {entry} for a short"
        if pos.side == "short" and kind == "stop_loss" and trigger_value <= entry:
            return f"stop-loss price {trigger_value} must be above entry {entry} for a short"
        return None

    # ── rules ─────────────────────────────────────────────────

    def list_rules(self, status: str | None = None) -> list[Rule]:
        self.rules.expire_stale()
        rules = self.rules.list_all()
        if status:
            rules = [r for r in rules if r.status == status.upper()]
        return sorted(rules, key=lambda r: r.confirmed_at or r.id)

    def confirm_rule(self, rule_id: str) -> Rule | None:
        """Confirm a PENDING rule → ACTIVE. Validates limits."""
        rule = self.rules.get(rule_id)
        if rule is None:
            return None
        # Validate: check max active rules
        max_active = 10
        if len(self.rules.list_active()) >= max_active:
            return None
        return self.rules.confirm(rule_id)

    def cancel_rule(self, rule_id: str) -> Rule | None:
        return self.rules.cancel(rule_id)

    # ── limits ────────────────────────────────────────────────

    def limits_list(self) -> list[dict[str, Any]]:
        return [
            {"name": k, "value": v, "hard_max": self._hard_max[k]} for k, v in self.limits.items()
        ]

    def set_limits(self, new: dict[str, Any]) -> str | None:
        """Apply limits. Returns an error message, or None on success."""
        staged: dict[str, float] = {}
        for key, raw in new.items():
            if key not in self.limits:
                return f"unknown limit: {key}"
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return f"{key} must be a number"
            if val < 0 or val > self._hard_max[key]:
                return f"{key} must be between 0 and {self._hard_max[key]}"
            staged[key] = val
        self.limits.update(staged)
        return None

    # ── orders / positions ────────────────────────────────────

    def orders(self) -> list[dict[str, Any]]:
        return [to_jsonable(o) for o in self.executor.list_orders()]

    def positions_list(self) -> list[dict[str, Any]]:
        return [to_jsonable(p) for p in self.positions.list_positions()]


__all__ = ["AgentController", "ProposalStatus", "to_jsonable"]
