"""Tests for frontend onboarding model-library proxy routes (DSS-140)."""

import httpx
from fastapi.testclient import TestClient

import app as app_module
from app import app


client = TestClient(app)


def test_onboarding_model_library_proxies_to_middleware(monkeypatch):
    """GET /api/onboarding/model-library should proxy to middleware."""
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
            captured["headers"] = dict(headers or {})
            response = httpx.Response(
                200,
                json={"status": "ok", "providers": [{"provider_id": "openrouter", "display_name": "OpenRouter"}]},
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/onboarding/model-library")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["providers"][0]["provider_id"] == "openrouter"
    assert "/account/current/model-library" in captured["url"]


def test_onboarding_model_library_select_proxies_to_middleware(monkeypatch):
    """POST /api/onboarding/model-library/select should forward selection to middleware."""
    captured: dict = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, json=None, headers=None, **kwargs):
            captured["url"] = url
            captured["payload"] = dict(json or {})
            response = httpx.Response(
                200,
                json={
                    "status": "ok",
                    "model_principal": {"principal_id": "model:abc", "provider": "openrouter", "model_id": "anthropic/claude-3.5-sonnet"},
                },
                request=httpx.Request("POST", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/api/onboarding/model-library/select",
        json={"provider": "openrouter", "model_id": "anthropic/claude-3.5-sonnet"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_principal"]["model_id"] == "anthropic/claude-3.5-sonnet"
    assert captured["payload"]["provider"] == "openrouter"
    assert captured["payload"]["model_id"] == "anthropic/claude-3.5-sonnet"


def test_onboarding_model_library_select_rejects_missing_fields():
    """POST should 422 if provider or model_id is missing."""
    resp = client.post("/api/onboarding/model-library/select", json={"provider": "openrouter"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "provider_and_model_id_required"

    resp = client.post("/api/onboarding/model-library/select", json={"model_id": "gpt-4o"})
    assert resp.status_code == 422


def test_onboarding_principals_proxies_to_middleware(monkeypatch):
    """GET /api/onboarding/principals should proxy to middleware."""
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
                json={"status": "ok", "principals": [{"principal_id": "owner:1", "principal_type": "human_owner"}]},
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/onboarding/principals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["principals"][0]["principal_type"] == "human_owner"
    assert "/account/current/principals" in captured["url"]
