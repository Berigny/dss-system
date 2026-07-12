"""Write-once ledger body layer tests (DS-REVIEW-192 P1-12)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2


def _seed_entry(
    store: LedgerStoreV2,
    namespace: str,
    identifier: str,
    content: str,
    created_at: datetime | None = None,
) -> str:
    entry = LedgerEntry(
        key=LedgerKey(namespace=namespace, identifier=identifier),
        state=ContinuousState(metadata={"content": content}),
        notes="seed",
        created_at=created_at or datetime.now(timezone.utc),
    )
    store.write(entry)
    return entry.key.as_path()


def test_body_is_content_addressed():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "immutable", "WX-1", "hello")

    body_hash = json.loads(db[store._overlay_key(ledger_id)])["body_hash"]
    assert f"body:{body_hash}".encode() in db


def test_body_overwrite_refused_when_hash_collides():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "immutable", "WX-1", "hello")

    # Pre-compute the body hash for a second entry and poison that body slot.
    fixed_ts = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)
    entry2 = LedgerEntry(
        key=LedgerKey(namespace="immutable", identifier="WX-2"),
        state=ContinuousState(metadata={"content": "world"}),
        created_at=fixed_ts,
    )
    body_bytes2 = store._canonical_body_bytes(entry2)
    body_hash2 = store._body_hash(body_bytes2)
    db[store._body_key(body_hash2)] = b"tampered-body"

    with pytest.raises(ValueError, match="cannot overwrite existing body"):
        store.write(entry2)


def test_overlay_update_preserves_body():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "immutable", "WX-1", "hello")

    original_body_hash = json.loads(db[store._overlay_key(ledger_id)])["body_hash"]
    store.set_pinned(ledger_id, True)

    updated = store.read(ledger_id)
    assert updated is not None
    assert updated.pinned is True
    assert json.loads(db[store._overlay_key(ledger_id)])["body_hash"] == original_body_hash


def test_coordinate_lookup_returns_body_after_overlay_update():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "immutable", "WX-1", "hello")

    store.submit_feedback(
        ledger_id,
        actor_id="human:test",
        actor_type="human",
        rating=3,
        reason="approved",
        source="test",
    )

    updated = store.read(ledger_id)
    assert updated is not None
    assert updated.state.metadata["content"] == "hello"
    assert "feedback_rollup" in updated.state.metadata


def test_reindex_deduplicates_identical_bodies():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    fixed_ts = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

    for i in range(3):
        _seed_entry(store, "immutable", f"WX-{i}", "shared content", created_at=fixed_ts)

    body_keys = {k for k in db if k.startswith(b"body:")}
    assert len(body_keys) == 1

    overlay_keys = {k for k in db if k.startswith(b"overlay:")}
    assert len(overlay_keys) == 3


def test_set_pinned_appends_overlay_and_preserves_body_hash():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "immutable", "WX-1", "hello")

    original_body_hash = json.loads(db[store._overlay_key(ledger_id)])["body_hash"]
    store.set_pinned(ledger_id, True)

    updated = store.read(ledger_id)
    assert updated is not None
    assert updated.pinned is True
    assert json.loads(db[store._overlay_key(ledger_id)])["body_hash"] == original_body_hash

    history_keys = [k for k in db if k.startswith(b"overlay-history:")]
    assert len(history_keys) == 1


def test_submit_feedback_appends_overlay_history():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "immutable", "WX-1", "hello")

    store.submit_feedback(
        ledger_id,
        actor_id="human:test",
        actor_type="human",
        rating=3,
        reason="approved",
        source="test",
    )

    history_keys = [k for k in db if k.startswith(b"overlay-history:")]
    assert len(history_keys) == 1


def test_update_metadata_overlay_does_not_rewrite_body():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "immutable", "WX-1", "hello")

    original_body_hash = json.loads(db[store._overlay_key(ledger_id)])["body_hash"]
    updated = store.update_metadata_overlay(
        ledger_id, {"derived_tag": "metadata-only"}
    )

    assert updated is not None
    assert updated.state.metadata["derived_tag"] == "metadata-only"
    assert json.loads(db[store._overlay_key(ledger_id)])["body_hash"] == original_body_hash
