from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.http import web4_router
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2


def _seed_feedback_without_rollup(store: LedgerStoreV2, key: LedgerKey) -> None:
    entry = LedgerEntry(
        key=key,
        state=ContinuousState(metadata={"provider": "test"}),
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
    updated = store.read(key.as_path())
    assert updated is not None
    metadata = dict(updated.state.metadata or {})
    metadata.pop("feedback_rollup", None)
    metadata.pop("ledger_hash", None)
    metadata.pop("ledger_prev_hash", None)
    updated.state.metadata = metadata
    store.write(updated)


def test_web4_decode_includes_feedback_rollup_even_when_metadata_missing() -> None:
    app = FastAPI()
    app.state.db = {}
    app.include_router(web4_router)
    client = TestClient(app)

    store = LedgerStoreV2(app.state.db)
    key = LedgerKey(namespace="chat-demo", identifier="WX-1772002817300")
    _seed_feedback_without_rollup(store, key)

    resp = client.post("/web4/decode", json={"coordinate": key.as_path()})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("coord") == key.as_path()
    meta = body.get("meta") or {}
    rollup = meta.get("feedback_rollup")
    assert isinstance(rollup, dict)
    assert int(rollup.get("actors") or 0) >= 1

