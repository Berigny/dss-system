"""Hysteresis Merkle inclusion proof generation and verification.

Before a Loam state can be elevated to Clay, the prover must demonstrate a
Merkle inclusion path to the immediately preceding Clay state and show that the
block delta is within ``MAX_HYSTERESIS_WINDOW``.
"""

from __future__ import annotations

from typing import Any, Mapping

from backend.kernel.merkle_poseidon import MerkleTree, leaf_hash
from backend.kernel.telos_consistency_circuit import MAX_HYSTERESIS_WINDOW


class HysteresisProofError(ValueError):
    """Raised when a hysteresis proof cannot be generated or verified."""


class HysteresisProof:
    """Generate and verify Merkle inclusion proofs for Clay hysteresis."""

    @staticmethod
    def _build_tree(clay_ledger: Mapping[str, Mapping[str, Any]]) -> tuple[MerkleTree, dict[str, int]]:
        """Return a Merkle tree and coord -> leaf index map for ``clay_ledger``."""
        coords = sorted(clay_ledger.keys())
        leaves = [leaf_hash(coord, clay_ledger[coord]["v_values"]) for coord in coords]
        tree = MerkleTree(leaves)
        index_map = {coord: idx for idx, coord in enumerate(coords)}
        return tree, index_map

    @classmethod
    def generate(
        cls,
        clay_ledger: Mapping[str, Mapping[str, Any]],
        previous_coord: str,
        *,
        previous_block_height: int,
        proposed_block_height: int,
    ) -> dict[str, Any]:
        """Generate a hysteresis proof for elevation from ``previous_coord``.

        Raises:
            HysteresisProofError: if ``previous_coord`` is absent from the Clay
                ledger or the block delta exceeds ``MAX_HYSTERESIS_WINDOW``.
        """
        block_delta = proposed_block_height - previous_block_height
        if block_delta > MAX_HYSTERESIS_WINDOW:
            raise HysteresisProofError(
                f"block_delta {block_delta} exceeds MAX_HYSTERESIS_WINDOW "
                f"{MAX_HYSTERESIS_WINDOW}"
            )
        if block_delta < 0:
            raise HysteresisProofError("proposed block is before previous Clay state")

        tree, index_map = cls._build_tree(clay_ledger)
        if previous_coord not in index_map:
            raise HysteresisProofError(
                f"previous_coord {previous_coord} not in Clay ledger"
            )

        idx = index_map[previous_coord]
        previous_state = clay_ledger[previous_coord]
        merkle_path = tree.get_proof(idx)

        return {
            "scheme": "hysteresis_merkle_stub",
            "clay_merkle_root": tree.root,
            "previous_coord": previous_coord,
            "previous_v_values": list(previous_state["v_values"]),
            "previous_block_height": previous_block_height,
            "proposed_block_height": proposed_block_height,
            "block_delta": block_delta,
            "merkle_path": merkle_path,
            "merkle_leaf": leaf_hash(previous_coord, previous_state["v_values"]),
        }

    @classmethod
    def verify(
        cls,
        proof: Mapping[str, Any],
        clay_root: int,
        previous_coord: str,
        previous_v_values: list[int],
    ) -> bool:
        """Verify a hysteresis proof against the current Clay Merkle root."""
        if proof.get("scheme") != "hysteresis_merkle_stub":
            return False
        if proof.get("clay_merkle_root") != clay_root:
            return False
        if proof.get("previous_coord") != previous_coord:
            return False
        if list(proof.get("previous_v_values", [])) != list(previous_v_values):
            return False
        block_delta = int(proof.get("block_delta", 0))
        if block_delta > MAX_HYSTERESIS_WINDOW or block_delta < 0:
            return False

        leaf = leaf_hash(previous_coord, previous_v_values)
        return MerkleTree.verify_proof(clay_root, leaf, proof.get("merkle_path", []))

    @classmethod
    def is_valid_for_elevation(
        cls,
        proof: Mapping[str, Any] | None,
        clay_ledger: Mapping[str, Mapping[str, Any]],
        proposed_block_height: int,
    ) -> bool:
        """Convenience check used by the ledger service before Clay writes.

        Returns True if ``proof`` is present, its block delta is within the
        window, and its Merkle path verifies against the current Clay ledger.
        """
        if proof is None:
            return False
        previous_coord = proof.get("previous_coord")
        if not previous_coord or previous_coord not in clay_ledger:
            return False
        previous_state = clay_ledger[previous_coord]
        previous_block_height = int(previous_state.get("block_height", 0))
        block_delta = proposed_block_height - previous_block_height
        if block_delta > MAX_HYSTERESIS_WINDOW or block_delta < 0:
            return False
        tree, _ = cls._build_tree(clay_ledger)
        return cls.verify(proof, tree.root, previous_coord, previous_state["v_values"])
