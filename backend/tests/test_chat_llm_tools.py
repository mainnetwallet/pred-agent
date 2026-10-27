"""ChatLLM tool-calling loop: provider quirks and fail-safe behaviour."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import httpx

from pred_agent.llm.chat import ChatLLM, Tool


def _tc(name: str, args: Any, call_id: str = "call_1") -> dict[str, Any]:
    raw = args if isinstance(args, str) else json.dumps(args)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}


def _resp(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None,
          finish: str = "stop") -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": finish}]})


def _llm(handler: Any, tools: list[Tool]) -> ChatLLM:
    return ChatLLM(api_key="k", model="m", base_url="http://llm.test/v1", tools=tools,
                   transport=httpx.MockTransport(handler))


def _echo_tool(calls: list[dict[str, Any]]) -> Tool:
    async def fn(value: str = "") -> dict[str, Any]:
        calls.append({"value": value})
        return {"echo": value, "amount": Decimal("1.5")}   # Decimal must not break JSON

    return Tool("echo", "echo", {"type": "object", "properties": {"value": {"type": "string"}},
                                 "required": []}, fn)


def _run(llm: ChatLLM, msg: str = "hi"):
    return asyncio.run(llm.chat([], msg))


def test_tool_call_roundtrip() -> None:
    calls: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        if len(seen) == 1:
            assert body["tools"][0]["function"]["name"] == "echo"
            return _resp(tool_calls=[_tc("echo", {"value": "x"})], finish="tool_calls")
        tool_msg = [m for m in body["messages"] if m["role"] == "tool"][0]
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"].startswith("[DATA]")
        return _resp("done")

    out = _run(_llm(handler, [_echo_tool(calls)]))
    assert out.reply == "done" and calls == [{"value": "x"}]


def test_tool_calls_with_finish_reason_stop_are_still_executed() -> None:
    """Several OpenAI-compatible providers return finish_reason='stop' alongside tool_calls."""
    calls: list[dict[str, Any]] = []
    n = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] == 1:
            return _resp(tool_calls=[_tc("echo", {"value": "y"})], finish="stop")
        return _resp("final")

    out = _run(_llm(handler, [_echo_tool(calls)]))
    assert calls == [{"value": "y"}]
    assert out.reply == "final"


def test_malformed_arguments_are_reported_to_model_not_executed() -> None:
    calls: list[dict[str, Any]] = []
    n = {"i": 0}
    tool_payloads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] == 1:
            return _resp(tool_calls=[_tc("echo", "{not json")], finish="tool_calls")
        tool_payloads.extend(m["content"] for m in json.loads(req.content)["messages"]
                             if m["role"] == "tool")
        return _resp("ok")

    _run(_llm(handler, [_echo_tool(calls)]))
    assert calls == []
    assert "invalid_arguments" in tool_payloads[0]


def test_unexpected_argument_names_return_error_not_crash() -> None:
    calls: list[dict[str, Any]] = []
    n = {"i": 0}
    payloads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] == 1:
            return _resp(tool_calls=[_tc("echo", {"bogus": 1})], finish="tool_calls")
        payloads.extend(m["content"] for m in json.loads(req.content)["messages"]
                        if m["role"] == "tool")
        return _resp("ok")

    out = _run(_llm(handler, [_echo_tool(calls)]))
    assert out.reply == "ok" and "invalid_arguments" in payloads[0]


def test_temperature_rejected_by_provider_is_dropped_and_retried() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {
                "message": "Unsupported value: 'temperature' does not support 0.2 with this model."}})
        return _resp("hello")

    out = _run(_llm(handler, []))
    assert out.reply == "hello" and out.error is None
    assert "temperature" not in bodies[-1]


def test_no_default_httpx_timeout(monkeypatch: Any) -> None:
    """httpx defaults to a 5s timeout, which kills slow multi-step tool loops."""
    seen: dict[str, Any] = {}
    real = httpx.AsyncClient

    def spy(*a: Any, **kw: Any) -> httpx.AsyncClient:
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", spy)
    _run(_llm(lambda r: _resp("hi"), []))
    assert "timeout" in seen and seen["timeout"] is None


def test_empty_final_reply_gets_fallback_text() -> None:
    out = _run(_llm(lambda r: _resp(None), []))
    assert out.reply.strip() != ""


def test_tool_loop_has_no_iteration_cap() -> None:
    calls: list[dict[str, Any]] = []
    n = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] <= 30:
            return _resp(tool_calls=[_tc("echo", {"value": str(n["i"])}, f"c{n['i']}")],
                         finish="tool_calls")
        return _resp("all done")

    out = _run(_llm(handler, [_echo_tool(calls)]))
    assert len(calls) == 30 and out.reply == "all done"
