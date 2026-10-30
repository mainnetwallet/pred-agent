"""Chat autopilot: direct execution + real outcome reports, automated exits, withdraw gate,
match research. No network: PAPER executor, stubs and httpx.MockTransport only."""
from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import httpx
import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from pred_agent.book import BookLevel, BookSnapshot
from pred_agent.config import load_settings
from pred_agent.control_api.app import create_app
from pred_agent.control_api.autopilot import (
    ExitPlan,
    deadline_from,
    market_order_price,
)
from pred_agent.control_api.controller import AgentController
from pred_agent.control_api.withdraw import WithdrawService, usdc_transfer_calldata
from pred_agent.positions import Position
from pred_agent.research.match import MatchResearcher, poisson_1x2
from pred_agent.rules import (
    EXEC_LIMIT,
    STATUS_ACTIVE,
    STATUS_DONE,
    STATUS_FAILED,
)
from pred_agent.scanner import DiscoveredParent
from pred_agent.signer.eip712 import private_to_address, safe_tx_digest

MID = "0x" + "ab" * 32
KEY = "0x" + "11" * 32
D = Decimal


def _book(bid: str = "60", ask: str = "62") -> BookSnapshot:
    b, a = D(bid), D(ask)
    return BookSnapshot(
        market_id=MID, bids=[BookLevel(price_cents=b, quantity=D(100))],
        asks=[BookLevel(price_cents=a, quantity=D(100))], ltp=None,
        spread_cents=a - b, mid_price_cents=(a + b) / 2, last_updated_at=None, sequence_number=None,
    )


@pytest.fixture
def ctl(monkeypatch: pytest.MonkeyPatch) -> AgentController:
    monkeypatch.setenv("LIVE_TRADING", "false")
    c = AgentController(load_settings())
    c.limits["max_trade_usd"] = 5.0
    c.executor._fill_probability = D(1)   # deterministic paper fills
    return c


def _buy(ctl: AgentController, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "market_id": MID, "parent_market_id": "pm1", "fixture_id": "fx1", "side": "long",
        "amount_usd": D(1), "order_type": "market", "limit_price_cents": None, "book": _book(), "plan": None,
    }
    args.update(kw)
    return ctl.autopilot.trade(**args)


# ── pricing helper ───────────────────────────────────────────

def test_market_order_price_caps_slippage_in_whole_cents() -> None:
    assert market_order_price("long", _book("60", "62"), D("1.5")) == D(64)
    assert market_order_price("short", _book("60", "62"), D("1.5")) == D(58)
    assert market_order_price("long", _book("60", "98.5"), D("1.5")) == D(99)   # never > 99


def test_market_order_price_none_without_liquidity() -> None:
    empty = BookSnapshot(MID, [], [], None, None, None, None, None)
    assert market_order_price("long", empty, D("1.5")) is None
    assert market_order_price("short", empty, D("1.5")) is None


# ── entry: market vs limit, honest reporting ─────────────────

def test_market_buy_reports_filled_and_opens_position(ctl: AgentController) -> None:
    r = _buy(ctl)
    assert r["status"] == "FILLED" and r["ok"] is True
    assert r["order_type"] == "market" and r["price_cents"] == "64"   # ask 62 + 1.5 cap, ceil
    pos = ctl.positions.get_position(MID)
    assert pos is not None and pos.status == "open"


def test_limit_buy_uses_the_users_price(ctl: AgentController) -> None:
    r = _buy(ctl, order_type="limit", limit_price_cents=D(55), book=None)
    assert r["order_type"] == "limit" and r["price_cents"] == "55"
    assert r["status"] == "FILLED"


def test_limit_buy_not_filled_reports_resting_not_filled(ctl: AgentController) -> None:
    ctl.executor._fill_probability = D(0)
    r = _buy(ctl, order_type="limit", limit_price_cents=D(40), book=None)
    assert r["status"] == "RESTING" and "not filled" in r["note"]


def test_limit_order_requires_a_price(ctl: AgentController) -> None:
    r = _buy(ctl, order_type="limit", limit_price_cents=None, book=None)
    assert r["status"] == "FAILED" and r["ok"] is False


def test_market_order_without_book_fails_closed(ctl: AgentController) -> None:
    r = _buy(ctl, book=None)
    assert r["status"] == "FAILED" and "book" in r["reason"]


def test_failure_reasons_are_reported_not_swallowed(ctl: AgentController) -> None:
    over = _buy(ctl, amount_usd=D(50))
    assert over["status"] == "FAILED" and "max_trade_usd" in over["reason"] or "<=" in over["reason"]
    ctl.pause()
    paused = _buy(ctl)
    assert paused["status"] == "FAILED" and "paused" in paused["reason"]


# ── exit plans ───────────────────────────────────────────────

def test_exit_plan_arms_three_rules_immediately(ctl: AgentController) -> None:
    plan = ExitPlan(market_id=MID, tp_pct=D(10), tp_price_cents=D(80),
                    deadline_ts=deadline_from(minutes=30))
    r = _buy(ctl, plan=plan)
    assert r["exit_plan"]["armed"] is True
    rules = {x.trigger_type: x for x in ctl.rules.list_all()}
    assert set(rules) == {"profit_pct", "price_cents", "deadline_ts"}
    assert all(x.status == STATUS_ACTIVE for x in rules.values())
    assert rules["profit_pct"].exec_mode == "market"        # 10% profit -> market sell
    assert rules["price_cents"].exec_mode == EXEC_LIMIT     # price 80 -> resting limit sell
    assert rules["deadline_ts"].kind == "time_exit"


def test_exit_plan_waits_for_a_resting_entry_then_arms(ctl: AgentController) -> None:
    ctl.executor._fill_probability = D(0)
    r = _buy(ctl, order_type="limit", limit_price_cents=D(40), book=None,
             plan=ExitPlan(market_id=MID, tp_pct=D(10)))
    assert r["exit_plan"]["armed"] is False
    assert ctl.autopilot.pending_plans
    armed = ctl.autopilot.apply_pending_plans()      # paper opens the position right away
    assert armed and not ctl.autopilot.pending_plans
    assert len(ctl.rules.list_active()) == 1


def test_limit_exit_must_be_above_entry_for_a_long(ctl: AgentController) -> None:
    _buy(ctl)
    rule, err = ctl.autopilot.add_rule(MID, "take_profit", "price_cents", D(10), EXEC_LIMIT)
    assert rule is None and "above entry" in (err or "")


def test_sell_now_closes_the_position_even_when_paused(ctl: AgentController) -> None:
    _buy(ctl)
    ctl.pause()
    r = ctl.autopilot.exit_now(market_id=MID, book=_book("60", "62"))
    assert r["status"] == "FILLED" and r["position_closed"] is True
    assert ctl.positions.get_position(MID).status == "closed"
    assert r["price_cents"] == "58"      # bid 60 - 1.5 cap, floor


def test_sell_without_a_position_fails(ctl: AgentController) -> None:
    r = ctl.autopilot.exit_now(market_id=MID, book=_book())
    assert r["status"] == "FAILED" and "no open position" in r["reason"]


def test_cancel_resolves_prefix_and_reports(ctl: AgentController) -> None:
    ctl.executor._fill_probability = D(0)
    _buy(ctl, order_type="limit", limit_price_cents=D(40), book=None)
    oid = ctl.executor.list_orders()[0].id
    assert ctl.autopilot.cancel_order(oid[:8])["status"] == "CANCELLED"
    assert ctl.autopilot.cancel_order("nope")["status"] == "FAILED"


# ── rules: deadline + pct triggers ───────────────────────────

def _pos() -> Position:
    return Position(market_id=MID, parent_id="pm1", side="long", quantity=D(100),
                    avg_price_cents=D(50), cost_usd=D(50))


def test_deadline_trigger_fires_only_after_the_time(ctl: AgentController) -> None:
    for ts, expect in ((time.time() + 600, False), (time.time() - 5, True)):
        rule = ctl.rules.create(market_id=MID, parent_market_id="pm1", position_ref=MID,
                                kind="time_exit", trigger_type="deadline_ts",
                                trigger_value=D(str(int(ts))))
        fire, _ = ctl.rules.should_trigger(rule, position=_pos(), current_price_cents=D(50))
        assert fire is expect


def test_profit_and_loss_pct_triggers(ctl: AgentController) -> None:
    tp = ctl.rules.create(market_id=MID, parent_market_id="pm1", position_ref=MID,
                          kind="take_profit", trigger_type="profit_pct", trigger_value=D(10))
    sl = ctl.rules.create(market_id=MID, parent_market_id="pm1", position_ref=MID,
                          kind="stop_loss", trigger_type="loss_pct", trigger_value=D(10))
    assert ctl.rules.should_trigger(tp, position=_pos(), current_price_cents=D(70), fee_rate_bps=0)[0]
    assert not ctl.rules.should_trigger(tp, position=_pos(), current_price_cents=D(52), fee_rate_bps=0)[0]
    assert ctl.rules.should_trigger(sl, position=_pos(), current_price_cents=D(40), fee_rate_bps=0)[0]
    assert not ctl.rules.should_trigger(sl, position=_pos(), current_price_cents=D(48), fee_rate_bps=0)[0]


# ── LIVE rule state machine (exit_now stubbed) ───────────────

def _armed(ctl: AgentController, ttype: str, value: Any, kind: str, mode: str = "market"):
    ctl.positions.open_position(market_id=MID, parent_id="pm1", side="long", quantity=D(100),
                                avg_price_cents=D(50), cost_usd=D(50))
    rule, err = ctl.autopilot.add_rule(MID, kind, ttype, D(str(value)), mode)
    assert rule is not None, err
    return rule


def test_limit_exit_rests_then_completes_on_fill(ctl: AgentController, monkeypatch) -> None:
    rule = _armed(ctl, "price_cents", 80, "take_profit", EXEC_LIMIT)
    pos = ctl.positions.get_position(MID)
    monkeypatch.setattr(ctl.autopilot, "exit_now", lambda **kw: {
        "status": "RESTING", "ok": True, "order_id": "o-1", "position_closed": False})
    ev = ctl.autopilot.live_rule_step(rule, pos, _book(), D(61))
    assert ev and ev["event"] == "armed" and rule.resting_order_id == "o-1"
    assert rule.status == STATUS_ACTIVE

    class _Filled:
        pred_order_id, id, state = "o-1", "o-1", "FILLED"
    monkeypatch.setattr(ctl.executor, "list_orders", lambda: [_Filled()])
    ev = ctl.autopilot.live_rule_step(rule, pos, _book(), D(79))
    assert ev and ev["event"] == "done" and rule.status == STATUS_DONE
    assert ctl.positions.get_position(MID).status == "closed"


def test_market_rule_sells_when_triggered_and_stays_quiet_otherwise(ctl, monkeypatch) -> None:
    rule = _armed(ctl, "profit_pct", 10, "take_profit")
    pos = ctl.positions.get_position(MID)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(ctl.autopilot, "exit_now", lambda **kw: calls.append(kw) or {
        "status": "FILLED", "ok": True, "position_closed": True})
    assert ctl.autopilot.live_rule_step(rule, pos, _book(), D(51)) is None and not calls
    ev = ctl.autopilot.live_rule_step(rule, pos, _book(), D(70))
    assert ev and ev["event"] == "done" and calls[0]["mode"] == "market"
    assert rule.status == STATUS_DONE


def test_failing_exit_retries_then_gives_up_loudly(ctl, monkeypatch) -> None:
    rule = _armed(ctl, "price_cents", 40, "stop_loss")
    pos = ctl.positions.get_position(MID)
    monkeypatch.setattr(ctl.autopilot, "exit_now", lambda **kw: {
        "status": "NOT_FILLED", "ok": False, "reason": "no liquidity", "position_closed": False})
    events = [ctl.autopilot.live_rule_step(rule, pos, _book("30", "32"), D(31)) for _ in range(10)]
    assert all(e["event"] == "retry" for e in events[:9])
    assert events[-1]["event"] == "failed" and rule.status == STATUS_FAILED


def test_unknown_exit_outcome_is_never_retried(ctl, monkeypatch) -> None:
    rule = _armed(ctl, "price_cents", 40, "stop_loss")
    pos = ctl.positions.get_position(MID)
    monkeypatch.setattr(ctl.autopilot, "exit_now", lambda **kw: {
        "status": "UNKNOWN", "ok": False, "position_closed": False})
    ev = ctl.autopilot.live_rule_step(rule, pos, _book("30", "32"), D(31))
    assert ev["event"] == "failed" and rule.status == STATUS_FAILED


# ── withdraw: one human confirm, never from chat ─────────────

class _S:
    private_key = KEY
    base_rpc_url = "http://rpc.test"
    usdc_address = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    chain_id = 8453
    withdraw_confirm_ttl_seconds = 300


SAFE = "0x" + "cd" * 20


def _svc(balance: str | None = "100", http: httpx.Client | None = None) -> WithdrawService:
    return WithdrawService(settings=_S(), safe_address_provider=lambda: SAFE,
                           balance_provider=lambda: balance, http=http)


def test_withdraw_intent_validation() -> None:
    svc = _svc()
    ok, _ = svc.create(amount_usd="5")
    assert ok and ok.recipient == private_to_address(KEY) and ok.status == "PENDING"
    assert svc.create(amount_usd="0")[0] is None
    assert svc.create(amount_usd="abc")[0] is None
    assert svc.create(amount_usd="500")[0] is None                      # more than the balance
    assert svc.create(amount_usd="1", recipient="0x123")[0] is None     # bad address


def test_withdraw_paper_never_moves_funds_and_runs_once() -> None:
    svc = _svc()
    intent, _ = svc.create(amount_usd="5")
    done, err = svc.execute(intent.id, live=False)
    assert err is None and done.status == "DONE" and "no funds moved" in done.note
    again, err2 = svc.execute(intent.id, live=False)
    assert err2 and "already" in err2


def test_withdraw_intent_expires() -> None:
    svc = _svc()
    intent, _ = svc.create(amount_usd="5")
    intent.expires_at = intent.created_at
    _, err = svc.execute(intent.id, live=False)
    assert err and "EXPIRED" in err


def test_withdraw_calldata_is_a_plain_usdc_transfer() -> None:
    data = usdc_transfer_calldata("0x" + "ab" * 20, D("1.5"))
    assert data.startswith("0xa9059cbb") and data.endswith(format(1_500_000, "x").rjust(64, "0"))


def test_withdraw_onchain_builds_a_correctly_signed_safe_exec() -> None:
    sent: list[str] = []

    def rpc(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        m = body["method"]
        result: Any = {
            "eth_call": hex(7), "eth_estimateGas": hex(80_000), "eth_gasPrice": hex(10**9),
            "eth_getTransactionCount": hex(3), "eth_getBalance": hex(10**18),
            "eth_getTransactionReceipt": {"status": "0x1"},
        }.get(m)
        if m == "eth_sendRawTransaction":
            sent.append(body["params"][0])
            result = "0x" + "99" * 32
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    svc = _svc(http=httpx.Client(transport=httpx.MockTransport(rpc)))
    intent, _ = svc.create(amount_usd="2")
    done, err = svc.execute(intent.id, live=True)
    assert err is None and done.status == "DONE" and done.tx_hash == "0x" + "99" * 32

    raw = bytes.fromhex(sent[0][2:])
    assert Account.recover_transaction(raw) == private_to_address(KEY)   # owner pays gas
    import rlp
    assert raw[0] == 2                                   # EIP-1559 tx
    fields = rlp.decode(raw[1:])                         # [chain,nonce,tip,fee,gas,to,value,data,...]
    tx = {"to": "0x" + fields[5].hex(), "data": fields[7]}
    assert tx["to"].lower() == SAFE.lower() and tx["data"][:4].hex() == "6a761202"
    # the signature inside the calldata recovers to the owner over the SafeTx digest
    inner = usdc_transfer_calldata(intent.recipient, intent.amount_usd)
    digest = safe_tx_digest(8453, SAFE, {
        "to": _S.usdc_address, "value": 0, "data": inner, "operation": 0, "safeTxGas": 0,
        "baseGas": 0, "gasPrice": 0, "gasToken": "0x" + "00" * 20,
        "refundReceiver": "0x" + "00" * 20, "nonce": 7})
    sig = bytes(tx["data"])[-96:-31]     # 65-byte signature, last ABI word padded to 96
    assert Account._recover_hash(digest, signature=sig) == private_to_address(KEY)


def test_withdraw_fails_closed_without_gas_or_rpc() -> None:
    svc = _svc()
    svc.settings = type("S", (), {**_S.__dict__, "base_rpc_url": None})()
    intent, _ = svc.create(amount_usd="1")
    done, err = svc.execute(intent.id, live=True)
    assert done.status == "FAILED" and "BASE_RPC_URL" in err


# ── research ─────────────────────────────────────────────────

def _sportsdb(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("searchteams.php"):
        name = request.url.params["t"]
        return httpx.Response(200, json={"teams": [{"idTeam": "1" if name == "Alpha" else "2",
                                                    "strTeam": name}]})
    if path.endswith("eventslast.php"):
        tid = request.url.params["id"]
        me, opp = ("Alpha", "X") if tid == "1" else ("Beta", "Y")
        wins = [{"dateEvent": f"2026-10-{10 + i}", "strHomeTeam": me, "strAwayTeam": opp,
                 "intHomeScore": "3" if tid == "1" else "0", "intAwayScore": "0" if tid == "1" else "2",
                 "strLeague": "L"} for i in range(5)]
        return httpx.Response(200, json={"results": wins})
    return httpx.Response(200, json={"event": None})


def test_research_favours_the_stronger_side_and_sums_to_100() -> None:
    r = MatchResearcher(api_key="k", http=httpx.Client(transport=httpx.MockTransport(_sportsdb)))
    out = r.research(home="Alpha", away="Beta")
    p = out["win_probability_pct"]
    assert p["Alpha win"] > p["Beta win"]
    assert abs(sum(p.values()) - 100) < 0.5
    assert out["form"]["Alpha"]["form_newest_first"] == "WWWWW"
    assert "no head-to-head history found" in out["data_gaps"]
    assert out["confidence"] == "low" and "not guarantees" in out["warning"]


def test_research_blends_with_market_prices() -> None:
    r = MatchResearcher(api_key="k", http=httpx.Client(transport=httpx.MockTransport(_sportsdb)))
    out = r.research(home="Alpha", away="Beta",
                     market_probs={"home": D("0.2"), "draw": D("0.3"), "away": D("0.5")})
    assert out["market_implied_pct"]["away"] == 50.0
    assert out["confidence"] == "medium"
    assert set(out["model_vs_market_pp"]) == {"home", "draw", "away"}


def test_research_degrades_honestly_without_data() -> None:
    dead = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    out = MatchResearcher(http=dead).research(home="A", away="B")
    assert out["confidence"] == "low"
    assert any("no recent results" in g for g in out["data_gaps"])
    assert any("no market prices" in g for g in out["data_gaps"])
    assert abs(sum(out["win_probability_pct"].values()) - 100) < 0.5


def test_poisson_is_a_probability_distribution() -> None:
    h, d, a = poisson_1x2(1.8, 0.9)
    assert abs(h + d + a - 1) < 1e-9 and h > a


# ── app wiring ───────────────────────────────────────────────

def test_autopilot_tools_are_exposed_and_withdraw_has_no_confirm_tool() -> None:
    app = create_app()
    names = set(app.state.chat_tools)
    assert {"place_trade", "sell_position", "set_exit_rules", "request_withdraw",
            "research_match", "cancel_order", "panic_stop"} <= names
    assert not {"approve", "confirm_withdraw", "withdraw", "execute"} & names
    assert "propose_trade" not in names    # no pending-card flow for the LLM in autopilot


def test_withdraw_confirm_endpoint_needs_auth_and_runs_the_intent() -> None:
    app = create_app()
    client = TestClient(app)
    intent, _ = app.state.withdraw.create(amount_usd="1", recipient="0x" + "ab" * 20)
    assert client.post(f"/v1/withdrawals/{intent.id}/confirm").status_code == 401
    code = app.state.auth.generate_pairing_code("t")
    tok = client.post("/v1/pair", json={"code": code, "device_name": "t"}).json()["data"]["device_token"]
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post(f"/v1/withdrawals/{intent.id}/confirm", headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "DONE"      # PAPER: simulated
    assert "no funds moved" in r.json()["data"]["note"]
    assert client.post(f"/v1/withdrawals/{intent.id}/confirm", headers=h).status_code == 409


def test_place_trade_tool_reports_failure_for_unknown_market() -> None:
    app = create_app()
    fn = app.state.autopilot_tool_fns["place_trade"]
    out, cards = asyncio.run(fn(market_id="nope", side="long", amount_usd="1"))
    assert out["status"] == "FAILED" and "not in the current fixture feed" in out["reason"]
    assert cards == []


def test_place_trade_tool_end_to_end_in_paper() -> None:
    app = create_app()
    app.state.controller.executor._fill_probability = D(1)
    app.state.fixture_cache.update([DiscoveredParent(
        parent_market_id="pm1", type_reference_id=None, contract_address=None,
        parent_market_type=None, parent_market_family=None, canonical_name=None, title="A v B",
        status="active", fee_rate_bps=250,
        fixture={"fixture_id": "fx1", "home_team_id": "t1", "away_team_id": "t2",
                 "game_start_time": "2099-01-01T12:00:00Z"},
        team_data=[{"team_id": "t1", "team_name": "Alpha"}, {"team_id": "t2", "team_name": "Beta"}],
        markets=[{"market_id": MID, "name": "Alpha to win", "status": "active"}],
    )])

    class _Feed:
        def get_book(self, _m: str) -> BookSnapshot:
            return _book()
    app.state.book_feed = _Feed()
    fn = app.state.autopilot_tool_fns["place_trade"]
    out, cards = asyncio.run(fn(market_id=MID, side="long", amount_usd="1",
                                take_profit_pct="10", sell_after_minutes="45"))
    assert out["status"] == "FILLED" and out["exit_plan"]["armed"] is True
    assert cards and cards[0].type == "trade_result"
    kinds = sorted(r.kind for r in app.state.controller.rules.list_active())
    assert kinds == ["take_profit", "time_exit"]


def test_safe_exec_abi_encoding_matches_the_reference_encoder() -> None:
    """Hand-rolled calldata must equal eth_abi's output, and the selectors must be the real ones."""
    from eth_abi import encode
    from eth_utils import keccak, to_checksum_address

    from pred_agent.control_api import withdraw as w

    sig = bytes(range(65))
    data_hex = usdc_transfer_calldata("0x" + "ab" * 20, D("2"))
    got = WithdrawService._encode_exec(_S.usdc_address, data_hex, sig)
    types = ["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256",
             "address", "address", "bytes"]
    want = encode(types, [to_checksum_address(_S.usdc_address), 0, bytes.fromhex(data_hex[2:]),
                          0, 0, 0, 0, w.ZERO, w.ZERO, sig])
    sel = keccak(text="execTransaction(" + ",".join(types) + ")")[:4].hex()
    assert sel == w.SEL_EXEC
    assert got == "0x" + sel + want.hex()
    assert keccak(text="transfer(address,uint256)")[:4].hex() == w.SEL_TRANSFER
    assert keccak(text="nonce()")[:4].hex() == w.SEL_SAFE_NONCE
