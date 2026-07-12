"""Per-agent ledger uniqueness tests (HENGE-011)."""

from __future__ import annotations

import pytest

from backend.kernel import constants
from backend.kernel.base_foundation import BaseFoundationService
from backend.kernel.merkle_poseidon import MerkleTree, leaf_hash
from backend.kernel.rocksdb_layer_store import RocksDBLayerStore


def _bootstrap_store(provision_id: str = "default") -> RocksDBLayerStore:
    db: dict[bytes, bytes] = {}
    BaseFoundationService(db).write_foundation(provision_id)
    return RocksDBLayerStore(db, provision_id=provision_id)


def _write_clay(
    store: RocksDBLayerStore,
    coord: str,
    values: tuple[int, int, int] = (6, 6, 6),
    content: str = "identical document content",
) -> None:
    store.write(
        {
            "layer": constants.LAYER_CLAY,
            "coord": coord,
            "v_awareness": values[0],
            "v_unity": values[1],
            "v_ethics": values[2],
            "value": content,
        }
    )


def test_two_agents_diverge_on_same_documents():
    """Agents with identical documents but different origins produce different roots."""
    alpha = _bootstrap_store("agent-alpha")
    beta = _bootstrap_store("agent-beta")

    _write_clay(alpha, "111")
    _write_clay(beta, "111")

    root_alpha = alpha.clay_merkle_root()
    root_beta = beta.clay_merkle_root()

    assert root_alpha != 0
    assert root_beta != 0
    assert root_alpha != root_beta


def test_different_prior_clay_produces_different_roots():
    """Prior Clay state makes identical new documents diverge."""
    alpha = _bootstrap_store("agent-alpha")
    beta = _bootstrap_store("agent-beta")

    _write_clay(alpha, "000", content="prior alpha-only state")
    _write_clay(alpha, "111")
    _write_clay(beta, "111")

    assert alpha.clay_merkle_root() != beta.clay_merkle_root()


def test_ledger_state_requires_proof_replay():
    """A Merkle proof from one agent cannot verify against another agent's root."""
    alpha = _bootstrap_store("agent-alpha")
    beta = _bootstrap_store("agent-beta")

    _write_clay(alpha, "111")
    _write_clay(beta, "111")

    root_alpha = alpha.clay_merkle_root()
    root_beta = beta.clay_merkle_root()

    ledger_alpha = alpha.clay_ledger()
    sorted_coords = sorted(ledger_alpha)
    coord = sorted_coords[0]
    state = ledger_alpha[coord]

    alpha_salt = alpha._origin_salt()
    beta_salt = beta._origin_salt()
    assert alpha_salt != beta_salt

    alpha_leaves = [
        leaf_hash(c, ledger_alpha[c]["v_values"], salt=alpha_salt)
        for c in sorted_coords
    ]
    proof = MerkleTree(alpha_leaves).get_proof(0)

    leaf_alpha = leaf_hash(coord, state["v_values"], salt=alpha_salt)
    leaf_beta = leaf_hash(coord, state["v_values"], salt=beta_salt)

    assert MerkleTree.verify_proof(root_alpha, leaf_alpha, proof)
    assert not MerkleTree.verify_proof(root_beta, leaf_alpha, proof)
    assert MerkleTree.verify_proof(root_beta, leaf_beta, proof)


def test_copied_raw_entry_does_not_transfer_root():
    """Copying a raw ledger value to another agent does not reproduce its root."""
    alpha = _bootstrap_store("agent-alpha")
    beta = _bootstrap_store("agent-beta")

    _write_clay(alpha, "222")
    _write_clay(beta, "111")

    root_alpha = alpha.clay_merkle_root()
    root_beta_before = beta.clay_merkle_root()

    # Simulate a naive state transfer: copy the raw Clay key/value from alpha to beta.
    clay_keys = [k for k in alpha._db if k.startswith(b"C:")]
    assert len(clay_keys) == 1
    beta._db[clay_keys[0]] = alpha._db[clay_keys[0]]

    root_beta_after = beta.clay_merkle_root()

    # Beta's root changes because it still hashes leaves with its own origin salt,
    # so alpha's ledger state is not portable.
    assert root_beta_after != root_beta_before
    assert root_beta_after != root_alpha
