from __future__ import annotations

from backend.fieldx_kernel.eval_ladder import evaluate_eq_ladder


def test_eval_ladder_deterministic_for_identical_inputs() -> None:
    a = evaluate_eq_ladder(
        mode=2,
        K=1,
        P=1,
        E=1,
        V_q=52000,
        momentum_min=1000,
        dW=0,
        output_tokens_est=120,
        law_score=0.9,
        grace_score=0.8,
    )
    b = evaluate_eq_ladder(
        mode=2,
        K=1,
        P=1,
        E=1,
        V_q=52000,
        momentum_min=1000,
        dW=0,
        output_tokens_est=120,
        law_score=0.9,
        grace_score=0.8,
    )
    assert a == b


def test_eq9_yield_zero_when_commit_blocked() -> None:
    blocked = evaluate_eq_ladder(
        mode=1,
        K=1,
        P=0,
        E=1,
        V_q=60000,
        momentum_min=100,
        dW=0,
        output_tokens_est=50,
        law_score=1.0,
        grace_score=1.0,
    )
    eq9 = blocked["eq9_metrics"]
    assert blocked["commit_allowed"] is False
    assert eq9["fulfillment"] == 0.0
    assert eq9["yield_per_token"] == 0.0


def test_eq9_yield_penalized_by_token_volume() -> None:
    small = evaluate_eq_ladder(
        mode=2,
        K=1,
        P=1,
        E=1,
        V_q=52000,
        momentum_min=1000,
        dW=0,
        output_tokens_est=50,
        law_score=1.0,
        grace_score=1.0,
    )
    large = evaluate_eq_ladder(
        mode=2,
        K=1,
        P=1,
        E=1,
        V_q=52000,
        momentum_min=1000,
        dW=0,
        output_tokens_est=500,
        law_score=1.0,
        grace_score=1.0,
    )

    assert small["eq9_metrics"]["yield_per_token"] > large["eq9_metrics"]["yield_per_token"]


def test_eq9_yield_respects_law_grace_product() -> None:
    high = evaluate_eq_ladder(
        mode=2,
        K=1,
        P=1,
        E=1,
        V_q=60000,
        momentum_min=1000,
        dW=0,
        output_tokens_est=100,
        law_score=1.0,
        grace_score=1.0,
    )
    low = evaluate_eq_ladder(
        mode=2,
        K=1,
        P=1,
        E=1,
        V_q=60000,
        momentum_min=1000,
        dW=0,
        output_tokens_est=100,
        law_score=0.5,
        grace_score=0.5,
    )

    assert high["eq9_metrics"]["law_grace_product"] > low["eq9_metrics"]["law_grace_product"]
    assert high["eq9_metrics"]["yield_per_token"] > low["eq9_metrics"]["yield_per_token"]


def test_eval_ladder_stops_on_first_failure_in_order() -> None:
    blocked = evaluate_eq_ladder(
        mode=1,
        K=0,
        P=0,
        E=0,
        V_q=1,
        momentum_min=60000,
        dW=5,
        output_tokens_est=10,
        law_score=0.2,
        grace_score=0.3,
    )
    assert blocked["commit_allowed"] is False
    assert blocked["failed_eq"] == "eq3_geometry_closure"
    failed_checks = blocked["failed_checks"]
    assert isinstance(failed_checks, list) and len(failed_checks) == 1
    assert failed_checks[0]["check_id"] == "dw_within_topology_bounds"
    repairs = blocked["repair_actions"]
    assert isinstance(repairs, list) and len(repairs) == 1
    assert repairs[0]["check_id"] == "dw_within_topology_bounds"


def test_eval_ladder_records_prior_eqs_before_failure() -> None:
    blocked = evaluate_eq_ladder(
        mode=1,
        K=1,
        P=0,
        E=1,
        V_q=65000,
        momentum_min=100,
        dW=0,
        output_tokens_est=10,
        law_score=1.0,
        grace_score=1.0,
    )
    assert blocked["commit_allowed"] is False
    assert blocked["failed_eq"] == "eq6_awareness"
    assert blocked["passed_eqs"] == ["eq3_geometry_closure"]
    profile = blocked["indefeasible_profile"]
    assert profile["first_failure_blocks"] is True
    assert profile["eq_order"] == [
        "eq3_geometry_closure",
        "eq6_awareness",
        "eq8_ethics",
        "eq7_unity",
        "eq9_telos",
    ]


def test_eq9_yield_penalized_by_low_provenance_confidence() -> None:
    high = evaluate_eq_ladder(
        mode=2,
        K=1,
        P=1,
        E=1,
        V_q=60000,
        momentum_min=1000,
        dW=0,
        output_tokens_est=100,
        law_score=1.0,
        grace_score=1.0,
        provenance_confidence=1.0,
        replay_protected=True,
        provenance_status="session_token",
    )
    low = evaluate_eq_ladder(
        mode=2,
        K=1,
        P=1,
        E=1,
        V_q=60000,
        momentum_min=1000,
        dW=0,
        output_tokens_est=100,
        law_score=1.0,
        grace_score=1.0,
        provenance_confidence=0.3,
        replay_protected=False,
        provenance_status="anonymous",
    )
    assert high["eq9_metrics"]["yield_per_token"] > low["eq9_metrics"]["yield_per_token"]
    assert high["eq9_metrics"]["provenance_confidence"] == 1.0
    assert low["eq9_metrics"]["replay_protected"] is False
