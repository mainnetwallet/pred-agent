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
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from pred_agent.auth.mode import resolve_auth_mode
from pred_agent.config import Settings
from pred_agent.control_api.autopilot import Autopilot
from pred_agent.execution.live_executor import LiveOrderIntent
from pred_agent.execution.live_runtime import LiveRuntime, MarketContext, MarketLookup
from pred_agent.execution.paper_executor import PaperExecutor
from pred_agent.execution.state_machine import OrderState
from pred_agent.positions import Position, PositionManager
from pred_agent.proposals import Proposal, ProposalService, ProposalStatus
from pred_agent.rules import Rule, RulesService
from pred_agent.store.repository import OrderRepo, PositionRepo, ProposalRepo, RuleRepo
from pred_agent.util.decimals import order_amount_usd, quantity_for_amount

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
        # LIVE approvals fail closed until ``arm_live`` succeeds (credentials, Safe address,
        # account readiness, startup reconcile). Nothing here can switch the mode itself.
        self.live_execution_ready = False
        self.live: LiveRuntime | None = None
        self.live_not_ready_reason: str | None = "live runtime not armed yet"
        self.market_lookup: MarketLookup | None = None
        # Why the last approve()/propose call returned None (shown to the operator).
        self.last_error: str | None = None
        self._live_lock = threading.Lock()  # one live approval at a time (no double placement)
        self.live_sync_error: str | None = None  # last failed exchange sync (blocks new LIVE orders)
        self.live_last_sync: dict[str, Any] | None = None
        self.executor: Any = PaperExecutor(
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
        # Chat autopilot: direct execution + automated exits (see control_api/autopilot.py).
        self.autopilot = Autopilot(self)
        # Feed freshness: set by scanner/book-feed tasks
        self._last_feed_update: datetime | None = None
        self._feed_diag: dict[str, Any] | None = None  # last scan stats / error
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

    def set_feed_diag(self, diag: dict[str, Any] | None) -> None:
        self._feed_diag = diag

    def set_balance(self, value: str | None) -> None:
        """Record available USDC (decimal string). None = unavailable (never a fake 0)."""
        self._balance_usd = value

    def mark_feed_updated(self) -> None:
        self._last_feed_update = datetime.now(timezone.utc)

    def status(self) -> dict[str, Any]:
        exposure = sum((p.cost_usd for p in self.positions.list_positions()), Decimal("0"))
        active_rules = len(self.rules.list_active())
        live_info: dict[str, Any] = {}
        if self.mode == "LIVE":
            live_info = {
                "live_ready": self.live_execution_ready,
                "live_note": (self.live_not_ready_reason if not self.live_execution_ready
                          else self.live_sync_error),
            }
        return {
            **live_info,
            "mode": self.mode,
            "auth_mode": resolve_auth_mode(self.settings),
            "feeds_ok": self.feeds_ok,
            "feed_diag": dict(self._feed_diag) if self._feed_diag is not None else None,
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
        live_failed = 0
        if self.live is not None:
            res = self.live.cancel_everything()
            cancelled = res["cancelled"]
            live_failed = res["failed"]
        else:
            cancelled = sum(1 for o in self.executor.list_orders() if self.executor.cancel(o.id))
        rules_cancelled = self.rules.cancel_all()
        out = {
            "proposals_rejected": rejected,
            "orders_cancelled": cancelled,
            "rules_cancelled": rules_cancelled,
        }
        if self.live is not None:
            out["live_cancel_failed"] = live_failed
        return out

    # ── live arming ───────────────────────────────────────────

    def arm_live(self, runtime: LiveRuntime) -> None:
        """Called once ``setup_live`` succeeded. Orders/cancels now go to the live runtime."""
        self.live = runtime
        self.executor = runtime
        self.live_execution_ready = True
        self.live_not_ready_reason = None

    def sync_live(self) -> dict[str, Any] | None:
        """Pull fills/open orders/positions from PRED and mirror them locally. Never raises.

        PRED is the source of truth: positions are upserted from its report (quantity and
        average price), never invented from our own bookkeeping. A failed sync blocks NEW live
        orders (fail closed) until the next successful pass.
        """
        rt = self.live
        if rt is None:
            return None
        with self._live_lock:
            try:
                report = rt.executor.sync()
                remote = rt.executor.fetch_positions()
            except Exception as exc:  # noqa: BLE001
                self.live_sync_error = f"{type(exc).__name__}: {exc}"[:200]
                log.error("live sync failed: %s", self.live_sync_error)
                return None
            seen = set()
            for pos in remote:
                seen.add(pos["market_id"])
                self.positions.open_position(
                    market_id=pos["market_id"],
                    parent_id=self._fixture_for(pos["market_id"]) or pos["parent_market_id"],
                    side=pos["side"], quantity=pos["quantity"],
                    avg_price_cents=pos["avg_price_cents"], cost_usd=pos["cost_usd"],
                )
            missing = [p.market_id for p in self.positions.list_positions() if p.market_id not in seen]
            if missing:
                # Closed/settled/exited on the exchange: realized PnL is unknown here, so we only
                # report it instead of booking a made-up number.
                log.warning("positions missing on exchange (not auto-closed): %s", missing)
            self.live_sync_error = None
            self.live_last_sync = {**report, "positions": len(remote), "missing_on_exchange": missing}
            return self.live_last_sync

    def _fee_target_bps(self, market_id: str) -> Decimal:
        """Discovery fee target for DISPLAY estimates only; orders never use this (they sign the
        exact recomputed rate and refuse when discovery has no fee). 250 only when unknown."""
        ctx = self.market_lookup(market_id) if self.market_lookup else None
        if ctx is not None and ctx.fee_target_bps is not None:
            return Decimal(ctx.fee_target_bps)
        return Decimal(250)

    def _fixture_for(self, market_id: str) -> str | None:
        existing = self.positions.get_position(market_id)
        return existing.parent_id if existing else None

    def disarm_live(self, reason: str) -> None:
        """Setup failed or was lost: LIVE approvals fail closed with this reason."""
        changed = reason != self.live_not_ready_reason or self.live_execution_ready
        self.live_execution_ready = False
        self.live_not_ready_reason = reason
        # Setup is retried on a timer: log at ERROR when the reason changes, DEBUG when it repeats.
        log.log(logging.ERROR if changed else logging.DEBUG, "live execution NOT ready: %s", reason)

    # ── proposals ─────────────────────────────────────────────

    def list_proposals(self, status: str | None = None) -> list[Proposal]:
        self.proposals.expire_stale()
        items = list(self.proposals._proposals.values())  # noqa: SLF001
        if status:
            # Clients (website + mobile app) ask for "pending"; the enum value is PROPOSED.
            want = "PROPOSED" if status.upper() == "PENDING" else status.upper()
            items = [p for p in items if p.status.value == want]
        return sorted(items, key=lambda p: p.created_at, reverse=True)

    def approve(self, proposal_id: str, via: str, amount: Decimal | None = None) -> Proposal | None:
        self.last_error = None
        existing = self.proposals.get(proposal_id)
        # Pause blocks new entries only; exits (reduce-only) must still be able to run.
        if self.paused and not (existing is not None and self._is_exit(existing)):
            self.last_error = "agent is paused"
            return None
        cap = Decimal(str(self.limits["max_trade_usd"]))
        if amount is not None and (amount <= 0 or amount > cap):
            self.last_error = f"amount must be > 0 and <= {cap}"
            return None
        if self.mode == "LIVE":
            with self._live_lock:
                return self._approve_live(proposal_id, via, amount)
        prop = self.proposals.approve(proposal_id, via=via, new_amount_usd=amount)
        if prop is None:
            return None
        if self.mode == "PAPER" and self._is_exit(prop):
            order = self.executor.place(
                proposal_id=prop.id, market_id=prop.market_id, side=prop.side,
                price_cents=prop.price_cents, quantity=prop.quantity,
                amount_usd=prop.amount_usd, order_type=prop.order_type,
            )
            pos = self.positions.get_position(prop.market_id)
            if pos is not None and pos.status == "open" and getattr(order, "status", "") == "filled":
                pnl = self.rules.compute_pnl(
                    position=pos, current_price_cents=prop.price_cents,
                    fee_rate_bps=int(self._fee_target_bps(prop.market_id)),
                )
                self.positions.close_position(prop.market_id, realized_pnl_usd=pnl["profit_usd"])
        elif self.mode == "PAPER":
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

    def _fail(self, message: str) -> Proposal | None:
        """Record why a LIVE approval was refused. Always returns None."""
        self.last_error = message
        log.warning("live approve refused: %s", message)
        return None

    @staticmethod
    def _is_exit(prop: Proposal) -> bool:
        return any(r.startswith("exit requested via") for r in prop.reasons)

    @staticmethod
    def _is_user_priced(prop: Proposal) -> bool:
        """Limit price chosen by the human (chat/app): the one-sided/spread/'book moved' guards
        are for agent-chosen prices and do not apply to a resting order at the user's price."""
        return prop.order_type == "limit" and any(r.startswith("user limit price") for r in prop.reasons)

    @staticmethod
    def _is_manual(prop: Proposal) -> bool:
        """Human-chosen trades (chat/app) skip the strategy edge gate; everything else applies."""
        return any(r.startswith(("requested via", "exit requested via")) for r in prop.reasons)

    def _approve_live(  # noqa: PLR0911, PLR0912, PLR0915
        self, proposal_id: str, via: str, amount: Decimal | None
    ) -> Proposal | None:
        """Place a REAL order for a pending proposal. Every refusal sets ``last_error``."""
        self.last_error = None
        rt = self.live
        if not self.live_execution_ready or rt is None:
            return self._fail(f"live execution not ready: {self.live_not_ready_reason or 'not armed'}")
        if self.live_sync_error:
            return self._fail(f"exchange sync failing, refusing new orders: {self.live_sync_error}")
        prop = self.proposals.get(proposal_id)
        if prop is None:
            return self._fail("proposal not found")
        if prop.is_expired():
            self.proposals.expire_stale()
            return self._fail("proposal expired")
        if prop.status != ProposalStatus.PROPOSED:
            return self._fail(f"proposal already {prop.status.value}")
        if prop.mode != "LIVE" or prop.advisory:
            return self._fail("only non-advisory LIVE proposals can be traded")
        ctx: MarketContext | None = self.market_lookup(prop.market_id) if self.market_lookup else None
        if ctx is None:
            return self._fail("market not found in the latest discovery scan")
        if not ctx.contract_address:
            return self._fail("discovery has no contract_address for this parent")
        if ctx.fee_target_bps is None:
            return self._fail("discovery has no fee_rate_bps for this parent; refusing to assume fee-free")

        is_exit = self._is_exit(prop)
        cap = Decimal(str(self.limits["max_trade_usd"]))
        try:
            if is_exit:
                pos = self.positions.get_position(prop.market_id)
                if pos is None or pos.status != "open":
                    return self._fail(f"no open position for market {prop.market_id}")
                qty = pos.quantity.quantize(Decimal("0.001"), rounding=ROUND_DOWN)
            else:
                budget = amount if amount is not None else prop.amount_usd
                qty = quantity_for_amount(prop.side, prop.price_cents, budget)
            amount_exact = order_amount_usd(prop.side, prop.price_cents, qty)
        except (ValueError, ArithmeticError) as exc:
            return self._fail(f"cannot size order: {exc}")

        if not is_exit:
            if amount_exact > cap:
                return self._fail(f"order notional {amount_exact} exceeds max_trade_usd {cap}")
            max_pos = int(self.limits["max_open_positions"])
            if len(self.positions.list_positions()) >= max_pos:
                return self._fail(f"max_open_positions ({max_pos}) reached")
            loss_cap = Decimal(str(self.limits["daily_loss_usd"]))
            if self.positions.daily_pnl() <= -loss_cap:
                return self._fail(f"daily loss limit ({loss_cap}) reached")

        intent = LiveOrderIntent(
            market_id=prop.market_id,
            parent_market_id=ctx.parent_market_id,
            contract_address=ctx.contract_address,
            side=prop.side,
            price_cents=prop.price_cents,
            quantity=qty,
            amount_usd=amount_exact,
            order_type=prop.order_type,
            fee_rate_bps=0,  # recomputed from fee_target_bps
            fee_target_bps=ctx.fee_target_bps,
            reduce_only=is_exit,
            user_priced=self._is_user_priced(prop),
            p_fair=prop.p_fair,
            min_edge_pp=None if self._is_manual(prop) else Decimal(str(self.settings.min_edge_pp)),
        )
        # Side-effect-free check first: a refusal leaves the proposal PROPOSED (retry/reject).
        reason = rt.executor.precheck(intent)
        if reason is not None:
            return self._fail(reason)

        # Persist the decision BEFORE the side effect.
        approved = self.proposals.approve(proposal_id, via=via, new_amount_usd=amount_exact)
        if approved is None:
            return self._fail("proposal could not be approved (expired or already decided)")
        approved.quantity = qty
        self.proposals._persist(approved)  # noqa: SLF001

        order = rt.executor.place_from_proposal(proposal_id=proposal_id, intent=intent)
        if order is None:
            self._abort(approved, rt.executor.last_abort_reason or "aborted before sending")
            return None
        if order.state == OrderState.FAILED:
            self._abort(approved, f"PRED rejected the order: {order.raw_response}")
            return None
        if order.state == OrderState.UNKNOWN:
            self.last_error = (
                "order outcome UNKNOWN (market blocked). Do NOT re-approve; check open orders/portfolio."
            )
            log.error("live order UNKNOWN id=%s", order.id)
            return approved
        self._record_live_fill(approved, order, ctx, is_exit=is_exit)
        return approved

    def _abort(self, prop: Proposal, reason: str) -> None:
        prop.status = ProposalStatus.ABORTED
        prop.reasons = [*prop.reasons, f"aborted: {reason}"]
        self.proposals._persist(prop)  # noqa: SLF001
        self._fail(reason)

    def _record_live_fill(
        self, prop: Proposal, order: Any, ctx: MarketContext, *, is_exit: bool
    ) -> None:
        """Mirror immediate fills into the position book. Resting orders have no fill yet."""
        filled = Decimal(str(order.filled_qty or "0"))
        if filled <= 0:
            return
        if is_exit:
            pos = self.positions.get_position(prop.market_id)
            if pos is not None and order.state == OrderState.FILLED:
                pnl = self.rules.compute_pnl(
                    position=pos, current_price_cents=prop.price_cents,
                    fee_rate_bps=ctx.fee_target_bps or 0,
                )
                self.positions.close_position(prop.market_id, realized_pnl_usd=pnl["profit_usd"])
            return
        self.positions.open_position(
            market_id=prop.market_id, parent_id=prop.fixture_id, side=prop.side,
            quantity=filled, avg_price_cents=prop.price_cents,
            cost_usd=order_amount_usd(prop.side, prop.price_cents, filled),
        )

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
        order_type: str = "limit",
        user_priced: bool = False,
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
        est_fee = amount_usd * self._fee_target_bps(market_id) / Decimal(10000)
        prop = self.proposals.create(
            mode=self.mode, advisory=False, fixture_id=fixture_id, market_id=market_id,
            side=side, order_type=order_type, price_cents=price_cents, quantity=qty,
            amount_usd=amount_usd, est_fee_usd=est_fee, edge_pp=Decimal("0"),
            p_fair=price_cents / 100,
            max_suggested_usd=cap, binding_cap=None,
            reasons=[f"requested via {created_via}"]
            + ([f"user limit price {price_cents}c"] if user_priced else []),
            risks=None,
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
        est_fee = pos.cost_usd * self._fee_target_bps(market_id) / Decimal(10000)
        prop = self.proposals.create(
            mode=self.mode, advisory=False, fixture_id=pos.parent_id, market_id=market_id,
            side=exit_side, order_type="limit", price_cents=price, quantity=pos.quantity,
            amount_usd=pos.cost_usd, est_fee_usd=est_fee, edge_pp=Decimal("0"),
            p_fair=price / 100,
            max_suggested_usd=Decimal(str(self.limits["max_trade_usd"])),
            binding_cap=None, reasons=[f"exit requested via {via}"], risks=None,
        )
        return prop, None

    def exit_proposal(
        self, market_id: str, *, order_type: str, price_cents: Decimal, via: str,
        user_priced: bool = False,
    ) -> tuple[Proposal | None, str | None]:
        """Create a reduce-only exit proposal with an explicit order type and price."""
        pos = self.positions.get_position(market_id)
        if pos is None or pos.status != "open":
            return None, f"no open position for market {market_id}"
        exit_side = "short" if pos.side == "long" else "long"
        est_fee = pos.cost_usd * self._fee_target_bps(market_id) / Decimal(10000)
        prop = self.proposals.create(
            mode=self.mode, advisory=False, fixture_id=pos.parent_id, market_id=market_id,
            side=exit_side, order_type=order_type, price_cents=price_cents, quantity=pos.quantity,
            amount_usd=pos.cost_usd, est_fee_usd=est_fee, edge_pp=Decimal("0"),
            p_fair=price_cents / 100,
            max_suggested_usd=Decimal(str(self.limits["max_trade_usd"])),
            binding_cap=None,
            reasons=[f"exit requested via {via}"]
            + ([f"user limit price {price_cents}c"] if user_priced else []),
            risks=None,
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
        exec_mode: str = "market",
    ) -> tuple[Rule | None, str | None]:
        """Create a PENDING exit rule from a chat intent. Returns (rule, error)."""
        pos = self.positions.get_position(market_id)
        if pos is None:
            return None, f"no open position for market {market_id}"
        if kind not in ("take_profit", "stop_loss", "time_exit"):
            return None, f"kind must be take_profit, stop_loss or time_exit (got {kind})"
        if trigger_type not in (
            "profit_usd", "loss_usd", "price_cents", "profit_pct", "loss_pct", "deadline_ts"
        ):
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
            exec_mode=exec_mode,
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
        out: list[dict[str, Any]] = []
        for o in self.executor.list_orders():
            d = to_jsonable(o)
            # Paper orders carry ``status``; live orders carry ``state``. The app and the
            # API contract read ``state`` — always provide it so clients never see it missing.
            if isinstance(d, dict) and not d.get("state"):
                d["state"] = str(d.get("status") or "UNKNOWN").upper()
            out.append(d)
        return out

    def positions_list(self) -> list[dict[str, Any]]:
        return [to_jsonable(p) for p in self.positions.list_positions()]


__all__ = ["AgentController", "ProposalStatus", "to_jsonable"]
