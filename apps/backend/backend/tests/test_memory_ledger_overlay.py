from __future__ import annotations

import json

from backend.fieldx_kernel.ledger import MemoryLedger


def test_replace_s2_records_overlay_without_mutating_base_state() -> None:
    db: dict[bytes, bytes] = {}
    ledger = MemoryLedger(db)
    entity = "overlay-e2e"

    # Seed canonical/base state.
    ledger.update_S2(entity, {"19": {"claims": [{"id": "base-claim"}]}})

    base_key = f"entity:{entity}:s2".encode()
    assert base_key in db
    base_before = json.loads(db[base_key])

    # Apply replace operation (destructive semantics) as overlay.
    ledger.replace_S2(entity, {"19": {"claims": [{"id": "overlay-claim"}]}})

    # Base payload remains unchanged.
    base_after = json.loads(db[base_key])
    assert base_after == base_before

    # Materialized read reflects overlay.
    materialized = ledger.get_S2(entity)
    claims = materialized.get("19", {}).get("claims", [])
    assert claims == [{"id": "overlay-claim"}]


def test_replace_s2_overlay_contains_lineage_fields() -> None:
    db: dict[bytes, bytes] = {}
    ledger = MemoryLedger(db)
    entity = "overlay-lineage"

    ledger.replace_S2(entity, {"11": {"summary_ref": "WX-123"}})

    overlay_key = f"entity:{entity}:s2:overlays".encode()
    assert overlay_key in db
    overlays = json.loads(db[overlay_key])
    assert isinstance(overlays, list) and overlays

    event = overlays[-1]
    assert event.get("kind") == "replace_s2_v1"
    assert event.get("derived_from") == f"entity:{entity}:s2"
    assert isinstance(event.get("seq"), int)
    assert isinstance(event.get("created_at"), str)


def test_update_s2_compacts_and_clears_overlays() -> None:
    db: dict[bytes, bytes] = {}
    ledger = MemoryLedger(db)
    entity = "overlay-compaction"

    ledger.replace_S2(entity, {"19": {"claims": [{"id": "overlay-claim"}]}})
    ledger.update_S2(entity, {"11": {"summary_ref": "WX-999"}})

    # Materialized state keeps overlay effect and includes the update.
    materialized = ledger.get_S2(entity)
    claims = materialized.get("19", {}).get("claims", [])
    assert claims == [{"id": "overlay-claim"}]
    assert materialized.get("11", {}).get("summary_ref") == "WX-999"

    # Overlay list cleared after compaction.
    overlay_key = f"entity:{entity}:s2:overlays".encode()
    assert overlay_key not in db
