"""Tests for resolver read tiers: blob_full and kernel_projections."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.agent_writes import record_full_payload_blob, record_turn
from backend.api.resolver import router as resolver_router
from backend.fieldx_kernel.ledger import MemoryLedger
from backend.fieldx_kernel.substrate import LedgerStoreV2, MemorySubstrate


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(resolver_router)
    return TestClient(app)


def test_tiered_resolve_blob_full_returns_raw_text() -> None:
    client = _make_client()
    db = client.app.state.db
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)

    raw_text = "This is the intact raw payload for tiered resolution."
    blob = record_full_payload_blob(
        entity="tier-ns",
        raw_text=raw_text,
        kind="attachment",
        metadata={},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    assert blob is not None

    resp = client.post(
        "/resolve/tiered",
        json={
            "namespace": "tier-ns",
            "identifier": f"blob-{blob['blob_hash']}",
            "read_tier": "blob_full",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["read_tier"] == "blob_full"
    assert body["payload"]["type"] == "blob_full"
    assert body["payload"]["text"] == raw_text
    assert body["payload"]["coordinate"] == blob["coordinate"]


def test_tiered_resolve_kernel_projections_returns_projection_metadata() -> None:
    client = _make_client()
    db = client.app.state.db
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)

    raw_text = (
        "We must pay attention and focus with awareness. "
        "Together we align in unity and collaborate. "
        "We refuse to harm and choose ethical safety."
    )
    blob = record_full_payload_blob(
        entity="proj-tier-ns",
        raw_text=raw_text,
        kind="attachment",
        metadata={},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    assert blob is not None
    assert blob["projections"]

    resp = client.post(
        "/resolve/tiered",
        json={
            "namespace": "proj-tier-ns",
            "identifier": f"blob-{blob['blob_hash']}",
            "read_tier": "kernel_projections",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["read_tier"] == "kernel_projections"
    assert body["payload"]["type"] == "kernel_projections"
    assert body["payload"]["count"] > 0
    assert body["parent"]["quaternary_layer"] is not None
    assert isinstance(body["parent"]["checksum_336_satisfied"], bool)

    # Each projection should carry layer-store details.
    for projection in body["payload"]["projections"]:
        assert "coord" in projection
        assert "layer" in projection
        assert "v_awareness" in projection
        assert "v_unity" in projection
        assert "v_ethics" in projection


def test_tiered_resolve_blob_full_not_found_for_missing_blob() -> None:
    client = _make_client()
    store = LedgerStoreV2(client.app.state.db)
    substrate = MemorySubstrate(client.app.state.db)
    ledger = MemoryLedger(client.app.state.db)

    # Write a ledger entry that claims a blob coordinate but the blob text is missing.
    blob = record_full_payload_blob(
        entity="missing-ns",
        raw_text="existing text",
        kind="attachment",
        metadata={},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    assert blob is not None
    # Delete the blob payload but leave the ledger entry referencing it.
    blob_key = f"blob:{blob['coordinate']}".encode()
    if blob_key in client.app.state.db:
        del client.app.state.db[blob_key]

    resp = client.post(
        "/resolve/tiered",
        json={
            "namespace": "missing-ns",
            "identifier": f"blob-{blob['blob_hash']}",
            "read_tier": "blob_full",
        },
    )
    assert resp.status_code == 404


def test_tiered_resolve_default_skim_still_works() -> None:
    client = _make_client()
    db = client.app.state.db
    store = LedgerStoreV2(db)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)

    raw_text = "A short attachment for default tier testing."
    blob = record_full_payload_blob(
        entity="default-tier-ns",
        raw_text=raw_text,
        kind="attachment",
        metadata={},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    assert blob is not None

    resp = client.post(
        "/resolve/tiered",
        json={
            "namespace": "default-tier-ns",
            "identifier": f"blob-{blob['blob_hash']}",
            "read_tier": "public_skim",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["read_tier"] == "public_skim"
    assert "entry" in body
    assert "redaction" in body
