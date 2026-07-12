"""Tests for Qp data-model backfill (DS-REVIEW-193 P2-02)."""

from __future__ import annotations

import json

from fastapi import FastAPI

from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.qp_coordinate import QpCoordinate, derive_p_adic_coordinate
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2


def _seed_entry(
    store: LedgerStoreV2,
    namespace: str,
    identifier: str,
    content: str,
) -> str:
    entry = LedgerEntry(
        key=LedgerKey(namespace=namespace, identifier=identifier),
        state=ContinuousState(metadata={"content": content}),
    )
    store.write(entry)
    return entry.key.as_path()


def test_write_backfills_p_adic_coordinate():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "entity", "A-1", "hello world")

    entry = store.read(ledger_id)
    assert entry is not None
    coord = entry.state.metadata.get("p_adic_coordinate")
    assert isinstance(coord, dict)
    assert "kernel_node" in coord
    assert "metric_prime" in coord
    assert "unit_digits" in coord


def test_p_adic_coordinate_round_trips():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "entity", "A-1", "hello world")

    entry = store.read(ledger_id)
    coord_dict = entry.state.metadata["p_adic_coordinate"]
    coord = QpCoordinate.from_dict(coord_dict)
    assert coord.kernel_node == coord_dict["kernel_node"]
    assert coord.metric_prime == coord_dict["metric_prime"]


def test_dual_complement_and_mediator_populated():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "entity", "A-1", "hello world")

    entry = store.read(ledger_id)
    coord_dict = entry.state.metadata["p_adic_coordinate"]
    assert coord_dict["dual_complement"]
    assert coord_dict.get("mediator_state") is not None


def test_reindex_backfills_missing_p_adic_coordinate():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "entity", "A-1", "hello world")

    # Remove the coordinate from the overlay to simulate a legacy entry.
    overlay_key = store._overlay_key(ledger_id)
    overlay = json.loads(db[overlay_key])
    del overlay["metadata"]["p_adic_coordinate"]
    db[overlay_key] = json.dumps(overlay).encode()

    # Reindex should backfill the missing coordinate.
    from backend.search.reindex import reindex_all

    app = FastAPI()
    app.state.db = db
    reindex_all(app, entity="entity")

    entry = store.read(ledger_id)
    assert entry is not None
    assert "p_adic_coordinate" in entry.state.metadata


def test_reindex_is_idempotent():
    db: dict[bytes, bytes] = {}
    store = LedgerStoreV2(db)
    ledger_id = _seed_entry(store, "entity", "A-1", "hello world")

    from backend.search.reindex import reindex_all

    app = FastAPI()
    app.state.db = db
    reindex_all(app, entity="entity")
    first = store.read(ledger_id).state.metadata["p_adic_coordinate"]
    reindex_all(app, entity="entity")
    second = store.read(ledger_id).state.metadata["p_adic_coordinate"]
    assert first == second


def test_derive_p_adic_coordinate_from_kernel_exponents():
    metadata = {
        "kernel_prime_exponents": {
            13: 1,   # Eq5 Persistence
            17: 2,   # Eq6 State Auditor (dominant)
            23: 1,   # body tier, ignored
        }
    }
    coord = derive_p_adic_coordinate(metadata)
    assert coord is not None
    assert coord.kernel_node == "Eq6"
    assert coord.metric_prime == 17
    assert coord.dual_complement == "Eq2"
    assert coord.mediator_state is not None
    assert coord.mediator_state.kernel_node == "Eq9"  # S2 -> Grace
