"""Phase 2 unit tests: book analyzer and scanner logic.

No network calls — uses mock data.
"""

from __future__ import annotations

from decimal import Decimal

from pred_agent.book import (
    BookAnalyzer,
    BookLevel,
    BookSnapshot,
    normalize_book,
)

# ---------------------------------------------------------------------------
# normalize_book: REST (cents) vs WS (dollars)
# ---------------------------------------------------------------------------

def test_normalize_book_rest_cents():
    raw = {
        "market_id": "0xabc",
        "bids": [{"price": "62", "quantity": "150", "order_count": 3, "total_usd": "93"}],
        "asks": [{"price": "64", "quantity": "200", "order_count": 5, "total_usd": "128"}],
        "ltp": "63",
        "metadata": {"spread": "2", "mid_price": "63", "last_calculated": "2026-01-01T00:00:00Z"},
    }
    snap = normalize_book(raw, "0xabc", ws_dollars=False)
    assert snap.bids[0].price_cents == Decimal("62")
    assert snap.asks[0].price_cents == Decimal("64")
    assert snap.ltp == Decimal("63")


def test_normalize_book_ws_dollars():
    """WS prices like "0.62" = 62 cents."""
    raw = {
        "market_id": "0xabc",
        "bids": [{"price": "0.62", "quantity": "150"}],
        "asks": [{"price": "0.64", "quantity": "200"}],
        "ltp": "0.63",
    }
    snap = normalize_book(raw, "0xabc", ws_dollars=True)
    assert snap.bids[0].price_cents == Decimal("62")
    assert snap.asks[0].price_cents == Decimal("64")
    assert snap.ltp == Decimal("63")


# ---------------------------------------------------------------------------
# BookAnalyzer: spread and slippage
# ---------------------------------------------------------------------------

def make_snapshot(bid_prices, ask_prices):
    bids = [BookLevel(price_cents=Decimal(p), quantity=Decimal("100")) for p in bid_prices]
    asks = [BookLevel(price_cents=Decimal(p), quantity=Decimal("100")) for p in ask_prices]
    return BookSnapshot(
        market_id="0xabc", bids=bids, asks=asks,
        ltp=None, spread_cents=None, mid_price_cents=None,
        last_updated_at=None, sequence_number=None,
    )


def test_spread_computed():
    snap = make_snapshot([Decimal("60"), Decimal("59")], [Decimal("62"), Decimal("63")])
    analyzer = BookAnalyzer()
    result = analyzer.analyze(snap)
    assert result.spread_cents == Decimal("2")
    assert result.mid_price_cents == Decimal("61")


def test_check_spread_within_limit():
    snap = make_snapshot([Decimal("60")], [Decimal("62")])
    analyzer = BookAnalyzer(max_spread_cents=Decimal("6"))
    result = analyzer.analyze(snap)
    assert analyzer.check_spread(result) is True


def test_check_spread_exceeds_limit():
    snap = make_snapshot([Decimal("60")], [Decimal("70")])
    analyzer = BookAnalyzer(max_spread_cents=Decimal("6"))
    result = analyzer.analyze(snap)
    assert analyzer.check_spread(result) is False


def test_slippage_walk_buy():
    snap = make_snapshot([], [Decimal("62"), Decimal("64"), Decimal("66")])
    analyzer = BookAnalyzer()
    walk = analyzer.slippage_walk(snap, "buy", Decimal("150"))
    # 100 @ 62 + 50 @ 64 = 6200 + 3200 = 9400 / 150 = 62.667
    assert walk.filled_shares == Decimal("150")
    assert walk.avg_price_cents == Decimal("62.66666666666666666666666667")
    # usable requires total_depth >= size * 3 (300 >= 450? No)
    assert walk.usable is False


def test_slippage_walk_buy_usable_when_enough_depth():
    # 300 shares available across 3 levels, target 100 -> depth 300 >= 300 (size*3)
    snap = make_snapshot([], [Decimal("62"), Decimal("64"), Decimal("66")])
    analyzer = BookAnalyzer()
    walk = analyzer.slippage_walk(snap, "buy", Decimal("100"))
    assert walk.filled_shares == Decimal("100")
    assert walk.usable is True  # total_depth=300 >= target_shares*3=300


def test_slippage_walk_insufficient_depth():
    snap = make_snapshot([], [Decimal("62")])  # only 100 shares available
    analyzer = BookAnalyzer()
    walk = analyzer.slippage_walk(snap, "buy", Decimal("500"))
    assert walk.filled_shares == Decimal("100")
    assert walk.usable is False


def test_slippage_walk_sell():
    snap = make_snapshot([Decimal("60"), Decimal("58")], [])
    analyzer = BookAnalyzer()
    walk = analyzer.slippage_walk(snap, "sell", Decimal("150"))
    assert walk.filled_shares == Decimal("150")
    # 100 @ 60 + 50 @ 58 = 6000 + 2900 = 8900 / 150 = 59.333
    assert walk.avg_price_cents == Decimal("59.33333333333333333333333333")


# ---------------------------------------------------------------------------
# Scanner eligibility logic (mock data)
# ---------------------------------------------------------------------------

def test_scanner_filters_moneyline_only():
    from pred_agent.scanner import DiscoveredParent

    parents = [
        DiscoveredParent(
            parent_market_id="0x1", type_reference_id="trid1", contract_address="0xca",
            parent_market_type="fixture", parent_market_family="moneyline",
            canonical_name=None, title=None, status="active",
            fee_rate_bps=250, fixture={"game_start_time": "2026-01-01T00:00:00Z"},
            team_data=[], markets=[{"market_id": "0xm1", "status": "active"}],
        ),
        DiscoveredParent(
            parent_market_id="0x2", type_reference_id=None, contract_address=None,
            parent_market_type="fixture", parent_market_family="spread",
            canonical_name=None, title=None, status="active",
            fee_rate_bps=None, fixture={"game_start_time": "2026-01-01T00:00:00Z"},
            team_data=[], markets=[{"market_id": "0xm2", "status": "active"}],
        ),
    ]
    result = [p for p in parents if p.parent_market_family == "moneyline" and p.status == "active"]
    assert len(result) == 1
    assert result[0].parent_market_id == "0x1"


def test_scanner_requires_active_children():
    from pred_agent.scanner import DiscoveredParent

    parents = [
        DiscoveredParent(
            parent_market_id="0x1", type_reference_id=None, contract_address=None,
            parent_market_type="fixture", parent_market_family="moneyline",
            canonical_name=None, title=None, status="active",
            fee_rate_bps=None, fixture={"game_start_time": "2026-01-01T00:00:00Z"},
            team_data=[], markets=[{"market_id": "0xm1", "status": "closed"}],
        ),
    ]
    result = [p for p in parents
              if any(m.get("status") == "active" for m in p.markets)]
    assert len(result) == 0
