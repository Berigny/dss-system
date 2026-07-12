from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.chat import router as chat_router
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2


def test_chat_web4_decode_includes_feedback_rollup_in_meta() -> None:
    app = FastAPI()
    app.state.db = {}
    app.include_router(chat_router)
    client = TestClient(app)

    store = LedgerStoreV2(app.state.db)
    key = LedgerKey(namespace="37a8eec1:a4561ff2", identifier="WX-1772002817300")
    entry = LedgerEntry(
        key=key,
        state=ContinuousState(metadata={"provider": "x-ai/grok-4.3"}),
        notes="demo turn",
    )
    store.write(entry)
    store.submit_feedback(
        key.as_path(),
        actor_id="human:demo",
        actor_type="human",
        rating=3,
        reason="approved",
        source="test",
    )

    resp = client.post("/chat/web4/decode", json={"coordinate": key.as_path()})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("coord") == key.as_path()
    meta = body.get("meta") or {}
    rollup = meta.get("feedback_rollup")
    assert isinstance(rollup, dict)
    assert int(rollup.get("actors") or 0) >= 1
    assert float(rollup.get("score") or 0.0) >= 0.0
