from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.agent_writes import record_full_payload_blob, record_turn
from backend.api.ingest import router as ingest_router
from backend.api.resolver import router as resolver_router
from backend.fieldx_kernel.ledger import MemoryLedger
from backend.fieldx_kernel.substrate import LedgerStoreV2, MemorySubstrate
from backend.kernel.rocksdb_layer_store import RocksDBLayerStore


def _raw_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def _make_resolver_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(resolver_router)
    return TestClient(app)


def _make_ingest_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(ingest_router)
    app.include_router(resolver_router)
    return TestClient(app)


def test_record_full_payload_blob_creates_blob_and_ledger_entry() -> None:
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)

    raw_text = "The quick brown fox jumps over the lazy dog."
    result = record_full_payload_blob(
        entity="test-ns",
        raw_text=raw_text,
        kind="attachment",
        metadata={"source": "unit-test"},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )

    assert result is not None
    coordinate = result["coordinate"]
    blob_hash = result["blob_hash"]
    assert coordinate == f"test-ns:blob-{blob_hash}"
    assert blob_hash == _raw_hash(raw_text)

    entry = store.read(coordinate)
    assert entry is not None
    meta = entry.state.metadata or {}
    assert meta.get("full_payload") is True
    assert meta.get("full_payload_coord") == coordinate
    assert meta.get("blob_hash") == blob_hash
    assert meta.get("kind") == "attachment"

    assert store.read_blob_text(coordinate) == raw_text


def test_record_full_payload_blob_deduplicates_identical_payloads() -> None:
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)

    raw_text = "Duplicate payload."
    first = record_full_payload_blob(
        entity="test-ns",
        raw_text=raw_text,
        kind="attachment",
        metadata={},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    second = record_full_payload_blob(
        entity="test-ns",
        raw_text=raw_text,
        kind="attachment",
        metadata={},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )

    assert first is not None
    assert second is not None
    assert first["coordinate"] == second["coordinate"]
    assert second["deduplicated"] is True
    assert first["deduplicated"] is False


def test_record_turn_links_user_and_assistant_blobs() -> None:
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)

    user_text = "What is the capital of France?"
    assistant_text = "The capital of France is Paris."

    user_blob = record_full_payload_blob(
        entity="test-ns",
        raw_text=user_text,
        kind="chat",
        metadata={"role": "user"},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    assistant_blob = record_full_payload_blob(
        entity="test-ns",
        raw_text=assistant_text,
        kind="chat",
        metadata={"role": "assistant"},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )

    assert user_blob is not None
    assert assistant_blob is not None

    turn_result = record_turn(
        entity="test-ns",
        session_id="session-1",
        turn_id="turn-1",
        user_message=user_text,
        assistant_reply=assistant_text,
        user_message_coord=user_blob["coordinate"],
        assistant_reply_coord=assistant_blob["coordinate"],
        metadata={"provider": "test"},
        store=store,
    )

    assert turn_result is not None
    turn_coord = turn_result["coordinate"]
    entry = store.read(turn_coord)
    assert entry is not None
    meta = entry.state.metadata or {}
    assert meta.get("role") == "turn"
    assert meta.get("user_message_coord") == user_blob["coordinate"]
    assert meta.get("assistant_reply_coord") == assistant_blob["coordinate"]
    assert meta.get("session_id") == "session-1"
    assert meta.get("turn_id") == "turn-1"


def test_resolver_returns_full_payload_for_blob_entry() -> None:
    client = _make_resolver_client()
    db = client.app.state.db
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)

    raw_text = "Resolving the full payload should return this text."
    blob = record_full_payload_blob(
        entity="resolve-ns",
        raw_text=raw_text,
        kind="attachment",
        metadata={},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    assert blob is not None

    identifier = f"blob-{blob['blob_hash']}"
    resp = client.post("/resolve", json={"namespace": "resolve-ns", "identifier": identifier})
    assert resp.status_code == 200
    meta = resp.json()["state"]["metadata"]
    assert meta.get("full_payload") == raw_text
    assert meta.get("full_payload_coord") == blob["coordinate"]


def test_ingest_endpoint_creates_full_payload_blob_and_resolves_it() -> None:
    client = _make_ingest_client()
    raw_text = "This is the intact attachment text for Epic 26."
    headers = {"x-ledger-id": "ingest-ns"}

    ingest_resp = client.post(
        "/ingest",
        json={
            "entity": "ingest-ns",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": raw_text,
            "kind": "text",
            "metadata": {},
        },
        headers=headers,
    )
    assert ingest_resp.status_code == 200, ingest_resp.text
    body = ingest_resp.json()
    full_payload_coordinate = body.get("full_payload_coordinate")
    assert full_payload_coordinate is not None
    assert body["ingest_diagnostics"].get("full_payload") is True
    assert body["ingest_diagnostics"].get("kernel_projection_count", 0) > 0

    identifier = full_payload_coordinate.split(":", 1)[1]
    resolve_resp = client.post(
        "/resolve",
        json={"namespace": "ingest-ns", "identifier": identifier},
        headers=headers,
    )
    assert resolve_resp.status_code == 200
    meta = resolve_resp.json()["state"]["metadata"]
    assert meta.get("full_payload") == raw_text
    assert meta.get("full_payload_coord") == full_payload_coordinate


def test_record_full_payload_blob_writes_kernel_projections() -> None:
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)
    layer_store = RocksDBLayerStore(db, provision_id="default")

    # Use text that triggers enough atoms to push all three primes to level 3.
    raw_text = (
        "We must pay attention and focus with awareness. "
        "Together we align in unity and collaborate. "
        "We refuse to harm and choose ethical safety."
    )
    result = record_full_payload_blob(
        entity="proj-ns",
        raw_text=raw_text,
        kind="attachment",
        metadata={},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )

    assert result is not None
    assert result["projections"]
    assert result["composite_coord"] is not None
    assert result["quaternary_layer"] is not None
    assert isinstance(result["checksum_336_satisfied"], bool)

    entry = store.read(result["coordinate"])
    assert entry is not None
    meta = entry.state.metadata or {}
    assert meta.get("kernel_projections") == result["projections"]
    assert meta.get("composite_coord") == result["composite_coord"]
    assert meta.get("quaternary_layer") == result["quaternary_layer"]
    assert meta.get("checksum_336_satisfied") == result["checksum_336_satisfied"]

    # Each projection coordinate resolves in the layer store.
    for coord in result["projections"]:
        retrieved = layer_store.retrieve_by_coord(coord)
        assert retrieved, f"missing layer-store entry for {coord}"

    # Raw text must not appear in any layer-store key.
    for key in db:
        if isinstance(key, bytes) and key.startswith((b"S:", b"L:", b"C:", b"I:")):
            assert raw_text not in key.decode("utf-8")


def test_record_turn_writes_kernel_projections() -> None:
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)

    user_text = "Please pay attention and work together safely."
    assistant_text = "I will stay aware, aligned, and avoid harm."

    user_blob = record_full_payload_blob(
        entity="turn-proj-ns",
        raw_text=user_text,
        kind="chat",
        metadata={"role": "user"},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    assistant_blob = record_full_payload_blob(
        entity="turn-proj-ns",
        raw_text=assistant_text,
        kind="chat",
        metadata={"role": "assistant"},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )

    assert user_blob is not None
    assert assistant_blob is not None

    turn_result = record_turn(
        entity="turn-proj-ns",
        session_id="session-2",
        turn_id="turn-2",
        user_message=user_text,
        assistant_reply=assistant_text,
        user_message_coord=user_blob["coordinate"],
        assistant_reply_coord=assistant_blob["coordinate"],
        metadata={},
        store=store,
    )

    assert turn_result is not None
    meta = turn_result["metadata"]
    assert meta.get("kernel_projections")
    assert meta.get("composite_coord") is not None
    assert meta.get("quaternary_layer") is not None
    assert isinstance(meta.get("checksum_336_satisfied"), bool)

    layer_store = RocksDBLayerStore(db, provision_id="default")
    for coord in meta["kernel_projections"]:
        assert layer_store.retrieve_by_coord(coord)
