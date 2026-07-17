from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.chat import router as chat_router


def _make_client(*, db: dict[bytes, bytes] | None = None) -> TestClient:
    app = FastAPI()
    if db is not None:
        app.state.db = db
    app.include_router(chat_router)
    return TestClient(app)


def test_chat_web4_decode_invalid_coordinate_returns_400() -> None:
    client = _make_client(db={})
    resp = client.post("/chat/web4/decode", json={"coordinate": ""})
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("status") == "error"
    assert body.get("error_code") == "invalid_coordinate"


def test_chat_web4_decode_library_locked_returns_503() -> None:
    client = _make_client()
    resp = client.post("/chat/web4/decode", json={"coordinate": "chat-team-a:WX-1"})
    assert resp.status_code == 503
    body = resp.json()
    assert body.get("status") == "error"
    assert body.get("error_code") == "library_database_locked"


def test_chat_web4_decode_ledger_scope_mismatch_returns_400() -> None:
    client = _make_client(db={})
    resp = client.post(
        "/chat/web4/decode",
        json={"coordinate": "chat-team-a:WX-1", "ledger_id": "chat-team-a"},
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body.get("status") == "error"
    assert body.get("error_code") == "ledger_scope_mismatch"
    detail = body.get("detail") or {}
    assert detail.get("error") == "ledger_scope_mismatch"


def test_chat_web4_decode_not_found_returns_404() -> None:
    client = _make_client(db={})
    resp = client.post("/chat/web4/decode", json={"coordinate": "chat-team-a:WX-NOTFOUND"})
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("status") == "error"
    assert body.get("error_code") == "coordinate_not_found"
