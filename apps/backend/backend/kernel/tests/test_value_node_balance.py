"""Tests for backend/kernel/value_node_balance.py."""

from __future__ import annotations

import math

import numpy as np
import pytest

from backend.kernel.value_node_balance import ValueNodeBalance


@pytest.fixture
def balance() -> ValueNodeBalance:
    return ValueNodeBalance()


def test_all_nodes_nonzero_and_balanced(balance: ValueNodeBalance) -> None:
    scores = {label: 0.12 for label in balance.NODE_LABELS}
    ok, diagnostics = balance.is_balanced(scores)
    assert ok is True
    assert diagnostics["min"] == pytest.approx(0.12)
    assert diagnostics["dominance_ratio"] == pytest.approx(1.0)
    assert diagnostics["entropy"] > balance._min_entropy


def test_one_node_zero_is_unbalanced(balance: ValueNodeBalance) -> None:
    scores = {label: 0.12 for label in balance.NODE_LABELS}
    scores[balance.NODE_LABELS[0]] = 0.0
    ok, diagnostics = balance.is_balanced(scores)
    assert ok is False
    assert diagnostics["min"] == 0.0


def test_one_node_dominates_is_unbalanced(balance: ValueNodeBalance) -> None:
    scores = {label: 0.01 for label in balance.NODE_LABELS}
    scores[balance.NODE_LABELS[0]] = 1.0
    ok, diagnostics = balance.is_balanced(scores)
    assert ok is False
    assert diagnostics["dominance_ratio"] > balance._max_dominance_ratio


def test_score_from_dimension_context(balance: ValueNodeBalance) -> None:
    # The personality-type overlay maps value nodes to broader dimensions.
    # See value_node_registry in semantic_registry.yaml for the current mapping.
    context = {"dimension_scores": {"interest": 0.9, "context": 0.5}}
    scores = balance.score(context=context)
    assert scores["novelty"] == pytest.approx(0.9)
    assert scores["uniqueness"] == pytest.approx(0.9)
    assert scores["relatedness"] == pytest.approx(0.5)
    assert scores["action"] == pytest.approx(0.5)
    # Unspecified dimensions fall back to default activation.
    for label in ("connection", "potential", "autonomy", "mastery", "centroid"):
        assert scores[label] >= balance._min_activation


def test_score_from_embedding_is_deterministic(balance: ValueNodeBalance) -> None:
    embedding = np.array([0.1, -0.2, 0.3, 0.4, -0.1, 0.0, 0.2, -0.3])
    scores_a = balance.score(query_embedding=embedding)
    scores_b = balance.score(query_embedding=embedding)
    assert scores_a == scores_b
    assert set(scores_a.keys()) == set(balance.NODE_LABELS)
    assert all(v >= 0.0 for v in scores_a.values())


def test_score_default_fills_all_nodes(balance: ValueNodeBalance) -> None:
    scores = balance.score()
    assert set(scores.keys()) == set(balance.NODE_LABELS)
    assert all(v > 0.0 for v in scores.values())


def test_low_entropy_is_unbalanced(balance: ValueNodeBalance) -> None:
    # Highly peaked distribution has low entropy.
    scores = {label: 1e-4 for label in balance.NODE_LABELS}
    scores[balance.NODE_LABELS[0]] = 1.0
    ok, diagnostics = balance.is_balanced(scores)
    assert ok is False
    assert diagnostics["entropy"] < balance._min_entropy


def test_empty_scores_use_zero_defaults(balance: ValueNodeBalance) -> None:
    ok, diagnostics = balance.is_balanced({})
    assert ok is False
    assert diagnostics["min"] == 0.0
    assert math.isfinite(diagnostics["dominance_ratio"])
