# DSS-CP-GOV-v1.0.0-alpha
"""Integration tests for the governance impact-analysis and connection-remove endpoints."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import control_plane_router


def _make_client(*, base_url: str = "http://testserver") -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(control_plane_router)
    return TestClient(app, base_url=base_url)


def _headers() -> dict[str, str]:
    return {"x-principal-id": "ops-admin", "x-principal-type": "admin"}


def _create_ledger(client: TestClient, ledger_id: str) -> None:
    client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": ledger_id,
            "name": ledger_id,
            "tenant_id": "tenant:demo",
            "status": "active",
            "provisioning_source": "control_plane",
            "idempotency_key": f"ledger-{ledger_id}",
        },
        headers=_headers(),
    )


def _create_principal(client: TestClient, principal_id: str, actor_type: str = "human") -> None:
    client.post(
        "/api/control-plane/principals",
        json={
            "principal_did": principal_id,
            "tenant_id": "tenant:demo",
            "display_name": principal_id,
            "status": "active",
            "provisioning_source": "control_plane",
            "idempotency_key": f"principal-{principal_id}",
            "metadata": {"actor_type": actor_type},
        },
        headers=_headers(),
    )


def _create_relationship(client: TestClient, rel_type: str, sub_type: str, sub_id: str, obj_type: str, obj_id: str) -> None:
    client.post(
        "/api/control-plane/relationships",
        json={
            "subject_entity_type": sub_type,
            "subject_entity_id": sub_id,
            "object_entity_type": obj_type,
            "object_entity_id": obj_id,
            "relationship_type": rel_type,
            "status": "active",
            "enabled_state": "enabled",
        },
        headers=_headers(),
    )


def test_impact_analysis_returns_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    _create_ledger(client, "LOAM")
    _create_principal(client, "p1")
    _create_relationship(client, "member_of", "principal", "p1", "ledger", "LOAM")

    response = client.post(
        "/api/control-plane/impact-analysis",
        json={"entity_type": "principal", "entity_id": "p1", "ledger_id": "LOAM"},
        headers=_headers(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "impact" in payload
    assert "confirmation_token" in payload
    assert payload["impact"]["affected_principals"] == ["p1"]


def test_connection_remove_with_stale_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    _create_ledger(client, "LOAM")
    _create_principal(client, "p1")
    _create_relationship(client, "member_of", "principal", "p1", "ledger", "LOAM")

    response = client.post(
        "/api/control-plane/connections/remove",
        json={
            "entity_type": "principal",
            "entity_id": "p1",
            "ledger_id": "LOAM",
            "confirmation_token": "stale-token",
        },
        headers=_headers(),
    )
    assert response.status_code == 409
    payload = response.json()
    assert "impact" in payload
    assert "confirmation_token" in payload


def test_connection_remove_success(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    _create_ledger(client, "LOAM")
    _create_principal(client, "p1")
    _create_relationship(client, "member_of", "principal", "p1", "ledger", "LOAM")

    impact = client.post(
        "/api/control-plane/impact-analysis",
        json={"entity_type": "principal", "entity_id": "p1", "ledger_id": "LOAM"},
        headers=_headers(),
    ).json()
    token = impact["confirmation_token"]

    response = client.post(
        "/api/control-plane/connections/remove",
        json={
            "entity_type": "principal",
            "entity_id": "p1",
            "ledger_id": "LOAM",
            "confirmation_token": token,
        },
        headers=_headers(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "committed_at" in payload

    relationships = client.get("/api/control-plane/relationships", headers=_headers()).json()
    assert not any(
        r.get("subject_entity_id") == "p1" and r.get("object_entity_id") == "LOAM"
        for r in relationships.get("relationships", [])
    )
