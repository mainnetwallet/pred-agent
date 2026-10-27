"""research package — research providers returning structured ResearchReport.

Grounded in kit/05_STRATEGY_AND_RISK.md section 1 (Baseline from research report).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ResearchReport:
    fixture_id: str
    home_team: str
    away_team: str
    league: str | None = None
    kickoff: str | None = None
    injuries: list[str] = field(default_factory=list)
    lineups: list[str] = field(default_factory=list)
    form: str | None = None
    home_advantage: bool = False
    news: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # URLs
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)

    def is_fresh(self, max_age_min: int) -> bool:
        age = (datetime.now(timezone.utc) - self.fetched_at).total_seconds() / 60.0
        return age <= max_age_min

    def has_sources(self) -> bool:
        return len(self.sources) > 0


class ResearchProvider(ABC):
    """Abstract research provider."""

    @abstractmethod
    async def research(self, fixture_id: str, home_team: str,
                       away_team: str) -> ResearchReport:
        ...


class LLMSearchResearchProvider(ResearchProvider):
    """LLM web-search based research provider (first choice)."""

    def __init__(self, llm_api_key: str | None = None) -> None:
        self.llm_api_key = llm_api_key

    async def research(self, fixture_id: str, home_team: str,
                       away_team: str) -> ResearchReport:
        # Placeholder: actual LLM web-search call wired in implementation.
        # No secrets in prompts — only structured team/fixture names.
        return ResearchReport(
            fixture_id=fixture_id,
            home_team=home_team,
            away_team=away_team,
            sources=[],
        )
