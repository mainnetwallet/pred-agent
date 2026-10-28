"""Telegram control bot (long polling, runs inside the web service process).

Env: ``TELEGRAM_BOT_TOKEN`` (from @BotFather) and ``TELEGRAM_ALLOWED_USER_ID``
(your numeric Telegram id). Only that user can talk to the bot; everyone else is
ignored. If either is missing the bot does not start (fail closed).
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from pred_agent.config import Settings
from pred_agent.control_api.controller import AgentController
from pred_agent.telegram import TelegramGate

BotApp = Application[Any, Any, Any, Any, Any, Any]

log = logging.getLogger(__name__)

HELP = (
    "PRED Agent control\\n"
    "/status - mode, pause state, PnL\\n"
    "/pause - stop new approvals\\n"
    "/resume - allow approvals again\\n"
    "/panic - pause + reject proposals + cancel open orders\\n"
    "/proposals - pending proposals (with buttons)\\n"
    "/approve <id> [amount_usd]\\n"
    "/reject <id>\\n"
    "/limits - current limits"
).replace("\\n", "\n")


def _find(controller: AgentController, prefix: str) -> str | None:
    matches = [p.id for p in controller.proposals.list_pending() if p.id.startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def _status_text(c: AgentController) -> str:
    s = c.status()
    return (
        f"Mode: {s['mode']}   Paused: {s['paused']}\n"
        f"Exposure: ${s['exposure']}   Daily PnL: ${s['daily_pnl']}\n"
        f"Pending proposals: {s['pending_proposals']}"
    )


def build_application(settings: Settings, controller: AgentController) -> BotApp | None:
    token = settings.telegram_bot_token
    allowed = settings.telegram_allowed_user_id
    if not token or not allowed:
        log.warning("telegram bot disabled: TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USER_ID not set")
        return None
    try:
        allowed_id = int(allowed)
    except ValueError:
        log.error("telegram bot disabled: TELEGRAM_ALLOWED_USER_ID must be a number")
        return None

    gate = TelegramGate(allowed_user_id=allowed_id)
    only_me = filters.User(user_id=allowed_id)
    app = Application.builder().token(token).build()

    async def reply(update: Update, text: str, markup: Any = None) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=markup)

    async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, HELP)

    async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, _status_text(controller))

    async def cmd_pause(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.pause()
        await reply(update, "Paused. No new approvals until /resume.")

    async def cmd_resume(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.resume()
        await reply(update, "Resumed.")

    async def cmd_panic(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("CONFIRM PANIC", callback_data="panic:yes"),
            InlineKeyboardButton("Cancel", callback_data="panic:no"),
        ]])
        await reply(update, "Panic pauses the agent, rejects all pending proposals and cancels open orders. Confirm?", kb)

    async def cmd_limits(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        lines = [f"{x['name']}: {x['value']} (max {x['hard_max']})" for x in controller.limits_list()]
        await reply(update, "\n".join(lines))

    async def cmd_proposals(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        pending = controller.proposals.list_pending()
        if not pending:
            await reply(update, "No pending proposals.")
            return
        for prop in pending:
            ttl = 0
            if prop.expires_at is not None:
                from datetime import datetime, timezone

                ttl = max(0, int((prop.expires_at - datetime.now(timezone.utc)).total_seconds()))
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Approve", callback_data=f"ok:{prop.id}"),
                InlineKeyboardButton("Reject", callback_data=f"no:{prop.id}"),
            ]])
            await reply(update, f"id {prop.id[:8]}\n" + gate.proposal_card(prop=prop, ttl_seconds=ttl), kb)

    async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args:
            await reply(update, "Usage: /approve <id> [amount_usd]")
            return
        pid = _find(controller, args[0])
        if pid is None:
            await reply(update, "No single pending proposal matches that id.")
            return
        amount = None
        if len(args) > 1:
            try:
                amount = Decimal(args[1])
            except InvalidOperation:
                await reply(update, "Bad amount.")
                return
        prop = await asyncio.to_thread(controller.approve, pid, "telegram", amount)
        await reply(update, _approve_text(controller, prop))

    async def cmd_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        pid = _find(controller, args[0]) if args else None
        if pid is None:
            await reply(update, "Usage: /reject <id> (id must match one pending proposal)")
            return
        controller.reject(pid, via="telegram")
        await reply(update, "Rejected.")

    async def on_button(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if q is None or q.from_user is None or not gate.is_allowed(q.from_user.id):
            return
        action, _sep, arg = (q.data or "").partition(":")
        if action == "panic":
            if arg == "yes":
                r = controller.panic()
                text = f"PANIC done. Rejected {r['proposals_rejected']}, cancelled {r['orders_cancelled']}. Paused."
            else:
                text = "Panic cancelled."
        elif action == "ok":
            text = _approve_text(controller, await asyncio.to_thread(controller.approve, arg, "telegram", None))
        elif action == "no":
            text = "Rejected." if controller.reject(arg, via="telegram") else "Already decided."
        else:
            return
        await q.answer()
        await q.edit_message_text(text)

    for name, fn in [
        ("start", cmd_help), ("help", cmd_help), ("status", cmd_status),
        ("pause", cmd_pause), ("resume", cmd_resume), ("panic", cmd_panic),
        ("limits", cmd_limits), ("proposals", cmd_proposals),
        ("approve", cmd_approve), ("reject", cmd_reject),
    ]:
        app.add_handler(CommandHandler(name, fn, filters=only_me))
    app.add_handler(CallbackQueryHandler(on_button))
    return app


async def start_bot(settings: Settings, controller: AgentController) -> BotApp | None:
    """Start polling. Never raises: a bad token must not take the web service down."""
    try:
        app = build_application(settings, controller)
        if app is None:
            return None
        await app.initialize()
        await app.start()
        if app.updater is not None:
            await app.updater.start_polling(drop_pending_updates=True)
        await app.bot.send_message(
            chat_id=int(settings.telegram_allowed_user_id or 0),
            text=f"PRED Agent online ({controller.mode}). Send /help.",
        )
        log.info("telegram bot started")
        return app
    except Exception:
        log.exception("telegram bot failed to start; continuing without it")
        return None


async def stop_bot(app: BotApp | None) -> None:
    if app is None:
        return
    try:
        if app.updater is not None and app.updater.running:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception:
        log.exception("telegram bot shutdown error")


async def send_alert(app: BotApp | None, settings: Settings, text: str) -> None:
    """Send an operator alert via Telegram. Never raises."""
    if app is None or app.bot is None:
        return
    try:
        chat_id = int(settings.telegram_allowed_user_id or 0)
        if not chat_id:
            return
        await app.bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        log.exception("telegram alert failed")


def _approve_text(controller: Any, prop: Any) -> str:
    """Operator-facing result of an approval. LIVE refusals/warnings come from last_error."""
    err = getattr(controller, "last_error", None)
    if prop is None:
        return f"NOT placed: {err}" if err else "Could not approve (paused, expired, or amount outside limits)."
    return f"Approved. WARNING: {err}" if err else "Approved."
