"""Tests for frontend onboarding write proxy routes (onboarding submit, provisioning, wallet-link)."""

import httpx
from fastapi.testclient import TestClient

import app as app_module
from app import app


client = TestClient(app)


def _patch_auth(monkeypatch):
    """Bypass frontdoor auth so tests reach route handlers."""
    async def _fake_auth(request):
        return True, "did:web:test"
    monkeypatch.setattr(app_module, "_shared_backend_session_identity", _fake_auth)


def test_onboarding_submit_proxies_to_middleware(monkeypatch):
    """POST /api/onboarding/submit should proxy to middleware."""
    _patch_auth(monkeypatch)
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
                json={"status": "ok", "onboarding": {"status": "accepted"}, "provisioning": {"status": "queued"}},
                request=httpx.Request("POST", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/api/onboarding/submit",
        json={
            "owner_display_name": "Test User",
            "workspace_or_dss_space_label": "My Space",
            "primary_contact": "test@example.com",
            "pilot_use_case": "trial",
            "free_trial_scope_acknowledgement": True,
            "idempotency_key": "key-123",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["onboarding"]["status"] == "accepted"
    assert captured["payload"]["pilot_use_case"] == "trial"
    assert "/account/current/onboarding" in captured["url"]


def test_onboarding_submit_rejects_missing_fields(monkeypatch):
    """POST should 422 if required fields are missing."""
    _patch_auth(monkeypatch)
    resp = client.post("/api/onboarding/submit", json={"owner_display_name": "Test"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "missing_required_fields"


def test_provisioning_status_proxies_to_middleware(monkeypatch):
    """GET /api/provisioning/status should proxy to middleware."""
    _patch_auth(monkeypatch)
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
                json={"status": "ok", "provisioning": {"status": "succeeded", "job_id": "provjob:test"}},
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/provisioning/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provisioning"]["status"] == "succeeded"
    assert "/account/current/provisioning" in captured["url"]


def test_provisioning_run_proxies_to_middleware(monkeypatch):
    """POST /api/provisioning/run should proxy to middleware."""
    _patch_auth(monkeypatch)
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
            response = httpx.Response(
                200,
                json={"status": "ok", "provisioning": {"status": "succeeded", "job_id": "provjob:test"}},
                request=httpx.Request("POST", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post("/api/provisioning/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provisioning"]["status"] == "succeeded"
    assert "/account/current/provisioning/run" in captured["url"]


def test_wallet_link_start_proxies_to_middleware(monkeypatch):
    """POST /api/wallet/link/start should proxy provider to middleware."""
    _patch_auth(monkeypatch)
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
                json={"status": "ok", "identity_status": {"wallet": {"provider": "mattr", "wallet_state": "in_progress"}}},
                request=httpx.Request("POST", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post("/api/wallet/link/start", json={"provider": "mattr"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["identity_status"]["wallet"]["provider"] == "mattr"
    assert captured["payload"]["provider"] == "mattr"
    assert "/account/current/identity/wallet-link/start" in captured["url"]


def test_wallet_link_complete_proxies_to_middleware(monkeypatch):
    """POST /api/wallet/link/complete should proxy wallet_did to middleware."""
    _patch_auth(monkeypatch)
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
                json={"status": "ok", "identity_status": {"wallet": {"provider": "mattr", "wallet_state": "linked", "wallet_did": "did:web:mattr"}}},
                request=httpx.Request("POST", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post("/api/wallet/link/complete", json={"provider": "mattr", "wallet_did": "did:web:mattr"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["identity_status"]["wallet"]["wallet_state"] == "linked"
    assert captured["payload"]["wallet_did"] == "did:web:mattr"
    assert "/account/current/identity/wallet-link/complete" in captured["url"]


def test_setup_checklist_proxies_to_middleware(monkeypatch):
    """GET /api/setup-checklist should proxy to middleware."""
    _patch_auth(monkeypatch)
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
                json={"status": "ok", "setup_checklist": {"items": [{"item_id": "wallet_linked", "state": "incomplete"}], "complete": False}},
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/setup-checklist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["setup_checklist"]["items"][0]["item_id"] == "wallet_linked"
    assert "/account/current/setup-checklist" in captured["url"]


def test_identity_proxies_to_middleware(monkeypatch):
    """GET /api/identity should proxy to middleware."""
    _patch_auth(monkeypatch)
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
                json={"status": "ok", "identity_status": {"wallet": {"provider": "mattr", "wallet_state": "linked"}}},
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/identity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["identity_status"]["wallet"]["wallet_state"] == "linked"
    assert "/account/current/identity" in captured["url"]


def test_surfaces_proxies_to_middleware(monkeypatch):
    """GET /api/surfaces should proxy to middleware."""
    _patch_auth(monkeypatch)
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
                json={"status": "ok", "surfaces": [{"surface_id": "chat", "surface_type": "chat", "surface_status": "ready"}]},
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/surfaces")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["surfaces"]) == 1
    assert "/account/current/surfaces" in captured["url"]
