"""Tests for frontend admin provisioning inspection proxy routes (DSS-143)."""

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


def test_admin_provisioning_job_proxies_to_middleware(monkeypatch):
    """GET /api/admin/provisioning/jobs/{job_id} should proxy to middleware."""
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
                json={
                    "status": "ok",
                    "inspection": {
                        "job": {"job_id": "provjob:test", "status": "succeeded", "resource_counts": {"total": 7}},
                        "read_only": True,
                        "rescue_recommendation": {"action": "none"},
                    },
                },
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/admin/provisioning/jobs/provjob:test-123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["inspection"]["job"]["status"] == "succeeded"
    assert body["inspection"]["read_only"] is True
    assert "/admin/provisioning/jobs/provjob:test-123" in captured["url"]


def test_admin_provisioning_job_steps_proxies_to_middleware(monkeypatch):
    """GET /api/admin/provisioning/jobs/{job_id}/steps should proxy to middleware."""
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
                json={
                    "status": "ok",
                    "inspection": {
                        "job_id": "provjob:test",
                        "steps": [
                            {"step_id": "dss_space", "status": "succeeded"},
                            {"step_id": "ledger_runtime", "status": "succeeded"},
                        ],
                        "step_counts": {"total": 2, "succeeded": 2, "failed": 0},
                        "read_only": True,
                    },
                },
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/admin/provisioning/jobs/provjob:test-456/steps")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["inspection"]["steps"]) == 2
    assert body["inspection"]["step_counts"]["succeeded"] == 2
    assert "/admin/provisioning/jobs/provjob:test-456/steps" in captured["url"]


def test_admin_provisioning_job_rejects_missing_job_id(monkeypatch):
    """GET should 422 if job_id path param is effectively missing."""
    _patch_auth(monkeypatch)
    resp = client.get("/api/admin/provisioning/jobs/")
    assert resp.status_code == 422
    assert resp.json()["error"] == "job_id_required"
