"""Tests for the genuine Qp distance contract (DS-REVIEW-193 P2-01)."""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from backend.fieldx_kernel.qp_arithmetic import QpElement, qp_distance, qp_score
from backend.fieldx_kernel.qp_coordinate import (
    QpCoordinate,
    circulation_depth_compatible,
    dual_state_compatible,
    mediator_state_compatible,
    qp_coordinate_distance,
)


class TestQpDistance:
    def test_distance_zero_for_identical_elements(self):
        a = QpElement.from_rational(5, 1, 1)
        assert qp_distance(a, a) == 0.0
        assert a.distance(a) == 0.0

    def test_distance_increases_with_valuation_separation(self):
        a = QpElement.from_rational(5, 0, 1)  # 0
        b = QpElement.from_int(5, 1)
        c = QpElement.from_int(5, 5)
        d_ab = qp_distance(a, b)
        d_ac = qp_distance(a, c)
        # b = 1 has valuation 0 => distance 1; c = 5 has valuation 1 => distance 1/5.
        assert d_ab == 1.0
        assert d_ac == pytest.approx(0.2)
        assert d_ac < d_ab

    def test_distance_formula_for_rational_difference(self):
        a = QpElement.from_rational(7, 1, 2)  # 1/2
        b = QpElement.from_rational(7, 3, 2)  # 3/2
        # a - b = -1 => valuation 0 => distance 1
        assert qp_distance(a, b) == 1.0

    def test_strong_triangle_inequality_random_triples(self):
        p = 5
        for _ in range(200):
            a = QpElement.from_rational(p, 1, 1 + (_ % 5))
            b = QpElement.from_rational(p, _ + 2, 7)
            c = QpElement.from_rational(p, _ * 3 + 1, 11)
            d_ab = qp_distance(a, b)
            d_bc = qp_distance(b, c)
            d_ac = qp_distance(a, c)
            assert d_ac <= max(d_ab, d_bc) + 1e-12

    def test_different_primes_raise(self):
        a = QpElement.from_int(5, 1)
        b = QpElement.from_int(7, 1)
        with pytest.raises(ValueError, match="same prime"):
            qp_distance(a, b)


class TestQpScore:
    def test_score_one_for_zero_distance(self):
        assert qp_score(0.0, 5, 4) == 1.0
        assert qp_score(-0.0, 5, 4) == 1.0

    def test_score_zero_for_distance_one_at_precision(self):
        # distance = p^{-N} -> v = N -> score = 1 -> but clamped? Wait v/N = 1.
        # Actually we want distance == 1 -> v=0 -> score 0.
        assert qp_score(1.0, 5, 4) == 0.0

    def test_score_clamps_to_zero_for_large_distance(self):
        assert qp_score(5.0, 5, 4) == 0.0

    def test_score_increases_with_closeness(self):
        p, N = 5, 4
        # distances 1/5, 1/25, 1/125 -> v = 1, 2, 3
        assert qp_score(1 / 5, p, N) == pytest.approx(0.25)
        assert qp_score(1 / 25, p, N) == pytest.approx(0.5)
        assert qp_score(1 / 125, p, N) == pytest.approx(0.75)

    def test_score_clamps_to_one_for_very_close(self):
        assert qp_score(1 / 625, 5, 4) == 1.0


class TestQpCoordinateDistance:
    def test_distance_on_qpelement_representatives(self):
        a = QpCoordinate.origin(5, 8, kernel_node="Eq2")
        b = QpCoordinate.origin(5, 8, kernel_node="Eq2")
        a = a.with_mediator_state(None)
        b = b.with_mediator_state(None)
        # Both have no rational representative, so raise.
        with pytest.raises(ValueError, match="rational representative"):
            qp_coordinate_distance(a, b)

    def test_distance_on_fraction_representatives(self):
        a = QpCoordinate.origin(5, 8, kernel_node="Eq2")
        b = QpCoordinate.origin(5, 8, kernel_node="Eq2")
        a = a.with_mediator_state(None)
        b = b.with_mediator_state(None)
        # Inject rational representatives via replace (frozen dataclass workaround).
        a = _with_rational(a, Fraction(1, 1))
        b = _with_rational(b, Fraction(6, 1))
        # 1 and 6 differ by 5 -> v_5(5) = 1 -> distance = 1/5.
        assert qp_coordinate_distance(a, b) == pytest.approx(0.2)

    def test_different_metric_primes_raise(self):
        a = QpCoordinate.origin(5, 4, kernel_node="Eq2")
        b = QpCoordinate.origin(7, 4, kernel_node="Eq3")
        a = _with_rational(a, Fraction(1, 1))
        b = _with_rational(b, Fraction(1, 1))
        with pytest.raises(ValueError, match="same prime"):
            qp_coordinate_distance(a, b)


class TestRetrievalCompatibilityFilters:
    def test_circulation_depth_compatible(self):
        a = QpCoordinate.origin(5, 4, kernel_node="Eq2")
        b = QpCoordinate.origin(5, 4, kernel_node="Eq2")
        assert circulation_depth_compatible(a, b) is True
        b_far = b.with_mediator_state(None)
        b_far = _replace_field(b_far, "circulation_pass", 5)
        assert circulation_depth_compatible(a, b_far, max_pass_delta=1) is False

    def test_dual_state_compatible(self):
        a = QpCoordinate.origin(5, 4, kernel_node="Eq2")
        dual_a = QpCoordinate.origin(5, 4, kernel_node="Eq6")
        a = a.with_dual_state(dual_a)
        b = QpCoordinate.origin(5, 4, kernel_node="Eq2")
        dual_b = QpCoordinate.origin(5, 4, kernel_node="Eq6")
        b = b.with_dual_state(dual_b)
        assert dual_state_compatible(a, b) is True

        dual_b_mismatch = _replace_field(dual_b, "kernel_node", "Eq7")
        b_bad = b.with_dual_state(dual_b_mismatch)
        assert dual_state_compatible(a, b_bad) is False

    def test_mediator_state_compatible(self):
        a = QpCoordinate.origin(5, 4, kernel_node="Eq2")
        med_a = QpCoordinate.origin(137, 4, kernel_node="Eq8")
        a = a.with_mediator_state(med_a)
        b = QpCoordinate.origin(5, 4, kernel_node="Eq2")
        med_b = QpCoordinate.origin(137, 4, kernel_node="Eq8")
        b = b.with_mediator_state(med_b)
        assert mediator_state_compatible(a, b) is True

        b_no_med = b.with_mediator_state(None)
        assert mediator_state_compatible(a, b_no_med) is False


def _with_rational(coord: QpCoordinate, value: Fraction) -> QpCoordinate:
    """Return a copy of ``coord`` with ``rational_representative`` set to ``value``."""
    return QpCoordinate(
        coordinate_id=coord.coordinate_id,
        kernel_node=coord.kernel_node,
        metric_prime=coord.metric_prime,
        tetrahedron=coord.tetrahedron,
        dual_complement=coord.dual_complement,
        unit_digits=coord.unit_digits,
        valuation_offset=coord.valuation_offset,
        working_precision=coord.working_precision,
        rational_representative=value,
        circulation_pass=coord.circulation_pass,
        pass_entry_node=coord.pass_entry_node,
        pass_exit_node=coord.pass_exit_node,
        hysteresis_depth=coord.hysteresis_depth,
        last_shift_map=coord.last_shift_map,
        dual_state=coord.dual_state,
        mediator_state=coord.mediator_state,
        coherence_threshold=coord.coherence_threshold,
        composition_history=coord.composition_history,
        parent_coordinate_id=coord.parent_coordinate_id,
        p_adic_write_cost=coord.p_adic_write_cost,
        padic_ball_hit_count=coord.padic_ball_hit_count,
        created_at=coord.created_at,
        sealed=coord.sealed,
    )


def _replace_field(coord: QpCoordinate, field: str, value):
    """Return a copy of ``coord`` with ``field`` set to ``value``."""
    kwargs = {k: getattr(coord, k) for k in coord.__dataclass_fields__}
    kwargs[field] = value
    return QpCoordinate(**kwargs)
