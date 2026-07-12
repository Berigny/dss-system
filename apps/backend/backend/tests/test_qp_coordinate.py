"""Smoke and contract tests for backend.fieldx_kernel.qp_coordinate."""

import random
from fractions import Fraction

import pytest

from backend.fieldx_kernel.qp_coordinate import (
    DigitSymbol,
    QpCoordinate,
    hensel_lift_coordinate,
)


class TestDigitSymbol:
    def test_enum_members(self):
        assert DigitSymbol.ORIGIN.value == 0
        assert DigitSymbol.GRACE.value == 9
        assert DigitSymbol.INF.value == 10

    def test_integer_alias_lookup(self):
        assert DigitSymbol(2) is DigitSymbol.TEMPORALIZATION
        assert DigitSymbol(8) is DigitSymbol.LAW


class TestQpCoordinateImportAndConstruction:
    def test_import(self):
        from backend.fieldx_kernel.qp_coordinate import QpCoordinate as Imported

        assert Imported is QpCoordinate

    def test_origin(self):
        coord = QpCoordinate.origin(metric_prime=5, working_precision=16)
        assert coord.metric_prime == 5
        assert coord.valuation_offset == 0
        assert coord.unit_digits == ()
        assert coord.sealed is True
        assert coord.kernel_node == "Eq0"
        assert coord.tetrahedron == "S1"
        assert coord.dual_complement == "Eq4"
        assert coord.coordinate_id.startswith("qp:")

    def test_construct_with_digit_sequence(self):
        coord = (
            QpCoordinate.origin(metric_prime=5, working_precision=16)
            .append(DigitSymbol.ORIGIN)
            .append(DigitSymbol.BOUNDARY)
            .append(DigitSymbol.TEMPORALIZATION)
        )
        assert coord.unit_digits == (
            DigitSymbol.ORIGIN,
            DigitSymbol.BOUNDARY,
            DigitSymbol.TEMPORALIZATION,
        )
        assert coord.valuation_offset == 3
        assert coord.kernel_node == "Eq2"


class TestQpCoordinateAppend:
    def test_append_returns_new_coordinate(self):
        origin = QpCoordinate.origin(metric_prime=5, working_precision=16)
        next_coord = origin.append(DigitSymbol.BOUNDARY)
        assert next_coord is not origin
        assert next_coord.valuation_offset == 1
        assert next_coord.unit_digits == (DigitSymbol.BOUNDARY,)
        assert next_coord.parent_coordinate_id == origin.coordinate_id
        assert next_coord.kernel_node == "Eq1"

    def test_append_does_not_mutate_parent(self):
        origin = QpCoordinate.origin(metric_prime=5, working_precision=16)
        origin.append(DigitSymbol.BOUNDARY)
        assert origin.valuation_offset == 0
        assert origin.unit_digits == ()

    def test_append_inf_is_rejected(self):
        origin = QpCoordinate.origin(metric_prime=5, working_precision=16)
        with pytest.raises(ValueError):
            origin.append(DigitSymbol.INF)

    def test_append_exceeding_precision_is_rejected(self):
        coord = QpCoordinate.origin(metric_prime=5, working_precision=1)
        filled = coord.append(DigitSymbol.BOUNDARY)
        with pytest.raises(ValueError):
            filled.append(DigitSymbol.TEMPORALIZATION)


class TestQpCoordinateImmutability:
    def test_frozen_dataclass_cannot_mutate(self):
        coord = QpCoordinate.origin(metric_prime=5, working_precision=16)
        with pytest.raises(AttributeError):
            coord.valuation_offset = 1

    def test_with_dual_state_returns_new_object(self):
        s1 = QpCoordinate.origin(metric_prime=5, working_precision=16)
        s2 = QpCoordinate.origin(metric_prime=17, working_precision=16)
        updated = s1.with_dual_state(s2)
        assert updated is not s1
        assert updated.dual_state is s2
        assert s1.dual_state is None

    def test_with_mediator_state_returns_new_object(self):
        s1 = QpCoordinate.origin(metric_prime=5, working_precision=16)
        mediator = QpCoordinate.origin(metric_prime=137, working_precision=16)
        updated = s1.with_mediator_state(mediator)
        assert updated is not s1
        assert updated.mediator_state is mediator
        assert s1.mediator_state is None


class TestQpCoordinateCoordinateId:
    def test_coordinate_id_is_deterministic(self):
        a = QpCoordinate.origin(metric_prime=5, working_precision=16)
        b = QpCoordinate.origin(metric_prime=5, working_precision=16)
        assert a.coordinate_id == b.coordinate_id

    def test_coordinate_id_changes_with_digits(self):
        a = QpCoordinate.origin(metric_prime=5, working_precision=16)
        b = a.append(DigitSymbol.BOUNDARY)
        assert a.coordinate_id != b.coordinate_id

    def test_invalid_coordinate_id_raises(self):
        with pytest.raises(ValueError):
            QpCoordinate(
                coordinate_id="qp:not-the-right-hash",
                kernel_node="Eq0",
                metric_prime=5,
                tetrahedron="S1",
                dual_complement="Eq4",
                unit_digits=(),
                valuation_offset=0,
                working_precision=16,
            )


class TestQpCoordinateValidation:
    def test_rejects_negative_valuation_offset(self):
        with pytest.raises(ValueError):
            QpCoordinate(
                coordinate_id="qp:placeholder",
                kernel_node="Eq0",
                metric_prime=5,
                tetrahedron="S1",
                dual_complement="Eq4",
                unit_digits=(),
                valuation_offset=-1,
                working_precision=16,
            )

    def test_rejects_mismatched_valuation_and_digits(self):
        with pytest.raises(ValueError):
            QpCoordinate(
                coordinate_id="qp:placeholder",
                kernel_node="Eq0",
                metric_prime=5,
                tetrahedron="S1",
                dual_complement="Eq4",
                unit_digits=(DigitSymbol.ORIGIN,),
                valuation_offset=0,
                working_precision=16,
            )

    def test_rejects_non_digit_in_unit_digits(self):
        with pytest.raises(ValueError):
            QpCoordinate(
                coordinate_id="qp:placeholder",
                kernel_node="Eq0",
                metric_prime=5,
                tetrahedron="S1",
                dual_complement="Eq4",
                unit_digits=(0, 1),  # type: ignore[arg-type]
                valuation_offset=2,
                working_precision=16,
            )


# -----------------------------------------------------------------------------
# Property-test harness fixtures (DS-REVIEW-192 P0-03)
# -----------------------------------------------------------------------------

_METRIC_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 137, 139]
_DUAL_PAIR_PRIMES = {5: 17, 7: 19, 11: 2, 137: 139}
_RANDOM = random.Random(20260624)


def random_metric_prime() -> int:
    """Return a deterministic metric prime from the kernel prime set."""
    return _RANDOM.choice(_METRIC_PRIMES)


def random_digit_sequence(length: int | None = None) -> tuple[DigitSymbol, ...]:
    """Return a deterministic sequence of DigitSymbol values."""
    if length is None:
        length = _RANDOM.randint(0, 8)
    return tuple(DigitSymbol(_RANDOM.randint(0, 9)) for _ in range(length))


def random_qp_coordinate(
    p: int | None = None, N: int = 16
) -> QpCoordinate:
    """Return a deterministic random QpCoordinate for property tests."""
    p = p or random_metric_prime()
    digits = random_digit_sequence()
    return QpCoordinate.origin(metric_prime=p, working_precision=N).append_sequence(digits)


def random_dual_pair(N: int = 16) -> tuple[QpCoordinate, QpCoordinate]:
    """Return a deterministic valid S1/S2 dual pair (S1 first, S2 second)."""
    s1_p = _RANDOM.choice([2, 3, 5, 7])
    s2_p = {2: 11, 3: 13, 5: 17, 7: 19}[s1_p]
    s1 = QpCoordinate.origin(metric_prime=s1_p, working_precision=N)
    s2 = QpCoordinate.origin(metric_prime=s2_p, working_precision=N)
    return s1, s2


# Extend QpCoordinate with a helper used only by the test harness for now.
def _append_sequence(
    self: QpCoordinate, digits: tuple[DigitSymbol, ...]
) -> QpCoordinate:
    coord = self
    for d in digits:
        if d == DigitSymbol.INF:
            continue
        coord = coord.append(d)
    return coord


QpCoordinate.append_sequence = _append_sequence  # type: ignore[attr-defined]


# -----------------------------------------------------------------------------
# Hensel lifting for coordinates (DS-REVIEW-192 P1-08)
# -----------------------------------------------------------------------------

class TestHenselLiftCoordinate:
    def test_lift_preserves_dual_and_mediator_references(self):
        origin = QpCoordinate.origin(metric_prime=5, working_precision=8)
        dual = QpCoordinate.origin(metric_prime=5, working_precision=8)
        mediator = QpCoordinate.origin(metric_prime=5, working_precision=8)

        state = origin.with_dual_state(dual).with_mediator_state(mediator)
        lifted = hensel_lift_coordinate(state, 16)

        assert lifted.dual_state is not None
        assert lifted.mediator_state is not None
        assert lifted.dual_state.coordinate_id == dual.coordinate_id
        assert lifted.mediator_state.coordinate_id == mediator.coordinate_id
        assert lifted.working_precision == 16
        assert lifted.dual_state.working_precision == 16
        assert lifted.mediator_state.working_precision == 16

    def test_lift_lifts_fraction_rational_representative(self):
        origin = QpCoordinate.origin(metric_prime=5, working_precision=8)
        state = QpCoordinate(
            coordinate_id=origin.coordinate_id,
            kernel_node=origin.kernel_node,
            metric_prime=origin.metric_prime,
            tetrahedron=origin.tetrahedron,
            dual_complement=origin.dual_complement,
            unit_digits=origin.unit_digits,
            valuation_offset=origin.valuation_offset,
            working_precision=origin.working_precision,
            rational_representative=Fraction(2, 3),
        )
        lifted = hensel_lift_coordinate(state, 16)
        assert lifted.rational_representative is not None
        assert lifted.rational_representative.to_rational() == Fraction(2, 3)

    def test_lift_lifts_qp_element_rational_representative(self):
        from backend.fieldx_kernel.qp_arithmetic import QpElement

        origin = QpCoordinate.origin(metric_prime=5, working_precision=8)
        # Use an integer representative so zero-padding extension is exact.
        rep = QpElement.from_int(5, 123, 8)
        state = QpCoordinate(
            coordinate_id=origin.coordinate_id,
            kernel_node=origin.kernel_node,
            metric_prime=origin.metric_prime,
            tetrahedron=origin.tetrahedron,
            dual_complement=origin.dual_complement,
            unit_digits=origin.unit_digits,
            valuation_offset=origin.valuation_offset,
            working_precision=origin.working_precision,
            rational_representative=rep,
        )
        lifted = hensel_lift_coordinate(state, 16)
        assert lifted.rational_representative.working_precision == 16
        assert lifted.rational_representative.to_int() == 123


# -----------------------------------------------------------------------------
# Dual-tetrahedron overlay (DS-REVIEW-192 P1-10)
# -----------------------------------------------------------------------------

class TestDualTetrahedronOverlay:
    def test_dual_mapping(self):
        from backend.fieldx_kernel.qp_coordinate import dual_complement, metric_prime, tetrahedron

        assert dual_complement("Eq0") == "Eq4"
        assert dual_complement("Eq4") == "Eq0"
        assert dual_complement("Eq2") == "Eq6"
        assert dual_complement("Eq8") == "Eq9"
        assert metric_prime("Eq2") == 5
        assert metric_prime("Eq6") == 17
        assert metric_prime("Eq8") == 137
        assert tetrahedron("Eq3") == "S1"
        assert tetrahedron("Eq7") == "S2"
        assert tetrahedron("Eq9") == "C"

    def test_pair_valid_for_dual_pair(self):
        from backend.fieldx_kernel.qp_coordinate import pair_valid

        s1 = QpCoordinate.origin(metric_prime=5, working_precision=16)
        s2 = QpCoordinate.origin(metric_prime=17, working_precision=16)
        s1 = QpCoordinate(
            coordinate_id=s1.coordinate_id,
            kernel_node="Eq2",
            metric_prime=5,
            tetrahedron="S1",
            dual_complement="Eq6",
            unit_digits=s1.unit_digits,
            valuation_offset=s1.valuation_offset,
            working_precision=s1.working_precision,
        )
        s2 = QpCoordinate(
            coordinate_id=s2.coordinate_id,
            kernel_node="Eq6",
            metric_prime=17,
            tetrahedron="S2",
            dual_complement="Eq2",
            unit_digits=s2.unit_digits,
            valuation_offset=s2.valuation_offset,
            working_precision=s2.working_precision,
            dual_state=s1,
        )
        assert pair_valid(s1, s2)

    def test_pair_valid_rejects_mismatched_pair(self):
        from backend.fieldx_kernel.qp_coordinate import pair_valid

        s1 = QpCoordinate.origin(metric_prime=5, working_precision=16)
        s1 = QpCoordinate(
            coordinate_id=s1.coordinate_id,
            kernel_node="Eq2",
            metric_prime=5,
            tetrahedron="S1",
            dual_complement="Eq6",
            unit_digits=s1.unit_digits,
            valuation_offset=s1.valuation_offset,
            working_precision=s1.working_precision,
        )
        wrong = QpCoordinate.origin(metric_prime=17, working_precision=16)
        wrong = QpCoordinate(
            coordinate_id=wrong.coordinate_id,
            kernel_node="Eq7",
            metric_prime=17,
            tetrahedron="S2",
            dual_complement="Eq3",
            unit_digits=wrong.unit_digits,
            valuation_offset=wrong.valuation_offset,
            working_precision=wrong.working_precision,
        )
        assert not pair_valid(s1, wrong)

    def test_append_preserves_dual_delta_v(self):
        from backend.fieldx_kernel.qp_coordinate import pair_valid

        s2 = QpCoordinate.origin(metric_prime=17, working_precision=16)
        s2 = QpCoordinate(
            coordinate_id=s2.coordinate_id,
            kernel_node="Eq6",
            metric_prime=17,
            tetrahedron="S2",
            dual_complement="Eq2",
            unit_digits=s2.unit_digits,
            valuation_offset=s2.valuation_offset,
            working_precision=s2.working_precision,
        )
        s1 = QpCoordinate.origin(metric_prime=5, working_precision=16)
        s1 = QpCoordinate(
            coordinate_id=s1.coordinate_id,
            kernel_node="Eq2",
            metric_prime=5,
            tetrahedron="S1",
            dual_complement="Eq6",
            unit_digits=s1.unit_digits,
            valuation_offset=s1.valuation_offset,
            working_precision=s1.working_precision,
            dual_state=s2,
        )
        s2 = QpCoordinate(
            coordinate_id=s2.coordinate_id,
            kernel_node="Eq6",
            metric_prime=17,
            tetrahedron="S2",
            dual_complement="Eq2",
            unit_digits=s2.unit_digits,
            valuation_offset=s2.valuation_offset,
            working_precision=s2.working_precision,
            dual_state=s1,
        )
        s1_next = s1.append(DigitSymbol.TEMPORALIZATION)
        assert pair_valid(s1_next, s2)

    def test_synchronize_audit(self):
        from backend.fieldx_kernel.qp_coordinate import synchronize_audit

        awareness = QpCoordinate.origin(metric_prime=5, working_precision=16)
        awareness = QpCoordinate(
            coordinate_id=awareness.coordinate_id,
            kernel_node="Eq2",
            metric_prime=5,
            tetrahedron="S1",
            dual_complement="Eq6",
            unit_digits=awareness.unit_digits,
            valuation_offset=awareness.valuation_offset,
            working_precision=awareness.working_precision,
            hysteresis_depth=0.5,
            circulation_pass=3,
        )
        audit = QpCoordinate.origin(metric_prime=17, working_precision=16)
        audit = QpCoordinate(
            coordinate_id=audit.coordinate_id,
            kernel_node="Eq6",
            metric_prime=17,
            tetrahedron="S2",
            dual_complement="Eq2",
            unit_digits=audit.unit_digits,
            valuation_offset=audit.valuation_offset,
            working_precision=audit.working_precision,
            hysteresis_depth=0.4,
            circulation_pass=0,
        )
        synced = synchronize_audit(audit, awareness)
        assert synced.kernel_node == "Eq6"
        assert synced.valuation_offset == audit.valuation_offset + 1
        assert synced.hysteresis_depth == awareness.hysteresis_depth
        assert synced.circulation_pass == awareness.circulation_pass


# -----------------------------------------------------------------------------
# Circulation engine (DS-REVIEW-192 P1-11)
# -----------------------------------------------------------------------------

class TestCirculationEngine:
    def test_eq2_temporalization_returns_new_object(self):
        from backend.fieldx_kernel.qp_coordinate import eq2_temporalization_qp

        state = QpCoordinate.origin(metric_prime=5, working_precision=16)
        state = QpCoordinate(
            coordinate_id=state.coordinate_id,
            kernel_node="Eq2",
            metric_prime=5,
            tetrahedron="S1",
            dual_complement="Eq6",
            unit_digits=state.unit_digits,
            valuation_offset=state.valuation_offset,
            working_precision=state.working_precision,
            rational_representative=Fraction(1, 1),
        )
        next_state = eq2_temporalization_qp(state)
        assert next_state is not state
        assert state.valuation_offset == 0
        assert next_state.valuation_offset == 1
        assert next_state.kernel_node == "Eq2"
        assert next_state.hysteresis_depth > state.hysteresis_depth

    def test_pass_lifecycle_walks_eq0_to_eq9(self):
        from backend.fieldx_kernel.qp_coordinate import PassLifecycle

        state = QpCoordinate.origin(metric_prime=5, working_precision=16)
        lifecycle = PassLifecycle(state, mode=2, coherence=0.99)
        lifecycle.run()
        assert lifecycle.state.kernel_node == "Eq9"
        assert len(lifecycle.state.unit_digits) == 10
        assert lifecycle.state.unit_digits[-1] == DigitSymbol.GRACE

    def test_commit_decision_blocked_for_low_mode(self):
        from backend.fieldx_kernel.qp_coordinate import PassLifecycle, commit_decision

        state = QpCoordinate.origin(metric_prime=5, working_precision=16)
        lifecycle = PassLifecycle(state, mode=1).run()
        assert not commit_decision(lifecycle)

    def test_commit_decision_allowed_for_high_mode(self):
        from backend.fieldx_kernel.qp_coordinate import PassLifecycle, commit_decision

        state = QpCoordinate.origin(metric_prime=5, working_precision=16)
        lifecycle = PassLifecycle(state, mode=2, coherence=0.99).run()
        assert commit_decision(lifecycle)

    def test_recurse_increments_circulation_pass(self):
        from backend.fieldx_kernel.qp_coordinate import recurse

        state = QpCoordinate.origin(metric_prime=5, working_precision=16)
        state = QpCoordinate(
            coordinate_id=state.coordinate_id,
            kernel_node="Eq9",
            metric_prime=5,
            tetrahedron="S1",
            dual_complement="Eq6",
            unit_digits=state.unit_digits,
            valuation_offset=state.valuation_offset,
            working_precision=state.working_precision,
            circulation_pass=2,
            pass_exit_node="Eq9",
        )
        next_pass = recurse(state)
        assert next_pass.circulation_pass == 3
        assert next_pass.kernel_node == "Eq0"
        assert next_pass.pass_exit_node is None

    def test_ten_pass_benchmark(self):
        from backend.fieldx_kernel.qp_coordinate import PassLifecycle, recurse, commit_decision

        state = QpCoordinate.origin(metric_prime=5, working_precision=200)
        valuations = [state.valuation_offset]
        for _ in range(10):
            lifecycle = PassLifecycle(state, mode=2, coherence=0.99).run()
            assert commit_decision(lifecycle)
            state = lifecycle.state
            valuations.append(state.valuation_offset)
            state = recurse(state)

        # Valuation should increase monotonically across passes.
        assert all(v < valuations[i + 1] for i, v in enumerate(valuations[:-1]))
        assert state.circulation_pass == 10
        assert len(state.unit_digits) >= 10
import json
import math
from fractions import Fraction

import pytest

from backend.fieldx_kernel.qp_coordinate import (
    DigitSymbol,
    QpCoordinate,
    _coordinate_hash,
    hensel_lift_coordinate,
)
from shared_types.coord_schema import parse_bigint


def test_fraction_rational_representative_bigint_safe():
    """A 200-prime numerator/denominator must round-trip as strings."""
    primes = [
        2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
        73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151, 157,
        163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233, 239, 241,
        251, 257, 263, 269, 271, 277, 281, 283, 293, 307, 311, 313, 317, 331, 337, 347,
        349, 353, 359, 367, 373, 379, 383, 389, 397, 401, 409, 419, 421, 431, 433, 439,
        443, 449, 457, 461, 463, 467, 479, 487, 491, 499, 503, 509, 521, 523, 541, 547,
        557, 563, 569, 571, 577, 587, 593, 599, 601, 607, 613, 617, 619, 631, 641, 643,
        647, 653, 659, 661, 673, 677, 683, 691, 701, 709, 719, 727, 733, 739, 743, 751,
        757, 761, 769, 773, 787, 797, 809, 811, 821, 823, 827, 829, 839, 853, 857, 859,
        863, 877, 881, 883, 887, 907, 911, 919, 929, 937, 941, 947, 953, 967, 971, 977,
        983, 991, 997, 1009, 1013, 1019, 1021, 1031, 1033, 1039, 1049, 1051, 1061, 1063,
        1069, 1087, 1091, 1093, 1097, 1103, 1109, 1117, 1123, 1129, 1151, 1153, 1163,
        1171, 1181, 1187, 1193, 1201, 1213, 1217, 1223,
    ]
    assert len(primes) == 200
    numerator = math.prod(primes)
    denominator = math.prod(primes[:100])

    metric_prime = 5
    coordinate_id = _coordinate_hash(metric_prime, 0, ())
    coord = QpCoordinate(
        coordinate_id=coordinate_id,
        kernel_node="Eq0",
        metric_prime=metric_prime,
        tetrahedron="S1",
        dual_complement="Eq4",
        unit_digits=(),
        valuation_offset=0,
        working_precision=16,
        rational_representative=Fraction(numerator, denominator),
        circulation_pass=0,
        sealed=True,
    )
    serialized = coord.as_dict()
    rational = serialized["rational_representative"]
    reduced = coord.rational_representative
    assert rational["type"] == "fraction"
    assert isinstance(rational["numerator"], str)
    assert isinstance(rational["denominator"], str)
    assert parse_bigint(rational["numerator"]) == reduced.numerator
    assert parse_bigint(rational["denominator"]) == reduced.denominator

    json_text = json.dumps(serialized)
    restored = QpCoordinate.from_dict(json.loads(json_text))
    assert restored.rational_representative == coord.rational_representative
