"""Phase 5 unit tests: Control API — pairing, auth, endpoints, WebSocket, secrets scan.

No network calls — uses TestClient.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from pred_agent.control_api.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def _get_auth(client: TestClient):
    return client.app.state.auth


# ---------------------------------------------------------------------------
# Health / ready
# ---------------------------------------------------------------------------

def test_health_head(client):
    # UptimeRobot (free plan) probes with HEAD; it must not 405.
    assert client.head("/health").status_code == 200


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_head_root_is_ok(client):
    """Render's probe sends HEAD / and used to get 405."""
    assert client.head("/").status_code == 200
    assert client.get("/").status_code == 200


def test_ready(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

def test_pair_and_devices(client):
    auth = _get_auth(client)
    code = auth.generate_pairing_code("test-phone")

    # Pair via API
    resp = client.post("/v1/pair", json={"code": code, "device_name": "test-phone", "device_public_key": "pubkey"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    token = data["data"]["device_token"]

    # List devices with token
    resp = client.get("/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    devices = resp.json()["data"]["devices"]
    assert len(devices) >= 1


def test_pair_invalid_code(client):
    resp = client.post("/v1/pair", json={"code": "invalid-code", "device_name": "x"})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_pair_missing_code(client):
    resp = client.post("/v1/pair", json={"device_name": "x"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth: unauthenticated rejected
# ---------------------------------------------------------------------------

def test_unauthenticated_rejected(client):
    resp = client.get("/v1/devices")
    assert resp.status_code == 401
    assert resp.json()["ok"] is False


def test_revoke_own_device(client):
    auth = _get_auth(client)
    code = auth.generate_pairing_code("revoke-test")
    resp = client.post("/v1/pair", json={"code": code, "device_name": "revoke-test"})
    token = resp.json()["data"]["device_token"]

    # Find device id
    resp = client.get("/v1/devices", headers={"Authorization": f"Bearer {token}"})
    device_id = resp.json()["data"]["devices"][0]["id"]

    # Revoke own device
    resp = client.delete(f"/v1/devices/{device_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["data"]["revoked"] is True

    # Token should no longer work
    resp = client.get("/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_revoke_forbidden(client):
    auth = _get_auth(client)
    code1 = auth.generate_pairing_code("device-a")
    code2 = auth.generate_pairing_code("device-b")
    r1 = client.post("/v1/pair", json={"code": code1, "device_name": "a"})
    r2 = client.post("/v1/pair", json={"code": code2, "device_name": "b"})
    token_a = r1.json()["data"]["device_token"]

    # Get device B's id (find by name since list_devices returns all devices)
    resp = client.get("/v1/devices", headers={"Authorization": f"Bearer {r2.json()['data']['device_token']}"})
    device_b_id = None
    for d in resp.json()["data"]["devices"]:
        if d["name"] == "b":
            device_b_id = d["id"]
            break
    assert device_b_id is not None

    # Try revoking device B with token A -> forbidden
    resp = client.delete(f"/v1/devices/{device_b_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Secrets scan: no response contains secrets
# ---------------------------------------------------------------------------

def test_no_secrets_in_responses(client):
    """Canary scan: none of the API responses should contain secret patterns."""
    canary = "0xdeadbeef" + "01" * 30
    patterns = [canary, "PRIVATE_KEY", "PRED_API_KEY", "LLM_API_KEY", "TELEGRAM_BOT_TOKEN"]

    endpoints = [
        ("GET", "/health"),
        ("GET", "/ready"),
    ]
    for method, path in endpoints:
        resp = client.request(method, path)
        body = resp.text
        for pat in patterns:
            assert pat not in body, f"Secret pattern {pat!r} found in {path} response"


# ---------------------------------------------------------------------------
# WebSocket connect (auth required)
# ---------------------------------------------------------------------------

def test_websocket_requires_auth(client):
    with client.websocket_connect("/v1/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "invalid-token"}))
        resp = ws.receive_text()
        assert "invalid_token" in resp


def test_websocket_auth_then_ping(client):
    auth = _get_auth(client)
    code = auth.generate_pairing_code("ws-test")
    resp = client.post("/v1/pair", json={"code": code, "device_name": "ws-test"})
    token = resp.json()["data"]["device_token"]

    with client.websocket_connect("/v1/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        resp = ws.receive_text()
        # Should not be an error; connection stays open
        assert "invalid_token" not in resp
        ws.send_text(json.dumps({"type": "ping"}))
        resp = ws.receive_text()
        assert "pong" in resp
