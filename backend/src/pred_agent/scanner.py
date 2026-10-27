"""Market discovery scanner — paging, eligibility filter, moneyline football filter.

Grounded in kit/02_PRED_API_SPEC.md section 6 (Market Discovery) and live docs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from pred_agent.config import load_settings
from pred_agent.store.models import Fixture, Market


@dataclass(frozen=True)
class DiscoveredParent:
    parent_market_id: str
    type_reference_id: str | None
    contract_address: str | None
    parent_market_type: str | None
    parent_market_family: str | None
    canonical_name: str | None
    title: str | None
    status: str | None
    fee_rate_bps: int | None
    fixture: dict[str, Any] | None
    team_data: list[dict[str, Any]]
    markets: list[dict[str, Any]]


class Scanner:
    """Pages through GET /api/v1/market-discovery/discover and filters active moneyline football fixtures."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        base_url: str | None = None,
    ) -> None:
        settings = load_settings()
        self._base_url = (base_url or settings.host).rstrip("/")
        self._discover_limit = settings.discover_limit
        self._max_rps = settings.discover_max_rps
        self._omit_cutoff = settings.omit_inactive_with_redeemed_on_before
        self._omit_days = settings.discover_omit_days
        self._scan_cutoff: str | None = self._omit_cutoff
        self._league_allowlist = settings.league_allowlist
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(30.0))
        self._last_request_time: float | None = None
        # Diagnostics of the most recent scan (exposed via /status feed_diag).
        self.last_scan: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def scan_active_moneyline(self) -> list[DiscoveredParent]:
        """Full discovery feed, filtered to active moneyline football fixtures.

        Pages with offset += limit until data.total is exhausted.
        Keeps cutoff fixed across pages.
        """
        results: list[DiscoveredParent] = []
        offset = 0
        self._scan_cutoff = self._resolve_cutoff()
        stats: dict[str, Any] = {
            "pages": 0, "total": None, "seen": 0, "families": {}, "moneyline_statuses": {},
            "dropped_league": 0, "dropped_no_fixture": 0, "dropped_no_active_child": 0,
            "kept": 0, "error": None, "cutoff": None,
        }
        stats["cutoff"] = self._scan_cutoff
        self.last_scan = stats
        while True:
            try:
                page = self._fetch_page(offset)
            except Exception as exc:
                stats["error"] = f"{type(exc).__name__}: {exc}"[:200]
                raise
            stats["pages"] += 1
            parents = page.get("parent_markets_list") or []
            stats["seen"] += len(parents)
            # Docs: keep paging on empty pages; stop only when data.total is exhausted.
            for entry in parents:
                pmd = entry.get("parent_market_data", {})
                fam = str(pmd.get("parent_market_family"))
                stats["families"][fam] = stats["families"].get(fam, 0) + 1
                if pmd.get("parent_market_family") != "moneyline":
                    continue
                st = str(pmd.get("status"))
                stats["moneyline_statuses"][st] = stats["moneyline_statuses"].get(st, 0) + 1
                if pmd.get("status") != "active":
                    continue
                # League allowlist
                if self._league_allowlist:
                    league = entry.get("league_data") or {}
                    if league.get("league_id") not in self._league_allowlist:
                        stats["dropped_league"] += 1
                        continue
                # Fixture filter: football only
                fixture = entry.get("fixture")
                if fixture is None:
                    stats["dropped_no_fixture"] += 1
                    continue
                # Active child markets only
                active_children = [
                    m for m in (entry.get("markets") or []) if m.get("status") == "active"
                ]
                if not active_children:
                    stats["dropped_no_active_child"] += 1
                    continue
                results.append(
                    DiscoveredParent(
                        parent_market_id=pmd.get("parent_market_id", ""),
                        type_reference_id=pmd.get("type_reference_id"),
                        contract_address=pmd.get("contract_address"),
                        parent_market_type=pmd.get("parent_market_type"),
                        parent_market_family=pmd.get("parent_market_family"),
                        canonical_name=pmd.get("canonical_name"),
                        title=pmd.get("title"),
                        status=pmd.get("status"),
                        fee_rate_bps=pmd.get("fee_rate_bps"),
                        fixture=fixture,
                        team_data=entry.get("team_data") or [],
                        markets=active_children,
                    )
                )
            total = page.get("total", 0)
            stats["total"] = total
            offset += self._discover_limit
            if offset >= total:
                break
        stats["kept"] = len(results)
        return results

    def upsert_to_db(self, session: Session, results: list[DiscoveredParent]) -> int:
        """Upsert discovered fixtures and markets into the DB. Returns count upserted."""
        count = 0
        for parent in results:
            fx = parent.fixture or {}
            kickoff = fx.get("game_start_time")
            kickoff_dt = None
            if kickoff:
                try:
                    kickoff_dt = _parse_rfc3339(kickoff)
                except Exception:
                    kickoff_dt = None
            existing = session.query(Fixture).filter_by(pred_fixture_id=fx.get("fixture_id", "")).first()
            if existing:
                existing.parent_market_id = parent.parent_market_id
                existing.type_reference_id = parent.type_reference_id
                existing.league_id = (parent.fixture or {}).get("league_id")
                existing.league_name = (parent.fixture or {}).get("league_name")
                existing.home_team = fx.get("home_team_id")
                existing.away_team = fx.get("away_team_id")
                existing.kickoff = kickoff_dt
                existing.status = parent.status
                existing.game_status = fx.get("game_status")
            else:
                session.add(
                    Fixture(
                        pred_fixture_id=fx.get("fixture_id", ""),
                        parent_market_id=parent.parent_market_id,
                        type_reference_id=parent.type_reference_id,
                        league_id=(parent.fixture or {}).get("league_id"),
                        league_name=(parent.fixture or {}).get("league_name"),
                        home_team=fx.get("home_team_id"),
                        away_team=fx.get("away_team_id"),
                        kickoff=kickoff_dt,
                        status=parent.status,
                        game_status=fx.get("game_status"),
                    )
                )
            count += 1
            for child in parent.markets:
                mid = child.get("market_id", "")
                existing_m = session.query(Market).filter_by(market_id=mid).first()
                if existing_m:
                    existing_m.status = child.get("status") or existing_m.status  # column is NOT NULL
                    existing_m.tick_size = child.get("tick_size")
                    existing_m.ltp = child.get("ltp")
                    existing_m.mid_price = child.get("mid_price")
                else:
                    session.add(
                        Market(
                            market_id=mid,
                            parent_market_id=parent.parent_market_id,
                            outcome_name=child.get("name", ""),
                            team_id=child.get("team_id"),
                            tick_size=child.get("tick_size"),
                            status=child.get("status", ""),
                            family=parent.parent_market_family,
                            contract_address=parent.contract_address,
                            fee_target_bps=parent.fee_rate_bps,
                            ltp=child.get("ltp"),
                            mid_price=child.get("mid_price"),
                        )
                    )
                    count += 1
        session.commit()
        return count

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _resolve_cutoff(self) -> str | None:
        """Fixed cutoff for one scan: explicit setting, else now minus discover_omit_days (UTC RFC3339)."""
        if self._omit_cutoff:
            return self._omit_cutoff
        if self._omit_days <= 0:
            return None
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._omit_days)
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _fetch_page(self, offset: int) -> dict[str, Any]:
        """Fetch one discovery page with rate limiting."""
        max_retries = 5
        for _attempt in range(max_retries):
            self._enforce_rate_limit()
            params = {
                "verbose": "true",
                "limit": str(self._discover_limit),
                "offset": str(offset),
            }
            if self._scan_cutoff:
                params["omit_inactive_with_redeemed_on_before"] = self._scan_cutoff
            resp = self._http.get(f"{self._base_url}/api/v1/market-discovery/discover", params=params)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                import time as _time

                wait = float(retry_after) if retry_after else 2.0
                _time.sleep(min(wait, 60.0))  # cap wait at 60s
                continue
            resp.raise_for_status()
            return _unwrap_page(resp.json())
        raise RuntimeError(f"Failed to fetch discovery page after {max_retries} retries (429 rate limited)")

    def _enforce_rate_limit(self) -> None:
        """Keep discovery under about 10 requests/second."""
        import time as _time

        if self._last_request_time is not None:
            elapsed = _time.time() - self._last_request_time
            min_interval = 1.0 / self._max_rps
            if elapsed < min_interval:
                _time.sleep(min_interval - elapsed)
        self._last_request_time = _time.time()


def _unwrap_page(body: Any) -> dict[str, Any]:
    """Discovery responds with {"data": {"parent_markets_list": [...], "total": N}}.

    Accept the bare shape too, so a docs/API change never silently yields an empty feed.
    """
    if not isinstance(body, dict):
        raise RuntimeError(f"unexpected discovery response type: {type(body).__name__}")
    inner = body.get("data")
    if isinstance(inner, dict) and ("parent_markets_list" in inner or "total" in inner):
        return inner
    if "parent_markets_list" in body:
        return body
    raise RuntimeError(f"unexpected discovery response shape; keys={sorted(body)[:10]}")


def _parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
