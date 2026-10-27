"""Discovery response shape: docs wrap the page in {"data": {...}}."""

from __future__ import annotations

import httpx
import pytest

from pred_agent.scanner import Scanner, _unwrap_page


def _entry(pid: str) -> dict:
    return {
        "parent_market_data": {
            "parent_market_id": pid,
            "parent_market_family": "moneyline",
            "status": "active",
        },
        "fixture": {"fixture_id": f"fx-{pid}", "home_team_id": "h", "away_team_id": "a"},
        "markets": [{"market_id": f"m-{pid}", "status": "active"}],
    }


def _scanner(handler) -> Scanner:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Scanner(http_client=client, base_url="https://example.test")


def test_unwrap_data_envelope():
    page = _unwrap_page({"data": {"parent_markets_list": [1], "total": 1}})
    assert page["total"] == 1


def test_unwrap_bare_shape():
    assert _unwrap_page({"parent_markets_list": [], "total": 0})["total"] == 0


def test_unwrap_unknown_shape_raises():
    with pytest.raises(RuntimeError):
        _unwrap_page({"error": "nope"})


def test_scan_reads_data_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"parent_markets_list": [_entry("p1")], "total": 1}})

    results = _scanner(handler).scan_active_moneyline()
    assert [r.parent_market_id for r in results] == ["p1"]


def test_scan_keeps_paging_past_empty_page():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params["offset"]
        calls.append(offset)
        parents = [] if offset == "0" else [_entry("p2")]
        return httpx.Response(200, json={"data": {"parent_markets_list": parents, "total": 100}})

    s = _scanner(handler)
    s._discover_limit = 50
    results = s.scan_active_moneyline()
    assert calls == ["0", "50"]
    assert [r.parent_market_id for r in results] == ["p2"]
