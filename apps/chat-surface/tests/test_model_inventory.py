from fastapi.testclient import TestClient

import app as app_module
from app import app


client = TestClient(app)


def test_models_endpoint_does_not_synthesize_fallback_inventory(monkeypatch):
    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            raise RuntimeError("middleware unavailable")

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    response = client.get(
        "/api/models",
        headers={
            "accept": "application/json",
            "cookie": f"{app_module.FRONTDOOR_AUTH_COOKIE}={app_module._frontdoor_cookie_signature()}",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "models": [],
        "local_models": [],
        "online_models": [],
        "fallback": False,
        "unavailable": True,
        "reason": "middleware_unavailable",
    }
