"""p-adic valuation range-proof stub.

Provides a non-interactive bit-decomposition proof that a private valuation
``v`` satisfies ``v >= min_v`` for a given prime. This implementation is a
structural ZK stub: it commits to the witness and proves the range via public
delta reconstruction. A production system would replace the hash commitment and
bit checks with a pairing-friendly or bullet-proof circuit.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any, Sequence


class PadicRangeProof:
    """Prove and verify ``v >= min_v`` for a p-adic valuation."""

    @staticmethod
    def _bit_decompose(n: int) -> list[int]:
        """Return little-endian bit decomposition of non-negative ``n``."""
        if n <= 0:
            return []
        bits: list[int] = []
        while n > 0:
            bits.append(n & 1)
            n >>= 1
        return bits

    @staticmethod
    def _commit(prime: int, min_v: int, v: int, salt: str) -> str:
        payload = f"{prime}:{min_v}:{v}:{salt}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def prove_v_ge(
        cls,
        v: int,
        prime: int,
        min_v: int = 6,
    ) -> dict[str, Any]:
        """Produce a non-interactive proof that ``v >= min_v``.

        Raises:
            ValueError: if ``v < min_v`` (the statement is false).
        """
        if v < min_v:
            raise ValueError(
                f"Cannot prove v >= {min_v}: v={v} is below the threshold."
            )
        if v < 0:
            raise ValueError("Valuation v must be non-negative.")

        delta = v - min_v
        delta_bits = cls._bit_decompose(delta)
        salt = secrets.token_hex(16)
        commitment = cls._commit(prime, min_v, v, salt)

        return {
            "prime": prime,
            "min_v": min_v,
            "public_v": v,
            "delta": delta,
            "delta_bits": delta_bits,
            "commitment": commitment,
            "salt": salt,
            "scheme": "bit_decomposition_sha256_stub",
        }

    @classmethod
    def verify(
        cls,
        proof: dict[str, Any],
        prime: int,
        min_v: int = 6,
    ) -> bool:
        """Verify a proof returned by :meth:`prove_v_ge`."""
        if proof.get("prime") != prime:
            return False
        if proof.get("min_v") != min_v:
            return False
        if proof.get("scheme") != "bit_decomposition_sha256_stub":
            return False

        v = proof.get("public_v")
        if not isinstance(v, int) or v < 0:
            return False
        if v < min_v:
            return False

        delta = v - min_v
        expected_bits = cls._bit_decompose(delta)
        if proof.get("delta_bits") != expected_bits:
            return False

        # Each bit must be 0 or 1.
        for bit in proof.get("delta_bits", []):
            if bit not in (0, 1):
                return False

        expected_commitment = cls._commit(prime, min_v, v, proof.get("salt", ""))
        return proof.get("commitment") == expected_commitment

    @classmethod
    def prove_elevation(
        cls,
        v_awareness: int,
        v_unity: int,
        v_ethics: int,
        min_v: int = 6,
    ) -> dict[str, Any]:
        """Produce a bundle of range proofs for Clay elevation."""
        return {
            "awareness": cls.prove_v_ge(v_awareness, 5, min_v),
            "unity": cls.prove_v_ge(v_unity, 7, min_v),
            "ethics": cls.prove_v_ge(v_ethics, 2, min_v),
            "min_v": min_v,
        }

    @classmethod
    def verify_elevation(
        cls,
        bundle: dict[str, Any],
        min_v: int = 6,
    ) -> bool:
        """Verify a proof bundle produced by :meth:`prove_elevation`."""
        expected_primes = {"awareness": 5, "unity": 7, "ethics": 2}
        for gate_key, prime in expected_primes.items():
            proof = bundle.get(gate_key)
            if proof is None or not cls.verify(proof, prime, min_v):
                return False
        return True
