from backend.api.agent_writes import (
    _apply_contradiction_bridge_floor,
    _build_e6_scoring_snapshot,
    _promotion_decision,
    _select_promotion_bridge_gate,
)


def test_e6_scoring_snapshot_contains_hard_soft_window_and_bridge_eval() -> None:
    snapshot = _build_e6_scoring_snapshot(
        metrics={
            "L_top": 1.0,
            "K": 1,
            "P": 1,
            "E": 1,
            "L_phys": 0.92,
            "L": 0.92,
            "H": 0.88,
            "A_corr": 0.95,
            "A_self": 0.97,
            "A": 0.9215,
            "U": 0.9,
            "dW": 0,
        },
        bridge_allowed_runtime=True,
        v_mean_3=0.86,
        v_std_3=0.05,
        thresholds={
            "theta_L": 0.85,
            "theta_H": 0.8,
            "theta_V": 0.85,
            "theta_sigma": 0.1,
            "theta_self": 0.9,
            "allowed_dW": [-1, 0, 1],
        },
    )

    assert snapshot["hard_gates"]["K_t"] == 1
    assert snapshot["soft_metrics"]["L_phys"] == 0.92
    assert snapshot["window"]["V_int_mean_3"] == 0.86
    assert snapshot["bridge_allowed_runtime"] is True
    assert snapshot["bridge_allowed_formula_eval"] is True


def test_promotion_decision_uses_existing_signals_without_blocking_write() -> None:
    allowed = _promotion_decision(
        quality_tier="express",
        governance_error=None,
        bridge_allowed=True,
        resolution_contradiction=False,
    )
    assert allowed["allowed"] is True
    assert allowed["route"] == 3

    contradicted = _promotion_decision(
        quality_tier="express",
        governance_error=None,
        bridge_allowed=True,
        resolution_contradiction=True,
    )
    assert contradicted["allowed"] is False
    assert contradicted["route"] == 1


def test_promotion_decision_halt_is_always_block_route() -> None:
    halted = _promotion_decision(
        quality_tier="halt",
        governance_error=None,
        bridge_allowed=True,
        resolution_contradiction=False,
    )
    assert halted["allowed"] is False
    assert halted["route"] == 0


def test_select_promotion_bridge_gate_defaults_to_runtime() -> None:
    gate, source = _select_promotion_bridge_gate(bridge_runtime=True, e6_scoring=None)
    assert gate is True
    assert source == "runtime"


def test_select_promotion_bridge_gate_formula_mode(monkeypatch) -> None:
    monkeypatch.setenv("E6_PROMOTION_GATE_MODE", "formula")
    gate, source = _select_promotion_bridge_gate(
        bridge_runtime=True,
        e6_scoring={"bridge_allowed_formula_eval": False},
    )
    assert gate is False
    assert source == "formula"


def test_contradiction_bridge_floor_blocks_when_a_self_below_theta() -> None:
    allowed, gate = _apply_contradiction_bridge_floor(
        metadata={"resolution_contradiction": True},
        metrics={"A_self": 0.4, "theta_self": 0.6},
        bridge_ok=True,
    )
    assert allowed is False
    assert isinstance(gate, dict)
    assert gate.get("reason") == "resolution_contradiction_a_self_below_theta"
