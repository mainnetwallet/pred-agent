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


def test_last_scan_stats_recorded():
    def handler(request: httpx.Request) -> httpx.Response:
        spread = _entry("p2")
        spread["parent_market_data"]["parent_market_family"] = "spread"
        return httpx.Response(
            200, json={"data": {"parent_markets_list": [_entry("p1"), spread], "total": 2}}
        )

    s = _scanner(handler)
    s.scan_active_moneyline()
    st = s.last_scan
    assert st["seen"] == 2
    assert st["kept"] == 1
    assert st["families"] == {"moneyline": 1, "spread": 1}
    assert st["error"] is None


def test_auto_cutoff_is_rfc3339_utc_and_explicit_overrides() -> None:
    import re

    sc = Scanner(http_client=httpx.Client(), base_url="https://example.test")
    sc._omit_cutoff, sc._omit_days = None, 3
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", sc._resolve_cutoff() or "")
    sc._omit_days = 0
    assert sc._resolve_cutoff() is None
    sc._omit_cutoff = "2026-10-20T00:00:00Z"
    assert sc._resolve_cutoff() == "2026-10-20T00:00:00Z"


def test_family_param_sent_and_falls_back_on_400() -> None:
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.url.params))
        if "family" in req.url.params:
            return httpx.Response(400, json={"message": "bad"})
        return httpx.Response(200, json={"data": {"parent_markets_list": [_entry("p1")], "total": 1}})

    sc = _scanner(handler)
    sc._max_rps = 1000
    out = sc.scan_active_moneyline()
    assert len(out) == 1
    assert "family" in seen[0] and "family" not in seen[-1]
    assert sc.last_scan["state"] == "done"
