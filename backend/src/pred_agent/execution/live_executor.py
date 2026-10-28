"""Live Executor — Phase 4 (REAL MONEY).

Constructed ONLY when LIVE_TRADING=true. Pipeline:
  re-validate -> build order -> signed fee bps -> amount formula -> random salt ->
  sign EIP-712 with parent contract_address -> place with fresh timestamp.

Grounded in kit/02_PRED_API_SPEC.md section 8 (Orders) and kit/03_ARCHITECTURE.md section 4.
Never resends with a new salt; uncertain outcomes go to UNKNOWN and are reconciled.

Fail-closed rules enforced here (see ``_revalidate_reason``):
  - maker Safe address known, market not blocked by an UNKNOWN order
  - discovery fee target present (never assume fee-free), signed fee recomputed from it
  - child/parent still active (``market_check``), fresh order book (``book_provider``)
  - spread, price-vs-book tolerance, IOC depth/slippage, and (for strategy trades) edge
  - docs validation: 3-decimal quantity, $0.90 opening notional, 0.001 reduce-only quantity
  - ``amount`` is ALWAYS recomputed from the docs formula; caller-supplied amount is ignored
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pred_agent.book import BookAnalyzer, BookSnapshot
from pred_agent.config import load_settings
from pred_agent.execution.state_machine import (
    OrderRecord,
    OrderState,
    OrderStateMachine,
)
from pred_agent.signer.eip712 import sign_order
from pred_agent.transport.protocol import PredTransport, PredTransportError
from pred_agent.util.decimals import (
    MAX_QTY_DECIMALS,
    MIN_OPENING_NOTIONAL,
    REDUCE_ONLY_MIN_QTY,
    order_amount_usd,
    plain_decimal,
)
from pred_agent.util.fees import signed_fee_bps
from pred_agent.util.redaction import redact
from pred_agent.util.time import CLOCK
from pred_agent.util.units import price_cents_to_wei, shares_to_wei

# (parent_market_id, market_id) -> fresh normalized book, or None when unavailable.
BookProvider = Callable[[str, str], BookSnapshot | None]
# market_id -> None when the child/parent are still tradable, else a human-readable reason.
MarketCheck = Callable[[str], str | None]

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BYTES32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# HTTP statuses that mean PRED definitely did NOT accept the order (safe to mark FAILED).
_DEFINITE_REJECT_STATUSES = frozenset({400, 401, 403, 404, 422})
_MAX_OPEN_ORDER_PAGES = 50
# trade-history also lists settlement rows (Redeemed / Lost); only trades are order fills.
_NON_FILL_ACTIVITIES = frozenset({"redeemed", "lost"})

_ACTIVE_STATES = frozenset({
    OrderState.SUBMITTED, OrderState.DELAYED, OrderState.OPEN, OrderState.PARTIAL,
})


class ReconcileError(RuntimeError):
    """Startup reconcile could not establish the exchange state (fail closed)."""


@dataclass
class LiveOrderIntent:
    market_id: str
    parent_market_id: str
    contract_address: str
    side: str  # "long" | "short"
    price_cents: Decimal
    quantity: Decimal  # shares
    amount_usd: Decimal  # informational only: ALWAYS recomputed from the docs formula
    order_type: str  # "limit" | "ioc"
    fee_rate_bps: int  # signed rate
    expiration: int = 0
    reduce_only: bool = False
    max_match_amount: Decimal | None = None
    # Discovery target fee (parent_market_data.fee_rate_bps). When set, the signed rate is
    # recomputed from it and price; fee_rate_bps is overwritten (docs: never sign the raw target).
    fee_target_bps: int | None = None
    # Strategy trades carry the model probability + minimum edge; manual (human-chosen)
    # trades leave min_edge_pp as None and skip the edge gate.
    p_fair: Decimal | None = None
    min_edge_pp: Decimal | None = None


class LiveExecutor:
    """Live order execution. Only constructed when LIVE_TRADING=true."""

    def __init__(
        self,
        *,
        transport: PredTransport,
        state_machine: OrderStateMachine,
        eoa_address: str,
        signer_private_key: str,
        chain_id: int = 8453,
        proxy_address: str | None = None,
        book_provider: BookProvider | None = None,
        market_check: MarketCheck | None = None,
        max_spread_cents: Decimal = Decimal("6"),
        max_slippage_pp: Decimal = Decimal("1.5"),
    ) -> None:
        settings = load_settings()
        if not settings.live_trading:
            raise RuntimeError("LiveExecutor requires LIVE_TRADING=true")
        self.transport = transport
        self.sm = state_machine
        self.eoa_address = eoa_address
        self.signer_private_key = signer_private_key
        self.chain_id = chain_id
        # Order `maker` must be the user's Safe (proxy wallet), `signer` is the EOA.
        self.proxy_address = proxy_address
        self.book_provider = book_provider
        self.market_check = market_check
        self.analyzer = BookAnalyzer(
            max_spread_cents=max_spread_cents, max_slippage_pp=max_slippage_pp
        )
        self.max_slippage_pp = max_slippage_pp
        # Why the last place_from_proposal aborted before sending (None if it did not abort).
        self.last_abort_reason: str | None = None

    def _order_prefix(self) -> str:
        """CLOB transports route trading under /clob; JWT transports are unprefixed."""
        return str(getattr(self.transport, "order_prefix", "") or "")

    def _now(self) -> int:
        """Unix seconds on PRED's clock when the transport tracks the offset, else local."""
        auth = getattr(self.transport, "auth", None)
        now_fn = getattr(auth, "_now", None)
        if callable(now_fn):
            return int(now_fn())
        return int(CLOCK.now())

    # ------------------------------------------------------------------ #
    # Public entry point: place a live order from an approved proposal
    # ------------------------------------------------------------------ #

    def place_from_proposal(  # noqa: PLR0911
        self,
        *,
        proposal_id: str,
        intent: LiveOrderIntent,
    ) -> OrderRecord | None:
        """Full live placement pipeline. Returns the order record or None on abort."""
        market_id = intent.market_id
        self.last_abort_reason = None

        if intent.fee_target_bps is not None:
            try:
                intent.fee_rate_bps = signed_fee_bps(intent.fee_target_bps, intent.price_cents)
            except ValueError as exc:
                self.last_abort_reason = str(exc)
                return None

        # 1. Re-validate before placing (child status, book, edge, slippage, units, fee target)
        reason = self._revalidate_reason(intent)
        if reason is not None:
            self.last_abort_reason = reason
            return None

        maker = self.proxy_address
        if maker is None:  # unreachable after revalidation; keeps the type honest
            self.last_abort_reason = "maker Safe (proxy wallet) address unknown"
            return None

        # The docs require `amount` to equal the side-aware notional exactly: never trust the
        # caller's figure.
        intent.amount_usd = order_amount_usd(intent.side, intent.price_cents, intent.quantity)

        # 2. Build order
        salt = secrets.randbelow(2**128)  # cryptographically random uint256 (subset for safety)
        price_wei = price_cents_to_wei(intent.price_cents)
        quantity_wei = shares_to_wei(intent.quantity)

        # 3. Sign EIP-712 Order with parent's contract_address
        intent_side = 0 if intent.side == "long" else 1
        signature = sign_order(
            chain_id=self.chain_id,
            verifying_contract=intent.contract_address,
            salt=salt,
            maker=maker,                 # user's Safe (proxy wallet)
            signer=self.eoa_address,     # EOA
            price=price_wei,
            quantity=quantity_wei,
            expiration=intent.expiration,
            nonce=0,
            question_id=intent.market_id,
            fee_rate_bps=intent.fee_rate_bps,
            intent=intent_side,
            signature_type=2,
            private_key=self.signer_private_key,
        )

        # 4. Place with fresh timestamp
        body: dict[str, Any] = {
            "salt": str(salt),
            "market_id": intent.market_id,
            "side": intent.side,
            "price": plain_decimal(intent.price_cents),
            "quantity": plain_decimal(intent.quantity),
            "amount": plain_decimal(intent.amount_usd),
            "signature": signature,
            "type": intent.order_type,
            "timestamp": self._now(),
            "fee_rate_bps": intent.fee_rate_bps,
            "expiration": intent.expiration,
            "reduce_only": intent.reduce_only,
        }
        if intent.max_match_amount is not None and intent.order_type in ("ioc", "market"):
            body["max_match_amount"] = plain_decimal(intent.max_match_amount)

        path = f"{self._order_prefix()}/api/v1/order/{intent.parent_market_id}/place"

        # Persist order BEFORE side effect (place call)
        order = OrderRecord(
            id=f"live-{salt}",
            pred_order_id=None,
            proposal_id=proposal_id,
            state=OrderState.SUBMITTED,
            side=intent.side,
            price_cents=price_wei,
            quantity=plain_decimal(intent.quantity),
            market_id=intent.market_id,
            parent_market_id=intent.parent_market_id,
            salt=str(salt),
            signed_fee_bps=intent.fee_rate_bps,
            reduce_only=intent.reduce_only,
            raw_request_redacted=redact(str(body)),
        )
        self.sm.create(order)

        try:
            resp = self.transport.post(path, json=body)
        except PredTransportError as exc:
            return self._handle_transport_error(order, intent, exc)
        except Exception as exc:  # noqa: BLE001
            # Timeout / connection reset / anything unexpected: outcome unknown, never resend.
            order.state = OrderState.UNKNOWN
            order.raw_response = redact(str(exc))
            self.sm.block_market(market_id)
            self.sm.block_market(intent.parent_market_id)
            return order

        # 5. Handle response
        status = resp.get("status", "failed")
        order.pred_order_id = resp.get("order_id")
        order.raw_response = redact(str(resp))

        if status in ("order_created", "matched", "partial_matched", "open_order"):
            order.state = OrderState.OPEN
            if status == "matched":
                order.state = OrderState.FILLED
            elif status == "partial_matched":
                order.state = OrderState.PARTIAL
            if resp.get("is_delayed"):
                order.state = OrderState.DELAYED
                order.is_delayed = True
                order.delay_until = resp.get("delay_until")
            filled_qty = resp.get("filled_quantity")
            if filled_qty:
                order.filled_qty = str(filled_qty)
                order.remaining_qty = str(
                    Decimal(intent.quantity) - Decimal(str(filled_qty))
                )
            return order

        if status == "failed":
            order.state = OrderState.FAILED
            return order

        # Unknown status -> fail closed
        order.state = OrderState.UNKNOWN
        self.sm.block_market(market_id)
        self.sm.block_market(intent.parent_market_id)
        return order

    def _handle_transport_error(
        self, order: OrderRecord, intent: LiveOrderIntent, exc: PredTransportError
    ) -> OrderRecord:
        """Classify a non-2xx reply. Only an explicit client-side rejection is a definite
        failure; 5xx, 409 placement_in_progress and anything else may have been accepted."""
        order.raw_response = redact(str(exc.raw_body)) if exc.raw_body else redact(str(exc))
        if exc.status in _DEFINITE_REJECT_STATUSES:
            order.state = OrderState.FAILED
            # Service-level failures (e.g. 422) can still carry an order_id.
            order.pred_order_id = _order_id_from_body(exc.raw_body)
            return order
        order.state = OrderState.UNKNOWN
        order.pred_order_id = _order_id_from_body(exc.raw_body)
        self.sm.block_market(intent.market_id)
        self.sm.block_market(intent.parent_market_id)
        return order

    # ------------------------------------------------------------------ #
    # Re-validation before placing
    # ------------------------------------------------------------------ #

    def precheck(self, intent: LiveOrderIntent) -> str | None:
        """Side-effect-free validation (recomputes the signed fee). None = would be sent."""
        if intent.fee_target_bps is not None:
            try:
                intent.fee_rate_bps = signed_fee_bps(intent.fee_target_bps, intent.price_cents)
            except ValueError as exc:
                return str(exc)
        return self._revalidate_reason(intent)

    def _revalidate(self, intent: LiveOrderIntent) -> bool:
        """True when the order may be sent. See ``_revalidate_reason`` for why not."""
        reason = self._revalidate_reason(intent)
        self.last_abort_reason = reason
        return reason is None

    def _revalidate_reason(self, intent: LiveOrderIntent) -> str | None:  # noqa: PLR0911, PLR0912
        """Return None when the order is safe to send, else the reason it must not be."""
        # Maker (Safe) address unknown -> fail closed
        if not self.proxy_address:
            return "maker Safe (proxy wallet) address unknown"
        if self.sm.is_market_blocked(intent.parent_market_id) or self.sm.is_market_blocked(
            intent.market_id
        ):
            return "market blocked by an UNKNOWN order that must be reconciled first"

        # Fee: the discovery target must be present; absent config is never assumed fee-free.
        if intent.fee_target_bps is None:
            return "discovery fee_rate_bps missing for this parent; refusing to assume fee-free"

        # Identity / format
        if not _ADDRESS_RE.match(intent.contract_address or ""):
            return "parent contract_address missing or malformed"
        if not _BYTES32_RE.match(intent.market_id or ""):
            return "market_id is not an exact 0x + 64 hex child market id"
        if intent.side not in ("long", "short"):
            return f"side must be long or short, got {intent.side!r}"
        if intent.order_type not in ("limit", "ioc"):
            return f"unsupported order type {intent.order_type!r}"

        # Price / quantity validity (docs: Orders -> Units, Validation)
        price = intent.price_cents
        if not Decimal(0) < price < Decimal(100):
            return f"price must be between 0 and 100 cents, got {price}"
        if price * Decimal(10_000) != (price * Decimal(10_000)).to_integral_value():
            return f"price {price} cannot be represented at 1e6 scale"
        qty = intent.quantity
        qty_exp = qty.as_tuple().exponent
        if not isinstance(qty_exp, int) or qty <= 0 or qty_exp < -MAX_QTY_DECIMALS:
            return f"quantity must be > 0 with at most {MAX_QTY_DECIMALS} decimals, got {qty}"
        if intent.reduce_only:
            if qty < REDUCE_ONLY_MIN_QTY:
                return f"reduce-only quantity below {REDUCE_ONLY_MIN_QTY}"
        else:
            notional = order_amount_usd(intent.side, price, qty)
            if notional < MIN_OPENING_NOTIONAL:
                return f"opening notional {notional} is below ${MIN_OPENING_NOTIONAL}"

        # Child/parent still tradable (fresh discovery state)
        if self.market_check is None:
            return "no market status check configured (fail closed)"
        market_reason = self.market_check(intent.market_id)
        if market_reason is not None:
            return market_reason

        # Fresh order book
        if self.book_provider is None:
            return "no order book provider configured (fail closed)"
        snapshot = self.book_provider(intent.parent_market_id, intent.market_id)
        if snapshot is None:
            return "no fresh order book available (fail closed)"
        return self._check_book(intent, self.analyzer.analyze(snapshot))

    def _check_book(self, intent: LiveOrderIntent, book: BookSnapshot) -> str | None:  # noqa: PLR0911
        price = intent.price_cents
        tol = self.max_slippage_pp
        is_long = intent.side == "long"

        if not intent.reduce_only:
            if not book.bids or not book.asks:
                return "order book is one-sided; refusing to open"
            if not self.analyzer.check_spread(book):
                return f"spread {book.spread_cents}c exceeds {self.analyzer.max_spread_cents}c"

        # Never pay/accept materially worse than the live top of book.
        if is_long and book.asks and price > book.asks[0].price_cents + tol:
            return (
                f"limit {price}c is more than {tol}pp above best ask "
                f"{book.asks[0].price_cents}c (book moved)"
            )
        if not is_long and book.bids and price < book.bids[0].price_cents - tol:
            return (
                f"limit {price}c is more than {tol}pp below best bid "
                f"{book.bids[0].price_cents}c (book moved)"
            )

        # Marketable IOC: walk the book (depth >= 3x size and slippage within tolerance).
        if intent.order_type == "ioc" and not intent.reduce_only:
            walk = self.analyzer.slippage_walk(
                book, "buy" if is_long else "sell", intent.quantity
            )
            if not walk.usable:
                return (
                    f"IOC not safe: depth/slippage check failed "
                    f"(slippage {walk.slippage_pp:.2f}pp, filled {walk.filled_shares})"
                )

        # Strategy trades only: edge must still hold at the live price.
        if intent.min_edge_pp is not None and not intent.reduce_only:
            if intent.p_fair is None:
                return "edge check requested but p_fair missing"
            fair_cents = intent.p_fair * Decimal(100)
            edge = fair_cents - price if is_long else price - fair_cents
            if edge < intent.min_edge_pp:
                return f"edge {edge:.2f}pp fell below minimum {intent.min_edge_pp}pp"
        return None

    # ------------------------------------------------------------------ #
    # Cancel
    # ------------------------------------------------------------------ #

    def _find_by_pred_id(self, pred_order_id: str) -> OrderRecord | None:
        for rec in self.sm.list_orders():
            if rec.pred_order_id == pred_order_id:
                return rec
        return None

    def cancel(self, order_id: str, market_id: str, parent_market_id: str) -> dict[str, Any]:
        """Cancel a single order. Inspect result; cancel_noop means NOT cancelled.

        ``order_id`` is the local record id (or PRED's order id); PRED is always sent its own id.
        """
        order = self.sm.get(order_id) or self._find_by_pred_id(order_id)
        if order is None:
            return {"status": "not_found", "order_id": order_id}
        pred_id = order.pred_order_id
        if not pred_id:
            # Never got an id from PRED (UNKNOWN placement): nothing safe to cancel by id.
            return {"status": "error", "order_id": order_id,
                    "error": "order has no PRED order_id yet; reconcile first"}

        path = f"{self._order_prefix()}/api/v1/order/{parent_market_id}/cancel"
        body = {"order_id": pred_id, "market_id": market_id}
        try:
            resp = self.transport.delete(path, json=body)
        except PredTransportError as exc:
            return {"status": "error", "order_id": order_id, "error": redact(str(exc.raw_body))}

        status = resp.get("status", "failed")
        order.raw_response = redact(str(resp))

        if status == "user_cancelled":
            order.state = OrderState.CLOSED  # cancelled
        elif status == "cancel_noop":
            # Not cancelled — order may be delayed; do NOT assume cancelled
            order.state = OrderState.OPEN
        elif status in ("not_found", "unauthorized"):
            order.state = OrderState.FAILED
        else:
            order.state = OrderState.UNKNOWN
            self.sm.block_market(market_id)

        return {"status": status, "order_id": order_id, "raw": resp}

    def cancel_all_per_parent(self, parent_market_id: str) -> dict[str, Any]:
        """Cancel all open orders for a parent. Inspect every result and total_failed."""
        path = f"{self._order_prefix()}/api/v1/order/{parent_market_id}/cancel/all"
        try:
            resp = self.transport.delete(path, json=None)
        except PredTransportError as exc:
            return {"status": "error", "error": redact(str(exc.raw_body))}

        # CLOB cancel/all returns no body with results; JWT version may return results.
        total_failed = resp.get("total_failed", 0)
        results = resp.get("results", [])
        for r in results:
            oid = r.get("order_id")
            if oid:
                order = self._find_by_pred_id(str(oid)) or self.sm.get(str(oid))
                if order:
                    st = r.get("status", "failed")
                    if st == "user_cancelled":
                        order.state = OrderState.CLOSED
                    elif st == "cancel_noop":
                        order.state = OrderState.OPEN  # not cancelled
                    else:
                        order.state = OrderState.FAILED

        return {
            "total_found": resp.get("total_found", 0),
            "total_cancelled": resp.get("total_cancelled", 0),
            "total_failed": total_failed,
            "results": results,
        }

    # ------------------------------------------------------------------ #
    # Exit (reduce-only close)
    # ------------------------------------------------------------------ #

    def reduce_only_close(
        self,
        *,
        proposal_id: str,
        market_id: str,
        parent_market_id: str,
        contract_address: str,
        side: str,  # opposite of existing position
        price_cents: Decimal,
        quantity: Decimal,
        amount_usd: Decimal,
        fee_target_bps: int,
    ) -> OrderRecord | None:
        """Create a reduce_only opposite-side order to close a position."""
        intent = LiveOrderIntent(
            market_id=market_id,
            parent_market_id=parent_market_id,
            contract_address=contract_address,
            side=side,
            price_cents=price_cents,
            quantity=quantity,
            amount_usd=amount_usd,
            order_type="limit",
            fee_rate_bps=0,  # overwritten from fee_target_bps in place_from_proposal
            fee_target_bps=fee_target_bps,
            reduce_only=True,
        )
        return self.place_from_proposal(proposal_id=proposal_id, intent=intent)

    # ------------------------------------------------------------------ #
    # Startup reconcile
    # ------------------------------------------------------------------ #

    def _fetch_all_open_orders(self) -> list[dict[str, Any]]:
        """Read every page of portfolio/open-orders (default page is only 50 rows).

        Docs: paginate with ``offset``/``limit`` (max 100) until ``pagination.has_more`` is false.
        A reply without ``pagination`` is treated as a single page.
        """
        path = f"{self._order_prefix()}/api/v1/portfolio/open-orders"
        limit = 100
        offset = 0
        out: list[dict[str, Any]] = []
        for _ in range(_MAX_OPEN_ORDER_PAGES):
            body = self.transport.get(path, params={"limit": limit, "offset": offset})
            page = _extract_open_orders(body)
            out.extend(page)
            pagination = body.get("pagination") if isinstance(body, dict) else None
            if not isinstance(pagination, dict) or not pagination.get("has_more") or not page:
                return out
            offset += int(pagination.get("limit") or limit)
        raise ReconcileError("open-orders pagination did not terminate; refusing to guess")

    def reconcile_startup(self) -> dict[str, Any]:
        """Reconcile local orders with the exchange's open orders. Fail closed.

        Never resends an uncertain order. Local orders PRED still reports open are refreshed;
        exchange-side open orders we do not know (e.g. placed before a restart) are adopted
        so they can be listed and cancelled; local orders that vanished from the exchange's
        open list stay UNKNOWN (filled vs cancelled cannot be told from this endpoint) and
        their markets stay blocked until a human resolves them.
        """
        remote = self._fetch_all_open_orders()
        remote_by_id = {str(o["order_id"]): o for o in remote if o.get("order_id")}

        refreshed = 0
        unresolved: list[str] = []
        for rec in self.sm.list_orders():
            if rec.state not in _ACTIVE_STATES and rec.state != OrderState.UNKNOWN:
                continue
            pred_id = rec.pred_order_id
            row = remote_by_id.get(str(pred_id)) if pred_id else None
            if row is None:
                total = Decimal(str(rec.quantity))
                done = self._filled_for(rec)
                if done > 0 and done >= total:
                    rec.state = OrderState.FILLED  # fully matched: fills prove it
                    rec.filled_qty = plain_decimal(done)
                    rec.remaining_qty = "0"
                    continue
                if rec.state != OrderState.UNKNOWN:
                    rec.state = OrderState.UNKNOWN
                for mkt in (rec.market_id, rec.parent_market_id):
                    if mkt:
                        self.sm.block_market(mkt)
                unresolved.append(rec.id)
                continue
            total = Decimal(str(row.get("quantity", rec.quantity)))
            remaining = Decimal(str(row.get("remaining_quantity", total)))
            rec.remaining_qty = str(remaining)
            rec.filled_qty = str(total - remaining)
            rec.state = OrderState.PARTIAL if remaining < total else OrderState.OPEN
            refreshed += 1
        self._unblock_resolved()

        adopted = 0
        known = {rec.pred_order_id for rec in self.sm.list_orders() if rec.pred_order_id}
        for oid, row in remote_by_id.items():
            if oid in known:
                continue
            total = Decimal(str(row.get("quantity", "0")))
            remaining = Decimal(str(row.get("remaining_quantity", total)))
            self.sm.create(OrderRecord(
                id=oid,
                pred_order_id=oid,
                proposal_id=None,
                state=OrderState.PARTIAL if remaining < total else OrderState.OPEN,
                side=str(row.get("side", "")),
                price_cents=int(Decimal(str(row.get("price", "0"))) * Decimal(10_000)),
                quantity=plain_decimal(total),
                market_id=row.get("market_id"),
                parent_market_id=row.get("parent_market_id"),
                filled_qty=plain_decimal(total - remaining),
                remaining_qty=plain_decimal(remaining),
                reduce_only=bool(row.get("reduce_only", False)),
            ))
            adopted += 1

        return {
            "exchange_open_orders": len(remote_by_id),
            "refreshed": refreshed,
            "adopted": adopted,
            "unresolved": unresolved,
        }


    # ------------------------------------------------------------------ #
    # Fills and positions (exchange is the source of truth)
    # ------------------------------------------------------------------ #

    def _filled_for(self, rec: OrderRecord) -> Decimal:
        return sum(
            (Decimal(f.quantity) for f in self.sm.list_fills() if f.order_id == rec.id),
            Decimal(0),
        )

    def _unblock_resolved(self) -> None:
        """Unblock markets that no longer have any UNKNOWN order."""
        still = {
            m for rec in self.sm.list_orders() if rec.state == OrderState.UNKNOWN
            for m in (rec.market_id, rec.parent_market_id) if m
        }
        for mkt in self.sm.blocked_markets() - still:
            self.sm.unblock_market(mkt)

    def sync_fills(self, *, limit: int = 100) -> dict[str, int]:
        """Apply the newest trade-history rows to local orders (deduped by trade_history_id).

        Resting orders that fill later are only visible here. Uses the amounts PRED reports and
        never subtracts fees from them. Unrecognised replies fail closed.
        """
        body = self.transport.get(
            f"{self._order_prefix()}/api/v1/portfolio/trade-history",
            params={"limit": limit, "offset": 0},
        )
        if not isinstance(body, dict) or body.get("success") is False or not isinstance(
            body.get("trade_history"), list
        ):
            raise ReconcileError("trade-history reply has an unrecognised shape; refusing to guess")
        by_pred = {r.pred_order_id: r for r in self.sm.list_orders() if r.pred_order_id}
        applied = 0
        for row in body["trade_history"]:
            if str(row.get("activity") or "").strip().lower() in _NON_FILL_ACTIVITIES:
                continue
            rec = by_pred.get(str(row.get("order_id") or ""))
            if rec is None or not row.get("trade_history_id"):
                continue
            before = len(self.sm.list_fills())
            self.sm.apply_fill(
                fill_id=str(row["trade_history_id"]), match_id=None, order_id=rec.id,
                price_cents=int(Decimal(str(row.get("price", "0"))) * Decimal(10_000)),
                quantity=str(row.get("quantity", "0")),
                matched_on_chain=bool(row.get("transaction_hash")),
                fee_usd=str(row.get("fee_amount") or "0"),
            )
            applied += len(self.sm.list_fills()) - before
        resolved = 0
        for rec in by_pred.values():
            if rec.state not in _ACTIVE_STATES and rec.state != OrderState.UNKNOWN:
                continue
            done = self._filled_for(rec)
            if done <= 0:
                continue
            total = Decimal(str(rec.quantity))
            rec.filled_qty = plain_decimal(done)
            rec.remaining_qty = plain_decimal(max(total - done, Decimal(0)))
            was_unknown = rec.state == OrderState.UNKNOWN
            rec.state = OrderState.FILLED if done >= total else OrderState.PARTIAL
            resolved += int(was_unknown)
        self._unblock_resolved()
        return {"new_fills": applied, "resolved_unknown": resolved}

    def fetch_positions(self) -> list[dict[str, Any]]:
        """Open positions as PRED reports them, normalised; ambiguous rows are skipped.

        ``average_price`` must be unambiguously cents (> 1 and < 100); anything else is
        dropped rather than guessed, because a unit mistake would corrupt risk limits.
        """
        body = self.transport.get(
            f"{self._order_prefix()}/api/v1/portfolio/positions", params={"verbose": "true"}
        )
        if not isinstance(body, dict) or body.get("success") is False or not isinstance(
            body.get("positions"), list
        ):
            raise ReconcileError("positions reply has an unrecognised shape; refusing to guess")
        out: list[dict[str, Any]] = []
        for row in body["positions"]:
            try:
                qty = Decimal(str(row["quantity"]))
                avg = Decimal(str(row["average_price"]))
                amount = Decimal(str(row["amount"]))
                side = str(row["side"])
                market_id = str(row["market_id"])
            except (KeyError, ArithmeticError, ValueError):
                continue
            if qty <= 0 or side not in ("long", "short") or not Decimal(1) < avg < Decimal(100):
                continue
            out.append({
                "market_id": market_id, "parent_market_id": row.get("parent_market_id") or "",
                "side": side, "quantity": qty, "avg_price_cents": avg, "cost_usd": amount,
            })
        return out

    def sync(self) -> dict[str, Any]:
        """One reconcile pass: fills first (so fully-filled orders are not mistaken for lost)."""
        fills = self.sync_fills()
        report = self.reconcile_startup()
        return {**report, **fills}


def _order_id_from_body(raw_body: str | None) -> str | None:
    """Best-effort order_id from an error body (service-level failures can carry one)."""
    if not raw_body:
        return None
    import json

    try:
        parsed = json.loads(raw_body)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict):
        oid = parsed.get("order_id")
        return str(oid) if oid else None
    return None


def _extract_open_orders(body: Any) -> list[dict[str, Any]]:
    """Find the order list in a portfolio/open-orders reply; unknown shapes fail closed."""
    candidates: list[Any] = []
    if isinstance(body, list):
        candidates.append(body)
    elif isinstance(body, dict):
        if body.get("success") is False:
            raise ReconcileError("open-orders reply reported success=false; refusing to guess")
        if body.get("success") is True and "orders" not in body and "data" not in body:
            return []  # docs: empty orders are omitted from the portfolio envelope
        candidates.append(body.get("orders"))
        data = body.get("data")
        if isinstance(data, list):
            candidates.append(data)
        elif isinstance(data, dict):
            candidates.append(data.get("orders"))
    for cand in candidates:
        if isinstance(cand, list) and all(isinstance(o, dict) for o in cand):
            return cand
    raise ReconcileError("open-orders reply has an unrecognised shape; refusing to guess")
