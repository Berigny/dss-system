"""Tests for the CI-enforced claims registry harness."""

from __future__ import annotations

import pytest

from harness.claims_registry import (
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


def test_registry_has_epic65_claims() -> None:
    registry = load_registry()
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
