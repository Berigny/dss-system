"""Tests for frontend onboarding agent bootstrap and connection graph proxy routes (DSS-141)."""

import httpx
from fastapi.testclient import TestClient

import app as app_module
from app import app


client = TestClient(app)


def test_onboarding_agent_bootstrap_proxies_to_middleware(monkeypatch):
    """POST /api/onboarding/principals/agent/bootstrap should proxy to middleware."""
    captured: dict = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers=None, **kwargs):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            response = httpx.Response(
                200,
                json={
                    "status": "ok",
                    "agent_principal": {
                        "principal_id": "agent:abc",
                        "principal_type": "agent",
                        "owner_principal_id": "owner:1",
                        "model_principal_id": "model:2",
                    },
                },
                request=httpx.Request("POST", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post("/api/onboarding/principals/agent/bootstrap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["agent_principal"]["principal_type"] == "agent"
    assert "/account/current/principals/agent/bootstrap" in captured["url"]


def test_onboarding_connections_proxies_to_middleware(monkeypatch):
    """GET /api/onboarding/connections should proxy to middleware."""
    captured: dict = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers=None, **kwargs):
            captured["url"] = url
            response = httpx.Response(
                200,
                json={
                    "status": "ok",
                    "connections": [
                        {"edge_id": "conn:1", "relation_type": "owns", "source_principal_id": "owner:1", "target_principal_id": "agent:abc"},
                        {"edge_id": "conn:2", "relation_type": "acts_through", "source_principal_id": "agent:abc", "target_principal_id": "model:2"},
                    ],
                },
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/onboarding/connections")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["connections"]) == 2
    assert body["connections"][0]["relation_type"] == "owns"
    assert "/account/current/connections" in captured["url"]


def test_onboarding_agent_bootstrap_propagates_upstream_error(monkeypatch):
    """Should propagate backend error status codes for agent bootstrap."""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers=None, **kwargs):
            response = httpx.Response(
                409,
                json={"error": "model_principal_not_selected"},
                request=httpx.Request("POST", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post("/api/onboarding/principals/agent/bootstrap")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "upstream_error"
