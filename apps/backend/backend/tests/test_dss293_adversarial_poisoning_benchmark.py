"""Tests for DSS-293 adversarial poisoning benchmark."""

from __future__ import annotations

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.dss293_adversarial_poisoning_benchmark import (
    BenchmarkConfig,
    build_cases,
    evaluate,
    evaluate_case,
    run_benchmark,
    run_single_seed,
)
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2


def test_build_cases_covers_all_poison_types() -> None:
    cases = build_cases(seed=193)
    types = {c.poison_type for c in cases}
    assert types == {"same_id_overwrite", "incompatible_coord", "compatible_coord_conflict"}


def test_same_id_overwrite_preserves_original() -> None:
    store = LedgerStoreV2(db={})
    cases = build_cases(seed=193)
    case = next(c for c in cases if c.poison_type == "same_id_overwrite")
    result = evaluate_case(store, case, seed=193)
    assert result.original_preserved is True
    assert result.silent_displacement is False
    assert result.conflict_flagged is True


def test_incompatible_coord_is_flagged() -> None:
    store = LedgerStoreV2(db={})
    cases = build_cases(seed=193)
    case = next(c for c in cases if c.poison_type == "incompatible_coord")
    result = evaluate_case(store, case, seed=193)
    assert result.compatibility_passed is False
    assert result.conflict_flagged is True
    assert result.silent_displacement is False


def test_compatible_coord_conflict_forces_invariant_flag() -> None:
    store = LedgerStoreV2(db={})
    cases = build_cases(seed=193)
    case = next(c for c in cases if c.poison_type == "compatible_coord_conflict")
    result = evaluate_case(store, case, seed=193)
    assert result.compatibility_passed is True
    assert result.invariant_flagged is True
    assert result.conflict_flagged is True
    assert result.silent_displacement is False


def test_evaluate_meets_gate_target() -> None:
    summary = evaluate(seed=193)
    assert summary.flagged_or_preserved == summary.cases
    assert summary.silent_displacements == 0
    assert summary.cases == 3


def test_single_seed_artifact_validates(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path,
        seeds=(193,),
    )
    artifact = run_single_seed(193, config)
    payload = artifact.model_dump(mode="json")
    validated = validate_benchmark_artifact(payload)
    assert validated.status == "success"
    assert validated.metrics["governance"].status == "present"
    assert validated.run_config["gate_passed"] is True


def test_multi_seed_benchmark_produces_aggregate(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path,
        seeds=(193, 42),
    )
    aggregate = run_benchmark(config)
    assert aggregate.status == "success"
    assert aggregate.run_config.get("aggregate") is True
    assert aggregate.run_config.get("seed_count") == 2
    assert aggregate.metrics["governance"].metrics["flagged_or_preserved_rate"].statistics is not None
