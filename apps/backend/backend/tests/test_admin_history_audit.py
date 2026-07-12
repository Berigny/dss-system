from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import router as admin_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(admin_router)
    return TestClient(app)


def test_history_audit_requires_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_client()
    resp = client.get("/admin/history/audit")
    assert resp.status_code == 403


def test_history_audit_lists_chat_entities_with_sample_coordinates(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_client()
    db = client.app.state.db
    db[b"chat-37a8eec1:ae95ca73:WX-1"] = b"{}"
    db[b"chat-37a8eec1:ae95ca73:WX-2"] = b"{}"
    db[b"de6dc544:27e27f9d:WX-3"] = b"{}"
    db[b"metrics:events:de6dc544:27e27f9d:2026-02-24T04:09:57.520948+00:00"] = b"{}"

    resp = client.get(
        "/admin/history/audit?limit=20&coord_limit=2",
        headers={"x-admin-token": "test-admin-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity_count"] == 2
    assert body["entry_count"] == 3
    entities = body.get("entities") or []
    by_entity = {row.get("entity"): row for row in entities if isinstance(row, dict)}

    first = by_entity.get("37a8eec1:ae95ca73")
    assert isinstance(first, dict)
    assert first["entry_count"] == 2
    assert first["namespaces"] == ["chat-37a8eec1:ae95ca73"]
    assert len(first["sample_coordinates"]) == 2
    assert all(coord.startswith("chat-37a8eec1:ae95ca73:") for coord in first["sample_coordinates"])

    second = by_entity.get("de6dc544:27e27f9d")
    assert isinstance(second, dict)
    assert second["entry_count"] == 1
    assert second["namespaces"] == ["de6dc544:27e27f9d"]
    assert second["sample_coordinates"] == ["de6dc544:27e27f9d:WX-3"]
