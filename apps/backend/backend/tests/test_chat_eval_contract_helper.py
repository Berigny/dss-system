from __future__ import annotations

from backend.api.chat import _build_eval_contract, _build_posture_policy


def test_build_eval_contract_returns_none_without_e6_header() -> None:
    contract = _build_eval_contract(
        metadata_payload={"eq9_target": {"score_min": 0.95}},
        appraisal_payload={"law_score": 1.0, "grace_score": 1.0},
    )
    assert contract is None


def test_build_eval_contract_returns_payload_for_valid_inputs() -> None:
    contract = _build_eval_contract(
        metadata_payload={
            "e6_header_v0_fields": {"mode": 2, "K": 1, "P": 1, "E": 1, "V_q": 65535, "dW": 0},
            "eq9_target": {"score_min": 0.95},
            "gen_output_tokens": 128,
        },
        appraisal_payload={"law_score": 1.0, "grace_score": 1.0},
    )
    assert isinstance(contract, dict)
    assert contract["commit_allowed"] is True
    assert contract["failed_eq"] is None
    metrics = contract.get("eq9_metrics")
    assert isinstance(metrics, dict)
    assert metrics.get("yield_per_token", 0.0) > 0.0


def test_build_eval_contract_includes_provenance_metrics() -> None:
    contract = _build_eval_contract(
        metadata_payload={
            "e6_header_v0_fields": {"mode": 2, "K": 1, "P": 1, "E": 1, "V_q": 65535, "dW": 0},
            "eq9_target": {"score_min": 0.95},
            "gen_output_tokens": 128,
            "provenance_dual_write": {
                "status": "legacy_only",
                "session_jti_present": False,
            },
        },
        appraisal_payload={"law_score": 1.0, "grace_score": 1.0},
    )
    assert isinstance(contract, dict)
    eq9 = contract.get("eq9_metrics")
    assert isinstance(eq9, dict)
    assert eq9.get("provenance_status") == "legacy_only"
    assert isinstance(eq9.get("provenance_confidence"), float)
    anti = eq9.get("anti_gaming")
    assert isinstance(anti, dict)
    assert anti.get("provenance_weight_applied") is True


def test_build_posture_policy_denies_when_eval_contract_blocked() -> None:
    eval_contract = _build_eval_contract(
        metadata_payload={
            "e6_header_v0_fields": {"mode": 1, "K": 1, "P": 0, "E": 1, "V_q": 10, "dW": 0},
            "eq9_target": {"score_min": 0.95},
            "gen_output_tokens": 42,
            "provenance_dual_write": {"status": "dual_write_ok", "session_jti_present": True},
            "appraisal": {"law_score": 0.4, "grace_score": 0.9},
        }
    )
    policy = _build_posture_policy(
        action="chat.respond",
        eval_contract=eval_contract,
        metadata_payload={"provenance_dual_write": {"status": "dual_write_ok", "session_jti_present": True}},
    )
    assert policy["policy_decision"] == "deny"
    assert str(policy["reason_code"]).startswith("eq_blocked")
    assert policy["policy_gate_version"] == "policy-gate-v1"
    assert policy["pp_version"] == "pp-v1"
    assert policy["cb_version"] == "cb-v1"
    assert policy["obs_posture_version"] == "obs-posture-v1"
