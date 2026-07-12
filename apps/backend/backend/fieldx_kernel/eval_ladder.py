"""Deterministic Eq-ladder evaluation contract for operational gates."""

from __future__ import annotations

from typing import Any, Dict


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def evaluate_eq_ladder(
    *,
    mode: int,
    K: int,
    P: int,
    E: int,
    V_q: int,
    momentum_min: int,
    dW: int = 0,
    output_tokens_est: int | None = None,
    law_score: float = 1.0,
    grace_score: float = 1.0,
    provenance_confidence: float | None = None,
    replay_protected: bool | None = None,
    provenance_status: str | None = None,
) -> Dict[str, Any]:
    """Evaluate a cumulative, indeafeasible-as-possible Eq ladder.

    First failure is primary (`failed_eq`) and blocks commit.
    """

    checks = [
        {
            "eq": "eq3_geometry_closure",
            "check_id": "dw_within_topology_bounds",
            "ok": abs(int(dW)) <= 1,
            "observed": int(dW),
            "required": "abs(dW) <= 1",
            "repair": "Reduce transition jump to at most one topology step per tick.",
        },
        {
            "eq": "eq6_awareness",
            "check_id": "ledger_integrity_K",
            "ok": int(K) == 1,
            "observed": int(K),
            "required": 1,
            "repair": "Rebuild or verify hash-chain before commit.",
        },
        {
            "eq": "eq6_awareness",
            "check_id": "provenance_integrity_P",
            "ok": int(P) == 1,
            "observed": int(P),
            "required": 1,
            "repair": "Provide complete provenance inputs and replay evidence.",
        },
        {
            "eq": "eq8_ethics",
            "check_id": "ethics_gate_E",
            "ok": int(E) == 1,
            "observed": int(E),
            "required": 1,
            "repair": "Resolve ethics admissibility violations before commit.",
        },
        {
            "eq": "eq7_unity",
            "check_id": "mode_requires_stabilise_or_express",
            "ok": int(mode) >= 2,
            "observed": int(mode),
            "required": ">=2",
            "repair": "Remain in PROBE and gather evidence until STABILISE threshold is met.",
        },
        {
            "eq": "eq9_telos",
            "check_id": "momentum_threshold",
            "ok": int(V_q) >= int(momentum_min),
            "observed": int(V_q),
            "required": int(momentum_min),
            "repair": "Increase coherent momentum (V_q) or lower configured threshold by policy.",
        },
    ]

    eq_order: list[str] = []
    for item in checks:
        eq_name = str(item["eq"])
        if eq_name not in eq_order:
            eq_order.append(eq_name)

    grouped_checks: dict[str, list[dict[str, Any]]] = {}
    for item in checks:
        grouped_checks.setdefault(str(item["eq"]), []).append(item)

    failed_checks = []
    passed_eqs: list[str] = []
    first_failure_check_id: str | None = None
    for eq in eq_order:
        checks_for_eq = grouped_checks.get(eq, [])
        failing_item = next((item for item in checks_for_eq if not bool(item["ok"])), None)
        if failing_item is not None:
            failed_checks.append(
                {
                    "eq": eq,
                    "check_id": failing_item["check_id"],
                    "observed": failing_item["observed"],
                    "required": failing_item["required"],
                    "reason": f"{failing_item['check_id']} failed",
                }
            )
            first_failure_check_id = str(failing_item["check_id"])
            break
        passed_eqs.append(eq)

    failed_eq = failed_checks[0]["eq"] if failed_checks else None
    commit_allowed = not failed_checks

    law = _clamp01(law_score)
    grace = _clamp01(grace_score)
    law_grace = law * grace
    v_norm = _clamp01(float(V_q) / 65535.0)
    prov_conf = (
        _clamp01(provenance_confidence)
        if isinstance(provenance_confidence, (int, float))
        else 1.0
    )
    replay_factor = 1.0 if replay_protected is not False else 0.75
    eq9_fulfillment = law_grace * v_norm * prov_conf * replay_factor * (1.0 if commit_allowed else 0.0)
    tokens = max(1, int(output_tokens_est or 1))
    eq9_yield_per_token = eq9_fulfillment / float(tokens)

    repair_actions = []
    if first_failure_check_id is not None:
        for item in checks:
            if str(item["check_id"]) == first_failure_check_id:
                repair_actions.append(
                    {
                        "eq": item["eq"],
                        "check_id": item["check_id"],
                        "action": item["repair"],
                    }
                )
                break

    return {
        "blocked": not commit_allowed,
        "commit_allowed": commit_allowed,
        "failed_eq": failed_eq,
        "failed_checks": failed_checks,
        "passed_eqs": passed_eqs,
        "observed_values": {
            "mode": int(mode),
            "K": int(K),
            "P": int(P),
            "E": int(E),
            "V_q": int(V_q),
            "dW": int(dW),
            "output_tokens_est": int(output_tokens_est or 0),
            "law_score": law,
            "grace_score": grace,
        },
        "required_thresholds": {
            "mode_min": 2,
            "K_required": 1,
            "P_required": 1,
            "E_required": 1,
            "momentum_min": int(momentum_min),
            "abs_dW_max": 1,
        },
        "repair_actions": repair_actions,
        "eq9_metrics": {
            "fulfillment": round(eq9_fulfillment, 8),
            "yield_per_token": round(eq9_yield_per_token, 10),
            "law_grace_product": round(law_grace, 8),
            "v_norm": round(v_norm, 8),
            "tokens": tokens,
            "provenance_confidence": round(prov_conf, 8),
            "provenance_status": str(provenance_status or ""),
            "replay_protected": replay_protected,
            "anti_gaming": {
                "token_floor_applied": int(output_tokens_est or 0) <= 0,
                "provenance_weight_applied": isinstance(provenance_confidence, (int, float)),
                "replay_penalty_applied": replay_protected is False,
            },
        },
        "indefeasible_profile": {
            "model": "cumulative_lexicographic",
            "first_failure_blocks": True,
            "llm_override": False,
            "eq_order": eq_order,
        },
    }


__all__ = ["evaluate_eq_ladder"]
