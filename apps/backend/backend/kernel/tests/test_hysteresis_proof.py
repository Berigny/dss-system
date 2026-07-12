"""Tests for backend/kernel/hysteresis_proof.py and ledger service wiring."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.kernel import constants
from backend.kernel.base_foundation import BaseFoundationService
from backend.kernel.hysteresis_proof import HysteresisProof, HysteresisProofError
from backend.kernel.rocksdb_layer_store import RocksDBLayerStore
from backend.services.ledger_service import LedgerService


def _clay_ledger() -> dict[str, dict]:
    return {
        "ethics/lawfulness/refusal": {
            "v_values": [6, 6, 6],
            "block_height": 100,
            "value": "clean_refusal",
        },
    }


def test_valid_hysteresis_proof_passes() -> None:
    ledger = _clay_ledger()
    proof = HysteresisProof.generate(
        ledger,
        "ethics/lawfulness/refusal",
        previous_block_height=100,
        proposed_block_height=150,
    )
    assert proof["block_delta"] == 50
    assert HysteresisProof.is_valid_for_elevation(proof, ledger, 150) is True


def test_missing_hysteresis_proof_rejects_clay() -> None:
    db: dict[bytes, bytes] = {}
    BaseFoundationService(db).write_foundation("default")
    service = LedgerService(db, provision_id="default")

    # Seed a Clay state so the ledger has a root.
    service.write_layer_entry(
        {
            "coord": "ethics/lawfulness/refusal",
            "block_height": 100,
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
            "elevation_bundle": {"hysteresis_proof": None},
        }
    )

    with pytest.raises(HTTPException, match="invalid or missing hysteresis proof"):
        service.write_layer_entry(
            {
                "coord": "ethics/lawfulness/refusal/firm_boundary",
                "block_height": 150,
                "v_awareness": 6,
                "v_unity": 6,
                "v_ethics": 6,
                "elevation_bundle": {},
            }
        )


def test_invalid_hysteresis_root_rejects() -> None:
    ledger = _clay_ledger()
    proof = HysteresisProof.generate(
        ledger,
        "ethics/lawfulness/refusal",
        previous_block_height=100,
        proposed_block_height=150,
    )
    proof["clay_merkle_root"] = 12345
    assert HysteresisProof.is_valid_for_elevation(proof, ledger, 150) is False


def test_block_delta_exceeds_window_rejects() -> None:
    ledger = _clay_ledger()
    with pytest.raises(HysteresisProofError, match="exceeds MAX_HYSTERESIS_WINDOW"):
        HysteresisProof.generate(
            ledger,
            "ethics/lawfulness/refusal",
            previous_block_height=100,
            proposed_block_height=500,
        )


def test_sand_loam_not_blocked_by_hysteresis() -> None:
    db: dict[bytes, bytes] = {}
    BaseFoundationService(db).write_foundation("default")
    service = LedgerService(db, provision_id="default")

    # Sand write without hysteresis proof is allowed.
    layer = service.write_layer_entry(
        {
            "coord": "ethics/lawfulness/refusal",
            "block_height": 100,
            "v_awareness": 1,
            "v_unity": 1,
            "v_ethics": 1,
        }
    )
    assert layer == constants.LAYER_SAND

    # Loam write without hysteresis proof is allowed.
    layer = service.write_layer_entry(
        {
            "coord": "ethics/lawfulness/refusal/firm_boundary",
            "block_height": 100,
            "v_awareness": 3,
            "v_unity": 3,
            "v_ethics": 3,
        }
    )
    assert layer == constants.LAYER_LOAM


def test_hysteresis_proof_stored_in_elevation_bundle() -> None:
    db: dict[bytes, bytes] = {}
    BaseFoundationService(db).write_foundation("default")
    service = LedgerService(db, provision_id="default")

    # Seed Clay state.
    service.write_layer_entry(
        {
            "coord": "ethics/lawfulness/refusal",
            "block_height": 100,
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
            "elevation_bundle": {"hysteresis_proof": None},
        }
    )

    # Generate a valid hysteresis proof.
    clay_ledger = service.layer_store.clay_ledger()
    proof = HysteresisProof.generate(
        clay_ledger,
        "ethics/lawfulness/refusal",
        previous_block_height=100,
        proposed_block_height=150,
    )

    # Clay elevation with valid proof succeeds.
    layer = service.write_layer_entry(
        {
            "coord": "ethics/lawfulness/refusal/firm_boundary",
            "block_height": 150,
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
            "elevation_bundle": {"hysteresis_proof": proof},
        }
    )
    assert layer == constants.LAYER_CLAY

    # Proof is retrievable from the persisted elevation bundle.
    clay_entries = service.layer_store.list_layer(constants.LAYER_CLAY)
    new_entries = [e for e in clay_entries if b"firm_boundary" in e[0]]
    assert len(new_entries) == 1
    stored_proof = new_entries[0][1].get("elevation_bundle", {}).get("hysteresis_proof")
    assert stored_proof is not None
    assert stored_proof["previous_coord"] == "ethics/lawfulness/refusal"
