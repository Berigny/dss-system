from __future__ import annotations

from pathlib import Path

import pytest

from backend.kernel.benchmarks.consilience_benchmark import (
    run_confidence_audit,
    run_constants_scope_check,
    run_iching_permutation_test,
)
from backend.kernel.ksr_crypto import load_ksr_yaml


@pytest.fixture
def ksr_data() -> dict:
    # Prefer the plaintext KSR when present; fall back to the test-password envelope.
    repo_root = Path(__file__).resolve().parents[3]
    yaml_path = repo_root / "backend" / "kernel" / "semantic_registry.yaml"
    if yaml_path.exists():
        import yaml

        return yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return load_ksr_yaml("DSS-KSR-TEST-195")


def test_iching_trigram_bijection_beats_random_permutations() -> None:
    result = run_iching_permutation_test(iterations=1000)
    assert result["passes"], (
        f"Structural mapping score {result['structural_score']} did not exceed "
        f"max random score {result['max_random_score']} (p={result['p_value']})"
    )


def test_ksr_has_confidence_and_relation_metadata(ksr_data: dict) -> None:
    audit = run_confidence_audit(ksr_data)
    assert audit["has_scope_statement"]
    assert audit["has_confidence_taxonomy"]
    assert audit["has_relation_types"]
    assert audit["glossary_confidence_counts"]
    assert audit["cross_domain_confidence_counts"]


def test_public_constants_include_scope_statement() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    check = run_constants_scope_check(repo_root)
    assert check["scope_statement_present"]
    assert check["confidence_taxonomy_present"]
    assert check["relation_types_present"]
