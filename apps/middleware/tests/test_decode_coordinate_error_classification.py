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
            body={"status": "error", "error_code": "surface_not_bound_to_ledger", "detail": {"surface_id": "s1", "ledger_id": "loam"}},
        )

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 403
    body = resp.json()
    assert body.get("error_code") == "surface_not_bound_to_ledger"
    assert "X-Decode-Diagnostics" in resp.headers


def test_decode_coordinate_returns_403_on_decode_requires_authenticated_principal(monkeypatch):
    async def fake_decode_coordinate(_coord: str, **kwargs):
        raise BackendDecodeError(
            status_code=403,
            body={
                "status": "error",
                "error_code": "decode_requires_authenticated_principal",
                "detail": {
                    "error": "decode_requires_authenticated_principal",
                    "surface_id": "surface:coord-demo",
                    "reason": "Decode through a surface requires an authenticated principal.",
                },
            },
        )

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 403
    body = resp.json()
    assert body.get("error_code") == "decode_requires_authenticated_principal"
    assert "error" not in body.get("detail", {})
    assert body.get("detail", {}).get("surface_id") == "surface:coord-demo"
    assert "X-Decode-Diagnostics" in resp.headers


def test_decode_coordinate_returns_401_on_token_validation_failed(monkeypatch):
    async def fake_decode_coordinate(_coord: str, **kwargs):
        raise BackendDecodeError(
            status_code=401,
            body={
                "status": "error",
                "error_code": "token_validation_failed",
                "detail": {"error": "token_validation_failed", "reason": "token_expired"},
            },
        )

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 401
    body = resp.json()
    assert body.get("error_code") == "token_validation_failed"
    assert "error" not in body.get("detail", {})
    assert body.get("detail", {}).get("reason") == "token_expired"
    assert "X-Decode-Diagnostics" in resp.headers


def test_decode_coordinate_returns_400_on_backend_client_error(monkeypatch):
    async def fake_decode_coordinate(_coord: str, **kwargs):
        raise BackendDecodeError(
            status_code=400,
            body={"status": "error", "error_code": "ledger_scope_mismatch", "detail": {"error": "ledger_scope_mismatch"}},
        )

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("error_code") == "ledger_scope_mismatch"
    assert "error" not in body.get("detail", {})
    assert "X-Decode-Diagnostics" in resp.headers


def test_decode_coordinate_flattens_double_nested_ledger_scope_mismatch(monkeypatch):
    """DSS-282: legacy backend bodies can be double-nested; ensure the response is flat."""
    async def fake_decode_coordinate(_coord: str, **kwargs):
        raise BackendDecodeError(
            status_code=400,
            body={
                "status": "error",
                "error_code": "ledger_scope_mismatch",
                "detail": {
                    "status": "error",
                    "error_code": "ledger_scope_mismatch",
                    "detail": {
                        "error": "ledger_scope_mismatch",
                        "payload_ledger_id": None,
                        "header_ledger_id": "loam-root-01",
                        "path_ledger_id": "loam",
                    },
                },
            },
        )

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("error_code") == "ledger_scope_mismatch"
    detail = body.get("detail") or {}
    assert "error" not in detail
    assert detail.get("header_ledger_id") == "loam-root-01"
    assert detail.get("path_ledger_id") == "loam"


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


def test_decode_coordinate_canonicalizes_loam_root_01_payload_ledger_id(monkeypatch):
    """DSS-280: legacy alias payload ledger_id is canonicalized before backend call."""
    captured: dict[str, object] = {}

    async def fake_decode_coordinate(_coord: str, *, auth_headers=None, **kwargs):
        captured["coord"] = _coord
        captured["auth_headers"] = auth_headers
        return {"coord": "loam:WX-1"}

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post(
        "/api/decode_coordinate",
        json={"coordinate": "loam:WX-1", "ledger_id": "loam-root-01"},
    )
    assert resp.status_code == 200
    assert app_module.api.ledger_id == "loam"
    assert (app_module.api.headers or {}).get("x-ledger-id") == "loam"
    body = resp.json()
    assert body.get("ledger_id") == "loam"
    assert body.get("canonical_ledger_did") == "did:web:legacy.local:ledgers:ledger-loam"


def test_decode_coordinate_canonicalizes_ledger_prefix_and_uppercase(monkeypatch):
    """DSS-280: ledger: prefix and casing are normalized."""
    captured: dict[str, object] = {}

    async def fake_decode_coordinate(_coord: str, *, auth_headers=None, **kwargs):
        captured["auth_headers"] = auth_headers
        return {"coord": "loam:WX-1"}

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post(
        "/api/decode_coordinate",
        json={"coordinate": "loam:WX-1", "ledger_id": "ledger:LOAM"},
    )
    assert resp.status_code == 200
    assert app_module.api.ledger_id == "loam"
    assert (app_module.api.headers or {}).get("x-ledger-id") == "loam"


def test_decode_coordinate_canonicalizes_alias_coordinate_namespace(monkeypatch):
    """DSS-280: coordinate namespace alias is rewritten to canonical before backend call."""
    captured: dict[str, object] = {}

    async def fake_decode_coordinate(_coord: str, *, auth_headers=None, **kwargs):
        captured["coord"] = _coord
        captured["auth_headers"] = auth_headers
        return {"coord": "loam:WX-1"}

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post(
        "/api/decode_coordinate",
        json={"coordinate": "loam-root-01:WX-1", "ledger_id": "loam"},
    )
    assert resp.status_code == 200
    assert captured.get("coord") == "loam:WX-1"
    assert (app_module.api.headers or {}).get("x-ledger-id") == "loam"


def test_decode_coordinate_diagnostics_header_includes_canonical_did(monkeypatch):
    """DSS-280: X-Decode-Diagnostics advertises the canonical ledger DID."""
    async def fake_decode_coordinate(_coord: str, **kwargs):
        return {"coord": "loam:WX-1"}

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    resp = client.post(
        "/api/decode_coordinate",
        json={"coordinate": "loam:WX-1", "ledger_id": "loam-root-01"},
    )
    assert resp.status_code == 200
    diag_header = resp.headers.get("X-Decode-Diagnostics")
    assert diag_header
    import json
    diagnostics = json.loads(diag_header)
    assert diagnostics.get("ledger_id") == "loam"
    assert diagnostics.get("canonical_ledger_did") == "did:web:legacy.local:ledgers:ledger-loam"


def test_decode_web4_canonicalizes_alias_namespace_and_session_ledger(monkeypatch):
    """DSS-280: /api/chat/web4/decode canonicalizes namespace and session ledger alias."""
    captured: dict[str, object] = {}

    async def fake_decode_web4(*, namespace: str, identifier: str):
        captured["namespace"] = namespace
        captured["identifier"] = identifier
        return {"coord": f"{namespace}:{identifier}"}

    monkeypatch.setattr(app_module.api, "decode_web4", fake_decode_web4)

    resp = client.post(
        "/api/chat/web4/decode",
        json={"namespace": "loam-root-01", "identifier": "WX-1"},
    )
    assert resp.status_code == 200
    assert captured.get("namespace") == "loam"
    assert captured.get("identifier") == "WX-1"
    assert app_module.api.ledger_id == "loam"
