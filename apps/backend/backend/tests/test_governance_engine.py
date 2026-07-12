"""Tests for GovernanceEngine patch evaluation (DS-REVIEW-196 Phase C/G)."""

from __future__ import annotations

import numpy as np
import pytest

from backend.fieldx_kernel.governance_engine import GovernanceEngine, GovernanceState


@pytest.fixture
def engine() -> GovernanceEngine:
    return GovernanceEngine()


@pytest.fixture
def passing_state() -> GovernanceState:
    state = GovernanceState()
    state.ledger_hash = "expected"
    state.provenance_commit = "expected"
    state.mismatch_history = [0.0] * 25
    state.V_history = [0.5, 0.5, 0.5]
    return state


def _passing_metrics() -> dict[str, object]:
    return {
        "eq0_distinction": True,
        "eq1_dual_substrate": True,
        "eq2_time_irreversible": True,
        "eq3_geometry_closure": True,
        "E": 1,
        "U": 1.0,
        "ethics_gate": 1,
        "V": 0.5,
        "theta_V": 0.45,
    }


def test_all_patches_pass(engine: GovernanceEngine, passing_state: GovernanceState) -> None:
    metrics = _passing_metrics()
    metadata = {"appraisal": {"law_score": 0.9, "grace_score": 0.9}}
    result = engine.evaluate_patches(passing_state, metrics, metadata=metadata)
    assert result.all_passed is True
    assert result.checksum_336_pass is True
    assert result.refusal is None
    assert result.first_failure is None
    assert all(result.status_map.values())


def test_patch_001_fails(engine: GovernanceEngine, passing_state: GovernanceState) -> None:
    metrics = _passing_metrics()
    metrics["eq0_distinction"] = False
    result = engine.evaluate_patches(passing_state, metrics)
    assert result.status_map["patch_001"] is False
    assert result.first_failure == "patch_001"
    assert result.refusal is not None
    assert result.refusal["engineering_replacement"] == "SINGULAR_ORIGIN_ENFORCEMENT"
    assert result.all_passed is False


def test_patch_005_fails_on_pure_state(engine: GovernanceEngine, passing_state: GovernanceState) -> None:
    metrics = _passing_metrics()
    metadata = {"appraisal": {"law_score": 0.0, "grace_score": 0.9}}
    result = engine.evaluate_patches(passing_state, metrics, metadata=metadata)
    assert result.status_map["patch_005"] is False
    assert result.first_failure == "patch_005"
    assert result.refusal["engineering_replacement"] == "COUPLING_CONSTANT_STABILIZATION"


def test_patch_009_fails_on_ethics_gate(engine: GovernanceEngine, passing_state: GovernanceState) -> None:
    metrics = _passing_metrics()
    metrics["ethics_gate"] = 0
    metadata = {"appraisal": {"law_score": 0.9, "grace_score": 0.9}}
    result = engine.evaluate_patches(passing_state, metrics, metadata=metadata)
    assert result.status_map["patch_009"] is False
    assert result.first_failure == "patch_009"


def test_order_enforcement_stops_at_first_failure(engine: GovernanceEngine, passing_state: GovernanceState) -> None:
    metrics = _passing_metrics()
    metrics["eq0_distinction"] = False
    metrics["eq1_dual_substrate"] = False
    result = engine.evaluate_patches(passing_state, metrics)
    assert result.first_failure == "patch_001"
    # Subsequent patch bits remain fail-closed (False) because evaluation stopped.
    assert result.status_map["patch_002"] is False


def test_refusal_payload_contains_no_commandment_text(engine: GovernanceEngine, passing_state: GovernanceState) -> None:
    metrics = _passing_metrics()
    metrics["eq0_distinction"] = False
    result = engine.evaluate_patches(passing_state, metrics)
    refusal = result.refusal
    assert refusal is not None
    banned = [
        "No other gods before me", "No carved images", "Do not take the name in vain",
        "Remember the Sabbath", "Honor father and mother", "Do not murder",
        "Do not commit adultery", "Do not steal", "Do not bear false witness",
        "Do not covet", "Aleph", "Bet", "Gimel",
    ]
    payload = str(refusal)
    for term in banned:
        assert term not in payload, f"Refusal payload contains banned term: {term}"


def test_value_node_balance_context(engine: GovernanceEngine, passing_state: GovernanceState) -> None:
    metrics = _passing_metrics()
    # Use the personality-type overlay dimension keys.
    # interest -> novelty, uniqueness
    # relatedness -> connection
    # context -> action, potential, autonomy, relatedness, mastery
    # integration -> centroid
    metadata = {
        "appraisal": {"law_score": 0.9, "grace_score": 0.9},
        "value_node_context": {
            "dimension_scores": {
                "interest": 0.12,
                "relatedness": 0.12,
                "context": 0.12,
                "integration": 0.12,
            }
        },
    }
    result = engine.evaluate_patches(passing_state, metrics, metadata=metadata)
    assert result.balance_context is not None
    assert result.balance_context["balanced"] is True
    assert "scores" in result.balance_context
    assert "diagnostics" in result.balance_context


def test_evaluate_integration_includes_patch_status(engine: GovernanceEngine) -> None:
    prev_state = GovernanceState()
    prev_state.ledger_hash = "genesis"
    prev_state.mismatch_history = [0.0] * 25
    curr_state = GovernanceState()
    curr_state.ledger_hash = "genesis"
    curr_state.mismatch_history = [0.0] * 25
    curr_state.V_history = [0.5, 0.5, 0.5]

    metrics_pack = engine.evaluate(
        prev_state=prev_state,
        curr_state=curr_state,
        prev_hash="genesis",
        payload="test",
        E_pred=0.1,
        E_baseline=0.5,
        expected_commit="",
        schema_complete=True,
        inputs_logged=True,
        version_pinned=True,
        ethics_gate=1,
        metadata={"appraisal": {"law_score": 0.9, "grace_score": 0.9}},
    )
    assert "patch_status_map" in metrics_pack.metrics
    assert "patch_all_passed" in metrics_pack.metrics
    assert isinstance(metrics_pack.metrics["patch_status_map"], dict)


def test_governance_engine_uses_coord_fsm_for_supercession(engine: GovernanceEngine) -> None:
    """GovernanceEngine exposes FSM-backed COORD supercession validation."""
    result = engine.check_coord_supercession(
        "ethics/lawfulness/refusal/clean_refusal/v6",
        "ethics/lawfulness/refusal/firm_boundary/v6",
    )
    assert result["valid"] is True
    assert result["reason"] == "valid FSM derivation"


def test_governance_engine_rejects_invalid_coord_supercession(engine: GovernanceEngine) -> None:
    result = engine.check_coord_supercession(
        "ethics/lawfulness/refusal/clean_refusal/v6",
        "awareness/perception/signal/sharp/v3",
    )
    assert result["valid"] is False
    assert result["reason"] == "COORD derivation invalid"
