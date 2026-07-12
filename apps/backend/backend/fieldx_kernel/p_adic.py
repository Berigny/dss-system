"""Finite-precision p-adic integer arithmetic and prime-lattice state types.

This module provides the mathematical primitives needed by the discrete DSS
substrate.  All operations are implemented on the finite-precision ring
``Z / p^N Z``; the types are immutable and hashable so they can be used as
keys and passed around safely.

Code-level claims (see Epic 22 claim register):

- CLAIM(definite): ``PAdicInteger`` represents an element of the finite-precision
  ring ``Z / p^N Z`` and is implemented as a thin wrapper around ``QpElement``.
  EVIDENCE: DSS-172, ``backend/fieldx_kernel/p_adic.py``, DS-REVIEW-192 P1-05

- CLAIM(definite): ``valuation()``, ``norm()`` and ``distance()`` follow the
  standard p-adic definitions restricted to the residue class.
  EVIDENCE: DSS-172, tests in ``backend/tests/test_p_adic.py``

- CLAIM(definite): ``v_p(0)`` returns ``math.inf`` because the zero p-adic
  number has infinite valuation by convention.
  EVIDENCE: DSS-172, ``backend/tests/test_p_adic.py``

- CLAIM(definite): ``PrimeLatticeState`` represents a positive integer by its
  prime-exponent vector; uniqueness follows from the fundamental theorem of
  arithmetic.
  EVIDENCE: DSS-173, ``backend/fieldx_kernel/p_adic.py``
"""

from __future__ import annotations

import math
from collections import Counter
from functools import lru_cache
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from backend.fieldx_kernel.qp_arithmetic import QpElement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_prime(n: int) -> bool:
    """Return True if ``n`` is a prime number."""
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    limit = int(math.sqrt(n)) + 1
    for d in range(3, limit, 2):
        if n % d == 0:
            return False
    return True


def integer_valuation(p: int, n: int) -> int | float:
    """Return the p-adic valuation ``v_p(n)`` for an integer ``n``.

    For ``n == 0`` this returns ``math.inf``; for non-zero ``n`` it returns the
    exponent of the highest power of ``p`` dividing ``n``.
    """
    if n == 0:
        return math.inf
    v = 0
    nn = abs(int(n))
    while nn % p == 0:
        nn //= p
        v += 1
    return v


def p_adic_distance_between_integers(a: int, b: int, p: int) -> float:
    """Return the genuine p-adic ultrametric distance ``|a - b|_p``.

    The result is ``0`` when ``a == b``, ``1`` when the difference is not
    divisible by ``p``, and ``p**(-v)`` when ``v = v_p(a - b)``.
    """
    diff = int(a) - int(b)
    if diff == 0:
        return 0.0
    v = integer_valuation(p, diff)
    if v == math.inf:
        return 0.0
    return float(p) ** (-int(v))


def _factor_fingerprint(
    factors: Sequence[Mapping[Any, Any]],
) -> tuple[tuple[int, int], ...]:
    """Return a hashable, canonical fingerprint of a factor list."""
    items: list[tuple[int, int]] = []
    for f in factors:
        try:
            prime = int(float(f.get("prime")))
            exp = int(float(f.get("delta", 1.0)))
        except Exception:
            continue
        if prime < 2 or exp < 0:
            continue
        items.append((prime, exp))
    return tuple(sorted(items))


@lru_cache(maxsize=4096)
def _cached_factor_int_value(fingerprint: tuple[tuple[int, int], ...]) -> int:
    """Convert a canonical factor fingerprint into an integer product."""
    total = 1
    for prime, exp in fingerprint:
        total *= prime**exp
    return total


def _factor_int_value(factors: Sequence[Mapping[Any, Any]]) -> int:
    """Convert a factor list ``[{prime, delta}, ...]`` into an integer product."""
    return _cached_factor_int_value(_factor_fingerprint(factors))


def p_adic_distance_for_factors(
    a_factors: Sequence[Mapping[Any, Any]],
    b_factors: Sequence[Mapping[Any, Any]],
    *,
    metric_prime: int = 5,
    min_overlap: int = 1,
) -> tuple[float, int]:
    """Genuine p-adic distance between two factor-encoded integers.

    ``delta`` in each factor is treated as the exponent of the corresponding
    ``prime``.  The metric prime for the distance is ``metric_prime``.
    """
    if not a_factors or not b_factors:
        return float("inf"), 0

    a_fp = _factor_fingerprint(a_factors)
    b_fp = _factor_fingerprint(b_factors)

    a_primes = {prime for prime, _ in a_fp}
    b_primes = {prime for prime, _ in b_fp}

    overlap = len(a_primes & b_primes)
    if overlap < min_overlap:
        return float("inf"), overlap

    a_value = _cached_factor_int_value(a_fp)
    b_value = _cached_factor_int_value(b_fp)
    distance = p_adic_distance_between_integers(a_value, b_value, metric_prime)
    return distance, overlap


def _digits_from_value(p: int, N: int, value: int) -> Tuple[int, ...]:
    """Return the least-significant-first digit vector for ``value`` mod ``p**N``."""
    digits: list[int] = []
    v = int(value) % (p**N)
    for _ in range(N):
        digits.append(v % p)
        v //= p
    return tuple(digits)


# ---------------------------------------------------------------------------
# PAdicInteger
# ---------------------------------------------------------------------------


class PAdicInteger:
    """A finite-precision p-adic integer in ``Z / p^N Z``.

    Digits are stored least-significant first, so ``digits[0]`` is the
    coefficient of ``p**0``, ``digits[1]`` the coefficient of ``p**1``, and so
    on.  The type is immutable and hashable.

    Attributes:
        p: prime base.
        N: precision (positive integer).
        digits: normalized digit tuple of length ``N``.
    """

    __slots__ = ("_p", "_N", "_digits", "_hash", "_qp")

    def __init__(self, p: int, N: int, digits: Sequence[int]) -> None:
        if not isinstance(p, int) or p < 2 or not _is_prime(p):
            raise ValueError(f"p must be a prime >= 2, got {p!r}")
        if not isinstance(N, int) or N < 1:
            raise ValueError(f"N must be a positive integer, got {N!r}")

        raw = tuple(int(d) % p for d in digits)
        if len(raw) < N:
            raw = raw + (0,) * (N - len(raw))
        elif len(raw) > N:
            raw = raw[:N]

        self._p = p
        self._N = N
        self._digits = raw
        self._hash = hash((p, N, raw))
        # Backing genuine-Qp element; ensures PAdicInteger shares the same
        # arithmetic path as QpElement under DS-REVIEW-192.
        self._qp = QpElement.from_int(p, self._value(), N)

    @classmethod
    def from_int(cls, p: int, n: int, N: int) -> "PAdicInteger":
        """Construct a ``PAdicInteger`` from a Python integer residue.

        The integer ``n`` is reduced modulo ``p**N`` before digit extraction,
        so negative values are handled correctly for the residue class.
        """
        if not isinstance(p, int) or p < 2 or not _is_prime(p):
            raise ValueError(f"p must be a prime >= 2, got {p!r}")
        if not isinstance(N, int) or N < 1:
            raise ValueError(f"N must be a positive integer, got {N!r}")
        return cls(p, N, _digits_from_value(p, N, n))

    @property
    def p(self) -> int:
        return self._p

    @property
    def N(self) -> int:
        return self._N

    @property
    def digits(self) -> Tuple[int, ...]:
        return self._digits

    def _value(self) -> int:
        """Return the canonical integer representative in ``[0, p**N)``."""
        total = 0
        power = 1
        for d in self._digits:
            total += d * power
            power *= self._p
        return total

    def value_mod(self, k: int) -> int:
        """Return the integer value modulo ``p**k``.

        ``k`` must satisfy ``1 <= k <= N``.
        """
        if not isinstance(k, int) or k < 1 or k > self._N:
            raise ValueError(f"k must be in [1, {self._N}], got {k!r}")
        return self._value() % (self._p**k)

    def valuation(self) -> int | float:
        """Return the p-adic valuation ``v_p(self)``.

        For the zero element this returns ``math.inf``.  For a non-zero
        residue it returns the number of trailing zero digits (the exponent of
        the highest power of ``p`` dividing the representative).
        """
        v = self._qp.valuation_offset
        if isinstance(v, int):
            return v
        return math.inf

    def norm(self) -> float:
        """Return the p-adic norm ``|self|_p``.

        The zero element has norm ``0``; a unit has norm ``1``; an element
        divisible by ``p**k`` has norm ``p**(-k)``.
        """
        v = self.valuation()
        if v == math.inf:
            return 0.0
        return float(self._p) ** (-int(v))

    def distance(self, other: "PAdicInteger") -> float:
        """Return the p-adic ultrametric distance ``|self - other|_p``."""
        return (self - other).norm()

    def _ensure_same_ring(self, other: "PAdicInteger") -> None:
        if not isinstance(other, PAdicInteger):
            raise TypeError(f"cannot combine PAdicInteger with {type(other).__name__}")
        if self._p != other._p or self._N != other._N:
            raise ValueError(
                "p-adic operations require the same prime and precision: "
                f"({self._p}, {self._N}) vs ({other._p}, {other._N})"
            )

    def __add__(self, other: "PAdicInteger") -> "PAdicInteger":
        self._ensure_same_ring(other)
        result = self._qp + other._qp
        return PAdicInteger.from_int(self._p, result.to_int() % (self._p**self._N), self._N)

    def __sub__(self, other: "PAdicInteger") -> "PAdicInteger":
        self._ensure_same_ring(other)
        result = self._qp - other._qp
        return PAdicInteger.from_int(self._p, result.to_int() % (self._p**self._N), self._N)

    def __mul__(self, other: "PAdicInteger") -> "PAdicInteger":
        self._ensure_same_ring(other)
        result = self._qp * other._qp
        return PAdicInteger.from_int(self._p, result.to_int() % (self._p**self._N), self._N)

    def __neg__(self) -> "PAdicInteger":
        result = -self._qp
        return PAdicInteger.from_int(self._p, result.to_int() % (self._p**self._N), self._N)

    def digit_rotation(self, steps: int = 1) -> "PAdicInteger":
        """Rotate the digit vector least-significant-first by ``steps`` places.

        A positive ``steps`` moves each digit toward higher powers of ``p`` and
        wraps digits that fall off the top back to the least-significant
        positions.  The result is still an element of ``Z / p^N Z``.
        """
        steps = int(steps) % self._N
        if steps == 0:
            return self
        return PAdicInteger(self._p, self._N, self._digits[-steps:] + self._digits[:-steps])

    def orientation_reversal(self) -> "PAdicInteger":
        """Return the additive inverse (orientation reversal) in the residue ring."""
        return -self

    def block_rotation(self, block_size: int, steps: int = 1) -> "PAdicInteger":
        """Split the digit vector into blocks of length ``block_size`` and rotate them.

        The last block may be shorter if ``N`` is not a multiple of
        ``block_size``.  Rotation is applied to the list of blocks and the
        result is flattened back into a digit vector of length ``N``.
        """
        if block_size < 1:
            raise ValueError(f"block_size must be positive, got {block_size!r}")
        blocks = [self._digits[i : i + block_size] for i in range(0, self._N, block_size)]
        if not blocks:
            return self
        steps = int(steps) % len(blocks)
        if steps == 0:
            return self
        rotated = blocks[-steps:] + blocks[:-steps]
        flat = tuple(d for block in rotated for d in block)[: self._N]
        return PAdicInteger(self._p, self._N, flat)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PAdicInteger):
            return NotImplemented
        return (
            self._p == other._p
            and self._N == other._N
            and self._digits == other._digits
        )

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"PAdicInteger(p={self._p}, N={self._N}, digits={self._digits})"

    def as_qp_element(self) -> QpElement:
        """Return the genuine ``QpElement`` backing this finite-ring element."""
        return self._qp


# ---------------------------------------------------------------------------
# PrimeLatticeState
# ---------------------------------------------------------------------------


class PrimeLatticeState:
    """A positive integer represented by its prime-exponent vector.

    The mapping from concepts to primes is engineered by the caller; the
    representation uniqueness itself is number-theoretic (fundamental theorem
    of arithmetic).

    The lattice order is by divisibility: ``a <= b`` iff ``a`` divides ``b``.
    ``join`` is least common multiple (coordinate-wise max) and ``meet`` is
    greatest common divisor (coordinate-wise min).
    """

    __slots__ = ("_exponents", "_hash")

    def __init__(self, exponents: Mapping[int, int]) -> None:
        """Construct from a prime -> exponent mapping.

        Zero and negative exponents are ignored/raised respectively.
        """
        cleaned: Dict[int, int] = {}
        for prime, exp in exponents.items():
            p = int(prime)
            e = int(exp)
            if e < 0:
                raise ValueError(f"exponents must be non-negative, got {e} for prime {p}")
            if e == 0:
                continue
            if not _is_prime(p):
                raise ValueError(f"keys must be prime numbers, got {p}")
            cleaned[p] = cleaned.get(p, 0) + e
        self._exponents = cleaned
        self._hash = hash(tuple(sorted(cleaned.items())))

    @classmethod
    def from_primes(cls, primes: Iterable[int]) -> "PrimeLatticeState":
        """Construct a lattice state from a list of assigned primes."""
        return cls(Counter(int(p) for p in primes))

    @classmethod
    def from_int(cls, n: int) -> "PrimeLatticeState":
        """Construct a lattice state by factoring a positive integer.

        This is intended for modest integers (validation, tests, and small
        coordinates).  Avoid using it on cryptographic-size values.
        """
        if not isinstance(n, int) or n < 1:
            raise ValueError(f"n must be a positive integer, got {n!r}")
        if n == 1:
            return cls({})

        exponents: Counter = Counter()
        remaining = n
        d = 2
        while d * d <= remaining:
            while remaining % d == 0:
                exponents[d] += 1
                remaining //= d
            d += 1 if d == 2 else 2  # 2, 3, 5, 7, ...
        if remaining > 1:
            exponents[remaining] += 1
        return cls(exponents)

    @property
    def exponents(self) -> Mapping[int, int]:
        return self._exponents

    def valuation(self, prime: int) -> int:
        """Return the exponent of ``prime`` in this state."""
        if not _is_prime(prime):
            raise ValueError(f"prime must be a prime number, got {prime}")
        return self._exponents.get(prime, 0)

    def contains(self, prime: int, tau: int = 1) -> bool:
        """Test ``v_prime(state) >= tau`` (membership above a divisibility threshold)."""
        return self.valuation(prime) >= tau

    def join(self, other: "PrimeLatticeState") -> "PrimeLatticeState":
        """Return the least upper bound (lcm) of two lattice states."""
        result = Counter(self._exponents)
        for p, e in other._exponents.items():
            result[p] = max(result.get(p, 0), e)
        return PrimeLatticeState(result)

    def meet(self, other: "PrimeLatticeState") -> "PrimeLatticeState":
        """Return the greatest lower bound (gcd) of two lattice states."""
        result: Counter = Counter()
        for p, e in self._exponents.items():
            other_e = other._exponents.get(p, 0)
            if other_e > 0:
                result[p] = min(e, other_e)
        return PrimeLatticeState(result)

    def is_orthogonal_to(self, other: "PrimeLatticeState") -> bool:
        """Return True iff the two states are coprime (no shared prime factors)."""
        smaller, larger = (
            (self._exponents, other._exponents)
            if len(self._exponents) <= len(other._exponents)
            else (other._exponents, self._exponents)
        )
        return not any(p in larger for p in smaller)

    def delta(self, other: "PrimeLatticeState") -> dict[int, int]:
        """Return exponent changes ``prime -> Δ exponent`` from ``other`` to ``self``.

        Positive values mean the prime was added or strengthened in ``self``;
        negative values mean it was removed or weakened.
        """
        changes: dict[int, int] = {}
        for prime in set(self._exponents) | set(other._exponents):
            diff = self._exponents.get(prime, 0) - other._exponents.get(prime, 0)
            if diff != 0:
                changes[prime] = diff
        return changes

    def value(self) -> int:
        """Return the integer product ``prod(p**e)``.

        Avoid calling this on states with huge exponents in production.
        """
        total = 1
        for p, e in self._exponents.items():
            total *= p**e
        return total

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PrimeLatticeState):
            return NotImplemented
        return self._exponents == other._exponents

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"PrimeLatticeState({dict(sorted(self._exponents.items()))})"
