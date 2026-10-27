"""telegram package — Telegram bot for proposals and controls.

Grounded in kit/06_TELEGRAM_BOT.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class TelegramMessage:
    chat_id: int
    text: str
    reply_markup: dict | None = None


class TelegramGate:
    """Telegram bot interface. Only TELEGRAM_ALLOWED_USER_ID may use the bot."""

    def __init__(self, *, allowed_user_id: int | None = None) -> None:
        self.allowed_user_id = allowed_user_id

    def is_allowed(self, user_id: int) -> bool:
        return self.allowed_user_id is not None and user_id == self.allowed_user_id

    def proposal_card(self, *, prop, ttl_seconds: int) -> str:
        """Build a proposal card message from structured fields."""
        mode_tag = "PAPER" if prop.mode == "PAPER" else "LIVE"
        adv_tag = " [ADVISORY]" if prop.advisory else ""
        mins = ttl_seconds // 60
        secs = ttl_seconds % 60
        lines = [
            f"[{mode_tag}{adv_tag}] {prop.fixture_id} (kickoff TBD)",
            f"Side: {prop.side.upper()} @ {prop.price_cents}c   qty {prop.quantity}   amount ${prop.amount_usd}",
            f"est fee ${prop.est_fee_usd}",
            f"p_fair {prop.p_fair:.0%}   edge {prop.edge_pp:+.2f}pp",
            f"Max suggested: ${prop.max_suggested_usd} (cap: {prop.binding_cap or 'none'})",
        ]
        if prop.reasons:
            lines.append("Why: " + "; ".join(prop.reasons[:3]))
        if prop.risks:
            lines.append("Risks: " + "; ".join(prop.risks[:2]))
        lines.append(f"Expires in {mins:02d}:{secs:02d}")
        lines.append("[Approve] [Change amount] [Reject]")
        return "\n".join(lines)

    def status_text(self, *, mode: str, paused: bool, balance_usd: Decimal,
                    daily_pnl_usd: Decimal, open_exposure_usd: Decimal) -> str:
        return (
            f"Mode: {mode}  Paused: {paused}\n"
            f"Balance: ${balance_usd}\n"
            f"Daily PnL: ${daily_pnl_usd}\n"
            f"Open exposure: ${open_exposure_usd}"
        )
