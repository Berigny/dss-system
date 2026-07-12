"""Tests for backend/kernel/ksr_crypto.py."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DSS_KSR_PBKDF2_ITERATIONS", "1000")

from backend.kernel.ksr_crypto import (  # noqa: E402
    KsrAuthenticationError,
    KsrIntegrityError,
    decrypt_ksr,
    encrypt_ksr,
)


@pytest.fixture
def password() -> str:
    return "test-password-195"


@pytest.fixture
def whitepaper_hash() -> str:
    return "0" * 64


def test_encrypt_decrypt_round_trip(password: str, whitepaper_hash: str) -> None:
    plaintext = b"kernel semantic registry payload"
    envelope = encrypt_ksr(plaintext, password, whitepaper_hash=whitepaper_hash, iterations=1000)
    decrypted = decrypt_ksr(envelope, password, whitepaper_hash=whitepaper_hash, iterations=1000)
    assert decrypted == plaintext


def test_decrypt_wrong_password_fails(password: str, whitepaper_hash: str) -> None:
    plaintext = b"kernel semantic registry payload"
    envelope = encrypt_ksr(plaintext, password, whitepaper_hash=whitepaper_hash, iterations=1000)
    with pytest.raises(KsrAuthenticationError):
        decrypt_ksr(envelope, "wrong-password", whitepaper_hash=whitepaper_hash, iterations=1000)


def test_decrypt_tampered_tag_fails(password: str, whitepaper_hash: str) -> None:
    plaintext = b"kernel semantic registry payload"
    envelope = encrypt_ksr(plaintext, password, whitepaper_hash=whitepaper_hash, iterations=1000)
    tampered = bytearray(envelope.tag)
    tampered[0] ^= 0xFF
    envelope.tag = bytes(tampered)
    with pytest.raises(KsrAuthenticationError):
        decrypt_ksr(envelope, password, whitepaper_hash=whitepaper_hash, iterations=1000)


def test_decrypt_tampered_ciphertext_fails(password: str, whitepaper_hash: str) -> None:
    plaintext = b"kernel semantic registry payload"
    envelope = encrypt_ksr(plaintext, password, whitepaper_hash=whitepaper_hash, iterations=1000)
    tampered = bytearray(envelope.ciphertext)
    tampered[0] ^= 0xFF
    envelope.ciphertext = bytes(tampered)
    with pytest.raises(KsrAuthenticationError):
        decrypt_ksr(envelope, password, whitepaper_hash=whitepaper_hash, iterations=1000)


def test_expected_hash_mismatch_raises(password: str, whitepaper_hash: str) -> None:
    plaintext = b"kernel semantic registry payload"
    envelope = encrypt_ksr(plaintext, password, whitepaper_hash=whitepaper_hash, iterations=1000)
    with pytest.raises(KsrIntegrityError):
        decrypt_ksr(
            envelope,
            password,
            whitepaper_hash=whitepaper_hash,
            iterations=1000,
            expected_sha256="f" * 64,
        )
