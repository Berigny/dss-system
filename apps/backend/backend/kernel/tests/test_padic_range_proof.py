"""Tests for backend/kernel/padic_range_proof.py."""

from __future__ import annotations

import pytest

from backend.kernel.padic_range_proof import PadicRangeProof


def test_range_proof_v6_passes() -> None:
    proof = PadicRangeProof.prove_v_ge(6, prime=5, min_v=6)
    assert PadicRangeProof.verify(proof, prime=5, min_v=6) is True


def test_range_proof_v7_passes() -> None:
    proof = PadicRangeProof.prove_v_ge(7, prime=7, min_v=6)
    assert PadicRangeProof.verify(proof, prime=7, min_v=6) is True


def test_range_proof_v10_passes() -> None:
    proof = PadicRangeProof.prove_v_ge(10, prime=2, min_v=6)
    assert PadicRangeProof.verify(proof, prime=2, min_v=6) is True


def test_range_proof_v5_fails() -> None:
    with pytest.raises(ValueError, match="below the threshold"):
        PadicRangeProof.prove_v_ge(5, prime=5, min_v=6)


def test_range_proof_wrong_prime_fails() -> None:
    proof = PadicRangeProof.prove_v_ge(8, prime=5, min_v=6)
    assert PadicRangeProof.verify(proof, prime=7, min_v=6) is False


def test_range_proof_wrong_min_v_fails() -> None:
    proof = PadicRangeProof.prove_v_ge(8, prime=5, min_v=6)
    assert PadicRangeProof.verify(proof, prime=5, min_v=7) is False


def test_range_proof_tampered_commitment_fails() -> None:
    proof = PadicRangeProof.prove_v_ge(8, prime=5, min_v=6)
    proof["commitment"] = "0" * 64
    assert PadicRangeProof.verify(proof, prime=5, min_v=6) is False


def test_range_proof_tampered_bits_fails() -> None:
    proof = PadicRangeProof.prove_v_ge(8, prime=5, min_v=6)
    proof["delta_bits"].append(1)
    assert PadicRangeProof.verify(proof, prime=5, min_v=6) is False


def test_elevation_bundle_passes() -> None:
    bundle = PadicRangeProof.prove_elevation(6, 7, 8)
    assert PadicRangeProof.verify_elevation(bundle) is True


def test_elevation_bundle_low_ethics_fails() -> None:
    with pytest.raises(ValueError, match="below the threshold"):
        PadicRangeProof.prove_elevation(6, 7, 5)
