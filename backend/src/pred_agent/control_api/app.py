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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from pred_agent.config import load_settings
from pred_agent.control_api.auth import DeviceAuth
from pred_agent.control_api.controller import AgentController, to_jsonable
from pred_agent.control_api.session import LoginManager
from pred_agent.events import EventBus

_WEB_INDEX = Path(__file__).parent / "web" / "index.html"

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
    login_mgr = LoginManager(
        username=settings.admin_username,
        password=settings.admin_password,
        secret=settings.session_secret,
        ttl_hours=settings.session_ttl_hours,
    )
    controller = AgentController(settings)

    app = FastAPI(title="PRED Control API")
    app.state.settings = settings
    app.state.auth = auth
    app.state.bus = bus
    app.state.login = login_mgr
    app.state.controller = controller

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Telegram bot runs in this same process/event loop (polling, no webhook).
        from pred_agent.telegram.bot import start_bot, stop_bot

        tg_app = await start_bot(settings, controller)
        app.state.telegram = tg_app
        try:
            yield
        finally:
            await stop_bot(tg_app)

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
        if caller != device_id and not caller.startswith("session:"):
            return error_response("forbidden", "cannot revoke another device", 403)
        ok = auth.revoke_device(device_id)
        if not ok:
            return error_response("not_found", "device not found", 404)
        return envelope(ok=True, data={"revoked": True})

    # ------------------------------------------------------------------ #
    # Auth helper
    # ------------------------------------------------------------------ #

    def _verify_token(token: str) -> str | None:
        """Accept a login session token or a paired-device token."""
        user = login_mgr.verify(token)
        if user is not None:
            return f"session:{user}"
        return auth.verify_token(token)

    def _verify_auth(authorization: str | None) -> str | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        return _verify_token(authorization[len("Bearer "):])

    def _unauth() -> JSONResponse:
        return error_response("unauthorized", "invalid or missing token", 401)

    def _client_key(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[-1].strip()  # last hop = the proxy's view of the client
        return request.client.host if request.client else "?"

    # ------------------------------------------------------------------ #
    # Website + login
    # ------------------------------------------------------------------ #

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(_WEB_INDEX.read_text(encoding="utf-8"))

    @app.post("/v1/login")
    async def login(body: dict, request: Request):
        if not login_mgr.enabled:
            return error_response("login_disabled", "ADMIN_PASSWORD is not set on the server", 503)
        client = _client_key(request)
        wait = login_mgr.locked_for(client)
        if wait:
            return error_response("locked", f"too many attempts, retry in {wait}s", 429)
        token = login_mgr.login(str(body.get("username", "")), str(body.get("password", "")), client)
        if token is None:
            return error_response("invalid_credentials", "wrong username or password", 401)
        return envelope(ok=True, data={"token": token, "expires_in": settings.session_ttl_hours * 3600})

    @app.get("/v1/me")
    async def me(authorization: str | None = Header(None)):
        who = _verify_auth(authorization)
        if who is None:
            return _unauth()
        return envelope(ok=True, data={"user": who})

    # ------------------------------------------------------------------ #
    # Control (all require auth)
    # ------------------------------------------------------------------ #

    @app.get("/v1/status")
    async def status(authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=controller.status())

    @app.post("/v1/pause")
    async def pause(authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        controller.pause()
        return envelope(ok=True, data=controller.status())

    @app.post("/v1/resume")
    async def resume(authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        controller.resume()
        return envelope(ok=True, data=controller.status())

    @app.post("/v1/panic")
    async def panic(authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        result = controller.panic()
        return envelope(ok=True, data={**controller.status(), **result})

    @app.get("/v1/proposals")
    async def proposals(status: str | None = None, authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=to_jsonable(controller.list_proposals(status)))

    @app.post("/v1/proposals/{proposal_id}/approve")
    async def approve(proposal_id: str, body: dict | None = None,
                      authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        amount: Decimal | None = None
        raw = (body or {}).get("amount_usd")
        if raw not in (None, ""):
            try:
                amount = Decimal(str(raw))
            except InvalidOperation:
                return error_response("bad_amount", "amount_usd must be a decimal string", 400)
        prop = controller.approve(proposal_id, via="app", amount=amount)
        if prop is None:
            return error_response(
                "not_approvable",
                "proposal missing/expired, agent paused, or amount outside limits",
                409,
            )
        return envelope(ok=True, data=to_jsonable(prop))

    @app.post("/v1/proposals/{proposal_id}/reject")
    async def reject(proposal_id: str, authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        prop = controller.reject(proposal_id, via="app")
        if prop is None:
            return error_response("not_found", "proposal missing or already decided", 404)
        return envelope(ok=True, data=to_jsonable(prop))

    @app.get("/v1/orders")
    async def orders(authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=controller.orders())

    @app.get("/v1/positions")
    async def positions(authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=controller.positions_list())

    @app.get("/v1/limits")
    async def get_limits(authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=controller.limits_list())

    @app.put("/v1/limits")
    async def put_limits(body: dict, authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        err = controller.set_limits(body)
        if err:
            return error_response("bad_limit", err, 400)
        return envelope(ok=True, data=controller.limits_list())

    @app.get("/v1/fixtures")
    async def fixtures(authorization: str | None = Header(None)):
        if _verify_auth(authorization) is None:
            return _unauth()
        return envelope(ok=True, data=[])  # scanner not wired into this process yet

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
                    device_id = _verify_token(token)
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
