from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.http import router as ledger_router
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(ledger_router)
    return TestClient(app)


def _seed_entry(store: LedgerStoreV2, namespace: str, identifier: str = "WX-1") -> str:
    entry = LedgerEntry(
        key=LedgerKey(namespace=namespace, identifier=identifier),
        state=ContinuousState(metadata={"content": "hello"}),
    )
    store.write(entry)
    return entry.key.as_path()


def test_chain_verify_namespace_reports_valid() -> None:
    client = _make_client()
    store = LedgerStoreV2(client.app.state.db)
    _seed_entry(store, "chat-chain-valid", "WX-1")

    resp = client.get("/ledger/chain/verify/chat-chain-valid")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("namespace") == "chat-chain-valid"
    assert body.get("valid") is True
    assert int(body.get("entries_checked") or 0) >= 1
    assert body.get("failure_reason") in {None, ""}


def test_chain_verify_namespace_reports_invalid_when_entry_hash_tampered() -> None:
    client = _make_client()
    store = LedgerStoreV2(client.app.state.db)
    ledger_id = _seed_entry(store, "chat-chain-tampered", "WX-9")

    # Tamper the mutable overlay metadata directly.
    overlay_key = store._overlay_key(ledger_id)
    raw = client.app.state.db[overlay_key]
    payload = json.loads(raw)
    payload["metadata"]["ledger_hash"] = "deadbeefdeadbeef"
    client.app.state.db[overlay_key] = json.dumps(payload).encode()

    resp = client.get("/ledger/chain/verify/chat-chain-tampered")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("valid") is False
    assert body.get("failure_reason") == "entry_hash_mismatch"
    assert body.get("failed_entry_id") == ledger_id


def test_read_entry_verify_chain_returns_409_on_tamper() -> None:
    client = _make_client()
    store = LedgerStoreV2(client.app.state.db)
    ledger_id = _seed_entry(store, "chat-read-verify", "WX-42")

    overlay_key = store._overlay_key(ledger_id)
    raw = client.app.state.db[overlay_key]
    payload = json.loads(raw)
    payload["metadata"]["ledger_hash"] = "0000000000000000"
    client.app.state.db[overlay_key] = json.dumps(payload).encode()

    resp = client.get(f"/ledger/read/{ledger_id}", params={"verify_chain": "true"})
    assert resp.status_code == 409
    body = resp.json()
    assert "Read-time chain verification failed" in str(body.get("detail") or "")
