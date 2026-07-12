"""Agent-side helpers for recording attachments and chat messages.

These helpers encapsulate the normalisation, substrate writes, S1/S2 ledger
updates, and advisory flow diagnostics used by the API layer. Diagnostics are
persisted under S2 ethics metadata but never block writes.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
import hashlib
from typing import Any, Dict, Mapping

from backend.fieldx_kernel.flow_rules import run_full_check, update_dynamic_mediator
from backend.fieldx_kernel.kernel_origin_equations import (
    calculate_alpha_from_primes,
    calculate_persistence_cost,
    equation_6_operational,
    solve_ethics,
)
from backend.fieldx_kernel.informational_unit import (
    CIU_ENTRY_CLASS,
    CIU_FACTORS,
    CIU_KERNEL_EXPONENTS,
    CIU_MMF_PROJECTIONS,
    CIU_RELATIONSHIP_LINKS,
    CIU_FLOW_RULE_TAGS,
)
from shared_types.coord_schema import bigint_str, parse_bigint
from backend.fieldx_kernel.governance_engine import (
    CoherenceException,
    GovernanceEngine,
    build_state_from_metadata,
)
from backend.ingestion.pipeline import project_blob
from backend.kernel.base_foundation import BaseFoundationService
from backend.kernel.rocksdb_layer_store import RocksDBLayerStore
from backend.fieldx_kernel.e6_packet import pack_header_v0
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.state import LAW_PRIME, S1_PRIMES
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.services.provenance import build_taxonomy_provenance
from backend.utils.normalise import normalise_text

_DEFAULT_COHERENCE = 0.9999
_ATTACHMENT_PREVIEW_LIMIT = 1000
_ATTACHMENT_CHUNK_CHARS = int(os.getenv("ATTACHMENT_CHUNK_CHARS", "2000"))
_ATTACHMENT_CHUNK_TOPIC_LIMIT = int(os.getenv("ATTACHMENT_CHUNK_TOPIC_LIMIT", "4"))
_ALPHA_VAL = calculate_alpha_from_primes()
_ETHICS_BASE = solve_ethics()
_SAFETY_MIN_SCORE = float(os.getenv("SAFETY_MIN_SCORE", "0.0"))
_MINIMAL_STUB_TEXT = os.getenv(
    "MINIMAL_STUB_TEXT",
    "[Content withheld due to safety constraints]",
)

LOGGER = logging.getLogger(__name__)
_UNRESOLVABLE_PATTERNS = (
    re.compile(r"\bcan(?:not|'t)\s+resolve\b", re.IGNORECASE),
    re.compile(r"\bunresolv(?:able|ed)\b", re.IGNORECASE),
    re.compile(r"\bno\s+access\s+to\s+.*(?:coord|coordinate|retrieval)\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+access\s+to\s+.*(?:external|systems?|storage|database|backend|ledger)\b", re.IGNORECASE),
    re.compile(r"\bonly\s+provide\s+an\s+interpretation\s+based\s+on\s+(?:its|the)\s+structure\b", re.IGNORECASE),
    re.compile(r"\bno\s+underlying\s+data\s+surfaces\b", re.IGNORECASE),
)

_CANONICAL_WEB4_RE = re.compile(r"^WX-[A-Za-z0-9]+-\d+(?:-[A-Za-z0-9]+)*$")
_LITE_WEB4_RE = re.compile(r"^WX-(\d+)$")


def _limit_list(values: Any, limit: int) -> list[str]:
    if not values:
        return []
    if isinstance(values, list):
        return [str(item) for item in values[:limit] if str(item)]
    return [str(values)][:limit]


def _coerce_coherence(metadata: Mapping[str, Any] | None) -> float:
    if not metadata:
        return _DEFAULT_COHERENCE
    hysteresis = metadata.get("hysteresis_coherence")
    if isinstance(hysteresis, (int, float)):
        return float(hysteresis)
    appraisal = metadata.get("appraisal")
    if isinstance(appraisal, Mapping):
        score = appraisal.get("score")
        if isinstance(score, (int, float)):
            return float(score)
    assessments = metadata.get("assessments")
    if isinstance(assessments, Mapping):
        score = assessments.get("score")
        if isinstance(score, (int, float)):
            return float(score)
    return _DEFAULT_COHERENCE


def _heuristic_summary(text: str, limit: int = 240) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    for marker in (".", "!", "?"):
        idx = cleaned.find(marker)
        if 0 < idx < limit:
            return cleaned[: idx + 1]
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit].rstrip()}..."


def _derive_safety(flow_diag: Mapping[str, Any]) -> tuple[float, bool]:
    ethics_value = float(_ETHICS_BASE.get("Ethics_Value", 1.0))
    lawfulness = flow_diag.get("lawfulness_level")
    lawfulness_score = (float(lawfulness) / 3.0) if isinstance(lawfulness, (int, float)) else 0.0
    safety_score = ethics_value * lawfulness_score
    flow_ok = flow_diag.get("flow_ok", True)
    minimal = bool((not flow_ok) or (lawfulness == 0) or (safety_score < _SAFETY_MIN_SCORE))
    return safety_score, minimal


def _extract_violation_info(metadata: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    violations: dict[str, Any] = {}
    total = 0
    for key in ("constraint_violations", "policy_violations", "violations"):
        value = metadata.get(key)
        if isinstance(value, (int, float)):
            count = max(0, int(value))
            violations[key] = count
            total += count
        elif isinstance(value, list):
            count = len([item for item in value if item])
            violations[key] = count
            total += count
        elif isinstance(value, dict):
            count = value.get("count")
            if isinstance(count, (int, float)):
                count_int = max(0, int(count))
                violations[key] = count_int
                total += count_int
    return total, violations


def _parse_allowed_dw(raw: str | None, fallback: list[int]) -> list[int]:
    if not raw:
        return list(fallback)
    parsed: list[int] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            parsed.append(int(text))
        except Exception:
            continue
    return parsed or list(fallback)


def _e6_threshold_snapshot(governance_engine: GovernanceEngine) -> dict[str, Any]:
    base = governance_engine.thresholds
    return {
        "theta_L": float(os.getenv("E6_THETA_L", str(base.get("theta_L", 0.85)))),
        "theta_H": float(os.getenv("E6_THETA_H", str(base.get("theta_H", 0.70)))),
        "theta_V": float(os.getenv("E6_THETA_V", str(base.get("theta_V", 0.45)))),
        "theta_sigma": float(os.getenv("E6_THETA_SIGMA", str(base.get("V_std_max", 0.1)))),
        "theta_self": float(os.getenv("E6_THETA_SELF", str(base.get("theta_self", 0.6)))),
        "allowed_dW": _parse_allowed_dw(
            os.getenv("E6_ALLOWED_DW"),
            [int(v) for v in base.get("allowed_dW", [-1, 0, 1])],
        ),
    }


def _e6_rollout_flags() -> dict[str, bool]:
    def _on(name: str) -> bool:
        return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "on"}

    return {
        "E6_SCORING_STRICT": _on("E6_SCORING_STRICT"),
        "E6_MODE_GATING_STRICT": _on("E6_MODE_GATING_STRICT"),
        "E6_HALT_MINIMAL_COMMIT_ONLY": _on("E6_HALT_MINIMAL_COMMIT_ONLY"),
    }


def _build_e6_scoring_snapshot(
    *,
    metrics: Mapping[str, Any],
    bridge_allowed_runtime: bool,
    v_mean_3: float | None,
    v_std_3: float | None,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    def _f(key: str, default: float = 0.0) -> float:
        value = metrics.get(key)
        return float(value) if isinstance(value, (int, float)) else default

    def _i(key: str, default: int = 0) -> int:
        value = metrics.get(key)
        return int(value) if isinstance(value, (int, float)) else default

    l_top = 1 if _f("L_top") >= 1.0 else 0
    k_t = 1 if _i("K") == 1 else 0
    p_t = 1 if _i("P") == 1 else 0
    e_t = 1 if _i("E") == 1 else 0
    l_phys = _f("L_phys")
    h_t = _f("H")
    a_corr = _f("A_corr")
    a_self = _f("A_self", 1.0)
    a_t = _f("A", a_corr * a_self)
    u_t = _f("U")
    v_int = a_t * u_t
    theta_l = float(thresholds.get("theta_L", 0.85))
    theta_h = float(thresholds.get("theta_H", 0.70))
    theta_v = float(thresholds.get("theta_V", 0.45))
    theta_sigma = float(thresholds.get("theta_sigma", 0.1))
    theta_self = float(thresholds.get("theta_self", 0.6))
    allowed_dw = thresholds.get("allowed_dW") if isinstance(thresholds.get("allowed_dW"), list) else [-1, 0, 1]
    d_w = _i("dW")
    l_top_from_policy = 1 if d_w in [int(v) for v in allowed_dw if isinstance(v, (int, float))] else 0
    bridge_formula_eval = bool(
        p_t == 1
        and e_t == 1
        and l_top_from_policy == 1
        and k_t == 1
        and l_phys >= theta_l
        and h_t >= theta_h
        and (v_mean_3 is not None and v_mean_3 >= theta_v)
        and (v_std_3 is not None and v_std_3 <= theta_sigma)
        and a_self >= theta_self
    )

    return {
        "hard_gates": {
            "L_top": l_top,
            "K_t": k_t,
            "P_t": p_t,
            "E_t": e_t,
        },
        "soft_metrics": {
            "L_phys": l_phys,
            "L_t": _f("L"),
            "H_t": h_t,
            "A_corr": a_corr,
            "A_self": a_self,
            "A_t": a_t,
            "U_t": u_t,
            "V_int_t": v_int,
        },
        "window": {
            "V_int_mean_3": v_mean_3,
            "V_int_std_3": v_std_3,
        },
        "thresholds": {
            "theta_L": theta_l,
            "theta_H": theta_h,
            "theta_V": theta_v,
            "theta_sigma": theta_sigma,
            "theta_self": theta_self,
            "allowed_dW": allowed_dw,
        },
        "bridge_allowed_runtime": bool(bridge_allowed_runtime),
        "bridge_allowed_formula_eval": bridge_formula_eval,
        "non_compensatory": True,
    }


def _assess_resolution_consistency(
    *,
    role: str,
    content: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any] | None:
    if role != "assistant":
        return None

    resolved_count = 0
    resolved_coords = metadata.get("resolved_coords")
    if isinstance(resolved_coords, list):
        resolved_count = len([coord for coord in resolved_coords if isinstance(coord, str) and coord.strip()])
    decoded_count = metadata.get("decoded_count")
    if isinstance(decoded_count, (int, float)):
        resolved_count = max(resolved_count, int(decoded_count))
    if resolved_count <= 0:
        return None

    matched = [p.pattern for p in _UNRESOLVABLE_PATTERNS if p.search(content or "")]
    if matched:
        return {
            "status": "contradiction",
            "reason": "resolved_context_but_claimed_unresolvable",
            "resolved_count": resolved_count,
            "matched_patterns": matched[:2],
        }

    return {
        "status": "consistent",
        "resolved_count": resolved_count,
    }


def _coerce_appraisal_values(metadata: Mapping[str, Any]) -> dict[str, float]:
    base = {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0}
    appraisal = metadata.get("appraisal")
    if not isinstance(appraisal, Mapping):
        return base
    for key in ("score", "law_score", "grace_score", "drift"):
        value = appraisal.get(key)
        if isinstance(value, (int, float)):
            base[key] = float(value)
    return base


def _apply_resolution_contradiction_penalty(metadata: Dict[str, Any]) -> None:
    if not bool(metadata.get("resolution_contradiction")):
        return

    caps = {
        "score": float(os.getenv("RESOLUTION_CONTRADICTION_SCORE_CAP", "0.65")),
        "law_score": float(os.getenv("RESOLUTION_CONTRADICTION_LAW_CAP", "0.55")),
        "grace_score": float(os.getenv("RESOLUTION_CONTRADICTION_GRACE_CAP", "0.85")),
        "drift": float(os.getenv("RESOLUTION_CONTRADICTION_DRIFT_FLOOR", "0.35")),
    }
    current = _coerce_appraisal_values(metadata)
    governance_metrics = metadata.get("governance_metrics")
    a_self_metric = None
    if isinstance(governance_metrics, Mapping):
        raw_a_self = governance_metrics.get("A_self")
        if isinstance(raw_a_self, (int, float)):
            a_self_metric = float(raw_a_self)
    penalized = {
        "score": min(current["score"], caps["score"]),
        "law_score": min(current["law_score"], caps["law_score"]),
        "grace_score": min(current["grace_score"], caps["grace_score"]),
        "drift": max(current["drift"], caps["drift"]),
    }
    if a_self_metric is not None:
        penalized["law_score"] = min(penalized["law_score"], a_self_metric)

    metadata["drift_structural"] = float(current["drift"])
    metadata["drift_grounding"] = float(caps["drift"])
    metadata["appraisal"] = penalized
    metadata["appraisal_penalties"] = {
        "resolution_contradiction": True,
        "applied": {
            "score_cap": caps["score"],
            "law_cap": caps["law_score"],
            "grace_cap": caps["grace_score"],
            "drift_floor": caps["drift"],
        },
    }

    loop_risk = metadata.get("loop_risk")
    if isinstance(loop_risk, dict):
        loop_risk["grounding_gap"] = max(float(loop_risk.get("grounding_gap") or 0.0), 0.5)
        loop_risk["loop_risk"] = max(float(loop_risk.get("loop_risk") or 0.0), 0.55)


def _apply_governance_block_penalty(metadata: Dict[str, Any]) -> None:
    governance_error = metadata.get("governance_error")
    if not isinstance(governance_error, Mapping):
        return

    caps = {
        "score": float(os.getenv("GOV_BLOCK_SCORE_CAP", "0.80")),
        "law_score": float(os.getenv("GOV_BLOCK_LAW_CAP", "0.40")),
        "grace_score": float(os.getenv("GOV_BLOCK_GRACE_CAP", "0.90")),
        "drift": float(os.getenv("GOV_BLOCK_DRIFT_FLOOR", "0.20")),
    }
    current = _coerce_appraisal_values(metadata)
    penalized = {
        "score": min(current["score"], caps["score"]),
        "law_score": min(current["law_score"], caps["law_score"]),
        "grace_score": min(current["grace_score"], caps["grace_score"]),
        "drift": max(current["drift"], caps["drift"]),
    }
    metadata["appraisal"] = penalized
    metadata["appraisal_penalties"] = {
        **(metadata.get("appraisal_penalties") if isinstance(metadata.get("appraisal_penalties"), Mapping) else {}),
        "governance_blocked": True,
        "governance_reason": str(governance_error.get("reason") or "governance_error"),
        "governance_applied": {
            "score_cap": caps["score"],
            "law_cap": caps["law_score"],
            "grace_cap": caps["grace_score"],
            "drift_floor": caps["drift"],
        },
    }


def _build_genesis_vector(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    metrics_view = metrics if isinstance(metrics, Mapping) else {}
    eq0 = bool(metrics_view.get("eq0_distinction"))
    eq1 = bool(metrics_view.get("eq1_dual_substrate"))
    eq2 = bool(metrics_view.get("eq2_time_irreversible"))
    eq3 = bool(metrics_view.get("eq3_geometry_closure"))
    return {
        "eq0_distinction": eq0,
        "eq1_dual_substrate": eq1,
        "eq2_time_irreversible": eq2,
        "eq3_geometry_closure": eq3,
        "all_ok": bool(eq0 and eq1 and eq2 and eq3),
    }


def _build_genesis_repair_hints(genesis_vector: Mapping[str, Any]) -> list[dict[str, str]]:
    if not isinstance(genesis_vector, Mapping):
        return []
    hints: list[dict[str, str]] = []
    if not bool(genesis_vector.get("eq0_distinction")):
        hints.append(
            {
                "equation": "eq0_distinction",
                "hint": "Record a non-zero state distinction (W or I1/I2) before governance evaluation.",
            }
        )
    if not bool(genesis_vector.get("eq1_dual_substrate")):
        hints.append(
            {
                "equation": "eq1_dual_substrate",
                "hint": "Provide both substrate states (continuous + discrete) so dual-substrate checks can pass.",
            }
        )
    if not bool(genesis_vector.get("eq2_time_irreversible")):
        hints.append(
            {
                "equation": "eq2_time_irreversible",
                "hint": "Preserve chain continuity (valid prev_hash and K=1) to enforce irreversible progression.",
            }
        )
    if not bool(genesis_vector.get("eq3_geometry_closure")):
        hints.append(
            {
                "equation": "eq3_geometry_closure",
                "hint": "Constrain transitions (|dW|<=1) and keep theta_var > 0 to satisfy closure constraints.",
            }
        )
    return hints


def _extract_unity_value(meta: Mapping[str, Any] | None) -> float | None:
    if not isinstance(meta, Mapping):
        return None
    governance_metrics = meta.get("governance_metrics")
    if isinstance(governance_metrics, Mapping):
        raw_u = governance_metrics.get("U")
        if isinstance(raw_u, (int, float)):
            return float(raw_u)
    governance = meta.get("governance")
    if isinstance(governance, Mapping):
        u_block = governance.get("U")
        if isinstance(u_block, Mapping):
            raw_u = u_block.get("U")
            if isinstance(raw_u, (int, float)):
                return float(raw_u)
    return None


def _apply_unity_conservation_indicators(
    *,
    metadata: Dict[str, Any],
    latest_meta: Mapping[str, Any] | None,
) -> None:
    current_u = _extract_unity_value(metadata)
    previous_u = _extract_unity_value(latest_meta)
    unity_delta: float | None = None
    if isinstance(current_u, float) and isinstance(previous_u, float):
        unity_delta = float(current_u - previous_u)
    metadata["unity_current"] = current_u
    metadata["unity_previous"] = previous_u
    metadata["unity_delta"] = unity_delta

    contradiction_count_turn = 0
    if bool(metadata.get("resolution_contradiction")):
        contradiction_count_turn += 1
    contradiction_gate = metadata.get("governance_contradiction_gate")
    if isinstance(contradiction_gate, Mapping) and contradiction_gate.get("blocked") is True:
        contradiction_count_turn += 1
    governance_error = metadata.get("governance_error")
    if isinstance(governance_error, Mapping):
        reason = str(governance_error.get("reason") or "").lower()
        if "contradiction" in reason:
            contradiction_count_turn += 1
    metadata["contradiction_count_turn"] = contradiction_count_turn

    prev_streak_raw = latest_meta.get("contradiction_streak") if isinstance(latest_meta, Mapping) else None
    prev_streak = int(prev_streak_raw) if isinstance(prev_streak_raw, int) else 0
    contradiction_streak = (prev_streak + 1) if contradiction_count_turn > 0 else 0
    metadata["contradiction_streak"] = contradiction_streak

    unity_drop_threshold = float(os.getenv("UNITY_DROP_ALERT_THRESHOLD", "-0.10"))
    contradiction_streak_threshold = int(os.getenv("CONTRADICTION_STREAK_ALERT_THRESHOLD", "3"))
    unity_drop_alert = isinstance(unity_delta, float) and unity_delta <= unity_drop_threshold
    contradiction_streak_alert = contradiction_streak >= max(1, contradiction_streak_threshold)
    metadata["unity_alerts"] = {
        "unity_drop": bool(unity_drop_alert),
        "contradiction_streak": bool(contradiction_streak_alert),
    }


def _apply_loop_integrity_indicators(metadata: Dict[str, Any], *, role: str, content: str) -> None:
    resolved_count = 0
    resolved_coords = metadata.get("resolved_coords")
    if isinstance(resolved_coords, list):
        resolved_count = len([coord for coord in resolved_coords if isinstance(coord, str) and coord.strip()])
    decoded_count_raw = metadata.get("decoded_count")
    if isinstance(decoded_count_raw, (int, float)):
        resolved_count = max(resolved_count, int(decoded_count_raw))
    context_window = metadata.get("context_window")
    if isinstance(context_window, Mapping):
        retrieved_count = context_window.get("retrieved_count")
        if isinstance(retrieved_count, (int, float)):
            resolved_count = max(resolved_count, int(retrieved_count))

    inhale_ok = resolved_count > 0
    exhale_ok = role == "assistant" and bool((content or "").strip())
    feedback_ok = isinstance(metadata.get("resolution_consistency"), Mapping) or bool(
        metadata.get("coord_feedback")
    )
    appraisal = metadata.get("appraisal")
    governance_metrics = metadata.get("governance_metrics")
    resolution_ok = isinstance(appraisal, Mapping) and isinstance(governance_metrics, Mapping)

    stages = {
        "inhale": bool(inhale_ok),
        "exhale": bool(exhale_ok),
        "feedback": bool(feedback_ok),
        "resolution": bool(resolution_ok),
    }
    missing = [name for name, ok in stages.items() if not ok]
    score = sum(1 for ok in stages.values() if ok) / 4.0
    metadata["loop_integrity"] = {
        "score": round(score, 4),
        "stages": stages,
        "missing": missing,
    }


def _apply_coherence_tax_indicators(
    metadata: Dict[str, Any],
    *,
    quality_tier: str,
    gravity_penalty: float,
) -> None:
    resolved_count = 0
    resolved_coords = metadata.get("resolved_coords")
    if isinstance(resolved_coords, list):
        resolved_count = len([coord for coord in resolved_coords if isinstance(coord, str) and coord.strip()])
    decoded_count_raw = metadata.get("decoded_count")
    if isinstance(decoded_count_raw, (int, float)):
        resolved_count = max(resolved_count, int(decoded_count_raw))
    context_window = metadata.get("context_window")
    if isinstance(context_window, Mapping):
        retrieved_count = context_window.get("retrieved_count")
        if isinstance(retrieved_count, (int, float)):
            resolved_count = max(resolved_count, int(retrieved_count))

    output_tokens = 0
    gen_output_tokens = metadata.get("gen_output_tokens")
    if isinstance(gen_output_tokens, (int, float)):
        output_tokens = int(gen_output_tokens)
    elif isinstance(context_window, Mapping):
        completion_tokens = context_window.get("completion_tokens")
        if isinstance(completion_tokens, (int, float)):
            output_tokens = int(completion_tokens)

    governance_metrics = metadata.get("governance_metrics")
    violations_count = 0
    if isinstance(governance_metrics, Mapping):
        raw_violations = governance_metrics.get("violations_count")
        if isinstance(raw_violations, (int, float)):
            violations_count = int(raw_violations)
    contradiction_count_turn = metadata.get("contradiction_count_turn")
    contradiction_count = int(contradiction_count_turn) if isinstance(contradiction_count_turn, int) else 0
    lawfulness = metadata.get("eq6_lawfulness_level")
    lawfulness_level = int(lawfulness) if isinstance(lawfulness, int) else 0

    base_weight = (
        0.12 * float(max(resolved_count, 0))
        + 0.004 * float(max(output_tokens, 0))
        + 0.45 * float(max(violations_count, 0))
        + 0.70 * float(max(contradiction_count, 0))
        + max(0.0, 1.5 - (0.4 * float(max(lawfulness_level, 0))))
    )
    tier_factor = {
        "express": 1.3,
        "stabilise": 1.0,
        "probe": 0.85,
        "halt": 0.70,
    }.get(str(quality_tier), 1.0)
    penalty_term = 0.25 * min(max(float(gravity_penalty), 0.0), 2.0)
    coherence_budget_spent = max(0.0, base_weight * tier_factor + penalty_term)

    if coherence_budget_spent < 1.0:
        bucket = "light"
    elif coherence_budget_spent < 2.5:
        bucket = "medium"
    else:
        bucket = "heavy"

    metadata["coherence_tax"] = {
        "coherence_budget_spent": round(coherence_budget_spent, 4),
        "decision_weight": round(base_weight, 4),
        "bucket": bucket,
        "inputs": {
            "retrieved_count": int(max(resolved_count, 0)),
            "output_tokens": int(max(output_tokens, 0)),
            "violations_count": int(max(violations_count, 0)),
            "contradiction_count_turn": int(max(contradiction_count, 0)),
            "lawfulness_level": int(max(lawfulness_level, 0)),
            "quality_tier": str(quality_tier),
            "gravity_penalty": float(gravity_penalty),
        },
    }


def _apply_alpha_balance_indicators(
    metadata: Dict[str, Any],
    *,
    latest_meta: Mapping[str, Any] | None,
) -> None:
    appraisal = _coerce_appraisal_values(metadata)
    law = float(appraisal.get("law_score", 0.0))
    grace = float(appraisal.get("grace_score", 0.0))
    product = law * grace
    tension = abs(law - grace)

    if min(law, grace) >= 0.85 and tension <= 0.15:
        state = "stable"
    elif min(law, grace) >= 0.60 and tension <= 0.35:
        state = "strained"
    else:
        state = "unstable"

    previous_product: float | None = None
    if isinstance(latest_meta, Mapping):
        prev_block = latest_meta.get("alpha_balance")
        if isinstance(prev_block, Mapping):
            prev_product = prev_block.get("law_grace_product")
            if isinstance(prev_product, (int, float)):
                previous_product = float(prev_product)

    trend_delta: float | None = None
    if isinstance(previous_product, float):
        trend_delta = float(product - previous_product)

    metadata["alpha_balance"] = {
        "law_score": round(law, 4),
        "grace_score": round(grace, 4),
        "law_grace_product": round(product, 4),
        "tension": round(tension, 4),
        "state": state,
        "target_alpha": float(_ALPHA_VAL),
        "trend_delta_product": round(trend_delta, 4) if isinstance(trend_delta, float) else None,
    }


def _series_slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float((values[-1] - values[0]) / float(len(values) - 1))


def _apply_eq89_trend_indicators(
    metadata: Dict[str, Any],
    *,
    latest_meta: Mapping[str, Any] | None,
) -> None:
    trend_window_raw = os.getenv("EQ89_TREND_WINDOW", "8")
    try:
        trend_window = max(int(trend_window_raw), 2)
    except ValueError:
        trend_window = 8

    appraisal = _coerce_appraisal_values(metadata)
    law = float(appraisal.get("law_score", 0.0))
    grace = float(appraisal.get("grace_score", 0.0))
    law_grace_product = law * grace

    governance_metrics = metadata.get("governance_metrics")
    awareness = 0.0
    unity = 0.0
    ethics = 0.0
    if isinstance(governance_metrics, Mapping):
        for key in ("A_self", "A_corr", "A"):
            value = governance_metrics.get(key)
            if isinstance(value, (int, float)):
                awareness = float(value)
                break
        raw_u = governance_metrics.get("U")
        if isinstance(raw_u, (int, float)):
            unity = float(raw_u)
        raw_ethics = governance_metrics.get("ethics_gate")
        if isinstance(raw_ethics, (int, float)):
            ethics = float(raw_ethics)
        else:
            raw_e = governance_metrics.get("E")
            if isinstance(raw_e, (int, float)):
                ethics = float(raw_e)
    telos_proxy = awareness * unity * ethics

    prev_trend = latest_meta.get("eq89_trend") if isinstance(latest_meta, Mapping) else None
    history_block = (
        prev_trend.get("history")
        if isinstance(prev_trend, Mapping) and isinstance(prev_trend.get("history"), Mapping)
        else {}
    )

    def _history_list(key: str) -> list[float]:
        values = history_block.get(key) if isinstance(history_block, Mapping) else None
        if not isinstance(values, list):
            return []
        out: list[float] = []
        for item in values:
            if isinstance(item, (int, float)):
                out.append(float(item))
        return out[-(trend_window - 1):]

    law_history = _history_list("law_score")
    grace_history = _history_list("grace_score")
    product_history = _history_list("law_grace_product")
    telos_history = _history_list("telos_proxy")

    law_history.append(law)
    grace_history.append(grace)
    product_history.append(law_grace_product)
    telos_history.append(telos_proxy)

    product_slope = _series_slope(product_history)
    if law_grace_product >= 0.8 and product_slope >= 0.0:
        state = "improving"
    elif law_grace_product >= 0.5:
        state = "steady"
    else:
        state = "fragile"

    metadata["eq89_trend"] = {
        "current": {
            "law_score": round(law, 4),
            "grace_score": round(grace, 4),
            "law_grace_product": round(law_grace_product, 4),
            "telos_proxy": round(telos_proxy, 4),
        },
        "history": {
            "law_score": [round(value, 4) for value in law_history[-trend_window:]],
            "grace_score": [round(value, 4) for value in grace_history[-trend_window:]],
            "law_grace_product": [round(value, 4) for value in product_history[-trend_window:]],
            "telos_proxy": [round(value, 4) for value in telos_history[-trend_window:]],
        },
        "slope": {
            "law_score": round(_series_slope(law_history), 4),
            "grace_score": round(_series_slope(grace_history), 4),
            "law_grace_product": round(product_slope, 4),
            "telos_proxy": round(_series_slope(telos_history), 4),
        },
        "state": state,
    }


def _apply_contradiction_bridge_floor(
    *,
    metadata: Mapping[str, Any],
    metrics: Mapping[str, Any] | None,
    bridge_ok: bool,
) -> tuple[bool, dict[str, Any] | None]:
    if not bool(metadata.get("resolution_contradiction")):
        return bool(bridge_ok), None
    if not isinstance(metrics, Mapping):
        return bool(bridge_ok), {"blocked": True, "reason": "resolution_contradiction"}

    a_self = metrics.get("A_self")
    theta_self = metrics.get("theta_self")
    if not isinstance(a_self, (int, float)) or not isinstance(theta_self, (int, float)):
        return bool(bridge_ok), {"blocked": True, "reason": "resolution_contradiction"}

    if float(a_self) < float(theta_self):
        return False, {
            "blocked": True,
            "reason": "resolution_contradiction_a_self_below_theta",
            "A_self": float(a_self),
            "theta_self": float(theta_self),
        }
    return bool(bridge_ok), None


def _apply_contradiction_violation_weight(
    *,
    metadata: Mapping[str, Any],
    violations_count: int,
    violations_detail: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if not bool(metadata.get("resolution_contradiction")):
        return violations_count, violations_detail
    weight = int(os.getenv("E6_CONTRADICTION_VIOLATIONS", "2"))
    if weight < 1:
        weight = 1
    updated = dict(violations_detail)
    updated["resolution_contradiction"] = int(updated.get("resolution_contradiction") or 0) + weight
    return violations_count + weight, updated


def _non_bypass_gate(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("policy_gate_bypass") is True:
        return False
    if metadata.get("tool_calls_unlogged") is True:
        return False
    if metadata.get("inputs_logged") is False:
        return False
    return True


def _chunk_text(text: str, max_chars: int) -> list[tuple[str, int, int]]:
    if not text:
        return []
    if max_chars <= 0 or len(text) <= max_chars:
        return [(text, 0, len(text))]
    chunks: list[tuple[str, int, int]] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + max_chars, text_len)
        if end < text_len:
            window = text[start:end]
            split_at = max(window.rfind(" "), window.rfind("\n"))
            if split_at > 0:
                end = start + split_at
        chunk = text[start:end].strip()
        if chunk:
            chunks.append((chunk, start, end))
        start = end
        while start < text_len and text[start].isspace():
            start += 1
    return chunks


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


def _cw_from_lawfulness(eq6_lawfulness_level: int | None) -> int:
    # Spec: flowRulesOperationalised.md "Clean base-4 policy (2 bits) from Eq6"
    if eq6_lawfulness_level is None:
        return 3
    level = _clamp_int(int(eq6_lawfulness_level), 0, 3)
    return 3 - level


_ODD_PRIME_BY_CW: tuple[int, int, int, int] = (7, 3, 13, 19)


def _pick_odd_at_C(cw: int) -> int:
    return _ODD_PRIME_BY_CW[cw & 0b11]


def _start_even_for_odd(odd_prime: int) -> int:
    return 2 if odd_prime in (3, 7) else 11


def _terminal_even_for_branch(odd_prime: int, commit_allowed: bool) -> int:
    if odd_prime == 3:
        return 5
    if odd_prime == 13:
        return 17
    if odd_prime == 7:
        return 11 if commit_allowed else 2
    if odd_prime == 19:
        return 2 if commit_allowed else 11
    return 11


def _build_flow_sequence(
    *,
    prime: int,
    mediator_prime: int,
    eq6_lawfulness_level: int | None,
    eq6_commit_allowed: bool | None,
) -> list[int]:
    cw = _cw_from_lawfulness(eq6_lawfulness_level)
    odd_prime = _pick_odd_at_C(cw)
    start_even = _start_even_for_odd(odd_prime)
    commit_allowed = bool(eq6_commit_allowed)
    terminal_even = _terminal_even_for_branch(odd_prime, commit_allowed)
    return [start_even, mediator_prime, odd_prime, terminal_even, mediator_prime, prime]


def _run_flow_advisory(
    prime: int,
    *,
    kind: str,
    role: str | None = None,
    eq6_lawfulness_level: int | None = None,
    eq6_commit_allowed: bool | None = None,
    eq6_mediator_prime: int | None = None,
) -> Dict[str, Any]:
    """Run flow rules in advisory mode for ``prime``.

    Any exception is captured into the diagnostics payload so that writes remain
    best-effort.
    """

    try:
        mediator_for_path = eq6_mediator_prime or update_dynamic_mediator(LAW_PRIME, _DEFAULT_COHERENCE)
        flow_sequence = _build_flow_sequence(
            prime=prime,
            mediator_prime=mediator_for_path,
            eq6_lawfulness_level=eq6_lawfulness_level,
            eq6_commit_allowed=eq6_commit_allowed,
        )
        flow_ok, flow_msg, mediator_prime, lawfulness_level = run_full_check(
            flow_sequence, _DEFAULT_COHERENCE
        )
        diagnostics: Dict[str, Any] = {
            "flow_ok": flow_ok,
            "flow_message": flow_msg,
            "mediator_prime": mediator_prime,
            "lawfulness_level": lawfulness_level,
            "kind": kind,
        }
        if role:
            diagnostics["role"] = role
        return diagnostics
    except Exception as exc:  # pragma: no cover - defensive
        diagnostics = {
            "flow_ok": False,
            "flow_message": str(exc),
            "mediator_prime": None,
            "lawfulness_level": None,
            "kind": kind,
            "error": True,
        }
        if role:
            diagnostics["role"] = role
        return diagnostics


def _persist_s1(entity: str, *, prime: int, ledger, kind: str, role: str | None = None) -> Dict[str, Any]:
    s1_updates = {
        str(prime_id): {
            "refs": [prime],
            "metadata": {"kind": kind, **({"role": role} if role else {})},
        }
        for prime_id in S1_PRIMES
    }
    return ledger.update_S1(entity, s1_updates)


def _build_s2_updates(
    *,
    prime: int,
    norm: Dict[str, Any],
    kind: str,
    role: str | None = None,
    flow_diag: Dict[str, Any],
) -> Dict[str, Any]:
    s2_updates: Dict[str, Any] = {
        "11": {"summary_ref": prime, "metadata": {"kind": kind}},
        "13": {"taxonomy": norm.get("topics", [])},
        "17": {"linkmap": norm.get("tags", [])},
        "19": {"claims": norm.get("quotes", [])},
    }

    if role:
        s2_updates["11"]["metadata"]["role"] = role

    ethics_meta = s2_updates["11"].setdefault("metadata", {}).setdefault("ethics", {})
    ethics_meta["flow_rules"] = flow_diag

    return s2_updates


def _build_attachment_identifier(
    entity: str,
    metadata: Mapping[str, Any] | None,
    raw_text: str | None = None,
) -> str:
    attachment_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    attachment_block = attachment_metadata.get("attachment", {})
    identifier_source = None
    if isinstance(attachment_block, dict):
        identifier_source = attachment_block.get("sha256")
    if not identifier_source:
        identifier_source = attachment_metadata.get("sha256")

    timestamp_part = str(int(time.time() * 1000))
    if identifier_source:
        tag = re.sub(r"[^A-Za-z0-9]", "", str(identifier_source))[:8]
        if tag:
            return f"ATT-{tag}-{timestamp_part}"
    seed = "|".join(
        [
            str(entity or ""),
            str(attachment_metadata.get("session_id") or ""),
            str(attachment_metadata.get("turn_id") or ""),
            str(raw_text or "")[:256],
            timestamp_part,
        ]
    )
    tag = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return f"ATT-{tag}-{timestamp_part}"


def _canonical_web4_key(entity: str, candidate: Any) -> str:
    candidate_str = str(candidate or "").strip()
    if candidate_str and _CANONICAL_WEB4_RE.match(candidate_str):
        return candidate_str
    lite_match = _LITE_WEB4_RE.match(candidate_str)
    if lite_match:
        entity_hash = hashlib.md5(str(entity).encode("utf-8")).hexdigest()[:8].upper()
        return f"WX-{entity_hash}-{lite_match.group(1)}"
    entity_hash = hashlib.md5(str(entity).encode("utf-8")).hexdigest()[:8].upper()
    return f"WX-{entity_hash}-{int(time.time())}"


def record_attachment_fast(
    entity: str,
    raw_text: str,
    kind: str,
    metadata: Mapping[str, Any] | None,
    summary_override: str | None,
    chunk_chars: int | None,
    substrate,
    ledger,
    store: LedgerStoreV2 | None = None,
) -> Dict[str, Any]:
    """Persist attachment bodies and S1 only, returning data for async S2/store writes."""

    chunk_limit = chunk_chars if chunk_chars is not None else _ATTACHMENT_CHUNK_CHARS
    chunks = _chunk_text(raw_text, chunk_limit)
    if not chunks:
        chunks = [(raw_text, 0, len(raw_text))]

    chunk_primes: list[int] = [substrate.allocate_body_prime(entity) for _ in chunks]
    prime_value = chunk_primes[0] if chunk_primes else 0
    flow_diag = _run_flow_advisory(prime_value, kind=kind)
    safety_score, minimal_stub = _derive_safety(flow_diag)

    for chunk_index, (chunk_text, _, _) in enumerate(chunks):
        stored_text = _MINIMAL_STUB_TEXT if minimal_stub else chunk_text
        chunk_norm = normalise_text(stored_text)
        chunk_prime = chunk_primes[chunk_index]
        substrate.write_body_prime(entity, chunk_prime, stored_text, chunk_norm)

    summary_text = summary_override or ""
    summary_prime: int | None = None
    summary_body: Dict[str, Any] | None = None
    if summary_text and not minimal_stub:
        summary_norm = normalise_text(summary_text)
        summary_prime = substrate.allocate_body_prime(entity)
        summary_body = substrate.write_body_prime(entity, summary_prime, summary_text, summary_norm)

    prime = summary_prime if summary_prime is not None else (chunk_primes[0] if chunk_primes else None)
    body = summary_body if summary_body is not None else {}

    s1_state = _persist_s1(entity, prime=prime, ledger=ledger, kind=kind) if prime is not None else {}

    base_identifier = _build_attachment_identifier(entity, metadata, raw_text)
    part_count = len(chunks)
    part_range = f"T001-T{part_count:03d}" if part_count else ""
    part_coordinates: list[str] = []
    coordinate: str | None = None
    if store is not None:
        coordinate = f"{entity}:{base_identifier}"

    attachment_job = {
        "entity": entity,
        "raw_text": raw_text,
        "kind": kind,
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else None,
        "summary_override": summary_override,
        "chunk_chars": chunk_chars,
        "chunk_primes": chunk_primes,
        "summary_prime": summary_prime,
        "base_identifier": base_identifier,
        "flow_diagnostics": flow_diag,
        "minimal_stub": minimal_stub,
        "safety_score": safety_score,
    }

    return {
        "prime": prime,
        "body": body,
        "s1": s1_state,
        "s2": {},
        "flow_diagnostics": flow_diag,
        "coordinate": coordinate or (str(prime) if prime is not None else None),
        "part_coordinates": part_coordinates,
        "part_count": part_count,
        "part_range": part_range,
        "attachment_job": attachment_job,
    }


def record_attachment_finalize(
    *,
    entity: str,
    raw_text: str,
    kind: str,
    metadata: Mapping[str, Any] | None,
    summary_override: str | None,
    chunk_chars: int | None,
    chunk_primes: list[int],
    summary_prime: int | None,
    base_identifier: str,
    flow_diagnostics: Dict[str, Any],
    minimal_stub: bool,
    safety_score: float,
    ledger,
    store: LedgerStoreV2 | None = None,
) -> Dict[str, Any]:
    """Finalize S2 and ledger-store writes for an attachment."""

    chunk_limit = chunk_chars if chunk_chars is not None else _ATTACHMENT_CHUNK_CHARS
    chunks = _chunk_text(raw_text, chunk_limit)
    if not chunks:
        chunks = [(raw_text, 0, len(raw_text))]

    norm_source = "" if minimal_stub else raw_text
    norm = normalise_text(norm_source)
    norm["kind"] = kind
    if metadata:
        norm["metadata"] = dict(metadata)
    if minimal_stub:
        norm["topics"] = []
        norm["tags"] = []
        norm["quotes"] = []

    prime_value = chunk_primes[0] if chunk_primes else 0
    s2_updates = _build_s2_updates(prime=prime_value, norm=norm, kind=kind, flow_diag=flow_diagnostics)
    s2_state = ledger.update_S2(entity, s2_updates)

    prime = summary_prime if summary_prime is not None else (chunk_primes[0] if chunk_primes else None)

    entry_coordinate: str | None = None
    fallback_coordinate: str | None = None
    part_coordinates: list[str] = []
    part_count = len(chunks)
    part_range = f"T001-T{part_count:03d}" if part_count else ""
    if store is not None:
        preview_source = summary_override if summary_override is not None else raw_text
        preview = preview_source.strip()
        if len(preview) > _ATTACHMENT_PREVIEW_LIMIT:
            preview = preview[:_ATTACHMENT_PREVIEW_LIMIT].rstrip() + "…"
        if minimal_stub:
            preview = _MINIMAL_STUB_TEXT

        attachment_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        provenance_keys = (
            "ledger_id",
            "contributor_id",
            "contributor",
            "context_id",
            "provider_id",
            "model_id",
            "session_id",
            "turn_id",
            "auth_method",
            "provenance_dual_write",
        )
        attachment_coherence = _coerce_coherence(attachment_metadata)
        summary_cost = calculate_persistence_cost(_ALPHA_VAL, attachment_coherence, len(raw_text))

        part_count = len(chunks)

        attachment_parts: list[dict[str, Any]] = []
        for index, (chunk_text, start, end) in enumerate(chunks, start=1):
            time.sleep(0.005)
            stored_text = _MINIMAL_STUB_TEXT if minimal_stub else chunk_text
            part_summary = _MINIMAL_STUB_TEXT if minimal_stub else _heuristic_summary(chunk_text)
            chunk_norm = normalise_text(stored_text)
            chunk_topics = _limit_list(chunk_norm.get("topics", []), _ATTACHMENT_CHUNK_TOPIC_LIMIT)
            chunk_tags = _limit_list(chunk_norm.get("tags", []), _ATTACHMENT_CHUNK_TOPIC_LIMIT)
            chunk_cost = calculate_persistence_cost(
                _ALPHA_VAL,
                attachment_coherence,
                len(chunk_text),
            )
            chunk_prime = chunk_primes[index - 1] if len(chunk_primes) >= index else None
            part_identifier = f"{base_identifier}-T{index:03d}"
            part_coordinate = f"{entity}:{part_identifier}"
            part_entry_metadata: Dict[str, Any] = {
                "role": "attachment",
                "content": chunk_text,
                "kind": kind,
                "attachment": True,
                "attachment_part": True,
                "attachment_group": base_identifier,
                "part_index": index,
                "part_count": part_count,
                "summary": part_summary,
                "topics": chunk_topics,
                "claims": chunk_norm.get("quotes", []),
                "tags": chunk_tags,
                "gravity_cost": chunk_cost,
                "safety_score": safety_score,
                "persistence_mode": "minimal" if minimal_stub else "full",
                "full_text_pointer": chunk_prime,
            }
            for provenance_key in provenance_keys:
                if provenance_key in attachment_metadata:
                    part_entry_metadata[provenance_key] = attachment_metadata.get(provenance_key)
            if attachment_metadata:
                part_entry_metadata["metadata"] = dict(attachment_metadata)
            part_entry = LedgerEntry(
                LedgerKey(namespace=entity, identifier=part_identifier),
                ContinuousState({}, "attachment", part_entry_metadata),
            )
            try:
                store.write(part_entry)
                part_coordinate = part_entry.key.as_path()
            except Exception as exc:  # pragma: no cover - defensive logging
                LOGGER.exception("Failed to write attachment part", exc_info=exc)
            if isinstance(part_coordinate, str) and part_coordinate.strip():
                part_coordinates.append(part_coordinate.strip())
            attachment_parts.append(
                {
                    "index": index,
                    "start": start,
                    "end": end,
                    "part_suffix": f"T{index:03d}",
                    "topics": chunk_topics,
                    "tags": chunk_tags,
                }
            )

        entry_metadata: Dict[str, Any] = {
            "role": "attachment",
            "content": preview,
            "kind": kind,
            "attachment": True,
            "attachment_summary": True,
            "attachment_group": base_identifier,
            "part_count": part_count,
            "topics": norm.get("topics", []),
            "claims": norm.get("quotes", []),
            "gravity_cost": summary_cost,
            "safety_score": safety_score,
            "persistence_mode": "minimal" if minimal_stub else "full",
            "full_text_pointer": prime,
            "attachment_parts": attachment_parts,
        }
        for provenance_key in provenance_keys:
            if provenance_key in attachment_metadata:
                entry_metadata[provenance_key] = attachment_metadata.get(provenance_key)
        if attachment_metadata:
            entry_metadata["metadata"] = dict(attachment_metadata)

        summary_entry = LedgerEntry(
            LedgerKey(namespace=entity, identifier=base_identifier),
            ContinuousState({}, "attachment", entry_metadata),
        )

        try:
            store.write(summary_entry)
            entry_coordinate = summary_entry.key.as_path()
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to write attachment to store", exc_info=exc)
            fallback_coordinate = f"{entity}:{base_identifier}"

    return {
        "s2": s2_state,
        "flow_diagnostics": flow_diagnostics,
        "coordinate": entry_coordinate or fallback_coordinate or (str(prime) if prime is not None else None),
        "part_coordinates": part_coordinates,
        "part_count": part_count,
        "part_range": part_range,
    }


def _write_kernel_projections(
    store: LedgerStoreV2,
    base_coord: str,
    raw_text: str,
) -> dict[str, Any] | None:
    """Write HENGE-008 semantic projections for ``raw_text`` as children of ``base_coord``.

    Each chunk becomes a layer-store entry under ``{base_coord}-proj-{idx:03d}``
    and the full text gets a composite entry under ``{base_coord}-proj-composite``.
    The parent ledger entry is updated with projection metadata.

    Returns a summary dict with ``projection_coords``, ``composite_coord``,
    ``quaternary_layer``, and ``checksum_336_satisfied``.
    """
    if store is None or not raw_text:
        return None

    result = project_blob(raw_text, base_coord)

    # The layer store keeps quaternary-state counters in the base foundation.
    foundation_service = BaseFoundationService(store._db)
    if foundation_service.read_foundation("default") is None:
        foundation_service.write_foundation("default")

    layer_store = RocksDBLayerStore(store._db, provision_id="default")
    projection_coords: list[str] = []

    for chunk_result in result.chunks:
        child_coord = chunk_result.coord
        if child_coord is None:
            continue
        layer_store.write(
            {
                "coord": child_coord,
                "v_awareness": chunk_result.exponents.get(5, 0),
                "v_unity": chunk_result.exponents.get(7, 0),
                "v_ethics": chunk_result.exponents.get(2, 0),
                "merkle_path": hashlib.sha256(chunk_result.chunk.text.encode("utf-8")).hexdigest()[:16],
                "zk_proof_stub": "",
            }
        )
        projection_coords.append(child_coord)

    composite_coord = result.composite_coord
    if composite_coord:
        layer_store.write(
            {
                "coord": composite_coord,
                "v_awareness": result.composite_exponents.get(5, 0),
                "v_unity": result.composite_exponents.get(7, 0),
                "v_ethics": result.composite_exponents.get(2, 0),
                "merkle_path": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16],
                "zk_proof_stub": "",
            }
        )
        projection_coords.append(composite_coord)

    return {
        "projection_coords": projection_coords,
        "composite_coord": composite_coord,
        "quaternary_layer": result.composite_layer,
        "checksum_336_satisfied": result.checksum_336_satisfied,
        "chunks": [
            {
                "coord": chunk_result.coord,
                "exponents": dict(chunk_result.exponents),
                "layer": chunk_result.layer,
                "atom_coords": [atom.coord for atom in chunk_result.atoms],
            }
            for chunk_result in result.chunks
            if chunk_result.coord is not None
        ],
    }


def record_full_payload_blob(
    entity: str,
    raw_text: str,
    kind: str,
    metadata: Mapping[str, Any] | None,
    substrate,
    ledger,
    store: LedgerStoreV2 | None = None,
) -> Dict[str, Any] | None:
    """Persist the intact ``raw_text`` as a content-addressed blob plus a ledger entry.

    The blob itself lives outside the kernel projection layer so the full
    payload can be resolved in a single call. The returned coordinate points at
    a lightweight ledger entry whose metadata references the blob hash.
    """
    if store is None or not raw_text:
        return None

    blob_result = store.write_blob(
        entity,
        raw_text,
        metadata={"kind": kind, **(dict(metadata) if metadata else {})},
    )
    coordinate = blob_result["coordinate"]
    blob_hash = blob_result["blob_hash"]
    identifier = f"blob-{blob_hash}"

    entry_metadata: Dict[str, Any] = {
        "role": (metadata.get("role") if isinstance(metadata, Mapping) else None) or "blob",
        "kind": kind,
        "blob_hash": blob_hash,
        "full_payload_coord": coordinate,
        "full_payload": True,
        "content_length": len(raw_text),
        "byte_length": blob_result.get("byte_length"),
        "deduplicated": blob_result.get("deduplicated", False),
    }
    if isinstance(metadata, Mapping):
        for key in ("session_id", "turn_id", "context_id", "provider", "model_id"):
            if key in metadata:
                entry_metadata[key] = metadata[key]

    entry = LedgerEntry(
        LedgerKey(namespace=entity, identifier=identifier),
        ContinuousState({}, "blob", entry_metadata),
    )
    store.write(entry)

    projection_summary: dict[str, Any] | None = None
    if not blob_result.get("deduplicated", False):
        projection_summary = _write_kernel_projections(store, coordinate, raw_text)
        if projection_summary:
            entry_metadata["kernel_projections"] = projection_summary.get("projection_coords")
            entry_metadata["quaternary_layer"] = projection_summary.get("quaternary_layer")
            entry_metadata["checksum_336_satisfied"] = projection_summary.get("checksum_336_satisfied")
            entry_metadata["composite_coord"] = projection_summary.get("composite_coord")
            entry.state.metadata = entry_metadata
            store.write(entry)

    return {
        "coordinate": coordinate,
        "blob_hash": blob_hash,
        "identifier": identifier,
        "entry": entry,
        "byte_length": blob_result.get("byte_length"),
        "deduplicated": blob_result.get("deduplicated", False),
        "projections": projection_summary.get("projection_coords") if projection_summary else None,
        "quaternary_layer": projection_summary.get("quaternary_layer") if projection_summary else None,
        "checksum_336_satisfied": projection_summary.get("checksum_336_satisfied") if projection_summary else None,
        "composite_coord": projection_summary.get("composite_coord") if projection_summary else None,
    }


def record_turn(
    entity: str,
    session_id: str,
    turn_id: str | None,
    user_message: str,
    assistant_reply: str,
    user_message_coord: str,
    assistant_reply_coord: str,
    metadata: Mapping[str, Any] | None,
    store: LedgerStoreV2 | None,
) -> Dict[str, Any] | None:
    """Persist a turn ledger entry that links the user and assistant blobs."""
    if store is None:
        return None

    safe_turn_id = str(turn_id or f"{int(time.time() * 1000)}")
    identifier = f"turn-{session_id}-{safe_turn_id}"
    coordinate = f"{entity}:{identifier}"

    entry_metadata: Dict[str, Any] = {
        "role": "turn",
        "kind": "chat",
        "session_id": session_id,
        "turn_id": safe_turn_id,
        "user_message": user_message,
        "assistant_reply": assistant_reply,
        "user_message_coord": user_message_coord,
        "assistant_reply_coord": assistant_reply_coord,
        "full_payload": True,
    }
    if isinstance(metadata, Mapping):
        for key in ("provider", "context_id", "web4_key"):
            if key in metadata:
                entry_metadata[key] = metadata[key]

    entry = LedgerEntry(
        LedgerKey(namespace=entity, identifier=identifier),
        ContinuousState({}, "chat", entry_metadata),
    )
    store.write(entry)

    combined_text = f"{user_message or ''}\n{assistant_reply or ''}".strip()
    if combined_text:
        projection_summary = _write_kernel_projections(store, coordinate, combined_text)
        if projection_summary:
            entry_metadata["kernel_projections"] = projection_summary.get("projection_coords")
            entry_metadata["quaternary_layer"] = projection_summary.get("quaternary_layer")
            entry_metadata["checksum_336_satisfied"] = projection_summary.get("checksum_336_satisfied")
            entry_metadata["composite_coord"] = projection_summary.get("composite_coord")
            entry.state.metadata = entry_metadata
            store.write(entry)

    return {
        "coordinate": coordinate,
        "identifier": identifier,
        "entry": entry,
        "metadata": entry_metadata,
    }


def record_attachment(
    entity: str,
    raw_text: str,
    kind: str,
    metadata: Mapping[str, Any] | None,
    summary_override: str | None,
    chunk_chars: int | None,
    substrate,
    ledger,
    store: LedgerStoreV2 | None = None,
) -> Dict[str, Any]:
    """Persist a new attachment and update S1/S2 with flow diagnostics."""

    fast_result = record_attachment_fast(
        entity,
        raw_text,
        kind,
        metadata,
        summary_override,
        chunk_chars,
        substrate,
        ledger,
        store,
    )
    finalize_result = record_attachment_finalize(
        **fast_result["attachment_job"],
        ledger=ledger,
        store=store,
    )
    return {
        "prime": fast_result["prime"],
        "body": fast_result["body"],
        "s1": fast_result["s1"],
        "s2": finalize_result["s2"],
        "flow_diagnostics": finalize_result["flow_diagnostics"],
        "coordinate": finalize_result["coordinate"],
        "part_coordinates": finalize_result["part_coordinates"],
        "part_count": finalize_result.get("part_count"),
        "part_range": finalize_result.get("part_range"),
    }


def _build_e6_header_metadata(
    *,
    latest_meta: Mapping[str, Any],
    metrics_pack: Any,
    governance_error: dict[str, Any] | None,
    quality_tier: str,
    eq6_lawfulness_level: int | None,
    flow_last_even: int,
    route_override: int | None = None,
) -> Dict[str, Any]:
    """Build compact 128-bit E6 header metadata for edge transport."""
    use_policy_table = os.getenv("E6_PACKET_POLICY_TABLE", "0").strip().lower() in {"1", "true", "yes", "on"}
    strict_mode_packet = (
        os.getenv("E6_MODE_PACKET_STRICT", "0").strip().lower() in {"1", "true", "yes", "on"}
        or os.getenv("E6_MODE_GATING_STRICT", "0").strip().lower() in {"1", "true", "yes", "on"}
    )

    def _mode_from_tier(tier: str) -> int:
        if tier == "halt":
            return 0
        if tier == "probe":
            return 1
        if tier == "stabilise":
            return 2
        if tier == "express":
            return 3
        return 2

    def _ptype_from_tier(tier: str) -> int:
        if tier == "express":
            return 0  # WU
        if tier == "halt":
            return 1  # HR
        if tier == "probe":
            return 2  # PP
        return 3      # CA

    def _route_from_tier(tier: str, blocked: bool) -> int:
        if blocked:
            return 0  # block
        if tier == "probe":
            return 1  # quarantine
        if tier == "stabilise":
            return 2  # local-commit
        if tier == "express":
            return 3  # ledger-commit
        return 2

    def _mode_ptype_from_route(route_value: int) -> tuple[int, int]:
        if route_value <= 0:
            return 0, 1  # HALT + HR
        if route_value == 1:
            return 1, 2  # PROBE + PP
        if route_value == 2:
            return 2, 3  # STABILISE + CA
        return 3, 0      # EXPRESS + WU

    policy_table: dict[tuple[str, bool], tuple[int, int, int]] = {
        ("halt", True): (0, 1, 0),       # HR, blocked
        ("probe", True): (1, 1, 0),      # HR, blocked
        ("stabilise", True): (2, 1, 0),  # HR, blocked
        ("express", True): (3, 1, 0),    # HR, blocked
        ("halt", False): (0, 1, 1),      # HR, quarantine
        ("probe", False): (1, 2, 1),     # PP, quarantine
        ("stabilise", False): (2, 3, 2), # CA, local-commit
        ("express", False): (3, 0, 3),   # WU, ledger-commit
    }

    metrics = metrics_pack.metrics if metrics_pack is not None else {}
    K = _clamp_int(int(metrics.get("K", 0) if isinstance(metrics, Mapping) else 0), 0, 1)
    P = _clamp_int(int(metrics.get("P", 0) if isinstance(metrics, Mapping) else 0), 0, 1)
    E = _clamp_int(int(metrics.get("E", 0) if isinstance(metrics, Mapping) else 0), 0, 1)
    dW = _clamp_int(int(metrics.get("dW", 0) if isinstance(metrics, Mapping) else 0), -128, 127)
    V = float(metrics.get("V", 0.0) if isinstance(metrics, Mapping) else 0.0)
    V_q = _clamp_int(int(round(max(0.0, min(V, 1.0)) * 65535.0)), 0, 65535)

    law = _clamp_int(int(eq6_lawfulness_level or 0), 0, 3)
    node = _clamp_int(int(flow_last_even) % 8, 0, 15)
    blocked = governance_error is not None
    halt_forced = blocked or quality_tier == "halt"
    if use_policy_table:
        mode, ptype, route = policy_table.get((quality_tier, blocked), (2, 3, 2))
    else:
        mode = _mode_from_tier(quality_tier)
        ptype = _ptype_from_tier(quality_tier)
        route = _route_from_tier(quality_tier, blocked)
    if isinstance(route_override, int) and not halt_forced:
        route = _clamp_int(route_override, 0, 3)
    if strict_mode_packet:
        mode, ptype = _mode_ptype_from_route(route)
    if halt_forced:
        mode, ptype, route = 0, 1, 0  # HALT + HR + block
    valid = 0 if governance_error else 1

    prev_seq_raw = latest_meta.get("e6_seq")
    prev_seq = int(prev_seq_raw) if isinstance(prev_seq_raw, (int, float, str)) and str(prev_seq_raw).isdigit() else -1
    seq = (prev_seq + 1) & 0xFFFFFF if prev_seq >= 0 else 0
    t_ms = int(time.time() * 1000.0) & 0xFFFFFF

    header = pack_header_v0(
        mode=mode,
        ptype=ptype,
        law=law,
        route=route,
        node=node,
        K=K,
        P=P,
        E=E,
        valid=valid,
        dW=dW,
        seq=seq,
        t_ms=t_ms,
        V_q=V_q,
    )

    return {
        "e6_seq": seq,
        "e6_policy_mode": "table" if use_policy_table else "heuristic",
        "e6_header_v0_hex": header.hex(),
        "e6_header_v0_fields": {
            "mode": mode,
            "ptype": ptype,
            "law": law,
            "route": route,
            "node": node,
            "K": K,
            "P": P,
            "E": E,
            "valid": valid,
            "dW": dW,
            "seq": seq,
            "t_ms": t_ms,
            "V_q": V_q,
        },
    }


def _promotion_route_for_tier(quality_tier: str) -> int:
    if quality_tier == "express":
        return 3  # ledger-commit
    if quality_tier == "stabilise":
        return 2  # local-commit
    return 1      # quarantine


def _promotion_decision(
    *,
    quality_tier: str,
    governance_error: Mapping[str, Any] | None,
    bridge_allowed: bool,
    resolution_contradiction: bool,
) -> dict[str, Any]:
    # Always-write invariant: this function never blocks writes; it only controls promotion route.
    if quality_tier == "halt":
        return {
            "allowed": False,
            "route": 0,
            "reason": str((governance_error or {}).get("reason") or "halt_mode"),
        }
    if governance_error is not None:
        return {
            "allowed": False,
            "route": 0,
            "reason": str(governance_error.get("reason") or "governance_error"),
        }
    if resolution_contradiction:
        return {
            "allowed": False,
            "route": 1,
            "reason": "resolution_contradiction",
        }
    if not bridge_allowed:
        return {
            "allowed": False,
            "route": 1,
            "reason": "bridge_not_allowed",
        }
    return {
        "allowed": True,
        "route": _promotion_route_for_tier(quality_tier),
        "reason": "bridge_allowed",
    }


def _select_promotion_bridge_gate(
    *,
    bridge_runtime: bool,
    e6_scoring: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    mode = os.getenv("E6_PROMOTION_GATE_MODE", "runtime").strip().lower()
    if mode == "formula" and isinstance(e6_scoring, Mapping):
        formula = e6_scoring.get("bridge_allowed_formula_eval")
        if isinstance(formula, bool):
            return formula, "formula"
    return bool(bridge_runtime), "runtime"


def record_message(
    entity: str,
    role: str,
    content: str,
    kind: str,
    metadata: Mapping[str, Any] | None,
    substrate,
    ledger,
    store: LedgerStoreV2 | None = None,
    *,
    retrieval_payload: Mapping[str, Any] | list[Any] | None = None,
    draft_text: str | None = None,
    persist_content: bool = True,
    hysteresis_coherence: float | None = None,
    eq6_lawfulness_level: int | None = None,
    hop_lawfulness: list[int] | None = None,
    eq6_mediator_prime: int | None = None,
) -> Dict[str, Any]:
    """Persist a chat message and enrich S2 with advisory flow diagnostics."""

    metadata_dict = dict(metadata) if metadata else {}
    taxonomy_provenance = build_taxonomy_provenance(metadata_dict)
    metadata_dict.setdefault("taxonomy_provenance", taxonomy_provenance)
    metadata_dict.setdefault("taxonomy_mode", taxonomy_provenance.get("taxonomy_mode"))
    metadata_dict.setdefault("taxonomy_version", taxonomy_provenance.get("taxonomy_version"))
    metadata_dict.setdefault("taxonomy_topology_ref", taxonomy_provenance.get("topology_ref"))
    if hysteresis_coherence is not None:
        metadata_dict.setdefault("hysteresis_coherence", hysteresis_coherence)

    resolution_consistency = _assess_resolution_consistency(
        role=role,
        content=content,
        metadata=metadata_dict,
    )
    if resolution_consistency is not None:
        metadata_dict["resolution_consistency"] = resolution_consistency
        if resolution_consistency.get("status") == "contradiction":
            metadata_dict["resolution_contradiction"] = True
            metadata_dict["resolution_contradiction_reason"] = str(
                resolution_consistency.get("reason") or "resolved_context_but_claimed_unresolvable"
            )

    if hop_lawfulness is None:
        hop_lawfulness_value = metadata_dict.get("hop_lawfulness")
        if isinstance(hop_lawfulness_value, list):
            hop_lawfulness = [item for item in hop_lawfulness_value if isinstance(item, int)]

    eq6_commit_allowed = metadata_dict.get("eq6_commit_allowed")
    if eq6_lawfulness_level is None:
        eq6_lawfulness_level = metadata_dict.get("eq6_lawfulness_level")
    if eq6_lawfulness_level is None and hop_lawfulness:
        eq6_lawfulness_level = hop_lawfulness[-1]
    if eq6_mediator_prime is None:
        eq6_mediator_prime = metadata_dict.get("eq6_mediator_prime")

    effective_draft_text = draft_text if persist_content else None
    if eq6_commit_allowed is None and retrieval_payload is not None and effective_draft_text:
        eq6_result = equation_6_operational(
            query_text=effective_draft_text,
            retrieval_payload=retrieval_payload,
            hysteresis_coherence=hysteresis_coherence,
            lawfulness_level=eq6_lawfulness_level,
            mediator_prime=eq6_mediator_prime,
        )
        eq6_commit_allowed = bool(eq6_result.get("commit_allowed"))
        eq6_lawfulness_level = int(eq6_result.get("lawfulness_level") or 0)
        eq6_mediator_prime = int(eq6_result.get("mediator_prime") or 0)

    if eq6_commit_allowed is False and eq6_mediator_prime != LAW_PRIME:
        metadata_dict.setdefault("eq6_commit_allowed", eq6_commit_allowed)
        metadata_dict.setdefault("eq6_lawfulness_level", eq6_lawfulness_level)
        metadata_dict.setdefault("eq6_mediator_prime", eq6_mediator_prime)
        metadata_dict.setdefault("eq6_commit_blocked", True)

    if eq6_commit_allowed is not None:
        metadata_dict.setdefault("eq6_commit_allowed", eq6_commit_allowed)
    if eq6_lawfulness_level is not None:
        metadata_dict.setdefault("eq6_lawfulness_level", eq6_lawfulness_level)
    if eq6_mediator_prime is not None:
        metadata_dict.setdefault("eq6_mediator_prime", eq6_mediator_prime)

    prime = substrate.allocate_body_prime(entity)
    flow_diag = _run_flow_advisory(
        prime,
        kind=kind,
        role=role,
        eq6_lawfulness_level=eq6_lawfulness_level,
        eq6_commit_allowed=eq6_commit_allowed,
        eq6_mediator_prime=eq6_mediator_prime,
    )

    # --- HARD GOVERNANCE GATE ---
    governance_engine = GovernanceEngine()
    strict_gate = os.getenv("GOVERNANCE_STRICT", "1").strip() in {"1", "true", "yes", "on"}
    metadata_dict.setdefault("body_prime", prime)
    metadata_dict.setdefault("flow_diag", flow_diag)
    metadata_dict.setdefault("schema_hash", metadata_dict.get("schema_hash") or "schema.py")
    metadata_dict.setdefault("schema_version", metadata_dict.get("schema_version") or "1.3-beta")

    latest_meta: dict[str, Any] = {}
    if store is not None:
        try:
            latest_entries = store.list_by_namespace(entity, limit=1)
            if latest_entries:
                latest_meta = dict(latest_entries[0].state.metadata or {})
        except Exception:
            latest_meta = {}

    prev_state = build_state_from_metadata(latest_meta, default_grid=governance_engine.N)
    curr_state = build_state_from_metadata(metadata_dict, default_grid=governance_engine.N)
    if not prev_state.mismatch_history:
        prev_state.mismatch_history = [0.0] * max(governance_engine.h_windows)

    governance_error: dict[str, Any] | None = None
    if strict_gate and curr_state.missing_invariants:
        governance_error = {
            "blocked": True,
            "reason": "missing_field_invariants",
        }

    provenance_inputs = metadata_dict.get("provenance_inputs")
    if isinstance(provenance_inputs, str):
        inputs_stub = provenance_inputs.encode()
    elif isinstance(provenance_inputs, bytes):
        inputs_stub = provenance_inputs
    else:
        inputs_stub = content.encode()

    schema_hash = str(metadata_dict.get("schema_hash") or "schema.py")
    schema_version = str(metadata_dict.get("schema_version") or "1.3-beta")
    expected_commit = governance_engine.compute_provenance_commit(inputs_stub, schema_hash, schema_version)
    provided_commit = metadata_dict.get("provenance_commit")
    curr_state.provenance_commit = str(provided_commit or expected_commit)

    prev_hash = prev_state.ledger_hash or "genesis"
    payload = str(metadata_dict.get("web4_key") or metadata_dict.get("turn_id") or hash(content))
    expected_hash = governance_engine.expected_ledger_hash(prev_hash, payload, curr_state)
    provided_hash = metadata_dict.get("ledger_hash")
    curr_state.ledger_hash = str(provided_hash or expected_hash)

    governance_engine.validate_schema_primes([prime])
    metrics_pack = None
    bridge_ok = False
    contradiction_bridge_gate: dict[str, Any] | None = None
    flow_sequence: list[int] | None = None

    try:
        token_primes = metadata_dict.get("token_primes")
        if isinstance(token_primes, list):
            primes = [
                int(p)
                for p in token_primes
                if isinstance(p, (int, float, str)) and str(p).isdigit()
            ]
            if primes:
                governance_engine.validate_schema_primes(primes)

        mediator_prime = eq6_mediator_prime or flow_diag.get("mediator_prime") or LAW_PRIME
        flow_sequence = _build_flow_sequence(
            prime=prime,
            mediator_prime=mediator_prime,
            eq6_lawfulness_level=eq6_lawfulness_level,
            eq6_commit_allowed=eq6_commit_allowed,
        )
        coherence_value = _coerce_coherence(metadata_dict)
        governance_engine.validate_flow_sequence(flow_sequence, coherence_value)

        E_pred = float(metadata_dict.get("E_pred") or 0.1)
        E_baseline = float(metadata_dict.get("E_baseline") or 0.5)
        schema_complete = bool(schema_hash)
        inputs_logged = bool(metadata_dict.get("provenance_inputs") or metadata_dict.get("inputs_logged"))
        version_pinned = bool(schema_version)
        violations_count, violations_detail = _extract_violation_info(metadata_dict)
        violations_count, violations_detail = _apply_contradiction_violation_weight(
            metadata=metadata_dict,
            violations_count=violations_count,
            violations_detail=violations_detail,
        )
        ethics_gate = governance_engine.compute_ethics_gate(metadata_dict)
        metrics_pack = governance_engine.evaluate(
            prev_state=prev_state,
            curr_state=curr_state,
            prev_hash=prev_hash,
            payload=payload,
            E_pred=E_pred,
            E_baseline=E_baseline,
            expected_commit=expected_commit,
            schema_complete=schema_complete,
            inputs_logged=inputs_logged,
            version_pinned=version_pinned,
            ethics_gate=ethics_gate,
            violations_count=violations_count,
        )

        eq0_ok = bool(metrics_pack.metrics.get("eq0_distinction"))
        eq1_ok = bool(metrics_pack.metrics.get("eq1_dual_substrate"))
        eq2_ok = bool(metrics_pack.metrics.get("eq2_time_irreversible"))
        eq3_ok = bool(metrics_pack.metrics.get("eq3_geometry_closure"))
        if strict_gate and not (eq0_ok and eq1_ok and eq2_ok and eq3_ok):
            governance_error = {
                "blocked": True,
                "reason": "genesis_ladder_blocked",
                "details": {
                    "eq0_distinction": eq0_ok,
                    "eq1_dual_substrate": eq1_ok,
                    "eq2_time_irreversible": eq2_ok,
                    "eq3_geometry_closure": eq3_ok,
                },
            }

        bridge_ok = governance_engine.bridge_allowed(metrics_pack.metrics, metrics_pack.state)
        bridge_ok, contradiction_bridge_gate = _apply_contradiction_bridge_floor(
            metadata=metadata_dict,
            metrics=metrics_pack.metrics if metrics_pack else None,
            bridge_ok=bridge_ok,
        )
    except CoherenceException as exc:
        governance_error = exc.as_dict()

    if metrics_pack:
        metrics = metrics_pack.metrics
        genesis_vector = _build_genesis_vector(metrics)
        metadata_dict["genesis_vector"] = genesis_vector
        metadata_dict["repair_hints"] = _build_genesis_repair_hints(genesis_vector)
        v_history = metrics_pack.state.V_history
        recent_v = v_history[-3:] if len(v_history) >= 3 else v_history
        mean_v = None
        std_v = None
        if recent_v:
            mean_v = sum(recent_v) / float(len(recent_v))
            std_v = math.sqrt(
                sum((value - mean_v) ** 2 for value in recent_v) / float(len(recent_v))
            )
        eq6_block = {
            "A_corr": metrics.get("A_corr"),
            "A_self": metrics.get("A_self"),
            "A": metrics.get("A"),
            "theta_self": metrics.get("theta_self"),
            "violations_count": metrics.get("violations_count"),
            "violations": violations_detail,
            "commit_allowed": metadata_dict.get("eq6_commit_allowed"),
            "lawfulness_level": metadata_dict.get("eq6_lawfulness_level"),
            "mediator_prime": metadata_dict.get("eq6_mediator_prime"),
        }
        eq6_law = eq6_block.get("lawfulness_level")
        eq6_cw = _cw_from_lawfulness(eq6_law if isinstance(eq6_law, (int, float)) else None)
        eq6_block["cw"] = eq6_cw
        metadata_dict.setdefault("eq6_cw", eq6_cw)
        metadata_dict["governance"] = {
            "eq6": eq6_block,
            "L": {
                "L": metrics.get("L"),
                "L_phys": metrics.get("L_phys"),
                "L_top": metrics.get("L_top"),
                "L_ledger": metrics.get("L_ledger"),
                "K": metrics.get("K"),
                "dW": metrics.get("dW"),
                "dI1": metrics.get("dI1"),
                "dI2": metrics.get("dI2"),
            },
            "H": {
                "H": metrics.get("H"),
                "var": metrics.get("H_var"),
                "slope": metrics.get("H_slope"),
            },
            "P": {
                "P": metrics.get("P"),
                "schema_complete": metrics.get("schema_complete"),
                "inputs_logged": metrics.get("inputs_logged"),
                "version_pinned": metrics.get("version_pinned"),
                "replayable": metrics.get("replayable"),
            },
            "E": {
                "E": metrics.get("E"),
                "ethics_gate": metrics.get("ethics_gate"),
                "non_bypass": _non_bypass_gate(metadata_dict),
            },
            "U": {"U": metrics.get("U")},
            "V": {
                "V": metrics.get("V"),
                "mean_V": mean_v,
                "std_V": std_v,
                "theta_V": metrics.get("theta_V"),
                "V_std_max": metrics.get("V_std_max"),
            },
            "bridge_allowed": bridge_ok,
        }
        metadata_dict["governance_state"] = {
            "ledger_hash": curr_state.ledger_hash,
            "provenance_commit": curr_state.provenance_commit,
            "mismatch_history": curr_state.mismatch_history[-max(governance_engine.h_windows):],
            "V_history": curr_state.V_history[-5:],
        }
        metadata_dict["governance_metrics"] = metrics
        if contradiction_bridge_gate is not None:
            metadata_dict["governance_contradiction_gate"] = contradiction_bridge_gate
        metadata_dict["e6_scoring"] = _build_e6_scoring_snapshot(
            metrics=metrics,
            bridge_allowed_runtime=bridge_ok,
            v_mean_3=mean_v,
            v_std_3=std_v,
            thresholds=_e6_threshold_snapshot(governance_engine),
        )
        non_null = abs(metrics.get("I1", 0.0)) > 1e-9 or abs(
            metrics.get("I2", 0.0)
        ) > 1e-9
        metadata_dict["non_null"] = bool(non_null)
    if governance_error:
        metadata_dict["governance_error"] = governance_error
        if not isinstance(metadata_dict.get("genesis_vector"), Mapping):
            details = governance_error.get("details") if isinstance(governance_error, Mapping) else None
            if isinstance(details, Mapping):
                fallback_vector = {
                    "eq0_distinction": bool(details.get("eq0_distinction")),
                    "eq1_dual_substrate": bool(details.get("eq1_dual_substrate")),
                    "eq2_time_irreversible": bool(details.get("eq2_time_irreversible")),
                    "eq3_geometry_closure": bool(details.get("eq3_geometry_closure")),
                }
                fallback_vector["all_ok"] = bool(
                    fallback_vector["eq0_distinction"]
                    and fallback_vector["eq1_dual_substrate"]
                    and fallback_vector["eq2_time_irreversible"]
                    and fallback_vector["eq3_geometry_closure"]
                )
                metadata_dict["genesis_vector"] = fallback_vector
                metadata_dict["repair_hints"] = _build_genesis_repair_hints(fallback_vector)

    # Penalize appraisal metrics when governance blocks the turn.
    _apply_governance_block_penalty(metadata_dict)

    # Penalize appraisal metrics when resolved context contradicts the assistant claim.
    _apply_resolution_contradiction_penalty(metadata_dict)
    _apply_unity_conservation_indicators(metadata=metadata_dict, latest_meta=latest_meta)
    _apply_loop_integrity_indicators(metadata_dict, role=role, content=content)

    promotion_bridge_allowed, promotion_gate_source = _select_promotion_bridge_gate(
        bridge_runtime=bridge_ok,
        e6_scoring=metadata_dict.get("e6_scoring") if isinstance(metadata_dict.get("e6_scoring"), Mapping) else None,
    )

    # --- Classification (HALT/PROBE/STABILISE/EXPRESS) ---
    quality_tier = "stabilise"
    persistence_mode = "standard"
    gravity_penalty = 0.3
    if governance_error:
        quality_tier = "halt"
        persistence_mode = "ephemeral"
        gravity_penalty = 10.0
    elif metrics_pack:
        L = float(metrics_pack.metrics.get("L", 0.0))
        H = float(metrics_pack.metrics.get("H", 0.0))
        P = int(metrics_pack.metrics.get("P", 0))
        theta_L = float(governance_engine.thresholds.get("theta_L", 0.85))
        theta_H = float(governance_engine.thresholds.get("theta_H", 0.70))
        if bridge_ok:
            quality_tier = "express"
            persistence_mode = "tower"
            gravity_penalty = 0.0
        elif L >= theta_L and H >= theta_H and P == 1:
            quality_tier = "stabilise"
            persistence_mode = "standard"
            gravity_penalty = 0.3
        else:
            quality_tier = "probe"
            persistence_mode = "ephemeral"
            gravity_penalty = 1.0

    promotion = _promotion_decision(
        quality_tier=quality_tier,
        governance_error=governance_error,
        bridge_allowed=promotion_bridge_allowed,
        resolution_contradiction=bool(metadata_dict.get("resolution_contradiction")),
    )

    flow_last_even = 0
    flow_substrate = 0
    if isinstance(flow_sequence, list) and len(flow_sequence) >= 4:
        flow_last_even = flow_sequence[3]
        start_even = flow_sequence[0]
        flow_substrate = 1 if start_even in (4, 6, 11, 13, 17, 19) else 0
    metadata_dict.setdefault("flow_last_even", flow_last_even)
    metadata_dict.setdefault("flow_substrate", flow_substrate)
    metadata_dict["quality_tier"] = quality_tier
    metadata_dict["persistence_mode"] = persistence_mode
    metadata_dict["gravity_penalty"] = gravity_penalty
    _apply_alpha_balance_indicators(metadata_dict, latest_meta=latest_meta)
    _apply_eq89_trend_indicators(metadata_dict, latest_meta=latest_meta)
    _apply_coherence_tax_indicators(
        metadata_dict,
        quality_tier=quality_tier,
        gravity_penalty=float(gravity_penalty),
    )
    metadata_dict["promotion"] = promotion
    metadata_dict["promotion_gate_source"] = promotion_gate_source
    metadata_dict["always_write_raw"] = True
    metadata_dict["e6_rollout_flags"] = _e6_rollout_flags()
    review_reasons: list[str] = []
    if quality_tier == "halt":
        review_reasons.append("halt_mode")
    if bool(metadata_dict.get("resolution_contradiction")):
        review_reasons.append("resolution_contradiction")
    if review_reasons:
        metadata_dict["review_trigger"] = {
            "triggered": True,
            "mode": "triggered_only",
            "reasons": review_reasons,
        }
    metadata_dict.update(
        _build_e6_header_metadata(
            latest_meta=latest_meta,
            metrics_pack=metrics_pack,
            governance_error=governance_error,
            quality_tier=quality_tier,
            eq6_lawfulness_level=eq6_lawfulness_level,
            flow_last_even=flow_last_even,
            route_override=int(promotion.get("route")) if isinstance(promotion.get("route"), (int, float)) else None,
        )
    )
    e6_header_fields = metadata_dict.get("e6_header_v0_fields")
    e6_scoring = metadata_dict.get("e6_scoring")
    promotion_meta = metadata_dict.get("promotion")
    metadata_dict["e6_diagnostics"] = {
        "mode": e6_header_fields.get("mode") if isinstance(e6_header_fields, Mapping) else None,
        "route": e6_header_fields.get("route") if isinstance(e6_header_fields, Mapping) else None,
        "quality_tier": metadata_dict.get("quality_tier"),
        "bridge_allowed_runtime": (
            e6_scoring.get("bridge_allowed_runtime") if isinstance(e6_scoring, Mapping) else None
        ),
        "promotion_allowed": (
            promotion_meta.get("allowed") if isinstance(promotion_meta, Mapping) else None
        ),
        "promotion_reason": (
            promotion_meta.get("reason") if isinstance(promotion_meta, Mapping) else None
        ),
    }
    safety_score, minimal_stub = _derive_safety(flow_diag)
    stored_content = _MINIMAL_STUB_TEXT if minimal_stub else (content if persist_content else "")
    norm = normalise_text(stored_content)
    norm.update({"role": role, "kind": kind})
    if metadata_dict:
        norm["metadata"] = metadata_dict
    if minimal_stub or not persist_content:
        norm["topics"] = []
        norm["tags"] = []
        norm["quotes"] = []
    body = substrate.write_body_prime(entity, prime, stored_content, norm)

    s1_state = _persist_s1(entity, prime=prime, ledger=ledger, kind=kind, role=role)

    s2_updates = _build_s2_updates(
        prime=prime, norm=norm, kind=kind, role=role, flow_diag=flow_diag
    )
    s2_state = ledger.update_S2(entity, s2_updates)

    entry_coordinate: str | None = None
    fallback_coordinate: str | None = None
    identifier = _canonical_web4_key(entity, metadata_dict.get("web4_key"))
    fallback_coordinate = f"{entity}:{identifier}"
    if store is not None and persistence_mode != "audit":
        # Mirror effective metadata into the ledger entry so search surfaces
        # summaries immediately without needing to resolve the full body.
        message_coherence = _coerce_coherence(metadata_dict)
        message_cost = calculate_persistence_cost(
            _ALPHA_VAL,
            message_coherence,
            len(stored_content),
            non_null=metadata_dict.get("non_null") if isinstance(metadata_dict.get("non_null"), bool) else None,
        )
        message_cost *= 1.0 + float(gravity_penalty or 0.0)
        entry_metadata: Dict[str, Any] = {
            "role": role,
            "content": stored_content,
            "kind": kind,
            "topics": norm.get("topics", []),
            "claims": norm.get("quotes", []),
            "coherence": flow_diag.get("mediator_prime"),
            "gravity_cost": message_cost,
            "safety_score": safety_score,
            "storage_mode": "minimal"
            if minimal_stub
            else ("metadata_only" if not persist_content else "full"),
            "transcript_persisted": bool(persist_content),
        }
        if role == "user" and persist_content:
            entry_metadata["user_message"] = stored_content
        if metadata_dict:
            owned_keys = {
                "role",
                "content",
                "kind",
                "assistant_reply",
                "user_message",
            }
            safe_meta = {
                key: value for key, value in metadata_dict.items() if key not in owned_keys
            }
            entry_metadata.update(safe_meta)

        entry = LedgerEntry(
            LedgerKey(namespace=entity, identifier=identifier),
            ContinuousState({}, "chat", entry_metadata),
        )

        try:
            store.write(entry)
            entry_coordinate = entry.key.as_path()
            indexed_meta = entry.state.metadata if isinstance(entry.state.metadata, Mapping) else {}
            token_primes = indexed_meta.get("token_primes")
            if isinstance(token_primes, list):
                normalized_primes = [
                    parse_bigint(p)
                    for p in token_primes
                    if isinstance(p, (int, float, str)) and str(p).lstrip("-").isdigit()
                ]
                if normalized_primes:
                    metadata_dict["token_primes"] = normalized_primes
                    metadata_dict.setdefault("token_prime_product", bigint_str(math.prod(normalized_primes)))
            token_prime_product = indexed_meta.get("token_prime_product")
            try:
                token_prime_product_int = parse_bigint(token_prime_product)
            except (TypeError, ValueError):
                token_prime_product_int = None
            if isinstance(token_prime_product_int, int):
                metadata_dict["token_prime_product"] = bigint_str(token_prime_product_int)
                metadata_dict.setdefault("prime_multiplicative_value", bigint_str(token_prime_product_int))
            elif "prime_multiplicative_value" not in metadata_dict:
                token_primes_value = metadata_dict.get("token_primes")
                if isinstance(token_primes_value, list):
                    normalized_primes = [
                        parse_bigint(p)
                        for p in token_primes_value
                        if isinstance(p, (int, float, str)) and str(p).lstrip("-").isdigit()
                    ]
                    if normalized_primes:
                        metadata_dict["prime_multiplicative_value"] = bigint_str(math.prod(normalized_primes))

            # Propagate the persisted core informational unit and p-adic write
            # cost back into the returned metadata so chat diagnostics can
            # observe them without re-reading the ledger.
            if indexed_meta.get(CIU_FACTORS):
                metadata_dict[CIU_FACTORS] = indexed_meta[CIU_FACTORS]
            if indexed_meta.get(CIU_KERNEL_EXPONENTS) is not None:
                metadata_dict[CIU_KERNEL_EXPONENTS] = indexed_meta[CIU_KERNEL_EXPONENTS]
            if indexed_meta.get(CIU_MMF_PROJECTIONS) is not None:
                metadata_dict[CIU_MMF_PROJECTIONS] = indexed_meta[CIU_MMF_PROJECTIONS]
            if indexed_meta.get(CIU_ENTRY_CLASS) is not None:
                metadata_dict[CIU_ENTRY_CLASS] = indexed_meta[CIU_ENTRY_CLASS]
            if indexed_meta.get(CIU_FLOW_RULE_TAGS) is not None:
                metadata_dict[CIU_FLOW_RULE_TAGS] = indexed_meta[CIU_FLOW_RULE_TAGS]
            if indexed_meta.get(CIU_RELATIONSHIP_LINKS) is not None:
                metadata_dict[CIU_RELATIONSHIP_LINKS] = indexed_meta[CIU_RELATIONSHIP_LINKS]
            if "p_adic_write_cost" in indexed_meta:
                metadata_dict["p_adic_write_cost"] = indexed_meta["p_adic_write_cost"]
            if "prime_lattice_exponents" in indexed_meta:
                metadata_dict["prime_lattice_exponents"] = indexed_meta["prime_lattice_exponents"]
            if "p_adic_coordinate" in indexed_meta:
                metadata_dict["p_adic_coordinate"] = indexed_meta["p_adic_coordinate"]
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to write ledger entry to store", exc_info=exc)

    coordinate = entry_coordinate or fallback_coordinate or str(prime)

    return {
        "prime": prime,
        "body": body,
        "s1": s1_state,
        "s2": s2_state,
        "flow_enrich": flow_diag,
        "coordinate": coordinate,
        "metadata": metadata_dict,
    }


__all__ = [
    "record_attachment",
    "record_attachment_fast",
    "record_attachment_finalize",
    "record_message",
]
