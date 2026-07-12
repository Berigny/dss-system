"""Tests for middleware proxy routes to backend account endpoints (DSS-140 / DSS-141)."""

import httpx
from fastapi.testclient import TestClient

import app as app_module


client = TestClient(app_module.app)


def test_proxy_account_model_library_forwards_auth(monkeypatch):
    """GET /account/current/model-library should forward session token to backend."""
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
            response = httpx.Response(200, json={"status": "ok", "providers": []})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get(
        "/account/current/model-library",
        headers={"x-session-token": "test-session-123"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "providers": []}
    assert captured["headers"].get("x-session-token") == "test-session-123"
    assert "/account/current/model-library" in captured["url"]


def test_proxy_account_model_library_select_forwards_payload(monkeypatch):
    """POST /account/current/model-library/select should forward payload and auth."""
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
            captured["headers"] = dict(headers or {})
            response = httpx.Response(
                200,
                json={
                    "status": "ok",
                    "model_principal": {"principal_id": "model:test", "provider": "openrouter"},
                },
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/account/current/model-library/select",
        json={"provider": "openrouter", "model_id": "anthropic/claude-3.5-sonnet"},
        headers={"Authorization": "Bearer test-bearer-456"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_principal"]["provider"] == "openrouter"
    assert captured["payload"]["provider"] == "openrouter"
    assert captured["payload"]["model_id"] == "anthropic/claude-3.5-sonnet"
    assert captured["headers"].get("x-session-token") == "test-bearer-456"


def test_proxy_account_principals_forwards_cookie_auth(monkeypatch):
    """GET /account/current/principals should forward cookie auth to backend."""
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
                json={"status": "ok", "principals": [{"principal_id": "owner:1", "principal_type": "human_owner"}]},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get(
        "/account/current/principals",
        cookies={app_module.BACKEND_SESSION_TOKEN_COOKIE: "cookie-session-789"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert captured["headers"].get("x-session-token") == "cookie-session-789"


def test_proxy_account_agent_principal_bootstrap_forwards_payload(monkeypatch):
    """POST /account/current/principals/agent/bootstrap should forward payload and auth."""
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
            captured["headers"] = dict(headers or {})
            response = httpx.Response(
                200,
                json={
                    "status": "ok",
                    "agent_principal": {"principal_id": "agent:test", "principal_type": "agent"},
                    "idempotent_replay": False,
                },
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/account/current/principals/agent/bootstrap",
        json={"idempotency_key": "bootstrap-001"},
        headers={"Authorization": "Bearer agent-bootstrap-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["agent_principal"]["principal_type"] == "agent"
    assert captured["payload"]["idempotency_key"] == "bootstrap-001"
    assert captured["headers"].get("x-session-token") == "agent-bootstrap-token"
    assert "/account/current/principals/agent/bootstrap" in captured["url"]


def test_proxy_account_connections_forwards_cookie_auth(monkeypatch):
    """GET /account/current/connections should forward cookie auth to backend."""
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
                json={"status": "ok", "connections": [], "agent_principal": None},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get(
        "/account/current/connections",
        cookies={app_module.BACKEND_SESSION_TOKEN_COOKIE: "cookie-session-321"},
    )
    assert resp.status_code == 200
    assert resp.json()["connections"] == []
    assert captured["headers"].get("x-session-token") == "cookie-session-321"
    assert "/account/current/connections" in captured["url"]


def test_proxy_account_onboarding_forwards_auth(monkeypatch):
    """GET /account/current/onboarding should forward auth to backend."""
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
                json={"status": "ok", "onboarding": {"status": "accepted", "next_route": "model_library_selection"}},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get(
        "/account/current/onboarding",
        headers={"x-session-token": "test-session-onboarding"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["onboarding"]["next_route"] == "model_library_selection"
    assert captured["headers"].get("x-session-token") == "test-session-onboarding"


def test_proxy_account_setup_prompt_forwards_auth(monkeypatch):
    """GET /account/current/setup-prompt should forward auth to backend."""
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
                json={"status": "ok", "setup_prompt": {"required_item_ids": ["model_principal_selected"]}},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get(
        "/account/current/setup-prompt",
        headers={"Authorization": "Bearer test-bearer-setup"},
    )
    assert resp.status_code == 200
    assert "model_principal_selected" in resp.json()["setup_prompt"]["required_item_ids"]
    assert captured["headers"].get("x-session-token") == "test-bearer-setup"


def test_proxy_account_setup_prompt_dismiss_forwards_payload(monkeypatch):
    """POST /account/current/setup-prompt/dismiss should forward payload and auth."""
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
            captured["headers"] = dict(headers or {})
            response = httpx.Response(200, json={"status": "ok", "dismissed": True})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/account/current/setup-prompt/dismiss",
        json={"mode": "permanent"},
        headers={"x-session-token": "test-session-dismiss"},
    )
    assert resp.status_code == 200
    assert resp.json()["dismissed"] is True
    assert captured["payload"]["mode"] == "permanent"
    assert captured["headers"].get("x-session-token") == "test-session-dismiss"


def test_proxy_wallet_credential_offer_forwards_query_params(monkeypatch):
    """GET /wallet/credential-offer should forward query params and auth."""
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
                json={"status": "ok", "wallet_provider": "mattr", "credential_offer": {}},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get(
        "/wallet/credential-offer?session_id=test-123&wallet_provider=mattr",
        headers={"x-session-token": "wallet-session-999"},
    )
    assert resp.status_code == 200
    assert resp.json()["wallet_provider"] == "mattr"
    assert "session_id=test-123" in captured["url"]
    assert captured["headers"].get("x-session-token") == "wallet-session-999"


def test_proxy_wallet_did_document_forwards_path(monkeypatch):
    """GET /wallet/{wallet_id}/did.json should forward path to backend."""
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
                json={"@context": ["https://www.w3.org/ns/did/v1"], "id": "did:web:id.dualsubstrate.com"},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/wallet/mattr/did.json")
    assert resp.status_code == 200
    assert resp.json()["id"] == "did:web:id.dualsubstrate.com"
    assert "/wallet/mattr/did.json" in captured["url"]


def test_proxy_admin_provisioning_job_forwards_auth(monkeypatch):
    """GET /admin/provisioning/jobs/{job_id} should forward auth to backend."""
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
                json={"status": "ok", "inspection": {"job": {"job_id": "provjob:test", "status": "succeeded"}, "read_only": True}},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get(
        "/admin/provisioning/jobs/provjob:test-123",
        headers={"x-admin-token": "admin-test-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["inspection"]["job"]["status"] == "succeeded"
    assert "/admin/provisioning/jobs/provjob:test-123" in captured["url"]
    assert captured["headers"].get("x-admin-token") == "admin-test-token"


def test_proxy_admin_provisioning_job_steps_forwards_path(monkeypatch):
    """GET /admin/provisioning/jobs/{job_id}/steps should forward path to backend."""
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
                json={"status": "ok", "inspection": {"steps": [], "step_counts": {"total": 0}}},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/admin/provisioning/jobs/provjob:test-456/steps")
    assert resp.status_code == 200
    assert "/admin/provisioning/jobs/provjob:test-456/steps" in captured["url"]


def test_proxy_account_model_library_returns_upstream_error(monkeypatch):
    """Should propagate backend error status codes."""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers=None, **kwargs):
            response = httpx.Response(401, text="Unauthorized")
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/account/current/model-library")
    assert resp.status_code == 401


def test_proxy_auth_pilot_signup_forwards_payload(monkeypatch):
    """POST /api/auth/pilot/signup should forward payload to backend."""
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
                json={"status": "ok", "signup_id": "signup:test", "principal_did": "did:web:test"},
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/api/auth/pilot/signup",
        json={"primary_contact": "test@example.com", "owner_display_name": "Test", "pilot_terms_acknowledgement": True, "idempotency_key": "key-123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["signup_id"] == "signup:test"
    assert "/auth/pilot/signup" in captured["url"]
    assert captured["payload"]["primary_contact"] == "test@example.com"


def test_proxy_auth_signin_sets_session_cookie(monkeypatch):
    """POST /api/auth/signin should forward payload and set session cookie on success."""
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
                    "session": {"token": "sess-token-abc", "principal_did": "did:web:test"},
                },
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/api/auth/signin",
        json={"primary_contact": "test@example.com"},
    )
    assert resp.status_code == 200
    assert "/auth/signin" in captured["url"]
    assert resp.cookies.get("ds_backend_session_token") == "sess-token-abc"
    # Prevent cookie leakage into subsequent tests
    client.cookies.clear()


def test_proxy_account_onboarding_post_forwards_payload(monkeypatch):
    """POST /account/current/onboarding should forward payload and auth."""
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
            captured["headers"] = dict(headers or {})
            response = httpx.Response(200, json={"status": "ok", "onboarding": {"status": "accepted"}})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/account/current/onboarding",
        json={"owner_display_name": "Test", "workspace_or_dss_space_label": "My Space", "primary_contact": "test@example.com", "pilot_use_case": "trial", "free_trial_scope_acknowledgement": True, "idempotency_key": "key-456"},
        headers={"x-session-token": "test-session-789"},
    )
    assert resp.status_code == 200
    assert captured["headers"].get("x-session-token") == "test-session-789"
    assert captured["payload"]["pilot_use_case"] == "trial"
    assert "/account/current/onboarding" in captured["url"]


def test_proxy_account_provisioning_get_forwards_auth(monkeypatch):
    """GET /account/current/provisioning should forward auth to backend."""
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
            response = httpx.Response(200, json={"status": "ok", "provisioning": {"status": "succeeded"}})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/account/current/provisioning", headers={"Authorization": "Bearer test-bearer-999"})
    assert resp.status_code == 200
    assert captured["headers"].get("x-session-token") == "test-bearer-999"
    assert "/account/current/provisioning" in captured["url"]


def test_proxy_account_provisioning_run_triggers_backend(monkeypatch):
    """POST /account/current/provisioning/run should trigger backend provisioning."""
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
            captured["headers"] = dict(headers or {})
            response = httpx.Response(200, json={"status": "ok", "provisioning": {"status": "succeeded", "job_id": "provjob:test"}})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post("/account/current/provisioning/run", headers={"x-session-token": "test-session-run"})
    assert resp.status_code == 200
    assert captured["headers"].get("x-session-token") == "test-session-run"
    assert "/account/current/provisioning/run" in captured["url"]


def test_proxy_wallet_link_start_forwards_provider(monkeypatch):
    """POST /account/current/identity/wallet-link/start should forward provider to backend."""
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
            captured["headers"] = dict(headers or {})
            response = httpx.Response(200, json={"status": "ok", "identity_status": {"wallet": {"provider": "mattr", "wallet_state": "in_progress"}}})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/account/current/identity/wallet-link/start",
        json={"provider": "mattr"},
        headers={"x-session-token": "test-session-wallet"},
    )
    assert resp.status_code == 200
    assert captured["payload"]["provider"] == "mattr"
    assert captured["headers"].get("x-session-token") == "test-session-wallet"
    assert "/account/current/identity/wallet-link/start" in captured["url"]


def test_proxy_wallet_link_complete_forwards_did(monkeypatch):
    """POST /account/current/identity/wallet-link/complete should forward wallet_did to backend."""
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
            response = httpx.Response(200, json={"status": "ok", "identity_status": {"wallet": {"provider": "mattr", "wallet_state": "linked", "wallet_did": "did:web:mattr"}}})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.post(
        "/account/current/identity/wallet-link/complete",
        json={"provider": "mattr", "wallet_did": "did:web:mattr"},
    )
    assert resp.status_code == 200
    assert captured["payload"]["wallet_did"] == "did:web:mattr"
    assert "/account/current/identity/wallet-link/complete" in captured["url"]


def test_proxy_account_current_forwards_auth(monkeypatch):
    """GET /account/current should forward auth to backend."""
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
            response = httpx.Response(200, json={"status": "ok", "account": {"account_id": "acct:test"}})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/account/current", headers={"x-session-token": "test-session-summary"})
    assert resp.status_code == 200
    assert captured["headers"].get("x-session-token") == "test-session-summary"
    assert "/account/current" in captured["url"]


def test_proxy_account_setup_checklist_returns_items(monkeypatch):
    """GET /account/current/setup-checklist should return checklist items."""
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
            response = httpx.Response(200, json={"status": "ok", "setup_checklist": {"items": [{"item_id": "model_principal_selected", "state": "incomplete"}]}})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/account/current/setup-checklist")
    assert resp.status_code == 200
    assert resp.json()["setup_checklist"]["items"][0]["item_id"] == "model_principal_selected"
    assert "/account/current/setup-checklist" in captured["url"]


def test_proxy_account_identity_returns_wallet_state(monkeypatch):
    """GET /account/current/identity should return identity and wallet state."""
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
            response = httpx.Response(200, json={"status": "ok", "identity_status": {"wallet": {"wallet_state": "linked", "provider": "mattr"}}})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/account/current/identity")
    assert resp.status_code == 200
    assert resp.json()["identity_status"]["wallet"]["provider"] == "mattr"
    assert "/account/current/identity" in captured["url"]


def test_proxy_account_surfaces_returns_bindings(monkeypatch):
    """GET /account/current/surfaces should return surface bindings."""
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
            response = httpx.Response(200, json={"status": "ok", "surfaces": [{"surface_id": "chat", "surface_type": "chat"}]})
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/account/current/surfaces")
    assert resp.status_code == 200
    assert resp.json()["surfaces"][0]["surface_id"] == "chat"
    assert "/account/current/surfaces" in captured["url"]
