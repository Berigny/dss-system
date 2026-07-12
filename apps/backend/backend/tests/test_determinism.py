"""Tests for deterministic execution helpers."""

from __future__ import annotations

import random

import pytest

from backend.benchmarks.determinism import (
    DEFAULT_DETERMINISTIC_SEED,
    deterministic_context,
    ensure_deterministic,
    is_deterministic_mode,
    set_global_seed,
)


def test_set_global_seed_makes_random_deterministic() -> None:
    set_global_seed(123)
    first = [random.random() for _ in range(10)]
    set_global_seed(123)
    second = [random.random() for _ in range(10)]
    assert first == second


def test_different_seeds_produce_different_sequences() -> None:
    set_global_seed(123)
    first = [random.random() for _ in range(10)]
    set_global_seed(456)
    second = [random.random() for _ in range(10)]
    assert first != second


def test_deterministic_context_restores_previous_state() -> None:
    set_global_seed(999)
    before = random.random()
    with deterministic_context(111):
        inside = random.random()
    after = random.random()
    # Inside should not equal the continuation of the outer sequence.
    assert inside != before
    # After the context, the outer sequence should resume.
    set_global_seed(999)
    _ = random.random()  # consume the same value as before
    assert random.random() == after


def test_is_deterministic_mode_true_inside_context() -> None:
    assert not is_deterministic_mode()
    with deterministic_context(222):
        assert is_deterministic_mode()
    assert not is_deterministic_mode()


def test_ensure_deterministic_uses_default_seed() -> None:
    seed = ensure_deterministic()
    assert seed == DEFAULT_DETERMINISTIC_SEED


def test_ensure_deterministic_honours_explicit_seed() -> None:
    seed = ensure_deterministic(777)
    assert seed == 777
    set_global_seed(777)
    expected = [random.random() for _ in range(5)]
    ensure_deterministic(777)
    actual = [random.random() for _ in range(5)]
    assert expected == actual
