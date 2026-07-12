"""Optional user-profile adapter for value-node balance seeding (DS-REVIEW-198).

The adapter reads the KSR personality-type overlay from generated constants and
produces value-node activation scores when a user opts in with a cognitive
profile and/or motivation-style profile. It is strictly optional and never
bypasses native ethics or patch evaluation.
"""

from __future__ import annotations

from typing import Any

from backend.kernel import constants


class UserProfileAdapter:
    """Seed ValueNodeBalance scores from an optional user-profile overlay."""

    # Minimum positive activation; must exceed ValueNodeBalance min_activation.
    _FLOOR = 0.02

    @staticmethod
    def _uniform_scores() -> dict[str, float]:
        """Return a uniform fallback activation across all value nodes."""
        labels = constants.VALUE_NODE_LABELS
        n = len(labels)
        return {label: 1.0 / n for label in labels}

    @staticmethod
    def _blend_weights(weights_list: list[tuple[dict[str, float], float]]) -> dict[str, float]:
        """Blend a list of weight maps by their blend coefficients."""
        labels = constants.VALUE_NODE_LABELS
        total_weight = sum(c for _, c in weights_list)
        if total_weight <= 0.0:
            return UserProfileAdapter._uniform_scores()

        blended: dict[str, float] = {label: 0.0 for label in labels}
        for weights, coeff in weights_list:
            for label in labels:
                blended[label] += weights.get(label, 0.0) * coeff

        for label in labels:
            blended[label] /= total_weight
        return blended

    @staticmethod
    def _apply_pole_bias(
        scores: dict[str, float],
        axis_name: str,
        pole_label: str,
    ) -> dict[str, float]:
        """Apply the value-node bias for a selected cognitive-preference pole."""
        axis = constants.COGNITIVE_PREFERENCE_AXES.get(axis_name)
        if axis is None:
            return scores

        pole = None
        if axis["pole_a"]["label"] == pole_label:
            pole = axis["pole_a"]
        elif axis["pole_b"]["label"] == pole_label:
            pole = axis["pole_b"]
        if pole is None:
            return scores

        bias = pole.get("value_node_bias", {})
        if not bias:
            return scores

        adjusted = dict(scores)
        for label, delta in bias.items():
            if label in adjusted:
                adjusted[label] = max(UserProfileAdapter._FLOOR, adjusted[label] + float(delta))
        return adjusted

    @classmethod
    def seed_scores(cls, profile: str | None) -> dict[str, float]:
        """Return value-node activation scores seeded from ``profile``.

        ``profile`` may be:

        * a motivation-style profile id (``style_01_integrity``, ...,
          ``style_09_harmony``);
        * a cognitive profile id from the correlation table
          (e.g. ``external_focus-concrete_detail-systematic-structured``); or
        * ``None`` / empty → uniform fallback.

        Unknown profiles also fall back to uniform activation.
        """
        if not profile:
            return cls._uniform_scores()

        labels = constants.VALUE_NODE_LABELS

        # Motivation-style profile: use weights directly.
        if profile.startswith("style_"):
            style = constants.MOTIVATION_STYLE_PROFILES.get(profile)
            if style is None:
                return cls._uniform_scores()
            weights = style["value_node_weights"]
            return {label: max(cls._FLOOR, float(weights.get(label, 0.0))) for label in labels}

        # Cognitive profile: blend correlated motivation styles and apply pole biases.
        correlation = constants.PROFILE_CORRELATION_TABLE.get(profile)
        if correlation is None:
            return cls._uniform_scores()

        style_blends: list[tuple[dict[str, float], float]] = []
        for entry in correlation.get("top_styles", []):
            style_id = entry["style"]
            weight = entry["weight"]
            style = constants.MOTIVATION_STYLE_PROFILES.get(style_id)
            if style is not None:
                style_blends.append((style["value_node_weights"], weight))

        scores = cls._blend_weights(style_blends)

        # Apply per-axis pole biases encoded in the profile key.
        parts = profile.split("-")
        axis_names = list(constants.COGNITIVE_PREFERENCE_AXES.keys())
        for i, pole in enumerate(parts):
            if i >= len(axis_names):
                break
            scores = cls._apply_pole_bias(scores, axis_names[i], pole)

        return {label: max(cls._FLOOR, float(scores.get(label, cls._FLOOR))) for label in labels}

    def seed_balance(
        self,
        value_node_balance: Any,
        profile: str | None,
    ) -> dict[str, float]:
        """Return seeded value-node scores for ``value_node_balance``.

        The ``value_node_balance`` argument is accepted for API symmetry and to
        allow future per-instance rule overrides; the current implementation
        derives all weights from generated constants.
        """
        _ = value_node_balance
        return self.seed_scores(profile)
