from __future__ import annotations

import os
import random

import pytest

from backend.fieldx_kernel.orchestrator import p_adic_distance
from backend.fieldx_kernel.p_adic import p_adic_distance_between_integers


def test_distance_zero_for_identical_factors() -> None:
    factors = [{"prime": 2, "delta": 1}, {"prime": 3, "delta": 2}]
    distance, overlap = p_adic_distance(factors, list(factors))
    assert distance == 0.0
    assert overlap == 2


def test_distance_infinite_when_no_overlap() -> None:
    a = [{"prime": 2, "delta": 1}]
    b = [{"prime": 3, "delta": 1}]
    distance, overlap = p_adic_distance(a, b)
    assert distance == float("inf")
    assert overlap == 0


def test_distance_honours_min_overlap() -> None:
    a = [{"prime": 2, "delta": 1}, {"prime": 5, "delta": 1}]
    b = [{"prime": 2, "delta": 1}, {"prime": 7, "delta": 1}]
    distance, overlap = p_adic_distance(a, b, min_overlap=2)
    assert distance == float("inf")
    assert overlap == 1


def test_genuine_p_adic_distance_for_known_values(monkeypatch) -> None:
    monkeypatch.setenv("P_ADIC_DISTANCE_PRIME", "5")
    # A = 5, B = 30 = 2*3*5.  A - B = -25 = -5**2, so |A-B|_5 = 5**-2 = 0.04.
    a = [{"prime": 5, "delta": 1}]
    b = [{"prime": 2, "delta": 1}, {"prime": 3, "delta": 1}, {"prime": 5, "delta": 1}]
    distance, overlap = p_adic_distance(a, b)
    assert overlap == 1
    assert distance == pytest.approx(0.04)


def test_ultrametric_inequality_for_random_factor_sets(monkeypatch) -> None:
    monkeypatch.setenv("P_ADIC_DISTANCE_PRIME", "5")
    rng = random.Random(42)
    primes = [2, 3, 5, 7, 11, 13]

    def random_factors() -> list[dict[str, int]]:
        size = rng.randint(1, 4)
        chosen = rng.sample(primes, size)
        return [{"prime": p, "delta": rng.randint(1, 3)} for p in chosen]

    for _ in range(100):
        a = random_factors()
        b = random_factors()
        c = random_factors()
        d_ab, _ = p_adic_distance(a, b)
        d_bc, _ = p_adic_distance(b, c)
        d_ac, _ = p_adic_distance(a, c)
        if d_ab == float("inf") or d_bc == float("inf") or d_ac == float("inf"):
            continue
        assert d_ac <= max(d_ab, d_bc) + 1e-15


def test_p_adic_distance_between_integers_ultrametric() -> None:
    # Example: under p=5, |25 - 0|_5 = 1/25, |125 - 25|_5 = 1/25,
    # |125 - 0|_5 = 1/125.  The two largest distances are equal.
    assert p_adic_distance_between_integers(25, 0, 5) == pytest.approx(1 / 25)
    assert p_adic_distance_between_integers(125, 25, 5) == pytest.approx(1 / 25)
    assert p_adic_distance_between_integers(125, 0, 5) == pytest.approx(1 / 125)


def test_p_adic_distance_respects_metric_prime(monkeypatch) -> None:
    monkeypatch.setenv("P_ADIC_DISTANCE_PRIME", "3")
    a = [{"prime": 3, "delta": 1}]
    b = [{"prime": 3, "delta": 2}]
    # A=3, B=9, A-B=-6, v_3(-6)=1 => distance 1/3.
    distance, overlap = p_adic_distance(a, b)
    assert overlap == 1
    assert distance == pytest.approx(1 / 3)


def test_empty_factors_return_infinite() -> None:
    assert p_adic_distance([], [{"prime": 2, "delta": 1}]) == (float("inf"), 0)
    assert p_adic_distance([{"prime": 2, "delta": 1}], []) == (float("inf"), 0)
