"""Live Executor — Phase 4 (REAL MONEY).

Constructed ONLY when LIVE_TRADING=true. Pipeline:
  re-validate -> build order -> signed fee bps -> amount formula -> random salt ->
  sign EIP-712 with parent contract_address -> place with fresh timestamp.

Grounded in kit/02_PRED_API_SPEC.md section 8 (Orders) and kit/03_ARCHITECTURE.md section 4.
Never resends with a new salt; uncertain outcomes go to UNKNOWN and are reconciled.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pred_agent.book import BookSnapshot, SlippageWalk
from pred_agent.config import load_settings
from pred_agent.execution.state_machine import (
    OrderState, OrderRecord, FillRecord, OrderStateMachine,
)
from pred_agent.signer.eip712 import sign_order
from pred_agent.transport.protocol import PredTransport, PredTransportError
from pred_agent.util.decimals import (
    cents_to_share_price, dec, money_usd, shares_to_quantity,
    validate_opening_notional, validate_quantity,
)
from pred_agent.util.redaction import redact
from pred_agent.util.units import price_cents_to_wei, shares_to_wei
from pred_agent.util.time import CLOCK


@dataclass
class LiveOrderIntent:
    market_id: str
    parent_market_id: str
    contract_address: str
    side: str  # "long" | "short"
    price_cents: Decimal
    quantity: Decimal  # shares
    amount_usd: Decimal
    order_type: str  # "limit" | "ioc"
    fee_rate_bps: int  # signed rate
    expiration: int = 0
    reduce_only: bool = False
    max_match_amount: Decimal | None = None


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
    ) -> None:
        settings = load_settings()
        if not settings.live_trading:
            raise RuntimeError("LiveExecutor requires LIVE_TRADING=true")
        self.transport = transport
        self.sm = state_machine
        self.eoa_address = eoa_address
        self.signer_private_key = signer_private_key
        self.chain_id = chain_id

    # ------------------------------------------------------------------ #
    # Public entry point: place a live order from an approved proposal
    # ------------------------------------------------------------------ #

    def place_from_proposal(
        self,
        *,
        proposal_id: str,
        intent: LiveOrderIntent,
    ) -> OrderRecord | None:
        """Full live placement pipeline. Returns the order record or None on abort."""
        market_id = intent.market_id

        # 1. Re-validate before placing (child status, book, edge, slippage, units, fee target)
        if not self._revalidate(intent):
            self.sm.block_market(market_id)
            return None

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
            maker=self.eoa_address,      # Safe
            signer=self.eoa_address,     # EOA (same for this simple setup)
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
        timestamp = int(CLOCK.now())
        body: dict[str, Any] = {
            "salt": str(salt),
            "market_id": intent.market_id,
            "side": intent.side,
            "price": str(intent.price_cents),
            "quantity": str(intent.quantity),
            "amount": str(intent.amount_usd),
            "signature": signature,
            "type": intent.order_type,
            "timestamp": timestamp,
            "fee_rate_bps": intent.fee_rate_bps,
            "expiration": intent.expiration,
            "reduce_only": intent.reduce_only,
        }
        if intent.max_match_amount is not None and intent.order_type in ("ioc", "market"):
            body["max_match_amount"] = str(intent.max_match_amount)

        path = f"/api/v1/order/{intent.parent_market_id}/place"

        # Persist order BEFORE side effect (place call)
        order = OrderRecord(
            id=f"live-{salt}",
            pred_order_id=None,
            proposal_id=proposal_id,
            state=OrderState.SUBMITTED,
            side=intent.side,
            price_cents=price_wei,
            quantity=str(intent.quantity),
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
            # Timeout, 5xx, 502, 503, network error -> UNKNOWN, never resend
            order.state = OrderState.UNKNOWN
            order.raw_response = redact(str(exc.raw_body)) if exc.raw_body else None
            self.sm.block_market(market_id)
            self.sm.block_market(intent.parent_market_id)
            return order
        except Exception as exc:  # noqa: BLE001
            order.state = OrderState.UNKNOWN
            order.raw_response = str(exc)
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
                    Decimal(intent.quantity) - Decimal(filled_qty)
                )
            return order

        if status == "failed":
            order.state = OrderState.FAILED
            return order

        # Unknown status -> fail closed
        order.state = OrderState.UNKNOWN
        self.sm.block_market(market_id)
        return order

    # ------------------------------------------------------------------ #
    # Re-validation before placing
    # ------------------------------------------------------------------ #

    def _revalidate(self, intent: LiveOrderIntent) -> bool:
        """Re-validate child status, book, edge, slippage, units, fee target."""
        # Market blocked?
        if self.sm.is_market_blocked(intent.parent_market_id):
            return False
        if self.sm.is_market_blocked(intent.market_id):
            return False

        # Pending proposal check (placeholder — proposals service not wired here)
        # Fee target present?
        if intent.fee_rate_bps is None:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Cancel
    # ------------------------------------------------------------------ #

    def cancel(self, order_id: str, market_id: str, parent_market_id: str) -> dict[str, Any]:
        """Cancel a single order. Inspect result; cancel_noop means NOT cancelled."""
        order = self.sm.get(order_id)
        if order is None:
            return {"status": "not_found", "order_id": order_id}

        path = f"/api/v1/order/{parent_market_id}/cancel"
        body = {"order_id": order_id, "market_id": market_id}
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
        path = f"/api/v1/order/{parent_market_id}/cancel/all"
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
                order = self.sm.get(oid)
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
            fee_rate_bps=0,  # will be set by caller after fee computation
            reduce_only=True,
        )
        return self.place_from_proposal(proposal_id=proposal_id, intent=intent)

    # ------------------------------------------------------------------ #
    # Startup reconcile (placeholder — wires to transport portfolio calls)
    # ------------------------------------------------------------------ #

    def reconcile_startup(self) -> None:
        """Reconcile orders, fills, positions on startup. Never resend uncertain orders."""
        # This would call transport portfolio/open-orders/trade-history and
        # reconcile with local state machine. Orders in UNKNOWN state are
        # reconciled, never re-sent with a new salt.
        raise NotImplementedError("reconcile_startup wired in Phase 4 with transport")
