"""OpenAI-compatible chat-completions client.

Works with any provider that speaks ``POST {base_url}/chat/completions``
(OpenAI, OpenRouter, DeepSeek, Groq, Together, local vLLM/Ollama, ...).
Configured only from env: ``LLM_API_KEY``, ``LLM_MODEL``, ``LLM_BASE_URL``.

Fail closed: any HTTP error, bad JSON, or schema mismatch returns
``valid=False`` and never raises, so callers apply adjustment 0.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from pred_agent.config import Settings

from .client import DummyLLMClient, LLMClient, LLMRequest, LLMResponse

log = logging.getLogger(__name__)

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _normalize_base(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    return url


def _validate(data: Any, schema: dict[str, Any]) -> bool:
    """Minimal JSON-Schema check: top-level object, required keys, property types."""
    if not isinstance(data, dict):
        return False
    if any(k not in data for k in schema.get("required", [])):
        return False
    for key, spec in (schema.get("properties") or {}).items():
        if key not in data:
            continue
        expected = _TYPES.get(spec.get("type", ""))
        value = data[key]
        if expected and (not isinstance(value, expected) or (
            isinstance(value, bool) and bool not in expected
        )):
            return False
    return True


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        daily_max_calls: int = 200,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.base_url = _normalize_base(base_url)
        self._daily_max = daily_max_calls
        self._timeout = timeout
        self._transport = transport
        self._day = time.strftime("%Y-%m-%d", time.gmtime())
        self._calls = 0

    def _budget_ok(self) -> bool:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day:
            self._day, self._calls = today, 0
        if self._calls >= self._daily_max:
            return False
        self._calls += 1
        return True

    async def _post(self, client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
        return await client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=body,
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if not self._budget_ok():
            log.warning("llm daily call cap reached")
            return LLMResponse(content="", valid=False)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                resp = await self._post(c, body)
                if resp.status_code == 400 and "response_format" in resp.text:
                    body.pop("response_format")  # provider has no JSON mode
                    resp = await self._post(c, body)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            # Log the exception type only: never the request (contains no secrets, but be safe).
            log.warning("llm call failed: %s", type(exc).__name__)
            return LLMResponse(content="", valid=False)
        try:
            parsed = _extract_json(content)
        except json.JSONDecodeError:
            return LLMResponse(content=content, valid=False)
        if not _validate(parsed, request.response_schema):
            return LLMResponse(content=content, valid=False)
        return LLMResponse(content=content, parsed=parsed, valid=True)


def build_llm_client(settings: Settings) -> LLMClient:
    """Real client when LLM_API_KEY and LLM_MODEL are set, otherwise the no-op client."""
    if settings.llm_api_key and settings.llm_model:
        return OpenAICompatClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            daily_max_calls=settings.llm_daily_max_calls,
        )
    return DummyLLMClient()
