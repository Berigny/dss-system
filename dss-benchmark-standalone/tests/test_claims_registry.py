"""Tests for the CI-enforced claims registry harness."""

from __future__ import annotations

import pytest

from harness.claims_registry import (
    BACKLOG_REGISTRY_PATH,
    MERGED_REGISTRY_PATH,
    REGISTRY_PATH,
    bind_claims,
    check_registry,
    load_registry,
    _validate_registry,
)


def test_registry_loads_without_duplicate_ids() -> None:
    registry = load_registry()
    errors = _validate_registry(registry)
    assert errors == [], f"Registry validation errors: {errors}"


def test_unknown_claim_triggers_fail_ci() -> None:
    registry = {
        "unknown_claims_policy": "fail_ci",
        "claims": [
            {
                "id": "peo.core_01",
                "suite": "peo",
                "metric": "core_01",
                "description": "test",
                "threshold": None,
                "operator": ">=",
            }
        ],
    }
    results = [{"suite": "peo", "core_02": 1.0}]
    overall, binding = check_registry(registry, results)
    assert not overall
    assert "peo.core_02" in binding["unknown_claims"]


def test_missing_claim_triggers_fail_ci() -> None:
    registry = {
        "unknown_claims_policy": "fail_ci",
        "claims": [
            {
                "id": "peo.core_01",
                "suite": "peo",
                "metric": "core_01",
                "description": "test",
                "threshold": None,
                "operator": ">=",
            }
        ],
    }
    results: list[dict] = []
    overall, binding = check_registry(registry, results)
    assert not overall
    assert "peo.core_01" in binding["missing_claims"]


def test_explicit_claim_id_binding() -> None:
    registry = {
        "unknown_claims_policy": "fail_ci",
        "claims": [
            {
                "id": "PEO-CORE-01",
                "suite": "peo",
                "metric": "core_01",
                "description": "test",
                "threshold": 1.0,
                "operator": ">=",
            }
        ],
    }
    results = [{"suite": "peo", "claim_ids": {"PEO-CORE-01": 1.0}}]
    overall, binding = check_registry(registry, results)
    assert overall
    assert binding["bound"]["PEO-CORE-01"]["status"] == "passed"


def test_active_registry_excludes_backlog_suites() -> None:
    """The default active registry contains only suites with runnable harnesses."""
    registry = load_registry(REGISTRY_PATH)
    suites = {c["suite"] for c in registry.get("claims", [])}
    active_suites = {
        "poisoning",
        "integrity",
        "abstention",
        "ingestion",
        "physical_twin",
        "adversarial",
        "epic69_storage",
    }
    assert suites == active_suites, f"Unexpected suites in active registry: {suites - active_suites}"


def test_backlog_registry_contains_future_suite_claims() -> None:
    """Backlog claims for suites without runnable harnesses are kept separate."""
    registry = load_registry(BACKLOG_REGISTRY_PATH)
    suites = {c["suite"] for c in registry.get("claims", [])}
    backlog_suites = {
        "peo",
        "quaternary",
        "ladder",
        "grace",
        "poe",
        "mesh",
        "efficiency",
        "gate",
        "compatibility",
        "epic55",
        "epic56",
        "epic57",
        "epic58",
        "epic59",
        "epic60",
        "epic61",
        "epic62",
        "epic63",
        "epic64",
    }
    assert suites == backlog_suites, f"Unexpected suites in backlog registry: {suites - backlog_suites}"


def test_merged_registry_has_epic65_claims() -> None:
    """The merged registry remains the canonical source of all public claims."""
    registry = load_registry(MERGED_REGISTRY_PATH)
    ids = {c["id"] for c in registry.get("claims", [])}
    required = {
        "PEO-CORE-01",
        "PEO-CORE-02",
        "PEO-GOV-02",
        "PEO-EFF-02",
        "ING-REPLAY-01",
        "ING-VEL-01",
        "PHY-SCHEMA-01",
        "PHY-XFORM-01",
        "ATK-01",
        "ATK-10",
        "QSL-01",
        "LDR-01",
    }
    missing = required - ids
    assert not missing, f"Missing Epic 65 claims: {missing}"


def test_run_suites_filter_ignores_missing_unrun_suite_claims() -> None:
    """A runner that only executes the base suites must not fail CI because
    claims belonging to unrun suites (e.g. Epic 65 harnesses) were not produced.
    """
    registry = {
        "unknown_claims_policy": "fail_ci",
        "claims": [
            {
                "id": "poisoning.pass",
                "suite": "poisoning",
                "metric": "pass",
                "description": "Base suite claim",
                "threshold": None,
                "operator": ">=",
            },
            {
                "id": "PEO-CORE-01",
                "suite": "peo",
                "metric": "core_01",
                "description": "Epic 65 claim",
                "threshold": None,
                "operator": ">=",
            },
        ],
    }
    results = [{"suite": "poisoning", "pass": True}]
    overall, binding = check_registry(registry, results, run_suites={"poisoning"})
    assert overall
    assert "poisoning.pass" in binding["bound"]
    assert "PEO-CORE-01" not in binding["missing_claims"]
    assert not binding["fail_ci"]


def test_run_suites_filter_still_fails_on_missing_run_suite_claims() -> None:
    """Claims belonging to suites that were run must still be produced."""
    registry = {
        "unknown_claims_policy": "fail_ci",
        "claims": [
            {
                "id": "poisoning.pass",
                "suite": "poisoning",
                "metric": "pass",
                "description": "Base suite claim",
                "threshold": None,
                "operator": ">=",
            },
        ],
    }
    results: list[dict] = [{"suite": "poisoning"}]
    overall, binding = check_registry(registry, results, run_suites={"poisoning"})
    assert not overall
    assert "poisoning.pass" in binding["missing_claims"]
    assert binding["fail_ci"]
