import httpx
from fastapi.testclient import TestClient

import app as app_module


client = TestClient(app_module.app)


def test_proxy_ledger_all_returns_504_on_timeout(monkeypatch):
    class _TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _TimeoutClient())

    resp = client.get("/ledger/all?limit=5")
    assert resp.status_code == 504
    assert "Upstream timeout:" in resp.text


def test_proxy_sync_pull_returns_502_on_request_error(monkeypatch):
    class _ErrorClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("boom", request=httpx.Request("POST", "http://example"))

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _ErrorClient())

    resp = client.post("/sync/v0/pull", json={"peer_id": "t"})
    assert resp.status_code == 502
    assert "Upstream request error:" in resp.text


def test_proxy_ledger_history_preserves_coord_meta(monkeypatch):
    async def fake_backend_fetch_json(*, path: str, timeout: float = 20.0, method: str = "GET", payload=None, params=None):
        assert params == {"limit": 5}
        assert path == "/ledger/history/chat-demo"
        return {
            "history": [
                {
                    "role": "assistant",
                    "content": "History-backed answer.",
                    "timestamp": "2026-04-08T00:00:00+00:00",
                    "entry_id": "WX-9C2621E0-1775565322",
                    "coordinate": "chat-demo:WX-9C2621E0-1775565322",
                    "coord_meta": {
                        "coord": "chat-demo:WX-9C2621E0-1775565322",
                        "coord_type": "WX",
                        "identifier": "WX-9C2621E0-1775565322",
                        "runtime_namespace": "chat-demo",
                        "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                        "canonical_subject_source": "did:web:ledger",
                    },
                    "metadata": {"kind": "chat", "role": "assistant"},
                }
            ]
        }

    monkeypatch.setattr(app_module, "_backend_fetch_json", fake_backend_fetch_json)

    resp = client.get("/ledger/history/chat-demo?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    history = body.get("history") or []
    assert history[0]["coord_meta"]["canonical_subject"] == "did:web:id.dualsubstrate.com:ledgers:chat-demo"
