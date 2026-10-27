"""LLM client interface.

Grounded in kit/03_ARCHITECTURE.md (LLM access through an LLMClient interface) and
kit/05_STRATEGY_AND_RISK.md section 1 (LLM adjustment).

Rules:
- Provider and model come from config; no hardcoded model names.
- Structured JSON output with schema validation.
- Daily budget cap.
- No secrets ever in prompts.
- Invalid LLM output means adjustment 0 (fail closed).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMRequest:
    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any]  # JSON Schema for the expected output
    temperature: float = 0.0
    max_tokens: int | None = None  # None = no cap; the provider decides


@dataclass
class LLMResponse:
    content: str
    parsed: dict[str, Any] | None = None  # schema-validated JSON, or None if invalid
    valid: bool = False
    error: str | None = None  # short, secret-free reason when valid=False


class LLMClient(ABC):
    """Abstract LLM client. Implementations call the provider's API."""

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return a schema-validated LLMResponse. Never raises on invalid JSON —
        returns valid=False instead so callers can fail closed."""


class DummyLLMClient(LLMClient):
    """No-op client used when no LLM_API_KEY is configured. Returns no adjustment."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content="{}",
            parsed={},
            valid=True,
        )
