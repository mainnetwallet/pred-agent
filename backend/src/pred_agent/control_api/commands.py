"""Chat slash-commands — deterministic control of the agent by typing.

Everything the website/app can do by clicking can also be typed in the chat
box as a ``/command``. These commands are parsed and executed *here*, on the
server, by plain code. The LLM is never involved: it cannot trigger them and
its safety rules (ARCHITECTURE.md) are untouched.

Safety model
------------
* The caller is already authenticated (Bearer token) — a typed command is a
  human act, like a button press.
* Creating things (``/buy``, ``/sell``, ``/close``, ``/tp``, ``/sl``) only
  creates PENDING proposals/rules, exactly like the buttons do.
* Money-moving or destructive actions (``/approve``, ``/confirm``,
  ``/cancel <order>``) execute immediately in PAPER mode. In LIVE mode they
  need the same ``X-Confirm`` proof as the REST endpoints; without it the
  command returns the matching confirmation *card* instead, and the human
  taps Confirm (which runs the client's own confirm gate).
* ``/panic`` always returns a confirmation card — never fires from one line.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from pred_agent.control_api.controller import AgentController, to_jsonable
from pred_agent.events import Event

HELP_TEXT = """\
**Commands** — type any of these (or just chat normally):

**See**
`/status` · `/live` · `/upcoming` · `/match <fixture>`
`/positions` · `/orders` · `/rules` · `/proposals` · `/limits`

**Trade** (creates a PENDING proposal → you approve)
`/buy <market> <usd>` — long (YES)
`/sell <market> <usd>` — short (NO)
`/close <market>` — exit a position
`/approve <id> [usd]` · `/reject <id>`

**Exit rules**
`/tp <market> <5 | 20% | 70c>` — take-profit ($ / % / price in cents)
`/sl <market> <3 | 30c>` — stop-loss ($ / price in cents)
`/confirm <rule>` · `/cancel <order or rule>`

**Control**
`/pause` · `/resume` · `/panic`
`/limit <name> <value>` — change a limit

Ids can be shortened to any unique prefix. Money-moving commands need a
confirm tap in LIVE mode."""


@dataclass
class CmdResult:
    reply: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    error: bool = False


@dataclass
class CommandContext:
    controller: AgentController
    fixtures: Callable[[str | None], list[dict[str, Any]]]
    tool_fns: dict[str, Callable[..., Awaitable[Any]]]
    publish: Callable[[Event], Awaitable[None]]
    xconfirmed: bool = False  # request carried the X-Confirm header


def is_command(message: str) -> bool:
    return message.startswith("/") and len(message) > 1 and not message[1].isspace()


# ── helpers ──────────────────────────────────────────────────


def _err(msg: str) -> CmdResult:
    return CmdResult(f"❌ {msg}", error=True)


def _short(i: str | None, n: int = 8) -> str:
    return (i or "?")[:n]


def _dec(raw: str) -> Decimal | None:
    try:
        v = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return v if v.is_finite() else None


def _resolve(prefix: str, ids: list[str], what: str) -> tuple[str | None, str | None]:
    """Resolve an id or unique prefix. Returns (id, error)."""
    p = prefix.strip().lower()
    if not p:
        return None, f"{what} id is required"
    exact = [i for i in ids if i.lower() == p]
    if exact:
        return exact[0], None
    hits = [i for i in ids if i.lower().startswith(p)]
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, f"no {what} matching “{prefix}”"
    return None, f"“{prefix}” matches {len(hits)} {what}s — type more of the id"


def _market_ids(ctx: CommandContext) -> list[str]:
    seen: dict[str, None] = {}
    for fx in ctx.fixtures(None):
        for m in fx.get("markets", []):
            if m.get("market_id"):
                seen[m["market_id"]] = None
    for p in ctx.controller.positions.list_positions():
        seen[p.market_id] = None
    return list(seen)


def _find_fixture(ctx: CommandContext, ref: str) -> dict[str, Any] | None:
    items = ctx.fixtures(None)
    r = ref.lower()
    for f in items:
        if str(f.get("id", "")).lower() == r or str(f.get("parent_market_id", "")).lower() == r:
            return f
    hits = [f for f in items if str(f.get("id", "")).lower().startswith(r)
            or str(f.get("parent_market_id", "")).lower().startswith(r)]
    return hits[0] if len(hits) == 1 else None


def _fixture_line(f: dict[str, Any]) -> str:
    state = str(f.get("state", "?")).upper()
    score = ""
    if f.get("home_score") is not None:
        score = f" {f.get('home_score')}–{f.get('away_score') or 0}"
    minute = f" {f['minute']}′" if f.get("minute") is not None else ""
    return f"**{f.get('home_team') or '?'}** vs **{f.get('away_team') or '?'}** · {state}{minute}{score}"


async def _call_write(ctx: CommandContext, name: str, **kwargs: Any) -> tuple[dict[str, Any], list[Any]]:
    fn = ctx.tool_fns.get(name)
    if fn is None:
        return {"error": f"{name} is not available"}, []
    out = await fn(**kwargs)
    if isinstance(out, tuple):
        return out[0], list(out[1])
    return out, []


def _cards(cards: list[Any]) -> list[dict[str, Any]]:
    return [{"type": c.type, "data": c.data} for c in cards]


def _live_gated(ctx: CommandContext) -> bool:
    """True when this action must wait for a confirm tap (LIVE, no X-Confirm)."""
    return ctx.controller.mode == "LIVE" and not ctx.xconfirmed


# ── read commands ────────────────────────────────────────────


async def _cmd_help(ctx: CommandContext, args: list[str]) -> CmdResult:
    return CmdResult(HELP_TEXT)


async def _cmd_status(ctx: CommandContext, args: list[str]) -> CmdResult:
    s = ctx.controller.status()
    bal = s.get("balance")
    lines = [
        f"**Mode:** {s['mode']} · **Agent:** {'⏸ paused' if s['paused'] else '▶ running'}",
        f"**Feeds:** {'connected' if s['feeds_ok'] else '⚠️ offline'}",
        f"**Exposure:** ${s['exposure']} · **Daily PnL:** ${s['daily_pnl']}"
        + (f" · **Balance:** ${bal}" if bal is not None else ""),
        f"**Pending proposals:** {s['pending_proposals']} · **Active rules:** {s['active_rules']}",
    ]
    if s.get("live_note"):
        lines.append(f"ℹ️ {s['live_note']}")
    return CmdResult("\n".join(lines))


async def _fixtures_cmd(ctx: CommandContext, state: str | None) -> CmdResult:
    items = ctx.fixtures(state)
    if not items:
        return CmdResult("No matches right now." if state else "No fixtures loaded.")
    lines = [f"{i}. {_fixture_line(f)} · `{_short(f.get('id'), 10)}`" for i, f in enumerate(items[:12], 1)]
    more = f"\n…and {len(items) - 12} more." if len(items) > 12 else ""
    return CmdResult(
        "\n".join(lines) + more + "\n\nUse `/match <id>` to see markets and prices.",
        [{"type": "fixture_list", "data": {"fixtures": items[:8]}}],
    )


async def _cmd_live(ctx: CommandContext, args: list[str]) -> CmdResult:
    return await _fixtures_cmd(ctx, "live")


async def _cmd_upcoming(ctx: CommandContext, args: list[str]) -> CmdResult:
    return await _fixtures_cmd(ctx, "upcoming")


async def _cmd_fixtures(ctx: CommandContext, args: list[str]) -> CmdResult:
    state = args[0].lower() if args else None
    if state not in (None, "live", "upcoming", "all"):
        return _err("usage: `/fixtures [live|upcoming|all]`")
    return await _fixtures_cmd(ctx, None if state in (None, "all") else state)


async def _cmd_match(ctx: CommandContext, args: list[str]) -> CmdResult:
    if not args:
        return _err("usage: `/match <fixture id>`")
    f = _find_fixture(ctx, args[0])
    if f is None:
        return _err(f"no unique match for “{args[0]}” — see `/live` or `/upcoming`")
    lines = [_fixture_line(f), ""]
    get_book = ctx.tool_fns.get("get_book")
    for m in f.get("markets", []):
        mid = m.get("market_id")
        quote = "no price"
        if get_book is not None and mid:
            book = await get_book(market_id=mid)
            if "error" not in book:
                quote = f"bid {book.get('best_bid_cents') or '—'} / ask {book.get('best_ask_cents') or '—'}¢"
        lines.append(f"• `{mid}` — {quote}")
    lines.append("\nTrade: `/buy <market> <usd>` or `/sell <market> <usd>`")
    return CmdResult("\n".join(lines))


async def _cmd_positions(ctx: CommandContext, args: list[str]) -> CmdResult:
    pos = ctx.controller.positions_list()
    if not pos:
        return CmdResult("No open positions.")
    lines = []
    for p in pos:
        lines.append(f"• `{p.get('market_id')}` — {p.get('side')} · qty {p.get('quantity')} "
                     f"· cost ${p.get('cost_usd')} · avg {p.get('avg_price_cents')}¢")
    lines.append("\nExit: `/close <market>` · Protect: `/tp <market> 5` / `/sl <market> 3`")
    return CmdResult("\n".join(lines))


async def _cmd_orders(ctx: CommandContext, args: list[str]) -> CmdResult:
    orders = ctx.controller.orders()
    if not orders:
        return CmdResult("No orders.")
    lines = [f"• `{_short(o.get('id'))}` — {o.get('side')} @ {o.get('price_cents')}¢ · "
             f"${o.get('amount_usd', '?')} · {o.get('state') or o.get('status')}" for o in orders[-15:]]
    lines.append("\nCancel: `/cancel <id>`")
    return CmdResult("\n".join(lines))


async def _cmd_rules(ctx: CommandContext, args: list[str]) -> CmdResult:
    rules = [r.to_dict() for r in ctx.controller.list_rules()]
    if not rules:
        return CmdResult("No exit rules.")
    lines = [f"• `{_short(r['id'])}` — {r['kind']} {r['trigger_type']} @ {r['trigger_value']} "
             f"on `{_short(r['market_id'], 10)}` · **{r['status']}**" for r in rules]
    return CmdResult("\n".join(lines) + "\n\nConfirm: `/confirm <id>` · Cancel: `/cancel <id>`")


async def _cmd_proposals(ctx: CommandContext, args: list[str]) -> CmdResult:
    props = ctx.controller.list_proposals("PROPOSED")
    if not props:
        return CmdResult("No pending proposals.")
    lines = [f"• `{_short(p.id)}` — {p.side} {p.amount_usd} USD @ {p.price_cents}¢ on "
             f"`{_short(p.market_id, 10)}`" for p in props]
    cards = [{"type": "proposal", "data": to_jsonable(p)} for p in props[:5]]
    return CmdResult("\n".join(lines) + "\n\nApprove: `/approve <id>` · Reject: `/reject <id>`", cards)


async def _cmd_limits(ctx: CommandContext, args: list[str]) -> CmdResult:
    lines = [f"• **{lim['name']}** = {lim['value']} (max {lim['hard_max']})" for lim in ctx.controller.limits_list()]
    return CmdResult("\n".join(lines) + "\n\nChange: `/limit <name> <value>`")


# ── write commands ───────────────────────────────────────────


async def _trade(ctx: CommandContext, side: str, market: str, usd: str) -> CmdResult:
    amount = _dec(usd)
    if amount is None or amount <= 0:
        return _err("amount must be a positive number, e.g. `/buy <market> 5`")
    mid, err = _resolve(market, _market_ids(ctx), "market")
    if err:
        return _err(err)
    out, cards = await _call_write(ctx, "propose_trade", market_id=mid, side=side, amount_usd=str(amount))
    if "error" in out:
        return _err(out["error"])
    pid = out.get("proposal_id", "")
    return CmdResult(
        f"📝 Proposal `{_short(pid)}` created — **PENDING**. {side.upper()} ${amount} on `{mid}`.\n"
        f"Tap **Approve** below, or type `/approve {_short(pid)}`.",
        _cards(cards),
    )


async def _cmd_buy(ctx: CommandContext, args: list[str]) -> CmdResult:
    if len(args) != 2:
        return _err("usage: `/buy <market> <usd>`")
    return await _trade(ctx, "long", args[0], args[1])


async def _cmd_sell(ctx: CommandContext, args: list[str]) -> CmdResult:
    if len(args) != 2:
        return _err("usage: `/sell <market> <usd>`")
    return await _trade(ctx, "short", args[0], args[1])


async def _cmd_trade(ctx: CommandContext, args: list[str]) -> CmdResult:
    if len(args) != 3 or args[1].lower() not in ("long", "short", "buy", "sell", "yes", "no"):
        return _err("usage: `/trade <market> <long|short> <usd>`")
    side = "long" if args[1].lower() in ("long", "buy", "yes") else "short"
    return await _trade(ctx, side, args[0], args[2])


async def _cmd_close(ctx: CommandContext, args: list[str]) -> CmdResult:
    if len(args) != 1:
        return _err("usage: `/close <market>`")
    ids = [p.market_id for p in ctx.controller.positions.list_positions()]
    mid, err = _resolve(args[0], ids, "open position")
    if err:
        return _err(err)
    out, cards = await _call_write(ctx, "close_position", market_id=mid)
    if "error" in out:
        return _err(out["error"])
    return CmdResult(f"📝 Close proposal for `{mid}` created — **PENDING**. Tap **Approve** to exit.", _cards(cards))


_TRIGGER = re.compile(r"^(\d+(?:\.\d+)?)(%|c|¢|\$)?$", re.IGNORECASE)


async def _rule(ctx: CommandContext, kind: str, args: list[str]) -> CmdResult:
    cmd = "tp" if kind == "take_profit" else "sl"
    if len(args) != 2:
        hint = "5 | 20% | 70c" if kind == "take_profit" else "3 | 30c"
        return _err(f"usage: `/{cmd} <market> <{hint}>`")
    m = _TRIGGER.match(args[1])
    if not m:
        return _err("trigger must look like `5` ($), `20%` or `70c` (price in cents)")
    num, unit = m.group(1), (m.group(2) or "$").lower()
    if unit in ("$",):
        ttype = "profit_usd" if kind == "take_profit" else "loss_usd"
    elif unit == "%":
        if kind != "take_profit":
            return _err("stop-loss supports $ amounts or a price in cents (e.g. `30c`)")
        ttype = "profit_pct"
    else:
        ttype = "price_cents"
    ids = [p.market_id for p in ctx.controller.positions.list_positions()]
    mid, err = _resolve(args[0], ids, "open position")
    if err:
        return _err(err)
    out, cards = await _call_write(ctx, "create_rule", market_id=mid, kind=kind, trigger_type=ttype, trigger_value=num)
    if "error" in out:
        return _err(out["error"])
    rid = out.get("rule_id", "")
    return CmdResult(
        f"📝 Rule `{_short(rid)}` created — **PENDING**. {kind.replace('_', '-')} {ttype} @ {num} on `{mid}`.\n"
        f"Tap **Confirm rule**, or type `/confirm {_short(rid)}`.",
        _cards(cards),
    )


async def _cmd_tp(ctx: CommandContext, args: list[str]) -> CmdResult:
    return await _rule(ctx, "take_profit", args)


async def _cmd_sl(ctx: CommandContext, args: list[str]) -> CmdResult:
    return await _rule(ctx, "stop_loss", args)


async def _cmd_approve(ctx: CommandContext, args: list[str]) -> CmdResult:
    if not args or len(args) > 2:
        return _err("usage: `/approve <proposal id> [usd]`")
    pending = ctx.controller.list_proposals("PROPOSED")
    pid, err = _resolve(args[0], [p.id for p in pending], "pending proposal")
    if err:
        return _err(err)
    amount: Decimal | None = None
    if len(args) == 2:
        amount = _dec(args[1])
        if amount is None or amount <= 0:
            return _err("amount must be a positive number")
    prop = next(p for p in pending if p.id == pid)
    if _live_gated(ctx):
        return CmdResult(
            "🔒 **LIVE mode** — this spends real money. Tap **Approve** on the card to confirm.",
            [{"type": "proposal", "data": to_jsonable(prop)}],
        )
    done = await asyncio.to_thread(ctx.controller.approve, pid, "chat", amount)
    if done is None:
        return _err(ctx.controller.last_error or "could not approve — expired, paused, or amount outside limits")
    await ctx.publish(Event("proposal_updated", to_jsonable(done)))
    return CmdResult(f"✅ Proposal `{_short(pid)}` approved ({ctx.controller.mode}).",
                     [{"type": "proposal", "data": to_jsonable(done)}])


async def _cmd_reject(ctx: CommandContext, args: list[str]) -> CmdResult:
    if len(args) != 1:
        return _err("usage: `/reject <proposal id>`")
    pending = ctx.controller.list_proposals("PROPOSED")
    pid, err = _resolve(args[0], [p.id for p in pending], "pending proposal")
    if err:
        return _err(err)
    done = ctx.controller.reject(pid, via="chat")
    if done is None:
        return _err("proposal missing or already decided")
    await ctx.publish(Event("proposal_updated", to_jsonable(done)))
    return CmdResult(f"🚫 Proposal `{_short(pid)}` rejected.")


async def _cmd_confirm(ctx: CommandContext, args: list[str]) -> CmdResult:
    if len(args) != 1:
        return _err("usage: `/confirm <rule id>`")
    pending = ctx.controller.rules.list_pending()
    rid, err = _resolve(args[0], [r.id for r in pending], "pending rule")
    if err:
        return _err(err)
    rule = next(r for r in pending if r.id == rid)
    if _live_gated(ctx):
        return CmdResult("🔒 **LIVE mode** — tap **Confirm rule** on the card.", [{"type": "rule", "data": rule.to_dict()}])
    done = ctx.controller.confirm_rule(rid)
    if done is None:
        return _err("rule not found, not PENDING, or max active rules reached")
    await ctx.publish(Event("rule_updated", done.to_dict()))
    return CmdResult(f"✅ Rule `{_short(rid)}` is now **ACTIVE**.", [{"type": "rule", "data": done.to_dict()}])


async def _cmd_cancel(ctx: CommandContext, args: list[str]) -> CmdResult:
    if len(args) != 1:
        return _err("usage: `/cancel <order or rule id>`")
    c = ctx.controller
    order_ids = [o.id for o in c.executor.list_orders()]
    rule_ids = [r.id for r in c.rules.list_all() if r.status in ("PENDING", "ACTIVE")]
    ref = args[0]
    oid, _ = _resolve(ref, order_ids, "order")
    rid, _ = _resolve(ref, rule_ids, "rule")
    if oid and rid:
        return _err(f"“{ref}” matches both an order and a rule — type more of the id")
    if oid:
        if _live_gated(ctx):
            return CmdResult("🔒 **LIVE mode** — tap Confirm to cancel.",
                             [{"type": "action", "data": {"action": "cancel_order", "order_id": oid,
                                                          "label": f"Cancel order {_short(oid)}?"}}])
        if not c.executor.cancel(oid):
            return _err("order not found or not cancellable")
        return CmdResult(f"🚫 Order `{_short(oid)}` cancelled.")
    if rid:
        done = c.cancel_rule(rid)
        if done is None:
            return _err("rule not found or already terminal")
        await ctx.publish(Event("rule_updated", done.to_dict()))
        return CmdResult(f"🚫 Rule `{_short(rid)}` cancelled.")
    return _err(f"no order or rule matching “{ref}”")


async def _cmd_pause(ctx: CommandContext, args: list[str]) -> CmdResult:
    ctx.controller.pause()
    return CmdResult("⏸ Agent paused — no new proposals. Exit rules keep running. `/resume` to continue.")


async def _cmd_resume(ctx: CommandContext, args: list[str]) -> CmdResult:
    ctx.controller.resume()
    return CmdResult("▶ Agent resumed.")


async def _cmd_panic(ctx: CommandContext, args: list[str]) -> CmdResult:
    return CmdResult(
        "🚨 **PANIC** pauses the agent, rejects pending proposals and cancels open orders and ALL rules. "
        "Tap Confirm to proceed.",
        [{"type": "action", "data": {"action": "panic",
                                     "label": "PANIC: pause + reject proposals + cancel orders and ALL rules?"}}],
    )


async def _cmd_limit(ctx: CommandContext, args: list[str]) -> CmdResult:
    if len(args) != 2:
        return _err("usage: `/limit <name> <value>` — see `/limits`")
    names = [lim["name"] for lim in ctx.controller.limits_list()]
    name, err = _resolve(args[0], names, "limit")
    if err:
        return _err(err)
    val = _dec(args[1])
    if val is None:
        return _err("value must be a number")
    msg = ctx.controller.set_limits({name: float(val)})
    if msg:
        return _err(msg)
    return CmdResult(f"⚙️ **{name}** set to {val}.")


_COMMANDS: dict[str, Callable[[CommandContext, list[str]], Awaitable[CmdResult]]] = {
    "help": _cmd_help, "start": _cmd_help, "commands": _cmd_help,
    "status": _cmd_status,
    "live": _cmd_live, "upcoming": _cmd_upcoming, "fixtures": _cmd_fixtures, "match": _cmd_match,
    "positions": _cmd_positions, "orders": _cmd_orders, "rules": _cmd_rules,
    "proposals": _cmd_proposals, "limits": _cmd_limits,
    "buy": _cmd_buy, "long": _cmd_buy, "sell": _cmd_sell, "short": _cmd_sell, "trade": _cmd_trade,
    "close": _cmd_close, "tp": _cmd_tp, "sl": _cmd_sl,
    "approve": _cmd_approve, "reject": _cmd_reject, "confirm": _cmd_confirm, "cancel": _cmd_cancel,
    "pause": _cmd_pause, "resume": _cmd_resume, "panic": _cmd_panic, "limit": _cmd_limit,
}


async def run_command(message: str, ctx: CommandContext) -> CmdResult:
    """Execute one slash command. ``message`` must satisfy :func:`is_command`."""
    parts = message.strip().split()
    name = parts[0][1:].split("@", 1)[0].lower()  # tolerate Telegram-style /cmd@bot
    handler = _COMMANDS.get(name)
    if handler is None:
        return _err(f"unknown command `/{name}` — type `/help`")
    try:
        return await handler(ctx, parts[1:])
    except Exception as exc:  # noqa: BLE001 — never leak a traceback into chat
        return _err(f"command failed: {type(exc).__name__}")


__all__ = ["CmdResult", "CommandContext", "HELP_TEXT", "is_command", "run_command"]
