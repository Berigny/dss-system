"""Tests for backend.kernel.user_profile_adapter."""

from __future__ import annotations

import pytest

from backend.kernel import constants
from backend.kernel.user_profile_adapter import UserProfileAdapter
from backend.kernel.value_node_balance import ValueNodeBalance


@pytest.fixture
def adapter() -> UserProfileAdapter:
    return UserProfileAdapter()


@pytest.fixture
def balance() -> ValueNodeBalance:
    return ValueNodeBalance()


def test_known_motivation_profile_produces_expected_weights(adapter: UserProfileAdapter) -> None:
    scores = adapter.seed_scores("style_05_investigation")
    assert set(scores.keys()) == set(constants.VALUE_NODE_LABELS)
    # Investigation style weights novelty highest.
    assert scores["novelty"] > scores["relatedness"]
    assert scores["novelty"] > 0.0
    assert all(v > 0.0 for v in scores.values())


def test_known_cognitive_profile_produces_expected_bias_direction(adapter: UserProfileAdapter) -> None:
    scores = adapter.seed_scores("internal_focus-abstract_pattern-systematic-exploratory")
    # Abstract-pattern biases connection; systematic biases autonomy.
    assert scores["connection"] > 0.0
    assert scores["autonomy"] > 0.0
    assert set(scores.keys()) == set(constants.VALUE_NODE_LABELS)


def test_unknown_profile_falls_back_to_uniform(adapter: UserProfileAdapter) -> None:
    scores = adapter.seed_scores("style_99_nonexistent")
    expected = 1.0 / len(constants.VALUE_NODE_LABELS)
    for label in constants.VALUE_NODE_LABELS:
        assert scores[label] == pytest.approx(expected)


def test_empty_profile_falls_back_to_uniform(adapter: UserProfileAdapter) -> None:
    scores = adapter.seed_scores(None)
    expected = 1.0 / len(constants.VALUE_NODE_LABELS)
    for label in constants.VALUE_NODE_LABELS:
        assert scores[label] == pytest.approx(expected)


def test_seed_balance_api_symmetry(adapter: UserProfileAdapter, balance: ValueNodeBalance) -> None:
    scores = adapter.seed_balance(balance, "style_01_integrity")
    assert set(scores.keys()) == set(constants.VALUE_NODE_LABELS)
    assert all(v > 0.0 for v in scores.values())


def test_value_node_balance_disabled_by_default(balance: ValueNodeBalance) -> None:
    scores = balance.score()
    seeded = balance.score(profile="style_01_integrity")
    # Default and seeded scores should differ when a profile is supplied.
    assert scores != seeded


def test_value_node_balance_profile_seed_overridable_by_dimension_scores(balance: ValueNodeBalance) -> None:
    seeded = balance.score(profile="style_01_integrity")
    overridden = balance.score(
        profile="style_01_integrity",
        context={"dimension_scores": {"interest": 0.9}},
    )
    # dimension_scores should take priority over the profile seed.
    assert overridden["novelty"] == pytest.approx(0.9)
    assert overridden["uniqueness"] == pytest.approx(0.9)
    assert overridden != seeded
