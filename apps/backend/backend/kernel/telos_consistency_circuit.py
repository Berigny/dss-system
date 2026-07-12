"""Telos Consistency ZK circuit stub for Clay elevation.

This module implements the constraint system described in
``backlog_reqs/paper/telos_consistency_zk_circuit.md``. It is a structural stub:
it builds a witness, runs local constraint checks, and returns a proof object
that can be verified against public inputs. A production deployment would
replace the internal checks with a SNARK/STARK proving backend.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping, Sequence

from backend.kernel import constants
from backend.kernel.merkle_poseidon import MerkleTree, leaf_hash, poseidon_hash_stub
from backend.kernel.quaternary_gates import QuaternaryGate

MAX_HYSTERESIS_WINDOW: int = 256


def _coord_prefix(coord: str) -> tuple[str, ...]:
    """Return the COORD branch components."""
    return tuple(part.strip() for part in coord.lower().split("/") if part.strip())


def _coord_derivation_valid(parent: str, child: str) -> bool:
    """Return True if ``child`` is the same as or hierarchically under ``parent``."""
    if parent == child:
        return True
    parent_parts = _coord_prefix(parent)
    child_parts = _coord_prefix(child)
    if len(child_parts) <= len(parent_parts):
        return False
    return child_parts[: len(parent_parts)] == parent_parts


def _action_hash(action: Mapping[str, Any]) -> int:
    """Return a deterministic public hash for an action."""
    payload = {
        "coord": action.get("coord", ""),
        "semantic_atoms": sorted(
            (a.get("coord", ""), a.get("value", "")) for a in action.get("semantic_atoms", [])
        ),
    }
    text = str(sorted(payload.items())).encode("utf-8")
    return int(hashlib.sha256(text).hexdigest(), 16) % (2**61 - 1)


class TelosConsistencyCircuit:
    """ZK stub proving Telos Consistency for Loam -> Clay elevation."""

    def __init__(
        self,
        clay_ledger: Mapping[str, Mapping[str, Any]],
        coord_topology: Sequence[str],
    ) -> None:
        self.clay_ledger = dict(clay_ledger)
        self.coord_topology = set(coord_topology)
        self._leaf_index = {coord: idx for idx, coord in enumerate(self.clay_ledger)}
        self._merkle = MerkleTree(
            [
                leaf_hash(coord, state["v_values"])
                for coord, state in self.clay_ledger.items()
            ]
        )
        self.clay_root = self._merkle.root

    def prove(
        self,
        new_action: Mapping[str, Any],
        private_v_values: Sequence[int],
        elevation_bundle: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build a witness and return a stub proof object.

        The returned proof contains a ``valid`` boolean. If any constraint fails,
        ``valid`` is ``False`` and the failure reason is recorded in
        ``constraint_log``.
        """
        new_coord = str(new_action.get("coord", ""))
        new_hash = _action_hash(new_action)

        public_inputs = {
            "clay_merkle_root": self.clay_root,
            "new_action_hash": new_hash,
            "new_action_coord": new_coord,
            "gate_values_public": [
                int(private_v_values[0]),
                int(private_v_values[1]),
                int(private_v_values[2]),
            ],
        }

        merkle_path: list[tuple[int, str]] = []
        leaf = 0
        if new_coord in self._leaf_index:
            idx = self._leaf_index[new_coord]
            state = self.clay_ledger[new_coord]
            leaf = leaf_hash(new_coord, state["v_values"])
            merkle_path = self._merkle.get_proof(idx)
        elif self.clay_ledger:
            # Bind to the first Clay leaf as a membership anchor when the coord
            # itself is novel.
            first_coord = next(iter(self.clay_ledger))
            first_state = self.clay_ledger[first_coord]
            leaf = leaf_hash(first_coord, first_state["v_values"])
            merkle_path = self._merkle.get_proof(0)

        witness = {
            "public_inputs": public_inputs,
            "new_action": dict(new_action),
            "v_values_private": list(private_v_values),
            "elevation_bundle": dict(elevation_bundle),
            "merkle_path": merkle_path,
            "merkle_leaf": leaf,
        }

        constraint_log: list[dict[str, Any]] = []
        valid = True

        checks = [
            ("merkle_binding", self._check_merkle_binding),
            ("non_compensatory", self._check_non_compensatory),
            ("monotonic_v", self._check_monotonic_v),
            ("non_contradiction", self._check_non_contradiction),
            ("novelty_path", self._check_novelty_path),
            ("hysteresis", self._check_hysteresis),
        ]

        for name, checker in checks:
            passed, detail = checker(witness)
            constraint_log.append({"name": name, "passed": passed, "detail": detail})
            if not passed:
                valid = False

        proof = {
            "valid": valid,
            "scheme": "telos_consistency_stub",
            "timestamp": time.time(),
            "public_inputs": public_inputs,
            "private_commitment": poseidon_hash_stub(
                public_inputs["new_action_hash"],
                int(private_v_values[0]),
                int(private_v_values[1]),
                int(private_v_values[2]),
            ),
            "merkle_path": merkle_path,
            "merkle_leaf": leaf,
            "elevation_bundle_commitment": poseidon_hash_stub(
                *[
                    int.from_bytes(p.encode("utf-8"), "big")
                    for p in elevation_bundle.get("elevation_proofs", [])
                ]
            ),
            "constraint_log": constraint_log,
        }
        return proof

    def verify(
        self,
        proof: Mapping[str, Any],
        public_inputs: Mapping[str, Any],
    ) -> bool:
        """Verify ``proof`` against the provided ``public_inputs``."""
        if not proof.get("valid"):
            return False
        pi = proof.get("public_inputs") or {}
        for key in ("clay_merkle_root", "new_action_hash", "new_action_coord"):
            if pi.get(key) != public_inputs.get(key):
                return False
        # Verify the stored Merkle path still binds to the claimed root.
        path = proof.get("merkle_path", [])
        if self.clay_ledger and not MerkleTree.verify_proof(
            pi.get("clay_merkle_root", 0), proof.get("merkle_leaf", 0), path
        ):
            return False
        return True

    # ------------------------------------------------------------------
    # Constraint checks
    # ------------------------------------------------------------------

    def _check_merkle_binding(self, witness: Mapping[str, Any]) -> tuple[bool, str]:
        root = witness["public_inputs"]["clay_merkle_root"]
        leaf = witness["merkle_leaf"]
        path = witness["merkle_path"]
        if not self.clay_ledger:
            return True, "empty clay ledger; no binding required"
        if MerkleTree.verify_proof(root, leaf, path):
            return True, "merkle path verifies against clay root"
        return False, "merkle path does not verify against clay root"

    def _check_non_compensatory(
        self, witness: Mapping[str, Any]
    ) -> tuple[bool, str]:
        v_values = witness["v_values_private"]
        if len(v_values) != 3:
            return False, "expected exactly three v-values"
        if any(v <= 0 for v in v_values):
            return False, "non-compensatory: zero or negative gate collapses proof"
        if not QuaternaryGate.elevation_allowed(v_values[0], v_values[1], v_values[2]):
            return False, "non-compensatory: all gates must be >= 6 for Clay"
        return True, "all gates positive and >= 6"

    def _check_monotonic_v(self, witness: Mapping[str, Any]) -> tuple[bool, str]:
        new_coord = witness["public_inputs"]["new_action_coord"]
        new_v = witness["v_values_private"]
        existing = self.clay_ledger.get(new_coord)
        if existing is None:
            return True, "novel coord; monotonic check not applicable"
        old_v = existing["v_values"]
        if any(new_v[i] < old_v[i] for i in range(3)):
            return False, f"v regression: old={old_v} new={new_v}"
        return True, f"v monotonic: old={old_v} new={new_v}"

    def _check_non_contradiction(
        self, witness: Mapping[str, Any]
    ) -> tuple[bool, str]:
        new_action = witness["new_action"]
        new_v = witness["v_values_private"]
        for atom in new_action.get("semantic_atoms", []):
            coord = atom.get("coord")
            if coord is None:
                continue
            existing = self.clay_ledger.get(coord)
            if existing is None:
                continue
            # Exact value match is allowed.
            if atom.get("value") == existing.get("value"):
                continue
            # Otherwise require valid supercession.
            if not _coord_derivation_valid(coord, new_action.get("coord", "")):
                return False, f"coord {coord} not derivable from {new_action.get('coord')}"
            old_v = existing["v_values"]
            if any(new_v[i] < old_v[i] for i in range(3)):
                return False, f"supercession v regression at {coord}"
            old_product = QuaternaryGate.evaluate(*old_v)["checksum_factor_product"]
            new_product = QuaternaryGate.evaluate(*new_v)["checksum_factor_product"]
            if new_product < old_product:
                return False, f"telos direction negative at {coord}"
        return True, "no contradictions with existing Clay"

    def _check_novelty_path(self, witness: Mapping[str, Any]) -> tuple[bool, str]:
        new_action = witness["new_action"]
        elevation = witness["elevation_bundle"]
        novel_atoms = [
            atom
            for atom in new_action.get("semantic_atoms", [])
            if atom.get("coord") and atom.get("coord") not in self.clay_ledger
        ]
        if not novel_atoms:
            return True, "all atoms already in Clay"
        if not elevation.get("novelty_flag"):
            return False, "novel atoms require novelty_flag in elevation bundle"
        new_coord = new_action.get("coord", "")
        if new_coord and new_coord not in self.coord_topology:
            return False, f"coord {new_coord} not in topology"
        return True, "novelty path valid"

    def _check_hysteresis(self, witness: Mapping[str, Any]) -> tuple[bool, str]:
        elevation = witness["elevation_bundle"]
        if not elevation.get("previous_clay_proof"):
            return False, "missing previous_clay_proof"
        block_delta = int(elevation.get("block_delta", 0))
        if block_delta > MAX_HYSTERESIS_WINDOW:
            return False, f"block_delta {block_delta} exceeds MAX_HYSTERESIS_WINDOW"
        return True, f"hysteresis continuity within {block_delta} blocks"
