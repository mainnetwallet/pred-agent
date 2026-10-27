"""OpenAI-compatible chat-completions client.

Works with any provider that speaks ``POST {base_url}/chat/completions``
(OpenAI, OpenRouter, DeepSeek, Groq, Together, local vLLM/Ollama, ...).
Configured only from env: ``LLM_API_KEY``, ``LLM_MODEL``, ``LLM_BASE_URL``.

Structured output is obtained through tool calling only: the request schema is
exposed as a single function (``respond``) and the model is forced to call it.
There is no JSON-mode / free-text JSON fallback. A provider or model that does
not support tool calling yields ``valid=False``.

Fail closed: any HTTP error, missing tool call, or schema mismatch returns
``valid=False`` and never raises, so callers apply adjustment 0.
"""

from __future__ import annotations

import json
import logging
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


_TOOL_NAME = "respond"


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


def _provider_error(resp: httpx.Response) -> str:
    """Short provider error message (never includes request headers/keys)."""
    try:
        err = resp.json().get("error")
        text = err.get("message") if isinstance(err, dict) else err
    except (ValueError, AttributeError):
        text = None
    return str(text or resp.text or resp.reason_phrase)[:200]


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float | None = None,  # None = no timeout
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.base_url = _normalize_base(base_url)
        self._timeout = timeout
        self._transport = transport

    async def _post(self, client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
        return await client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=body,
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "tools": [{
                "type": "function",
                "function": {
                    "name": _TOOL_NAME,
                    "description": "Return the structured answer by calling this function.",
                    "parameters": request.response_schema,
                },
            }],
            "tool_choice": {"type": "function", "function": {"name": _TOOL_NAME}},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                resp = await self._post(c, body)
                # Some providers reject optional params: drop them one at a time and retry.
                for param in ("temperature",):
                    if resp.status_code in (400, 422) and param in resp.text and param in body:
                        body.pop(param)
                        resp = await self._post(c, body)
            if resp.status_code >= 400:
                detail = _provider_error(resp)
                log.warning("llm http %s: %s", resp.status_code, detail)
                return LLMResponse(content="", valid=False,
                                   error=f"HTTP {resp.status_code}: {detail}")
            choice = resp.json()["choices"][0]
            tool_calls = choice["message"].get("tool_calls") or []
            call = next(
                (tc for tc in tool_calls if tc.get("function", {}).get("name") == _TOOL_NAME),
                None,
            )
            if call is None:
                reason = choice.get("finish_reason")
                log.warning("llm returned no tool call, finish_reason=%s", reason)
                return LLMResponse(content=choice["message"].get("content") or "", valid=False,
                                   error=f"model did not call the tool (tool calling unsupported?), finish_reason={reason}")
            content = call["function"].get("arguments") or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            # Log the exception type only: never the request (contains no secrets, but be safe).
            log.warning("llm call failed: %s", type(exc).__name__)
            return LLMResponse(content="", valid=False, error=f"{type(exc).__name__} calling {self.base_url}")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return LLMResponse(content=content, valid=False, error="tool arguments were not valid JSON")
        if not _validate(parsed, request.response_schema):
            return LLMResponse(content=content, valid=False, error="reply did not match schema")
        return LLMResponse(content=content, parsed=parsed, valid=True)


def build_llm_client(settings: Settings) -> LLMClient:
    """Real client when LLM_API_KEY and LLM_MODEL are set, otherwise the no-op client."""
    if settings.llm_api_key and settings.llm_model:
        return OpenAICompatClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
        )
    return DummyLLMClient()
