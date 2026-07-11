"""Tests for frontend wallet interoperability proxy routes (DSS-144)."""

import httpx
from fastapi.testclient import TestClient

import app as app_module
from app import app


client = TestClient(app)


def test_wallet_credential_offer_proxies_to_middleware(monkeypatch):
    """GET /api/wallet/credential-offer should proxy to middleware."""
    captured: dict = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers=None, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = dict(params or {})
            response = httpx.Response(
                200,
                json={
                    "status": "ok",
                    "wallet_provider": "mattr",
                    "credential_offer": {
                        "credential_issuer": "did:web:id.dualsubstrate.com",
                        "credential_configuration_ids": ["DssSupplyChainIdentity"],
                    },
                },
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/wallet/credential-offer?session_id=test-123&wallet_provider=mattr")
    assert resp.status_code == 200
    body = resp.json()
    assert body["wallet_provider"] == "mattr"
    assert captured["params"].get("session_id") == "test-123"
    assert captured["params"].get("wallet_provider") == "mattr"


def test_wallet_credential_offer_rejects_missing_session_id():
    """GET should 422 if session_id is missing."""
    resp = client.get("/api/wallet/credential-offer?wallet_provider=mattr")
    assert resp.status_code == 422
    assert resp.json()["error"] == "session_id_required"


def test_wallet_providers_proxies_to_middleware(monkeypatch):
    """GET /api/wallet/providers should proxy to middleware."""
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
                    "providers": [
                        {"provider_id": "microsoft_authenticator", "display_name": "Microsoft Authenticator"},
                        {"provider_id": "mattr", "display_name": "MATTR Wallet"},
                    ],
                },
                request=httpx.Request("GET", "http://test"),
            )
            return response

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _FakeClient())

    resp = client.get("/api/wallet/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["providers"]) == 2
    assert body["providers"][1]["provider_id"] == "mattr"
    assert "/wallet/providers" in captured["url"]
