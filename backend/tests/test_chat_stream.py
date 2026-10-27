"""Streaming chat: SSE parsing, tool-call assembly, fallbacks, endpoint."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from pred_agent.llm.chat import ChatLLM, Tool


def _sse(*chunks: dict[str, Any] | str) -> httpx.Response:
    lines = []
    for c in chunks:
        lines.append("data: " + (c if isinstance(c, str) else json.dumps(c)) + "\n\n")
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join(lines).encode())


def _d(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {"choices": [{"delta": delta}]}


def _llm(handler: Any, tools: list[Tool] | None = None) -> ChatLLM:
    return ChatLLM(api_key="k", model="m", base_url="http://llm.test/v1", tools=tools or [],
                   transport=httpx.MockTransport(handler))


def _collect(llm: ChatLLM, msg: str = "hi") -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        return [e async for e in llm.chat_stream([], msg)]
    return asyncio.run(run())


def test_plain_text_streams_in_order() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert json.loads(req.content)["stream"] is True
        return _sse(_d("Hel"), _d("lo "), _d("world"), "[DONE]")

    events = _collect(_llm(handler))
    assert [e["text"] for e in events if e["type"] == "delta"] == ["Hel", "lo ", "world"]
    assert events[-1] == {"type": "done", "reply": "Hello world", "cards": [], "error": None}


def test_tool_call_deltas_are_assembled_and_executed() -> None:
    calls: list[str] = []

    async def fn(value: str = "") -> dict[str, Any]:
        calls.append(value)
        return {"echo": value}

    tool = Tool("echo", "echo", {"type": "object", "properties": {"value": {"type": "string"}}}, fn)
    n = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] == 1:
            return _sse(
                _d(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "ec", "arguments": ""}}]),
                _d(tool_calls=[{"index": 0, "function": {"name": "ho", "arguments": '{"val'}}]),
                _d(tool_calls=[{"index": 0, "function": {"arguments": 'ue":"x"}'}}]),
                "[DONE]",
            )
        body = json.loads(req.content)
        assert any(m["role"] == "tool" and m["content"].startswith("[DATA]") for m in body["messages"])
        return _sse(_d("done"), "[DONE]")

    events = _collect(_llm(handler, [tool]))
    assert calls == ["x"]
    assert {"type": "tool", "name": "echo"} in events
    assert events[-1]["reply"] == "done"


def test_non_streaming_provider_fallback() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "plain"}}]})

    events = _collect(_llm(handler))
    assert [e["text"] for e in events if e["type"] == "delta"] == ["plain"]
    assert events[-1]["reply"] == "plain"


def test_temperature_rejected_then_retried() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "temperature unsupported"}})
        return _sse(_d("ok"), "[DONE]")

    events = _collect(_llm(handler))
    assert len(seen) == 2 and "temperature" not in seen[1]
    assert events[-1]["reply"] == "ok"


def test_http_error_yields_done_with_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    events = _collect(_llm(handler))
    assert events[-1]["type"] == "done" and events[-1]["error"] == "llm_error"


def test_empty_stream_gives_fallback_reply() -> None:
    events = _collect(_llm(lambda req: _sse("[DONE]")))
    assert events[-1]["type"] == "done" and events[-1]["reply"]


# ---------------------------------------------------------------------------
# Endpoint: POST /v1/chat/stream
# ---------------------------------------------------------------------------

def _paired_client():
    from fastapi.testclient import TestClient

    from pred_agent.control_api.app import create_app

    client = TestClient(create_app())
    code = client.app.state.auth.generate_pairing_code("t")
    token = client.post("/v1/pair", json={"code": code, "device_name": "t",
                                          "device_public_key": "k"}).json()["data"]["device_token"]
    return client, {"Authorization": f"Bearer {token}"}


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for frame in text.strip().split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        out.append((ev, json.loads(data)))
    return out


def test_stream_endpoint_requires_auth() -> None:
    client, _ = _paired_client()
    assert client.post("/v1/chat/stream", json={"message": "hi"}).status_code == 401


def test_stream_endpoint_validates_message() -> None:
    client, h = _paired_client()
    assert client.post("/v1/chat/stream", json={"message": "  "}, headers=h).status_code == 400
    assert client.post("/v1/chat/stream", json={"message": "x" * 4001}, headers=h).status_code == 400


def test_stream_endpoint_unconfigured_llm_returns_done_event() -> None:
    client, h = _paired_client()
    r = client.post("/v1/chat/stream", json={"message": "hi"}, headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[0][0] == "session"
    assert events[-1][0] == "done"
    assert events[-1][1]["error"] in ("llm_not_configured", None)
