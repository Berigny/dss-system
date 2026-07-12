"""Smoke and contract tests for backend.fieldx_kernel.qp_arithmetic.QpElement."""

import math
import random
from fractions import Fraction

import pytest

from backend.fieldx_kernel.qp_arithmetic import QpElement, _digits_to_int


class TestQpElementImportAndConstruction:
    def test_import(self):
        from backend.fieldx_kernel.qp_arithmetic import QpElement as Imported

        assert Imported is QpElement

    def test_zero_element(self):
        zero = QpElement.zero(5, 8)
        assert zero.p == 5
        assert zero.valuation_offset == math.inf
        assert zero.unit_digits == ()
        assert zero.working_precision == 8

    def test_non_zero_element(self):
        elem = QpElement(p=5, valuation_offset=0, unit_digits=(1, 2, 3), working_precision=8)
        assert elem.p == 5
        assert elem.valuation_offset == 0
        assert elem.unit_digits == (1, 2, 3)
        assert elem.working_precision == 8

    def test_repr_includes_prime_valuation_precision(self):
        elem = QpElement(p=7, valuation_offset=2, unit_digits=(3, 4), working_precision=16)
        r = repr(elem)
        assert "p=7" in r
        assert "v=2" in r
        assert "N=16" in r


class TestQpElementImmutability:
    def test_frozen_dataclass_cannot_mutate(self):
        elem = QpElement(p=5, valuation_offset=0, unit_digits=(1,), working_precision=4)
        with pytest.raises(AttributeError):
            elem.valuation_offset = 1

    def test_equal_elements_hash_equal(self):
        a = QpElement(p=5, valuation_offset=0, unit_digits=(1, 2), working_precision=8)
        b = QpElement(p=5, valuation_offset=0, unit_digits=(1, 2), working_precision=8)
        assert a == b
        assert hash(a) == hash(b)


class TestQpElementValidation:
    def test_rejects_non_prime_p(self):
        with pytest.raises(ValueError):
            QpElement(p=4, valuation_offset=0, unit_digits=(1,), working_precision=4)

    def test_rejects_digit_out_of_range(self):
        with pytest.raises(ValueError):
            QpElement(p=5, valuation_offset=0, unit_digits=(5,), working_precision=4)

    def test_rejects_zero_with_non_empty_digits(self):
        with pytest.raises(ValueError):
            QpElement(p=5, valuation_offset=math.inf, unit_digits=(1,), working_precision=4)

    def test_rejects_leading_zero_digit(self):
        with pytest.raises(ValueError):
            QpElement(p=5, valuation_offset=0, unit_digits=(0, 1), working_precision=4)

    def test_rejects_too_many_digits(self):
        with pytest.raises(ValueError):
            QpElement(p=5, valuation_offset=0, unit_digits=(1, 2, 3), working_precision=2)

    def test_rejects_non_zero_with_empty_digits(self):
        with pytest.raises(ValueError):
            QpElement(p=5, valuation_offset=0, unit_digits=(), working_precision=4)

    def test_allows_negative_valuation_offset(self):
        # Negative valuation represents a denominator; the representation still holds.
        elem = QpElement(p=5, valuation_offset=-2, unit_digits=(3,), working_precision=8)
        assert elem.valuation_offset == -2


class TestQpElementPrecisionPolicy:
    def test_default_working_precision_constant_exists(self):
        from backend.fieldx_kernel.qp_arithmetic import DEFAULT_WORKING_PRECISION

        assert isinstance(DEFAULT_WORKING_PRECISION, int)
        assert DEFAULT_WORKING_PRECISION > 0

    def test_zero_uses_default_precision(self):
        zero = QpElement.zero(7)
        assert zero.working_precision == 16  # DEFAULT_WORKING_PRECISION

    def test_per_entry_precision_is_allowed(self):
        a = QpElement(p=5, valuation_offset=0, unit_digits=(1,), working_precision=8)
        b = QpElement(p=5, valuation_offset=0, unit_digits=(1,), working_precision=32)
        assert a.working_precision != b.working_precision

    def test_precision_policy_documented_in_module_docstring(self):
        import backend.fieldx_kernel.qp_arithmetic as mod

        assert "Precision policy" in mod.__doc__
        assert "cross-precision" in mod.__doc__.lower()

    def test_to_precision_extends_with_zero_padding(self):
        a = QpElement.from_int(5, 123, 4)
        extended = a.to_precision(8)
        assert extended.working_precision == 8
        assert extended.unit_digits[:4] == a.unit_digits
        assert extended.unit_digits[4:] == (0, 0, 0, 0)
        # The lower-precision residue is preserved.
        assert _digits_to_int(extended.unit_digits, 5) % (5**4) == _digits_to_int(
            a.unit_digits, 5
        )

    def test_to_precision_truncates_higher_order_digits(self):
        import warnings

        from backend.fieldx_kernel.qp_arithmetic import PrecisionLossWarning

        a = QpElement.from_int(5, 123, 8)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", PrecisionLossWarning)
            truncated = a.to_precision(4)
        assert truncated.working_precision == 4
        assert truncated.unit_digits == a.unit_digits[:4]

    def test_to_precision_truncation_emits_warning(self):
        import warnings
        from backend.fieldx_kernel.qp_arithmetic import PrecisionLossWarning

        a = QpElement.from_int(5, 123, 8)
        with pytest.warns(PrecisionLossWarning):
            a.to_precision(4)

    def test_zero_to_precision_preserves_value(self):
        zero = QpElement.zero(5, 4)
        extended = zero.to_precision(8)
        truncated = zero.to_precision(2)
        assert extended.is_zero and extended.working_precision == 8
        assert truncated.is_zero and truncated.working_precision == 2

    def test_promote_returns_both_at_higher_precision(self):
        a = QpElement.from_int(5, 1, 4)
        b = QpElement.from_int(5, 2, 8)
        pa, pb = QpElement.promote(a, b)
        assert pa.working_precision == 8
        assert pb.working_precision == 8
        assert pa == b - b + a  # same value

    def test_binary_operations_automatically_promote_precision(self):
        a = QpElement.from_int(5, 1, 4)
        b = QpElement.from_int(5, 2, 8)
        s = a + b
        p = a * b
        assert s.working_precision == 8
        assert p.working_precision == 8

    def test_to_precision_target_out_of_range_raises(self):
        a = QpElement.from_int(5, 1, 4)
        with pytest.raises(ValueError):
            a.to_precision(0)
        with pytest.raises(ValueError):
            a.to_precision(1025)


# -----------------------------------------------------------------------------
# Property-test harness fixtures (DS-REVIEW-192 P0-03)
# -----------------------------------------------------------------------------

_SMALL_PRIMES = [2, 3, 5, 7, 11, 13, 17]
_RANDOM = random.Random(20260624)


def random_prime() -> int:
    """Return a deterministic prime from the small-prime set."""
    return _RANDOM.choice(_SMALL_PRIMES)


def random_qpelement(
    p: int | None = None,
    N: int | None = None,
    *,
    allow_negative: bool = True,
) -> QpElement:
    """Return a deterministic random QpElement for property tests.

    The returned element always stores ``N`` unit digits so that the working
    precision is meaningful in field-operation property tests.
    """
    p = p or random_prime()
    N = N or _RANDOM.choice([4, 8, 16])
    valuation = _RANDOM.randint(-3 if allow_negative else 0, N)
    if valuation == math.inf or valuation > N:
        return QpElement.zero(p, N)
    digits = [_RANDOM.randint(0, p - 1) for _ in range(N)]
    if digits[0] == 0:
        digits[0] = _RANDOM.randint(1, p - 1)
    return QpElement(p, valuation, tuple(digits), N)


def random_rational() -> Fraction:
    """Return a deterministic small rational for round-trip tests."""
    num = _RANDOM.randint(-1000, 1000)
    den = _RANDOM.randint(1, 1000)
    return Fraction(num, den)


# -----------------------------------------------------------------------------
# Field axiom tests (enabled by DS-REVIEW-192 P1-03)
# -----------------------------------------------------------------------------

def _align_to_common_precision(a: QpElement, b: QpElement) -> tuple[QpElement, QpElement]:
    """Return ``a`` and ``b`` aligned to a common working precision.

    Normalisation can reduce the working precision of an operation result (for
    example, when factors of ``p`` are extracted from the unit).  Since p-adic
    arithmetic is only associative up to the minimum precision of the two
    expressions, the comparison is performed at the lower of the two working
    precisions.
    """
    target = min(a.working_precision, b.working_precision)
    return _truncate_precision(a, target), _truncate_precision(b, target)


def _truncate_precision(x: QpElement, target: int) -> QpElement:
    """Return ``x`` truncated to ``target`` unit digits.

    If ``target`` is larger than the current working precision, the element is
    extended with zero low-significance digits instead.
    """
    if x.working_precision == target:
        return x
    if x.is_zero:
        return QpElement.zero(x.p, target)
    if x.working_precision > target:
        digits = x.unit_digits[:target]
        # After truncation the least-significant digit must remain a unit.
        if digits and digits[0] == 0:
            # This can only happen when the true value is divisible by a higher
            # power of p than the truncated precision can capture; represent it
            # as zero at the target precision.
            return QpElement.zero(x.p, target)
        return QpElement(
            x.p, x.valuation_offset, digits, target
        )
    return x.to_precision(target)


class TestQpElementFieldOperations:
    def test_addition_commutativity(self):
        for _ in range(500):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            b = random_qpelement(p, N)
            left, right = _align_to_common_precision(a + b, b + a)
            assert left == right

    def test_addition_associativity(self):
        for _ in range(500):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            b = random_qpelement(p, N)
            c = random_qpelement(p, N)
            left, right = _align_to_common_precision((a + b) + c, a + (b + c))
            assert left == right

    def test_multiplication_commutativity(self):
        for _ in range(500):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            b = random_qpelement(p, N)
            left, right = _align_to_common_precision(a * b, b * a)
            assert left == right

    def test_multiplication_associativity(self):
        for _ in range(500):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            b = random_qpelement(p, N)
            c = random_qpelement(p, N)
            left, right = _align_to_common_precision((a * b) * c, a * (b * c))
            assert left == right

    def test_distributivity(self):
        for _ in range(500):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            b = random_qpelement(p, N)
            c = random_qpelement(p, N)
            left, right = _align_to_common_precision(
                a * (b + c), a * b + a * c
            )
            assert left == right

    def test_additive_identity(self):
        for _ in range(200):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            zero = QpElement.zero(p, N)
            left, right = _align_to_common_precision(a + zero, a)
            assert left == right

    def test_multiplicative_identity(self):
        for _ in range(200):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            one = QpElement.from_int(p, 1, N)
            left, right = _align_to_common_precision(a * one, a)
            assert left == right

    def test_additive_inverse(self):
        for _ in range(200):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            zero = QpElement.zero(p, N)
            left, right = _align_to_common_precision(a + (-a), zero)
            assert left == right

    def test_multiplicative_inverse(self):
        for _ in range(200):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            if a.is_zero:
                continue
            one = QpElement.from_int(p, 1, N)
            left, right = _align_to_common_precision(a * a.inverse(), one)
            assert left == right

    def test_subtraction_is_addition_of_negative(self):
        for _ in range(200):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            b = random_qpelement(p, N)
            left, right = _align_to_common_precision(a - b, a + (-b))
            assert left == right

    def test_division_is_multiplication_by_inverse(self):
        for _ in range(200):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            a = random_qpelement(p, N)
            b = random_qpelement(p, N)
            if b.is_zero:
                continue
            left, right = _align_to_common_precision(a / b, a * b.inverse())
            assert left == right

    def test_precision_promotion_for_addition(self):
        a = QpElement.from_int(5, 1, 4)
        b = QpElement.from_int(5, 1, 8)
        s = a + b
        assert s.working_precision == 8

    def test_precision_promotion_for_multiplication(self):
        a = QpElement.from_int(5, 2, 4)
        b = QpElement.from_int(5, 3, 10)
        p = a * b
        assert p.working_precision == 10

    def test_division_by_zero_raises(self):
        a = QpElement.from_int(5, 7, 8)
        zero = QpElement.zero(5, 8)
        with pytest.raises(ZeroDivisionError):
            a / zero

    def test_inverse_of_zero_raises(self):
        zero = QpElement.zero(5, 8)
        with pytest.raises(ZeroDivisionError):
            zero.inverse()


class TestQpElementIntegerEmbedding:
    def test_from_int_zero(self):
        zero = QpElement.from_int(5, 0, 8)
        assert zero.is_zero
        assert zero.working_precision == 8

    def test_from_int_positive(self):
        elem = QpElement.from_int(5, 123, 8)
        assert elem.p == 5
        assert elem.valuation_offset == 0
        assert elem.to_int() == 123

    def test_from_int_negative(self):
        elem = QpElement.from_int(5, -123, 8)
        assert elem.to_int() == -123

    def test_from_int_with_valuation(self):
        # 250 = 2 * 5^3, so v_5 = 3.
        elem = QpElement.from_int(5, 250, 8)
        assert elem.valuation_offset == 3
        assert elem.to_int() == 250

    def test_to_int_raises_on_negative_valuation(self):
        elem = QpElement.from_rational(5, 1, 25, 8)  # 1/25 = 5^{-2}
        assert elem.valuation_offset == -2
        with pytest.raises(ValueError):
            elem.to_int()

    def test_integer_round_trip_random_small_integers(self):
        primes = [2, 3, 5, 7, 11, 13, 17]
        for _ in range(1000):
            p = random_prime()
            N = _RANDOM.choice([8, 12, 16])
            # Keep within the symmetric representative range.
            bound = (p ** N) // 2 - 1
            n = _RANDOM.randint(-bound, bound)
            elem = QpElement.from_int(p, n, N)
            assert elem.to_int() == n


class TestQpElementRationalEmbedding:
    def test_from_rational_coprime_denominator(self):
        elem = QpElement.from_rational(5, 2, 3, 16)
        assert elem.p == 5
        assert elem.valuation_offset == 0
        assert elem.to_rational() == Fraction(2, 3)

    def test_from_rational_negative_numerator(self):
        elem = QpElement.from_rational(5, -2, 3, 16)
        assert elem.to_rational() == Fraction(-2, 3)

    def test_from_rational_denominator_divisible_by_p(self):
        elem = QpElement.from_rational(5, 1, 25, 16)
        assert elem.valuation_offset == -2
        assert elem.to_rational() == Fraction(1, 25)

    def test_from_rational_numerator_divisible_by_p(self):
        elem = QpElement.from_rational(5, 25, 1, 16)
        assert elem.valuation_offset == 2
        assert elem.to_rational() == Fraction(25, 1)

    def test_from_rational_zero_numerator(self):
        elem = QpElement.from_rational(5, 0, 7, 8)
        assert elem.is_zero

    def test_from_rational_zero_denominator_raises(self):
        with pytest.raises(ZeroDivisionError):
            QpElement.from_rational(5, 1, 0, 8)

    def test_rational_round_trip_random_small_rationals(self):
        primes = [2, 3, 5, 7, 11, 13]
        for _ in range(1000):
            p = _RANDOM.choice(primes)
            N = _RANDOM.choice([16, 20, 24])
            # Rational reconstruction recovers a/b only when |a| and b are
            # both < sqrt(p^N). Choose numerators/denominators accordingly.
            bound = int((p**N) ** 0.5) // 2
            a = _RANDOM.randint(-bound, bound)
            while True:
                b = _RANDOM.randint(1, bound)
                if b % p != 0:
                    break
            # Include some p-power denominators.
            if _RANDOM.random() < 0.3:
                b *= p ** _RANDOM.randint(1, 3)
            expected = Fraction(a, b)
            elem = QpElement.from_rational(p, a, b, N)
            assert elem.to_rational() == expected


# -----------------------------------------------------------------------------
# Phase 1 kill gate: 100,000 rational round-trips (DS-REVIEW-192 P1-07)
# -----------------------------------------------------------------------------

PRIMES_FOR_100K = [2, 3, 5, 7, 11, 13, 17]
PRECISIONS_FOR_100K = [8, 16, 24]


def test_100k_rational_round_trip():
    """Kill-gate test: 100,000 rational embeddings must recover exactly.

    Rational reconstruction is guaranteed to succeed when |a| and b are both
    below sqrt(p^N).  The test chooses bounds accordingly so the expected
    failure count is zero.
    """
    rng = random.Random(20260625)
    failures: list[tuple[int, int, int, int, Fraction, Fraction]] = []

    for _ in range(100_000):
        p = rng.choice(PRIMES_FOR_100K)
        N = rng.choice(PRECISIONS_FOR_100K)
        bound = int((p**N) ** 0.5) // 2
        if bound < 1:
            bound = 1
        a = rng.randint(-bound, bound)
        while True:
            b = rng.randint(1, bound)
            if b % p != 0:
                break
        if rng.random() < 0.3:
            b *= p ** rng.randint(1, 3)
        expected = Fraction(a, b)
        elem = QpElement.from_rational(p, a, b, N)
        recovered = elem.to_rational()
        if recovered != expected:
            failures.append((p, N, a, b, expected, recovered))

    assert not failures, f"{len(failures)} rational round-trip failures: {failures[:5]}"


# Remaining field-axiom skeleton tests (to be enabled as operations land)
