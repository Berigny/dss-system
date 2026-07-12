from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import router as admin_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(admin_router)
    return TestClient(app)


def test_create_tenant_is_idempotent_and_bootstraps_default_ledger(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    payload = {
        "tenant_id": "tenant:acme",
        "owner_principal_id": "alice",
        "owner_principal_type": "user",
        "policy_profile": "strict",
    }

    create_1 = client.post("/admin/tenants", json=payload, headers=headers)
    assert create_1.status_code == 200
    body_1 = create_1.json()
    assert body_1["status"] == "ok"
    assert body_1["tenant_created"] is True
    assert body_1["tenant"]["tenant_id"] == "tenant:acme"
    assert body_1["ledger_creates"]["chat-acme"] is True
    assert body_1["ledger_records"]["chat-acme"]["tenant_id"] == "tenant:acme"
    assert body_1["ledger_records"]["chat-acme"]["owner_principal_id"] == "alice"

    create_2 = client.post("/admin/tenants", json=payload, headers=headers)
    assert create_2.status_code == 200
    body_2 = create_2.json()
    assert body_2["tenant_created"] is False
    assert body_2["ledger_creates"]["chat-acme"] is False


def test_create_tenant_with_explicit_ledger_ids(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    payload = {
        "tenant_id": "tenant:zen",
        "ledger_ids": ["chat-zen-main", "chat-zen-support"],
    }

    resp = client.post("/admin/tenants", json=payload, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["ledger_creates"].keys()) == {"chat-zen-main", "chat-zen-support"}

    tenants = client.get("/admin/tenants", headers={"x-admin-token": "test-admin-token"})
    assert tenants.status_code == 200
    tenant_rows = tenants.json().get("tenants", [])
    assert any(row.get("tenant_id") == "tenant:zen" for row in tenant_rows)

