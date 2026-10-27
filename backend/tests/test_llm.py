"""OpenAI-compatible LLM client: config, parsing, fail-closed behaviour (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from pred_agent.config import load_settings
from pred_agent.control_api.app import create_app
from pred_agent.llm import DummyLLMClient, LLMRequest, OpenAICompatClient, build_llm_client

SCHEMA = {"type": "object", "required": ["adj"], "properties": {"adj": {"type": "number"}}}
REQ = LLMRequest(system_prompt="s", user_prompt="u", response_schema=SCHEMA)


def _client(handler, **kw):
    return OpenAICompatClient(
        api_key="k", model="m", base_url="https://x.test/v1/",
        transport=httpx.MockTransport(handler), **kw,
    )


def _reply(args):
    """Provider reply carrying a tool call. ``args`` is the raw arguments string."""
    call = {"id": "c1", "type": "function", "function": {"name": "respond", "arguments": args}}
    return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [call]}}]})


@pytest.mark.asyncio
async def test_valid_response_and_request_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _reply('{"adj": 1.5}')

    res = await _client(handler).complete(REQ)
    assert res.valid and res.parsed == {"adj": 1.5}
    assert seen["url"] == "https://x.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k" and seen["body"]["model"] == "m"
    body = seen["body"]
    assert "response_format" not in body
    assert body["tools"][0]["function"]["name"] == "respond"
    assert body["tools"][0]["function"]["parameters"] == SCHEMA
    assert body["tool_choice"]["function"]["name"] == "respond"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["not json", '{"adj": "high"}', "{}", "[1]", ""])
async def test_invalid_output_fails_closed(text):
    res = await _client(lambda r: _reply(text)).complete(REQ)
    assert res.valid is False and res.parsed is None


@pytest.mark.asyncio
async def test_plain_text_json_is_not_accepted():
    """No JSON fallback: a text reply without a tool call is invalid."""
    r = httpx.Response(200, json={"choices": [{"message": {"content": '{"adj": 2}'}}]})
    res = await _client(lambda _r: r).complete(REQ)
    assert res.valid is False and res.parsed is None and "tool" in res.error


@pytest.mark.asyncio
async def test_http_error_fails_closed():
    res = await _client(lambda r: httpx.Response(401, json={"error": "bad key"})).complete(REQ)
    assert res.valid is False


@pytest.mark.asyncio
async def test_tools_unsupported_fails_closed_without_json_fallback():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(sorted(k for k in ("tools", "response_format") if k in body))
        return httpx.Response(400, text="tools are not supported by this model")

    res = await _client(handler).complete(REQ)
    assert res.valid is False
    assert calls == [["tools"]]  # single attempt, never retried in JSON mode


@pytest.mark.asyncio
async def test_no_token_limit_and_unlimited_calls():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply('{"adj": 1}')

    c = _client(handler)
    for _ in range(300):
        assert (await c.complete(REQ)).valid
    assert all("max_tokens" not in b for b in bodies)


def test_base_url_normalized():
    c = OpenAICompatClient(api_key="k", model="m", base_url="https://h/v1/chat/completions")
    assert c.base_url == "https://h/v1"


def test_factory(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert isinstance(build_llm_client(load_settings()), DummyLLMClient)
    monkeypatch.setenv("LLM_API_KEY", "key")
    assert isinstance(build_llm_client(load_settings()), DummyLLMClient)  # model missing
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    c = build_llm_client(load_settings())
    assert isinstance(c, OpenAICompatClient) and c.base_url == "https://openrouter.ai/api/v1"


def test_llm_endpoints_hide_key(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("LLM_API_KEY", "sk-supersecret")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    c = TestClient(create_app())
    assert c.get("/v1/llm").status_code == 401
    tok = c.post("/v1/login", json={"username": "admin", "password": "pw"}).json()["data"]["token"]
    r = c.get("/v1/llm", headers={"Authorization": f"Bearer {tok}"})
    assert r.json()["data"]["configured"] is True
    assert "sk-supersecret" not in r.text


@pytest.mark.asyncio
async def test_error_reason_reported():
    res = await _client(lambda r: httpx.Response(404, json={"error": {"message": "model not found"}})).complete(REQ)
    assert res.valid is False and "404" in res.error and "model not found" in res.error


@pytest.mark.asyncio
async def test_no_tool_call_reports_finish_reason():
    r = httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    res = await _client(lambda _r: r).complete(REQ)
    assert res.valid is False and "length" in res.error


@pytest.mark.asyncio
async def test_reasoning_content_is_not_used_as_fallback():
    r = httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning_content": '{"adj": 3}'}}]})
    res = await _client(lambda _r: r).complete(REQ)
    assert res.valid is False


@pytest.mark.asyncio
async def test_retries_without_temperature():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append("temperature" in body)
        if "temperature" in body:
            return httpx.Response(400, text="unsupported value: temperature")
        return _reply('{"adj": 0}')

    res = await _client(handler).complete(REQ)
    assert res.valid and calls == [True, False]
