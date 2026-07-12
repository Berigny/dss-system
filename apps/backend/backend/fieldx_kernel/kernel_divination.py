from __future__ import annotations

from typing import Any, Mapping


def compute_configurational_foresight(
    *,
    teleology_alignment: float,
    law_score: float,
    grace_score: float,
    drift: float,
    walk_assessment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    assessment = walk_assessment if isinstance(walk_assessment, Mapping) else {}
    topology_health = _clamp01(assessment.get("topology_health"), default=0.5)
    stability = _clamp01(assessment.get("stability"), default=0.5)
    diversity = _clamp01(assessment.get("diversity"), default=0.5)
    teleology = _clamp01(teleology_alignment, default=0.5)
    law = _clamp01(law_score, default=0.5)
    grace = _clamp01(grace_score, default=0.5)
    drift_value = _clamp01(drift, default=0.0)

    law_grace_tension = round(abs(law - grace), 4)
    alpha_uncertainty = round(max(0.0, min(1.0, (law_grace_tension * 0.5) + (drift_value * 0.5))), 4)
    advisory_score = round(
        max(
            0.0,
            min(
                1.0,
                (teleology * 0.45)
                + (((law + grace) / 2.0) * 0.2)
                + (topology_health * 0.15)
                + (stability * 0.1)
                + (diversity * 0.1),
            ),
        ),
        4,
    )

    if advisory_score >= 0.85:
        quality = "favourable"
    elif advisory_score <= 0.35:
        quality = "cautionary"
    else:
        quality = "mixed"

    dominant = "law" if law > grace else "grace" if grace > law else "balanced"

    return {
        "quality": quality,
        "advisory_score": advisory_score,
        "dominant_tension": dominant,
        "alpha_uncertainty": alpha_uncertainty,
        "law_grace_tension": law_grace_tension,
        "inputs": {
            "teleology_alignment": teleology,
            "law_score": law,
            "grace_score": grace,
            "drift": drift_value,
            "topology_health": topology_health,
            "stability": stability,
            "diversity": diversity,
        },
        "advisory_only": True,
        "veto_allowed": False,
    }


def _clamp01(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number
