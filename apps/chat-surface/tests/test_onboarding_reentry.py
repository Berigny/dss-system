"""Tests for frontend onboarding re-entry proxy routes (DSS-142)."""

import httpx
from fastapi.testclient import TestClient

import app as app_module
from app import app


client = TestClient(app)


def test_onboarding_status_proxies_to_middleware(monkeypatch):
    """GET /api/onboarding/status should proxy to middleware."""
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
                    "onboarding": {
                        "status": "accepted",
                        "next_route": "model_library_selection",
                        "model_principal": {"selected": False},
                        "agent_principal": {"bootstrapped": False},
                    },
                },
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/onboarding/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["onboarding"]["next_route"] == "model_library_selection"
    assert "/account/current/onboarding" in captured["url"]


def test_setup_prompt_proxies_to_middleware(monkeypatch):
    """GET /api/setup-prompt should proxy to middleware."""
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
                    "setup_prompt": {
                        "required_item_ids": ["model_principal_selected", "agent_principal_bootstrapped"],
                    },
                },
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/setup-prompt")
    assert resp.status_code == 200
    body = resp.json()
    assert "model_principal_selected" in body["setup_prompt"]["required_item_ids"]
    assert "/account/current/setup-prompt" in captured["url"]


def test_setup_prompt_dismiss_proxies_to_middleware(monkeypatch):
    """POST /api/setup-prompt/dismiss should forward payload to middleware."""
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
                json={"status": "ok", "dismissed": True},
                request=httpx.Request("POST", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post("/api/setup-prompt/dismiss", json={"mode": "permanent"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dismissed"] is True
    assert captured["payload"]["mode"] == "permanent"
    assert "/account/current/setup-prompt/dismiss" in captured["url"]


def test_setup_prompt_dismiss_rejects_missing_mode():
    """POST should 422 if mode is missing."""
    resp = client.post("/api/setup-prompt/dismiss", json={})
    assert resp.status_code == 422
    assert resp.json()["error"] == "mode_required"
