"""ZK-friendly Merkle tree stub.

This module provides a Merkle tree with a Poseidon-like algebraic hash stub
suitable for simulating the Telos Consistency circuit. The hash is not a real
Poseidon permutation; it is a deterministic field hash used to exercise Merkle
binding semantics without a heavy SNARK backend.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

# A 61-bit Mersenne prime used as the stub finite field.
POSEIDON_PRIME: int = 2**61 - 1


def poseidon_hash_stub(*inputs: int) -> int:
    """Return a deterministic field element from integer inputs."""
    payload = "|".join(str(int(x) % POSEIDON_PRIME) for x in inputs).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return int(digest, 16) % POSEIDON_PRIME


def leaf_hash(
    coord: str,
    v_values: Sequence[int],
    salt: int = 0,
) -> int:
    """Hash a Clay leaf from its COORD, valuation vector, and optional origin salt."""
    inputs: list[int] = [int.from_bytes(coord.encode("utf-8"), "big")]
    if salt:
        inputs.append(salt)
    inputs.extend(v_values)
    return poseidon_hash_stub(*inputs)


class MerkleTree:
    """Binary Merkle tree over a list of integer leaves."""

    def __init__(self, leaves: Sequence[int]) -> None:
        self.leaves = list(leaves)
        if not self.leaves:
            self.leaves = [0]
        self._levels = self._build(self.leaves)
        self.root = self._levels[-1][0]

    @staticmethod
    def _build(leaves: list[int]) -> list[list[int]]:
        levels = [leaves[:]]
        current = leaves[:]
        while len(current) > 1:
            next_level: list[int] = []
            for i in range(0, len(current), 2):
                left = current[i]
                right = current[i + 1] if i + 1 < len(current) else left
                next_level.append(poseidon_hash_stub(left, right))
            current = next_level
            levels.append(current)
        return levels

    def get_proof(self, index: int) -> list[tuple[int, str]]:
        """Return the Merkle siblings for ``index`` as ``(sibling, side)``.

        ``side`` is ``"L"`` if the sibling is on the left, ``"R"`` otherwise.
        """
        proof: list[tuple[int, str]] = []
        idx = index
        for level in self._levels[:-1]:
            if len(level) == 1:
                break
            if idx % 2 == 0:
                sibling = level[idx + 1] if idx + 1 < len(level) else level[idx]
                proof.append((sibling, "R"))
            else:
                sibling = level[idx - 1]
                proof.append((sibling, "L"))
            idx //= 2
        return proof

    @staticmethod
    def verify_proof(
        root: int,
        leaf: int,
        proof: Sequence[tuple[int, str]],
    ) -> bool:
        """Verify a Merkle inclusion proof against ``root``."""
        current = leaf
        for sibling, side in proof:
            if side == "L":
                current = poseidon_hash_stub(sibling, current)
            else:
                current = poseidon_hash_stub(current, sibling)
        return current == root
