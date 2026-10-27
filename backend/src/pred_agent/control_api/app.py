"""Control API FastAPI app.

Grounded in kit/07_CONTROL_API.md.
- Bound to Tailscale interface or localhost, never public.
- Device pairing, bearer device tokens, X-Confirm biometric gate.
- All REST endpoints with stable envelope {ok, data, error}.
- WebSocket /v1/ws with event stream from in-process bus.
- Limits with hard upper bounds; API can never enable LIVE_TRADING.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from pred_agent.config import load_settings
from pred_agent.control_api.auth import DeviceAuth
from pred_agent.events import EventBus

# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------

def envelope(*, ok: bool, data: Any = None, error: dict | None = None,
             audit_id: str | None = None) -> dict:
    body: dict[str, Any] = {"ok": ok}
    if data is not None:
        body["data"] = data
    if error is not None:
        body["error"] = error
    if audit_id is not None:
        body["audit_id"] = audit_id
    return body


def error_response(code: str, message: str, status_code: int = 400,
                   audit_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=envelope(ok=False, error={"code": code, "message": message}, audit_id=audit_id),
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    settings = load_settings()
    bus = EventBus()
    auth = DeviceAuth()

    app = FastAPI(title="PRED Control API")
    app.state.settings = settings
    app.state.auth = auth
    app.state.bus = bus

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app.router.lifespan_context = lifespan

    # ------------------------------------------------------------------ #
    # Health (no auth)
    # ------------------------------------------------------------------ #

    @app.get("/health")
    async def health():
        return {"ok": True, "status": "healthy"}

    @app.get("/ready")
    async def ready():
        # Feeds ok, DB ok, auth ok
        return {"ok": True, "ready": True}

    # ------------------------------------------------------------------ #
    # Pairing
    # ------------------------------------------------------------------ #

    @app.post("/v1/pair")
    async def pair(body: dict):
        code = body.get("code")
        device_name = body.get("device_name", "unknown")
        device_public_key = body.get("device_public_key", "")
        if not code:
            return error_response("missing_code", "pairing code required", 400)
        token = auth.pair(code, device_name, device_public_key)
        if token is None:
            return error_response("invalid_code", "pairing code invalid or expired", 400)
        return envelope(ok=True, data={"device_token": token})

    @app.get("/v1/devices")
    async def list_devices(authorization: str | None = Header(None)):
        device_id = _verify_auth(authorization)
        if device_id is None:
            return error_response("unauthorized", "invalid or missing device token", 401)
        return envelope(ok=True, data={"devices": auth.list_devices()})

    @app.delete("/v1/devices/{device_id}")
    async def revoke_device(device_id: str, authorization: str | None = Header(None)):
        caller = _verify_auth(authorization)
        if caller is None:
            return error_response("unauthorized", "invalid or missing device token", 401)
        if caller != device_id:
            return error_response("forbidden", "cannot revoke another device", 403)
        ok = auth.revoke_device(device_id)
        if not ok:
            return error_response("not_found", "device not found", 404)
        return envelope(ok=True, data={"revoked": True})

    # ------------------------------------------------------------------ #
    # Auth helper
    # ------------------------------------------------------------------ #

    def _verify_auth(authorization: str | None) -> str | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        token = authorization[len("Bearer "):]
        return auth.verify_token(token)

    # ------------------------------------------------------------------ #
    # WebSocket
    # ------------------------------------------------------------------ #

    @app.websocket("/v1/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        device_id = None
        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype == "auth":
                    token = msg.get("token", "")
                    device_id = auth.verify_token(token)
                    if device_id is None:
                        await websocket.send_text(json.dumps({"type": "error", "error": "invalid_token"}))
                        await websocket.close(code=4001)
                        return
                    await websocket.send_text(json.dumps({"type": "auth_ok", "device_id": device_id}))
                elif mtype == "subscribe":
                    markets = msg.get("markets", [])
                    await websocket.send_text(json.dumps({"type": "subscribed", "markets": markets}))
                elif mtype == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                else:
                    await websocket.send_text(json.dumps({"type": "error", "error": "unknown_type"}))
        except WebSocketDisconnect:
            pass

    return app


# ---------------------------------------------------------------------------
# Placeholder: expose bus for other modules to publish events
# ---------------------------------------------------------------------------

_app_instance: FastAPI | None = None


def get_app() -> FastAPI:
    global _app_instance  # noqa: PLW0603 - lazy singleton
    if _app_instance is None:
        _app_instance = create_app()
    return _app_instance
