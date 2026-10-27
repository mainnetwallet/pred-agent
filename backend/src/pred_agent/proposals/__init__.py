"""proposals module — create, TTL expiry, approve, reject, change amount.

Grounded in kit/06_TELEGRAM_BOT.md (Proposal card) and kit/03_ARCHITECTURE.md section 4
(Order state machine).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pred_agent.store.repository import ProposalRepo


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    ABORTED = "ABORTED"


@dataclass
class Proposal:
    id: str
    mode: str  # "PAPER" or "LIVE"
    advisory: bool
    fixture_id: str
    market_id: str
    side: str  # "long" or "short"
    order_type: str  # "limit" or "ioc"
    price_cents: Decimal
    quantity: Decimal
    amount_usd: Decimal
    est_fee_usd: Decimal
    edge_pp: Decimal
    p_fair: Decimal
    max_suggested_usd: Decimal
    binding_cap: str | None
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    status: ProposalStatus = ProposalStatus.PROPOSED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    decided_at: datetime | None = None
    decided_via: str | None = None  # "telegram" | "app" | "auto"

    def is_expired(self) -> bool:
        if self.status != ProposalStatus.PROPOSED:
            return False
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


class ProposalService:
    """Manages proposal lifecycle with TTL expiry sweeper.

    ``db`` attaches a repository: state is loaded on startup and every
    mutation is written through, so proposals survive restarts.
    """

    def __init__(self, *, ttl_seconds: int = 300,
                 db: "ProposalRepo | None" = None) -> None:
        self.ttl_seconds = ttl_seconds
        self._db = db
        self._proposals: dict[str, Proposal] = db.load_all() if db else {}
        self._locks: dict[str, object] = {}  # per-market locks placeholder

    def create(self, *, mode: str, advisory: bool, fixture_id: str, market_id: str,
               side: str, order_type: str, price_cents: Decimal, quantity: Decimal,
               amount_usd: Decimal, est_fee_usd: Decimal, edge_pp: Decimal, p_fair: Decimal,
               max_suggested_usd: Decimal, binding_cap: str | None,
               reasons: list[str] | None = None, risks: list[str] | None = None) -> Proposal:
        prop = Proposal(
            id=str(uuid.uuid4()),
            mode=mode,
            advisory=advisory,
            fixture_id=fixture_id,
            market_id=market_id,
            side=side,
            order_type=order_type,
            price_cents=price_cents,
            quantity=quantity,
            amount_usd=amount_usd,
            est_fee_usd=est_fee_usd,
            edge_pp=edge_pp,
            p_fair=p_fair,
            max_suggested_usd=max_suggested_usd,
            binding_cap=binding_cap,
            reasons=reasons or [],
            risks=risks or [],
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds),
        )
        self._proposals[prop.id] = prop
        self._persist(prop)
        return prop

    def _persist(self, prop: Proposal) -> None:
        if self._db is not None:
            self._db.save(prop)

    def get(self, proposal_id: str) -> Proposal | None:
        return self._proposals.get(proposal_id)

    def list_pending(self) -> list[Proposal]:
        return [p for p in self._proposals.values() if p.status == ProposalStatus.PROPOSED]

    def approve(self, proposal_id: str, via: str = "telegram",
                new_amount_usd: Decimal | None = None) -> Proposal | None:
        prop = self._proposals.get(proposal_id)
        if prop is None or prop.status != ProposalStatus.PROPOSED:
            return None
        if prop.is_expired():
            prop.status = ProposalStatus.EXPIRED
            self._persist(prop)
            return None
        if new_amount_usd is not None:
            prop.amount_usd = new_amount_usd
        prop.status = ProposalStatus.APPROVED
        prop.decided_at = datetime.now(timezone.utc)
        prop.decided_via = via
        self._persist(prop)
        return prop

    def reject(self, proposal_id: str, via: str = "telegram") -> Proposal | None:
        prop = self._proposals.get(proposal_id)
        if prop is None or prop.status != ProposalStatus.PROPOSED:
            return None
        prop.status = ProposalStatus.REJECTED
        prop.decided_at = datetime.now(timezone.utc)
        prop.decided_via = via
        self._persist(prop)
        return prop

    def expire_stale(self) -> list[str]:
        """Mark expired proposals. Returns list of expired proposal IDs."""
        expired: list[str] = []
        for prop in self._proposals.values():
            if prop.status == ProposalStatus.PROPOSED and prop.is_expired():
                prop.status = ProposalStatus.EXPIRED
                self._persist(prop)
                expired.append(prop.id)
        return expired
