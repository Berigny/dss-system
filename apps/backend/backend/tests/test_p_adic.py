from __future__ import annotations

import math
import random

import pytest

from backend.fieldx_kernel.p_adic import PAdicInteger, PrimeLatticeState
from backend.fieldx_kernel.qp_arithmetic import QpElement


# -----------------------------------------------------------------------------
# PAdicInteger
# -----------------------------------------------------------------------------


def test_padic_from_int_zero_has_infinite_valuation() -> None:
    zero = PAdicInteger.from_int(p=3, n=0, N=5)
    assert zero.valuation() == math.inf
    assert zero.norm() == 0.0


def test_padic_from_int_negative() -> None:
    # -1 mod 3**4 == 80, whose base-3 digits are (2, 2, 2, 2).
    x = PAdicInteger.from_int(p=3, n=-1, N=4)
    assert x.digits == (2, 2, 2, 2)
    assert x._value() == 80


def test_padic_value_mod() -> None:
    x = PAdicInteger.from_int(p=5, n=123, N=6)
    assert x.value_mod(1) == (123 % 5)
    assert x.value_mod(2) == (123 % 25)
    assert x.value_mod(6) == (123 % 5**6)

    with pytest.raises(ValueError):
        x.value_mod(0)
    with pytest.raises(ValueError):
        x.value_mod(x.N + 1)


def test_padic_valuation() -> None:
    # 18 = 2 * 3**2 in base 3 -> digits (0, 2, 2) -> v_3 = 2
    x = PAdicInteger.from_int(p=3, n=18, N=5)
    assert x.valuation() == 2
    assert x.norm() == pytest.approx(3**-2)

    unit = PAdicInteger.from_int(p=3, n=5, N=5)
    assert unit.valuation() == 0
    assert unit.norm() == 1.0


def test_padic_arithmetic_mod_precision() -> None:
    p, N = 5, 4
    a = PAdicInteger.from_int(p, 123, N)
    b = PAdicInteger.from_int(p, 456, N)

    assert (a + b)._value() == (123 + 456) % (p**N)
    assert (a - b)._value() == (123 - 456) % (p**N)
    assert (a * b)._value() == (123 * 456) % (p**N)


def test_padic_integer_is_qp_element_wrapper() -> None:
    x = PAdicInteger.from_int(p=5, n=123, N=8)
    assert isinstance(x.as_qp_element(), QpElement)
    assert x.as_qp_element().p == 5
    assert x.as_qp_element().to_int() == 123


def test_padic_integer_wrapper_arithmetic_matches_direct() -> None:
    p, N = 7, 6
    a = PAdicInteger.from_int(p, 50, N)
    b = PAdicInteger.from_int(p, 19, N)

    # Arithmetic on the wrapper must agree with arithmetic on the backing QpElement.
    wrapped = (a + b).as_qp_element()
    direct = a.as_qp_element() + b.as_qp_element()
    assert wrapped.to_int() % (p**N) == direct.to_int() % (p**N)


def test_padic_addition_inverse() -> None:
    a = PAdicInteger.from_int(p=7, n=42, N=5)
    neg_a = -a
    assert (a + neg_a)._value() == 0


def test_padic_equality_and_hash() -> None:
    a = PAdicInteger.from_int(p=3, n=10, N=4)
    b = PAdicInteger(p=3, N=4, digits=a.digits)
    assert a == b
    assert hash(a) == hash(b)


def test_padic_distance_symmetry_and_identity() -> None:
    a = PAdicInteger.from_int(p=3, n=10, N=5)
    b = PAdicInteger.from_int(p=3, n=10, N=5)
    c = PAdicInteger.from_int(p=3, n=11, N=5)

    assert a.distance(b) == 0.0
    assert a.distance(c) == c.distance(a)
    assert a.distance(c) > 0.0


def test_padic_ultrametric_inequality_random_triples() -> None:
    """Verify d(a, c) <= max(d(a, b), d(b, c)) for random residues."""
    rng = random.Random(42)
    for p in (2, 3, 5, 7):
        for N in (4, 6, 8):
            for _ in range(50):
                a = PAdicInteger.from_int(p, rng.randint(0, p**N - 1), N)
                b = PAdicInteger.from_int(p, rng.randint(0, p**N - 1), N)
                c = PAdicInteger.from_int(p, rng.randint(0, p**N - 1), N)

                d_ab = a.distance(b)
                d_bc = b.distance(c)
                d_ac = a.distance(c)

                assert d_ac <= max(d_ab, d_bc) + 1e-15


def test_padic_strong_triangle_equality_of_max() -> None:
    """In an ultrametric, the two largest distances among three points are equal."""
    a = PAdicInteger.from_int(p=5, n=0, N=6)
    b = PAdicInteger.from_int(p=5, n=25, N=6)   # v_5 = 2
    c = PAdicInteger.from_int(p=5, n=125, N=6)  # v_5 = 3

    d_ab = a.distance(b)
    d_bc = b.distance(c)
    d_ac = a.distance(c)

    # In an ultrametric, the two largest distances among three points are equal.
    sorted_d = sorted([d_ab, d_bc, d_ac])
    assert sorted_d[1] == pytest.approx(sorted_d[2])


def test_padic_requires_same_ring() -> None:
    a = PAdicInteger.from_int(p=3, n=1, N=4)
    b = PAdicInteger.from_int(p=5, n=1, N=4)
    with pytest.raises(ValueError):
        _ = a + b

    c = PAdicInteger.from_int(p=3, n=1, N=5)
    with pytest.raises(ValueError):
        _ = a + c


# -----------------------------------------------------------------------------
# PrimeLatticeState
# -----------------------------------------------------------------------------


def test_prime_lattice_uniqueness_from_factorization() -> None:
    """Two states are equal iff their exponent vectors are equal."""
    a = PrimeLatticeState.from_primes([2, 3, 3, 5])
    b = PrimeLatticeState({2: 1, 3: 2, 5: 1})
    assert a == b
    assert a.value() == 90


def test_prime_lattice_valuation_and_contains() -> None:
    state = PrimeLatticeState.from_primes([2, 2, 3, 7])
    assert state.valuation(2) == 2
    assert state.valuation(3) == 1
    assert state.valuation(5) == 0
    assert state.contains(2, tau=2)
    assert not state.contains(2, tau=3)


def test_prime_lattice_join_meet() -> None:
    a = PrimeLatticeState({2: 2, 3: 1})
    b = PrimeLatticeState({2: 1, 3: 3, 5: 1})

    join = a.join(b)
    meet = a.meet(b)

    assert join.exponents == {2: 2, 3: 3, 5: 1}
    assert meet.exponents == {2: 1, 3: 1}

    # Verify number-theoretic interpretation.
    assert join.value() == math.lcm(a.value(), b.value())
    assert meet.value() == math.gcd(a.value(), b.value())


def test_prime_lattice_orthogonality() -> None:
    a = PrimeLatticeState.from_primes([2, 3])
    b = PrimeLatticeState.from_primes([5, 7])
    c = PrimeLatticeState.from_primes([3, 11])

    assert a.is_orthogonal_to(b)
    assert b.is_orthogonal_to(a)
    assert not a.is_orthogonal_to(c)
    assert math.gcd(a.value(), b.value()) == 1


def test_prime_lattice_lattice_laws() -> None:
    """Quick check of absorption/idempotence on small random states."""
    a = PrimeLatticeState({2: 3, 3: 1, 7: 2})
    b = PrimeLatticeState({2: 1, 5: 2})

    assert a.join(a) == a
    assert a.meet(a) == a
    assert a.join(a.meet(b)) == a
    assert a.meet(a.join(b)) == a


def test_prime_lattice_from_int() -> None:
    state = PrimeLatticeState.from_int(2 * 3**2 * 7)
    assert state.exponents == {2: 1, 3: 2, 7: 1}
    assert state.value() == 126

    assert PrimeLatticeState.from_int(1).exponents == {}
