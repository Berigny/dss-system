"""Tests for backend/kernel/structural_integrity.py."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

os.environ.setdefault("DSS_KSR_PBKDF2_ITERATIONS", "1000")

from backend.kernel.ksr_crypto import encrypt_ksr  # noqa: E402
from backend.kernel.structural_integrity import (  # noqa: E402
    IntegrityFailureReason,
    IntegrityState,
    StructuralIntegrityError,
    StructuralIntegrityProtocol,
)


@pytest.fixture
def password() -> str:
    return "structural-integrity-test"


@pytest.fixture
def whitepaper_hash() -> str:
    return "a" * 64


@pytest.fixture
def tmp_repo(tmp_path: Path, password: str, whitepaper_hash: str) -> Path:
    """Create a minimal repo layout with a valid encrypted KSR."""
    repo = tmp_path / "repo"
    kernel_dir = repo / "backend" / "kernel"
    ksr_dir = kernel_dir / ".ksr"
    ksr_dir.mkdir(parents=True)

    ksr_data = {
        "ksr_version": "1.1.0",
        "reference_documents": {
            "whitepaper": {"path": "backlog_reqs/paper/dss-whitepaper.pdf", "sha256": whitepaper_hash},
            "metaprompt": {"path": "backlog_reqs/paper/DSS_Kernel_Semantic_Encoding_Metaprompt_v1.1.md", "sha256": "b" * 64},
        },
        "digit_registry": {},
        "prime_registry": {},
        "dual_pair_registry": [],
        "octave_registry": [],
        "checksum_invariant": {"name": "checksum_336", "value": 336},
        "glossary": [{"term": "Omega Point", "replacement": "terminal_coherence_state", "category": "esoteric", "priority": "critical"}],
        "synonym_registry": {},
        "stripping_priority": {"critical": ["Omega Point"], "high": [], "medium": [], "low": []},
        "surface_policy": {"public_extensions": [".py"], "private_paths": []},
    }
    plaintext = yaml.safe_dump(ksr_data).encode("utf-8")
    envelope = encrypt_ksr(plaintext, password, whitepaper_hash=whitepaper_hash, iterations=1000)
    (kernel_dir / "semantic_registry.enc").write_text(envelope.to_string())

    ksr_hash = hashlib.sha256(plaintext).hexdigest()
    glossary_canon = json.dumps(ksr_data["glossary"], sort_keys=True, ensure_ascii=False)
    glossary_hash = hashlib.sha256(glossary_canon.encode("utf-8")).hexdigest()
    (ksr_dir / "ksr.hash").write_text(ksr_hash)
    (ksr_dir / "glossary.hash").write_text(glossary_hash)
    (ksr_dir / "whitepaper.hash").write_text(whitepaper_hash)
    return repo


def test_healthy(tmp_repo: Path, password: str) -> None:
    protocol = StructuralIntegrityProtocol(repo_root=tmp_repo, password=password)
    status = protocol.initialize()
    assert status.state == IntegrityState.HEALTHY
    assert protocol.can_write() is True
    protocol.require_healthy()


def test_missing_envelope(tmp_repo: Path, password: str) -> None:
    (tmp_repo / "backend" / "kernel" / "semantic_registry.enc").unlink()
    protocol = StructuralIntegrityProtocol(repo_root=tmp_repo, password=password)
    status = protocol.initialize()
    assert status.state == IntegrityState.DEGRADED_MODE
    assert status.reason == IntegrityFailureReason.MISSING_ENVELOPE
    assert protocol.can_write() is False
    with pytest.raises(StructuralIntegrityError):
        protocol.require_healthy()


def test_no_password(tmp_repo: Path) -> None:
    protocol = StructuralIntegrityProtocol(repo_root=tmp_repo, password="")
    status = protocol.initialize()
    assert status.state == IntegrityState.DEGRADED_MODE
    assert status.reason == IntegrityFailureReason.NO_PASSWORD


def test_wrong_password(tmp_repo: Path) -> None:
    protocol = StructuralIntegrityProtocol(repo_root=tmp_repo, password="wrong")
    status = protocol.initialize()
    assert status.state == IntegrityState.DEGRADED_MODE
    assert status.reason == IntegrityFailureReason.AUTHENTICATION_FAILURE


def test_tampered_ksr_hash(tmp_repo: Path, password: str) -> None:
    (tmp_repo / "backend" / "kernel" / ".ksr" / "ksr.hash").write_text("c" * 64)
    protocol = StructuralIntegrityProtocol(repo_root=tmp_repo, password=password)
    status = protocol.initialize()
    assert status.state == IntegrityState.DEGRADED_MODE
    assert status.reason == IntegrityFailureReason.KSR_HASH_MISMATCH


def test_glossary_hash_mismatch(tmp_repo: Path, password: str) -> None:
    (tmp_repo / "backend" / "kernel" / ".ksr" / "glossary.hash").write_text("d" * 64)
    protocol = StructuralIntegrityProtocol(repo_root=tmp_repo, password=password)
    status = protocol.initialize()
    assert status.state == IntegrityState.DEGRADED_MODE
    assert status.reason == IntegrityFailureReason.GLOSSARY_HASH_MISMATCH


def test_recovery_success(tmp_repo: Path, password: str) -> None:
    protocol = StructuralIntegrityProtocol(repo_root=tmp_repo, password="wrong")
    status = protocol.initialize()
    assert status.state == IntegrityState.DEGRADED_MODE
    recovered = protocol.attempt_recovery(password=password)
    assert recovered.state == IntegrityState.HEALTHY
