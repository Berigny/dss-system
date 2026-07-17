from __future__ import annotations

from unittest.mock import patch

from backend.fieldx_kernel import flow_rules, kernel_origin_equations
from backend.kernel import constants
from backend.kernel.layer_router import LayerRouter
from backend.kernel.quaternary_gates import QuaternaryGate
from backend.services.provenance import _build_gravity_tax_policy, _build_retention_tier


def test_evaluate_returns_levels_and_checksum() -> None:
    result = QuaternaryGate.evaluate(6, 6, 6)
    assert result["levels"] == {"awareness": "level_3", "unity": "level_3", "ethics": "level_3"}
    assert result["clay_admissible"] is True
    assert result["checksum_336_satisfied"] is True


def test_quaternary_delegates_to_flow_rules() -> None:
    with patch.object(flow_rules, "run_full_check") as mock_flow, patch.object(
        kernel_origin_equations, "equation_6_operational"
    ) as mock_eq6:
        mock_flow.return_value = (True, "Flow sequence lawful (L3).", flow_rules.GRACE_PRIME, flow_rules.LAW_FULL)
        mock_eq6.return_value = {
            "lawfulness_level": 3,
            "mediator_prime": flow_rules.GRACE_PRIME,
            "commit_allowed": True,
        }

        result = QuaternaryGate.evaluate_with_admissibility(
            6,
            3,
            2,
            query_text="test query",
            retrieval_payload={"summary": "test summary"},
            coherence=0.99,
        )

        assert result["flow_check"]["is_lawful"] is True
        assert result["flow_check"]["lawfulness_level"] == flow_rules.LAW_FULL
        assert result["flow_check"]["active_mediator"] == flow_rules.GRACE_PRIME
        assert result["equation_6"]["commit_allowed"] is True
        mock_flow.assert_called_once()
        mock_eq6.assert_called_once()


def test_quaternary_flow_check_prime_sequence_reflects_levels() -> None:
    # awareness=6 -> level_3 -> awareness prime repeated 3 times
    # unity=3 -> level_2 -> unity prime repeated 2 times
    # ethics=0 -> level_0 -> no primes
    result = QuaternaryGate.evaluate_with_admissibility(6, 3, 0, coherence=1.0)
    awareness_prime = constants.QUATERNARY_GATE_TO_PRIME["awareness"]
    unity_prime = constants.QUATERNARY_GATE_TO_PRIME["unity"]
    assert result["flow_check"]["prime_sequence"] == [awareness_prime] * 3 + [unity_prime] * 2
    assert isinstance(result["flow_check"]["is_lawful"], bool)
    assert "lawfulness_level" in result["flow_check"]


def test_all_level_zero_is_lawful_neutral() -> None:
    result = QuaternaryGate.evaluate_with_admissibility(0, 0, 0, coherence=1.0)
    assert result["flow_check"]["prime_sequence"] == []
    assert result["flow_check"]["is_lawful"] is True


def test_layer_router_maps_layers_to_retention_tiers() -> None:
    assert LayerRouter.layer_to_retention_tier(constants.LAYER_SAND) == "Sand"
    assert LayerRouter.layer_to_retention_tier(constants.LAYER_SILT) == "Silt"
    assert LayerRouter.layer_to_retention_tier(constants.LAYER_LOAM) == "Loam"
    assert LayerRouter.layer_to_retention_tier(constants.LAYER_CLAY) == "Clay"


def test_layer_router_maps_layers_to_candidate_tiers() -> None:
    assert LayerRouter.layer_to_candidate_tiers(constants.LAYER_SAND) == {
        "tier_rank": 0,
        "relevance_tier": 4,
    }
    assert LayerRouter.layer_to_candidate_tiers(constants.LAYER_SILT) == {
        "tier_rank": 1,
        "relevance_tier": 3,
    }
    assert LayerRouter.layer_to_candidate_tiers(constants.LAYER_LOAM) == {
        "tier_rank": 2,
        "relevance_tier": 2,
    }
    assert LayerRouter.layer_to_candidate_tiers(constants.LAYER_CLAY) == {
        "tier_rank": 3,
        "relevance_tier": 1,
    }


def test_geological_layers_map_to_retention_tiers() -> None:
    # Loam is the new fertile-pending intermediate tier.
    loam_payload = _build_retention_tier({"kind": "draft"})
    assert loam_payload["retention_tier"] == "Loam"
    assert loam_payload["retention_tier_reason"] == "fertile_pending_decay_candidate"

    silt_payload = _build_retention_tier({"kind": "autonomy_pattern"})
    assert silt_payload["retention_tier"] == "Silt"

    sand_payload = _build_retention_tier({"kind": "audio_stream", "input_mode": "audio", "streaming": True})
    assert sand_payload["retention_tier"] == "Sand"

    clay_payload = _build_retention_tier({"kind": "chat"})
    assert clay_payload["retention_tier"] == "Clay"


def test_gravity_tax_policy_includes_loam_defaults() -> None:
    payload = _build_gravity_tax_policy({"kind": "pending_commit"})
    assert payload["retention_tier"] == "Loam"
    assert payload["gravity_tax_accrual"] == "accruing_fertile_decay_pressure"
    assert payload["retention_decision_state"] == "decay_or_promote_after_review"
    assert payload["promotion_state"] == "governed_promotion_required"
    assert payload["consolidation_readiness"] == "review_pending"
