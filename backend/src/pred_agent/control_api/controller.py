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

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from pred_agent.config import Settings
from pred_agent.execution.paper_executor import PaperExecutor
from pred_agent.positions import PositionManager
from pred_agent.proposals import Proposal, ProposalService, ProposalStatus
from pred_agent.rules import Rule, RulesService


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


class AgentController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.paused = False
        self.proposals = ProposalService(ttl_seconds=settings.proposal_ttl_seconds)
        self.executor = PaperExecutor()
        self.positions = PositionManager()
        self.rules = RulesService()
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
        prop = self.proposals.approve(proposal_id, via=via, new_amount_usd=amount)
        if prop is None:
            return None
        if self.mode == "PAPER":
            self.executor.place(
                proposal_id=prop.id, market_id=prop.market_id, side=prop.side,
                price_cents=prop.price_cents, quantity=prop.quantity,
                amount_usd=prop.amount_usd, order_type=prop.order_type,
            )
        return prop

    def reject(self, proposal_id: str, via: str) -> Proposal | None:
        return self.proposals.reject(proposal_id, via=via)

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
