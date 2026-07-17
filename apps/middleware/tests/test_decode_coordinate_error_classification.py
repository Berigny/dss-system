from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

import app as app_module
from api.client import BackendDecodeError


client = TestClient(app_module.app)


def test_decode_coordinate_returns_403_on_authority_error(monkeypatch):
    async def fake_decode_coordinate(_coord: str, **kwargs):
        raise BackendDecodeError(
            status_code=403,
            body={"error": "surface_not_bound_to_ledger", "surface_id": "s1", "ledger_id": "loam"},
        )

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 403
    body = resp.json()
    assert body.get("error") == "surface_not_bound_to_ledger"
    assert "X-Decode-Diagnostics" in resp.headers


def test_decode_coordinate_returns_400_on_backend_client_error(monkeypatch):
    async def fake_decode_coordinate(_coord: str, **kwargs):
        raise BackendDecodeError(
            status_code=400,
            body={"status": "error", "error_code": "ledger_scope_mismatch", "detail": {}},
        )

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("error_code") == "ledger_scope_mismatch"
    assert "X-Decode-Diagnostics" in resp.headers


def test_decode_coordinate_returns_503_on_backend_database_locked(monkeypatch):
    async def fake_decode_coordinate(_coord: str, **kwargs):
        raise BackendDecodeError(
            status_code=503,
            body={"status": "error", "error_code": "library_database_locked", "detail": "locked"},
        )

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 503
    body = resp.json()
    assert body.get("error_code") == "library_database_locked"


def test_decode_coordinate_returns_502_on_transport_error(monkeypatch):
    async def fake_decode_coordinate(_coord: str, **kwargs):
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", "http://backend"))

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 502
    body = resp.json()
    assert body.get("error_code") == "upstream_unavailable"
    assert "connection refused" in body.get("detail", "")


def test_decode_coordinate_forwards_ledger_and_surface_in_request(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_decode_coordinate(_coord: str, *, auth_headers=None, **kwargs):
        captured["auth_headers"] = auth_headers
        return {"coord": "loam:WX-1"}

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post(
        "/api/decode_coordinate",
        json={"coordinate": "loam:WX-1", "ledger_id": "loam", "surface_id": "surface:chat:primary"},
    )
    assert resp.status_code == 200
    auth_headers = captured.get("auth_headers") or {}
    assert auth_headers.get("x-surface-id") == "surface:chat:primary"
