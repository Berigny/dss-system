from __future__ import annotations

import json

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.api import ledger as ledger_api
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(ledger_api.router, prefix="/ledger", tags=["ledger"])
    return TestClient(app)


def test_history_entities_lists_compact_entities_with_counts() -> None:
    client = _make_client()
    db = client.app.state.db
    db[b"chat-37a8eec1:ae95ca73:WX-1"] = b"{}"
    db[b"chat-37a8eec1:ae95ca73:WX-2"] = b"{}"
    db[b"chat-de6dc544:27e27f9d:WX-3"] = b"{}"
    db[b"bucket:23:index"] = b"[]"

    resp = client.get("/ledger/history_entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entities"][0] == "37a8eec1:ae95ca73"
    assert "de6dc544:27e27f9d" in body["entities"]
    counts = body.get("entity_counts") or {}
    assert int(counts.get("37a8eec1:ae95ca73", 0)) == 2
    assert int(counts.get("de6dc544:27e27f9d", 0)) == 1


def test_history_entities_includes_chat_prefixed_named_ledgers() -> None:
    client = _make_client()
    db = client.app.state.db
    db[b"chat-gate-alpha:ATT-1"] = b"{}"
    db[b"chat-gate-alpha:ATT-2"] = b"{}"
    db[b"chat-post-deploy-smoke:ATT-3"] = b"{}"

    resp = client.get("/ledger/history_entities")
    assert resp.status_code == 200
    body = resp.json()

    entities = body.get("entities") or []
    assert "gate-alpha" in entities
    assert "post-deploy-smoke" in entities

    counts = body.get("entity_counts") or {}
    assert int(counts.get("gate-alpha", 0)) == 2
    assert int(counts.get("post-deploy-smoke", 0)) == 1


def test_ledger_inventory_includes_registered_and_discovered() -> None:
    client = _make_client()
    db = client.app.state.db
    db[b"__ledgers__"] = json.dumps(["gate-alpha"]).encode()
    db[b"__ledgers_v1__"] = json.dumps({"version": 1, "ledgers": {"gate-beta": {}}}).encode()
    db[b"chat-de6dc544:27e27f9d:WX-3"] = b"{}"

    resp = client.get("/ledger/ledgers/inventory")
    assert resp.status_code == 200
    body = resp.json()

    ledgers = body.get("ledgers") or []
    assert "default" in ledgers
    assert "gate-alpha" in ledgers
    assert "gate-beta" in ledgers

    namespaces = body.get("namespaces") or []
    assert "chat-de6dc544:27e27f9d" in namespaces


def test_history_namespace_candidates_include_session_alias(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_DEFAULT_LEDGER", "chat-demo")
    request = Request({"type": "http", "headers": [], "query_string": b""})

    candidates = ledger_api._history_namespace_candidates(request, "chat-demo-session")

    assert "chat-demo-session" in candidates
    assert "chat-demo" in candidates


def test_entry_coord_meta_prefers_runtime_identity_when_persisted_coord_meta_missing() -> None:
    entry = LedgerEntry(
        key=LedgerKey(namespace="chat-demo", identifier="WX-123"),
        state=ContinuousState(
            metadata={
                "runtime_identity": {
                    "runtime_namespace": "chat-demo",
                    "ledger_canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                }
            }
        ),
        created_at=datetime.now(timezone.utc),
        notes=None,
    )

    coord_meta = ledger_api._entry_coord_meta(entry)

    assert coord_meta == {
        "coord": "chat-demo:WX-123",
        "coord_type": "WX",
        "identifier": "WX-123",
        "runtime_namespace": "chat-demo",
        "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
        "canonical_subject_source": "did:web:ledger",
    }


def test_ordered_history_includes_wx_entries_without_chat_role() -> None:
    client = _make_client()
    request = Request({"type": "http", "headers": [], "query_string": b"", "app": client.app})
    store = ledger_api.LedgerService.from_request(request).store
    store.write(
        LedgerEntry(
            key=LedgerKey(namespace="chat-demo", identifier="WX-9C2621E0-1775654602"),
            state=ContinuousState(
                metadata={
                    "payload": {
                        "blobs": {
                            "BLOB:WX:ANS-01": "Okay, I'm going to give you a detailed rundown of the internal process."
                        }
                    },
                    "skim": {"one_line": "Detailed rundown of the internal process."},
                    "runtime_identity": {
                        "runtime_namespace": "chat-demo",
                        "ledger_canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    },
                }
            ),
            created_at=datetime(2026, 4, 8, 13, 23, 22, tzinfo=timezone.utc),
            notes=None,
        )
    )

    resp = client.get("/ledger/history/chat-demo")
    assert resp.status_code == 200
    body = resp.json()
    history = body.get("history") or []
    assert len(history) == 1
    assert history[0]["entry_id"] == "WX-9C2621E0-1775654602"
    assert history[0]["role"] == "assistant"
    assert history[0]["coordinate"] == "chat-demo:WX-9C2621E0-1775654602"
    assert "detailed rundown" in history[0]["content"].lower()


def test_ordered_history_prefers_payload_blob_content_for_wx_entries() -> None:
    entry = LedgerEntry(
        key=LedgerKey(namespace="chat-demo", identifier="WX-blob-1"),
        state=ContinuousState(
            metadata={
                "payload": {"blobs": {"b1": "Blob-backed answer text."}},
                "runtime_identity": {
                    "runtime_namespace": "chat-demo",
                    "ledger_canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                },
            }
        ),
        created_at=datetime.now(timezone.utc),
        notes=None,
    )

    assert ledger_api._history_entry_content(entry, entry.state.metadata) == "Blob-backed answer text."


def test_ordered_history_ignores_entry_notes_when_content_missing() -> None:
    entry = LedgerEntry(
        key=LedgerKey(namespace="chat-demo", identifier="WX-notes-1"),
        state=ContinuousState(
            metadata={
                "runtime_identity": {
                    "runtime_namespace": "chat-demo",
                    "ledger_canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                }
            }
        ),
        created_at=datetime.now(timezone.utc),
        notes="hmac local-to-cloud replay",
    )

    assert ledger_api._history_entry_content(entry, entry.state.metadata) == ""
