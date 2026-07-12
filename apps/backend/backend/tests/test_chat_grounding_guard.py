from __future__ import annotations

from backend.api.chat import _apply_metrics_grounding_guard


def test_metrics_guard_blocks_ungrounded_numeric_deltas() -> None:
    memories = {
        "decoded_context": [
            "Score=1.0 Law=1.0 Drift=0.0 Output_tokens=199",
        ]
    }
    metadata = {"eq9_eval": {"output_tokens": 199}}
    reply = "Intent Fidelity +10%. Threshold moved to 0.92. 34% chances to exceed 220."
    text, applied = _apply_metrics_grounding_guard(
        user_message="I've consolidated metrics - what changes come through?",
        response_text=reply,
        memories=memories,
        metadata_payload=metadata,
    )
    assert applied is True
    assert "cannot verify percentage deltas" in text
    assert "Do you want me to stay with observed values only" in text


def test_metrics_guard_allows_grounded_numeric_response() -> None:
    memories = {
        "decoded_context": [
            "Score=1.0 Law=1.0 Drift=0.0 Output_tokens=219",
        ]
    }
    metadata = {"eq9_eval": {"output_tokens": 219}}
    reply = "Score=1.0, Law=1.0, Drift=0.0, output_tokens=219."
    text, applied = _apply_metrics_grounding_guard(
        user_message="what metrics are present?",
        response_text=reply,
        memories=memories,
        metadata_payload=metadata,
    )
    assert applied is False
    assert text == reply


def test_metrics_guard_ignores_non_metrics_query() -> None:
    memories = {"decoded_context": ["Score=1.0"]}
    metadata = {"eq9_eval": {"output_tokens": 219}}
    reply = "We improved by +20% and threshold moved to 0.8."
    text, applied = _apply_metrics_grounding_guard(
        user_message="write me a story",
        response_text=reply,
        memories=memories,
        metadata_payload=metadata,
    )
    assert applied is False
    assert text == reply


def test_metrics_guard_fallback_uses_dynamic_output_target() -> None:
    memories = {"decoded_context": ["Score=1.0 Law=1.0 Drift=0.0 Output_tokens=219"]}
    metadata = {
        "eq9_eval": {
            "checks": {
                "score": {"current": 1.0},
                "law": {"current": 1.0},
                "drift": {"current": 0.0},
            },
            "output_tokens": 219,
        },
        "eq9_target": {"output_tokens_soft": 333},
    }
    reply = "Intent Fidelity +10%. Threshold moved to 0.92. 34% chances to exceed 220."
    text, applied = _apply_metrics_grounding_guard(
        user_message="I've consolidated metrics - what changes come through?",
        response_text=reply,
        memories=memories,
        metadata_payload=metadata,
    )
    assert applied is True
    assert "soft target=333" in text
    assert "Do you want me to stay with observed values only" in text


def test_metrics_guard_fallback_defaults_output_target_when_missing() -> None:
    memories = {"decoded_context": ["Score=1.0 Law=1.0 Drift=0.0 Output_tokens=219"]}
    metadata = {
        "eq9_eval": {
            "checks": {
                "score": {"current": 1.0},
                "law": {"current": 1.0},
                "drift": {"current": 0.0},
            },
            "output_tokens": 219,
        }
    }
    reply = "Intent Fidelity +10%. Threshold moved to 0.92. 34% chances to exceed 220."
    text, applied = _apply_metrics_grounding_guard(
        user_message="I've consolidated metrics - what changes come through?",
        response_text=reply,
        memories=memories,
        metadata_payload=metadata,
    )
    assert applied is True
    assert "soft target=220" in text


def test_metrics_guard_fallback_defaults_output_target_when_invalid_type() -> None:
    memories = {"decoded_context": ["Score=1.0 Law=1.0 Drift=0.0 Output_tokens=219"]}
    metadata = {
        "eq9_eval": {
            "checks": {
                "score": {"current": 1.0},
                "law": {"current": 1.0},
                "drift": {"current": 0.0},
            },
            "output_tokens": 219,
        },
        "eq9_target": {"output_tokens_soft": "not-a-number"},
    }
    reply = "Intent Fidelity +10%. Threshold moved to 0.92. 34% chances to exceed 220."
    text, applied = _apply_metrics_grounding_guard(
        user_message="I've consolidated metrics - what changes come through?",
        response_text=reply,
        memories=memories,
        metadata_payload=metadata,
    )
    assert applied is True
    assert "soft target=220" in text


def test_metrics_guard_does_not_treat_generic_metrics_language_as_numeric_query() -> None:
    memories = {"decoded_context": ["Score=1.0"]}
    metadata = {"eq9_eval": {"output_tokens": 219}}
    reply = "We improved by +20% and threshold moved to 0.8."
    text, applied = _apply_metrics_grounding_guard(
        user_message="Yes please to this: Want to simulate a full Rung traversal with your metrics, or contrast it with Roberts' archetypes more explicitly?",
        response_text=reply,
        memories=memories,
        metadata_payload=metadata,
    )
    assert applied is False
    assert text == reply
