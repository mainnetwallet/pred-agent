"""Match research for chat: team form, head-to-head, a goals model, and market odds, combined
into win/draw/loss percentages with an honest confidence label.

Sources
-------
* TheSportsDB v1 (``SPORTS_API_KEY``; falls back to the public test key ``3``). The free key is
  heavily restricted (searches can return almost nothing), so every missing piece is reported in
  ``data_gaps`` instead of being guessed. A paid key gives much better coverage.
* The PRED market itself: mid prices of the home/draw/away markets are an implied probability and
  are usually the strongest single predictor, so the blend leans on them when they exist.

Model: independent Poisson goals for each side, attack/defence strength from the recent games we
got (shrunk toward league averages because 5 games is tiny), a home-advantage baseline, and a
small head-to-head tilt. It is a sanity check, NOT an edge: with <= 5 games of form its
uncertainty is large. Nothing here guarantees a result.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE = "https://www.thesportsdb.com/api/v1/json"
LG_HOME_GOALS = 1.45
LG_AWAY_GOALS = 1.15
LG_TEAM_GOALS = (LG_HOME_GOALS + LG_AWAY_GOALS) / 2
SHRINK_GAMES = 5.0
MARKET_WEIGHT = 0.65          # market-implied share of the final blend when available


@dataclass
class TeamForm:
    name: str
    team_id: str | None = None
    results: list[str] = field(default_factory=list)       # newest first: W/D/L
    games: list[dict[str, Any]] = field(default_factory=list)
    gf: float = 0.0
    ga: float = 0.0

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def ppg(self) -> float | None:
        if not self.n:
            return None
        pts = sum(3 if r == "W" else 1 if r == "D" else 0 for r in self.results)
        return round(pts / self.n, 2)

    def summary(self) -> dict[str, Any]:
        return {
            "team": self.name, "games_used": self.n, "form_newest_first": "".join(self.results),
            "points_per_game": self.ppg,
            "goals_for_per_game": round(self.gf / self.n, 2) if self.n else None,
            "goals_against_per_game": round(self.ga / self.n, 2) if self.n else None,
            "last_games": self.games[:5],
        }


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def poisson_1x2(lam_home: float, lam_away: float, max_goals: int = 10) -> tuple[float, float, float]:
    """P(home win), P(draw), P(away win) for independent Poisson goals."""
    def pmf(k: int, lam: float) -> float:
        return math.exp(-lam) * lam**k / math.factorial(k)

    ph = [pmf(k, lam_home) for k in range(max_goals + 1)]
    pa = [pmf(k, lam_away) for k in range(max_goals + 1)]
    home = draw = away = 0.0
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            p = x * y
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    total = home + draw + away
    return home / total, draw / total, away / total


def _shrunk(per_game: float, n: int) -> float:
    return (n * per_game + SHRINK_GAMES * LG_TEAM_GOALS) / (n + SHRINK_GAMES)


class MatchResearcher:
    def __init__(self, *, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        self._key = api_key or "3"
        self._free = not api_key
        self._http = http or httpx.Client(timeout=httpx.Timeout(15.0))

    # ── data fetch ────────────────────────────────────────────

    def _get(self, endpoint: str, **params: str) -> dict[str, Any] | None:
        try:
            resp = self._http.get(f"{BASE}/{self._key}/{endpoint}", params=params)
            if resp.status_code != 200:
                log.warning("sportsdb %s -> HTTP %s", endpoint, resp.status_code)
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError):
            log.warning("sportsdb %s failed", endpoint, exc_info=True)
            return None

    def _find_team(self, name: str) -> tuple[str | None, str | None]:
        data = self._get("searchteams.php", t=name)
        teams = (data or {}).get("teams") or []
        want = _norm(name)
        best = next((t for t in teams if _norm(t.get("strTeam", "")) == want), None) \
            or next((t for t in teams if want in _norm(t.get("strTeam", ""))), None)
        if best is None:
            return None, None
        return str(best.get("idTeam")), best.get("strTeam")

    def _form(self, name: str) -> TeamForm:
        form = TeamForm(name=name)
        tid, official = self._find_team(name)
        if tid is None:
            return form
        form.team_id = tid
        data = self._get("eventslast.php", id=tid)
        events = (data or {}).get("results") or []
        events = sorted(events, key=lambda e: e.get("dateEvent") or "", reverse=True)
        me = _norm(official or name)
        for e in events:
            hs, as_ = _int(e.get("intHomeScore")), _int(e.get("intAwayScore"))
            if hs is None or as_ is None:
                continue
            is_home = _norm(e.get("strHomeTeam", "")) == me
            gf, ga = (hs, as_) if is_home else (as_, hs)
            form.gf += gf
            form.ga += ga
            form.results.append("W" if gf > ga else "D" if gf == ga else "L")
            form.games.append({
                "date": e.get("dateEvent"), "venue": "home" if is_home else "away",
                "opponent": e.get("strAwayTeam") if is_home else e.get("strHomeTeam"),
                "score": f"{gf}-{ga}", "league": e.get("strLeague"),
            })
        return form

    def _h2h(self, home: str, away: str) -> dict[str, Any]:
        seen: dict[str, dict[str, Any]] = {}
        for a, b in ((home, away), (away, home)):
            data = self._get("searchevents.php", e=f"{a}_vs_{b}".replace(" ", "_"))
            for e in (data or {}).get("event") or []:
                if e.get("idEvent"):
                    seen[str(e["idEvent"])] = e
        hw = dr = aw = 0
        rows: list[dict[str, Any]] = []
        for e in sorted(seen.values(), key=lambda x: x.get("dateEvent") or "", reverse=True):
            hs, as_ = _int(e.get("intHomeScore")), _int(e.get("intAwayScore"))
            if hs is None or as_ is None:
                continue
            h_name = _norm(e.get("strHomeTeam", ""))
            home_is_home = h_name == _norm(home) or _norm(home) in h_name
            gh, ga = (hs, as_) if home_is_home else (as_, hs)
            hw += gh > ga
            dr += gh == ga
            aw += gh < ga
            rows.append({"date": e.get("dateEvent"), "score": f"{e.get('strHomeTeam')} {hs}-{as_} "
                         f"{e.get('strAwayTeam')}"})
        n = hw + dr + aw
        return {"meetings": n, f"{home}_wins": hw, "draws": dr, f"{away}_wins": aw,
                "recent": rows[:5]}

    # ── public ────────────────────────────────────────────────

    def research(
        self, *, home: str, away: str, league: str | None = None,
        market_probs: dict[str, Decimal | float | None] | None = None,
    ) -> dict[str, Any]:
        """``market_probs``: implied probabilities (0-1) keyed home/draw/away, when known."""
        gaps: list[str] = []
        fh, fa = self._form(home), self._form(away)
        for f in (fh, fa):
            if f.n == 0:
                gaps.append(f"no recent results found for {f.name}")
        h2h = self._h2h(home, away)
        if h2h["meetings"] == 0:
            gaps.append("no head-to-head history found")
        if self._free:
            gaps.append("using the free public data key (very limited); set SPORTS_API_KEY for better coverage")

        # Goals model (shrunk toward league averages when data is thin).
        att_h = _shrunk(fh.gf / fh.n, fh.n) if fh.n else LG_TEAM_GOALS
        def_a = _shrunk(fa.ga / fa.n, fa.n) if fa.n else LG_TEAM_GOALS
        att_a = _shrunk(fa.gf / fa.n, fa.n) if fa.n else LG_TEAM_GOALS
        def_h = _shrunk(fh.ga / fh.n, fh.n) if fh.n else LG_TEAM_GOALS
        lam_h = LG_HOME_GOALS * (att_h / LG_TEAM_GOALS) * (def_a / LG_TEAM_GOALS)
        lam_a = LG_AWAY_GOALS * (att_a / LG_TEAM_GOALS) * (def_h / LG_TEAM_GOALS)
        mh, md, ma = poisson_1x2(lam_h, lam_a)

        # Small head-to-head tilt (only with >= 3 meetings).
        n_h2h = h2h["meetings"]
        if n_h2h >= 3:
            w = min(0.15, 0.03 * n_h2h)
            sh = h2h[f"{home}_wins"] / n_h2h
            sd = h2h["draws"] / n_h2h
            sa = h2h[f"{away}_wins"] / n_h2h
            mh, md, ma = (1 - w) * mh + w * sh, (1 - w) * md + w * sd, (1 - w) * ma + w * sa

        model = {"home": mh, "draw": md, "away": ma}
        market = self._clean_market(market_probs)
        if market:
            final = {k: MARKET_WEIGHT * market[k] + (1 - MARKET_WEIGHT) * model[k] for k in model}
        else:
            final = model
            gaps.append("no market prices available; showing the goals model only")
        s = sum(final.values())
        final = {k: v / s for k, v in final.items()}

        games = fh.n + fa.n
        confidence = "medium" if market and games >= 8 else "low"
        out: dict[str, Any] = {
            "match": f"{home} vs {away}", "league": league,
            "win_probability_pct": {
                f"{home} win": round(final["home"] * 100, 1),
                "draw": round(final["draw"] * 100, 1),
                f"{away} win": round(final["away"] * 100, 1),
            },
            "model_only_pct": {k: round(v * 100, 1) for k, v in model.items()},
            "market_implied_pct": {k: round(v * 100, 1) for k, v in market.items()} if market else None,
            "model_vs_market_pp": {k: round((model[k] - market[k]) * 100, 1) for k in model}
            if market else None,
            "expected_goals": {home: round(lam_h, 2), away: round(lam_a, 2)},
            "form": {home: fh.summary(), away: fa.summary()},
            "head_to_head": h2h,
            "confidence": confidence,
            "data_gaps": gaps,
            "how_computed": (
                f"{int(MARKET_WEIGHT * 100)}% market-implied + {int((1 - MARKET_WEIGHT) * 100)}% goals "
                "model (recent form, home advantage, small head-to-head tilt)"
                if market else "goals model only (recent form, home advantage, small H2H tilt)"
            ),
            "warning": ("Probabilities are estimates from limited data, not guarantees. Injuries, "
                        "lineups, motivation and weather are NOT included."),
        }
        return out

    @staticmethod
    def _clean_market(raw: dict[str, Any] | None) -> dict[str, float] | None:
        if not raw:
            return None
        vals = {k: raw.get(k) for k in ("home", "draw", "away")}
        if any(v is None for v in vals.values()):
            return None
        nums = {k: float(v) for k, v in vals.items()}  # type: ignore[arg-type]
        total = sum(nums.values())
        if total <= 0:
            return None
        return {k: v / total for k, v in nums.items()}   # remove the overround
