"""Genuine p-adic field element representation and rational embedding.

This module defines `QpElement`, the mathematical p-adic field element used by the
genuine-Qp path. It is distinct from the finite-precision ``PAdicInteger`` in
``p_adic.py``: ``QpElement`` is intended to represent elements of the p-adic field
``Q_p`` (with rational embedding, negative valuation, and exact inverses) rather than
the finite ring ``Z / p^N Z``.

Precision policy
----------------

- ``DEFAULT_WORKING_PRECISION`` is the global default for new elements when no explicit
  precision is requested.
- Per-entry precision is allowed; callers may create elements with different ``N`` for
  different parts of the system.
- Cross-precision interaction is performed by promoting both operands to the higher
  precision, extending the shorter expansion with trailing zeros (higher-order
  digits). This preserves the represented value and is implemented in
  ``QpElement.to_precision`` and ``QpElement.promote`` (DS-REVIEW-192 P1-04).
- Precision reduction is allowed but must be explicit and named; silent truncation is
  not permitted.  When ``settings.QP_PRECISION_LOSS_WARNING`` is enabled,
  ``to_precision`` emits a ``PrecisionLossWarning``.

Code-level claims (see Epic 22 / Epic 23 claim registers):

- CLAIM(definite): ``QpElement`` represents a genuine ``Q_p`` field element as
  ``(p, valuation_offset, unit_digits, working_precision)``.
  EVIDENCE: runs/ds-review-192/tasks/192-P1-01-qp-element-representation.md

- CLAIM(definite): ``QpElement`` supports field operations ``+``, ``-``, ``*``, ``/``
  on elements of the same prime, promoted to the higher working precision.
  EVIDENCE: runs/ds-review-192/tasks/192-P1-03-field-operations.md

- CLAIM(definite): ``QpElement`` supports explicit precision promotion and
  truncation via ``to_precision`` and ``promote``, with precision-loss warnings
  controlled by ``settings.QP_PRECISION_LOSS_WARNING``.
  EVIDENCE: runs/ds-review-192/tasks/192-P1-04-precision-promotion.md

- CLAIM(definite): ``QpElement`` unit digits are integer coefficients in ``{0, ..., p-1}``,
  distinct from the symbolic ``DigitSymbol`` digits carried by ``QpCoordinate``.
  EVIDENCE: runs/ds-review-192/findings/06-circulation-dual-overlay-alignment.md
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from backend.config import settings as _settings
from shared_types.coord_schema import parse_bigint


class PrecisionLossWarning(UserWarning):
    """Emitted when a ``QpElement`` is truncated to a lower working precision."""



# Default precision for new QpElement instances when the caller does not specify one.
DEFAULT_WORKING_PRECISION: int = 16

# Absolute upper bound on working precision to guard against accidental memory blow-up.
# This is a sanity cap, not a mathematical limit.
MAX_WORKING_PRECISION: int = 1024


@dataclass(frozen=True, slots=True)
class QpElement:
    """A genuine p-adic field element.

    Representation
    --------------
    A non-zero element ``x`` of ``Q_p`` is stored as:

        x = p^{valuation_offset} * (d_0 + d_1 p + d_2 p^2 + ... + d_{N-1} p^{N-1})

    where ``d_0 != 0`` and each ``d_i`` is in ``{0, ..., p-1}``. ``working_precision``
    is the maximum number of unit digits ``N`` retained.

    The zero element is represented by ``valuation_offset = math.inf`` and an empty
    ``unit_digits`` tuple.

    Parameters
    ----------
    p: int
        The prime defining the p-adic metric.
    valuation_offset: int | float
        The p-adic valuation ``v_p(x)``. For the zero element this is ``math.inf``.
        Negative values are permitted and represent denominators in ``Q_p``.
    unit_digits: tuple[int, ...]
        The unit-part expansion as integer coefficients in ``{0, ..., p-1}``.
        The first digit is non-zero for non-zero elements.
    working_precision: int
        Maximum number of unit digits retained.
    """

    p: int
    valuation_offset: int | float
    unit_digits: tuple[int, ...]
    working_precision: int

    def __post_init__(self) -> None:
        # Validate invariants on the already-frozen object via object.__setattr__.
        if not isinstance(self.p, int) or self.p < 2:
            raise ValueError(f"p must be a prime >= 2, got {self.p!r}")
        if not _is_prime(self.p):
            raise ValueError(f"p must be prime, got {self.p}")
        if (
            not isinstance(self.working_precision, int)
            or self.working_precision < 0
            or self.working_precision > MAX_WORKING_PRECISION
        ):
            raise ValueError(
                f"working_precision must be an int in [0, {MAX_WORKING_PRECISION}], "
                f"got {self.working_precision!r}"
            )
        if not all(isinstance(d, int) and 0 <= d < self.p for d in self.unit_digits):
            raise ValueError(
                f"unit_digits must be ints in [0, {self.p - 1}], got {self.unit_digits}"
            )
        if len(self.unit_digits) > self.working_precision:
            raise ValueError(
                f"unit_digits length {len(self.unit_digits)} exceeds working_precision "
                f"{self.working_precision}"
            )

        is_zero = self.valuation_offset == math.inf
        if is_zero:
            if self.unit_digits:
                raise ValueError("zero element must have empty unit_digits")
            return

        if not isinstance(self.valuation_offset, int):
            raise ValueError(
                f"valuation_offset must be an int or math.inf, got {self.valuation_offset!r}"
            )
        if not self.unit_digits:
            raise ValueError(
                "non-zero element must have at least one unit digit"
            )
        if self.unit_digits[0] == 0:
            raise ValueError(
                "first unit digit must be non-zero for a non-zero element"
            )

    @property
    def is_zero(self) -> bool:
        """Return True if this is the zero element."""
        return self.valuation_offset == math.inf

    def __repr__(self) -> str:
        v = self.valuation_offset
        if v == math.inf:
            v_str = "inf"
        else:
            v_str = str(v)
        return (
            f"QpElement(p={self.p}, v={v_str}, "
            f"digits={list(self.unit_digits)}, N={self.working_precision})"
        )

    @classmethod
    def zero(cls, p: int, working_precision: int = DEFAULT_WORKING_PRECISION) -> "QpElement":
        """Return the zero element in ``Q_p`` at the given precision."""
        return cls(p, math.inf, (), working_precision)

    @classmethod
    def from_int(
        cls, p: int, value: int, working_precision: int = DEFAULT_WORKING_PRECISION
    ) -> "QpElement":
        """Embed an integer ``value`` into ``Q_p``.

        Negative integers are represented by their p-adic residue modulo
        ``p^{v_p(value) + working_precision}``. ``to_int()`` recovers the signed
        representative when the original integer is small enough relative to that
        modulus.
        """
        if value == 0:
            return cls.zero(p, working_precision)
        v = _integer_valuation(p, value)
        modulus = p ** (v + working_precision)
        residue = value % modulus
        unit = residue // (p**v)
        digits = _base_p_digits(unit, p, working_precision)
        return cls(p, v, tuple(digits), working_precision)

    @classmethod
    def from_rational(
        cls,
        p: int,
        numerator: int,
        denominator: int,
        working_precision: int = DEFAULT_WORKING_PRECISION,
    ) -> "QpElement":
        """Embed a rational number ``numerator / denominator`` into ``Q_p``.

        Handles:
        - positive and negative numerators,
        - denominators divisible by ``p`` (resulting in negative valuation),
        - denominators coprime to ``p`` (unit inverse modulo ``p^N``).
        """
        if denominator == 0:
            raise ZeroDivisionError("denominator must be non-zero")
        frac = Fraction(numerator, denominator)
        if frac == 0:
            return cls.zero(p, working_precision)
        v_num = _integer_valuation(p, frac.numerator)
        v_den = _integer_valuation(p, frac.denominator)
        v = v_num - v_den
        num_unit = frac.numerator // (p**v_num)
        den_unit = frac.denominator // (p**v_den)
        mod = p**working_precision
        inv_den = pow(den_unit, -1, mod)
        unit_residue = (num_unit * inv_den) % mod
        digits = _base_p_digits(unit_residue, p, working_precision)
        return cls(p, v, tuple(digits), working_precision)

    def to_int(self) -> int:
        """Recover the integer represented by this element.

        Raises ``ValueError`` when ``valuation_offset < 0`` (denominator present).
        Uses the symmetric representative convention so that negative integers
        whose absolute value fits in the available precision round-trip correctly.
        """
        if self.is_zero:
            return 0
        if not isinstance(self.valuation_offset, int):
            raise ValueError("valuation_offset must be an int or math.inf")
        if self.valuation_offset < 0:
            raise ValueError(
                "to_int() requires non-negative valuation; use to_rational() for denominators"
            )
        v = self.valuation_offset
        unit_int = _digits_to_int(self.unit_digits, self.p)
        value = unit_int * (self.p**v)
        modulus = self.p ** (v + len(self.unit_digits))
        if value > modulus // 2:
            value -= modulus
        return value

    def to_rational(self) -> Fraction:
        """Return the rational reconstructed from the truncated p-adic expansion.

        For sufficiently large ``working_precision`` relative to the numerator and
        denominator, this recovers the exact rational supplied to ``from_rational``.
        When the precision is too small, the result is a distinct rational that is
        congruent to the original modulo ``p^{valuation_offset + N}``.
        """
        if self.is_zero:
            return Fraction(0, 1)
        if not isinstance(self.valuation_offset, int):
            raise ValueError("valuation_offset must be an int or math.inf")
        v = self.valuation_offset
        unit_int = _digits_to_int(self.unit_digits, self.p)
        N = len(self.unit_digits)
        unit_frac = _rational_reconstruction(unit_int, self.p, N)
        if v < 0:
            return Fraction(
                unit_frac.numerator,
                unit_frac.denominator * (self.p ** (-v)),
            )
        return Fraction(unit_frac.numerator * (self.p**v), unit_frac.denominator)

    # -----------------------------------------------------------------------
    # Field operations
    # -----------------------------------------------------------------------

    def to_precision(self, target_precision: int) -> "QpElement":
        """Return a new element representing the same value at ``target_precision``.

        Increasing precision zero-pads the higher-order digits.  Decreasing
        precision truncates the higher-order digits and, if
        ``settings.QP_PRECISION_LOSS_WARNING`` is enabled, emits a
        ``PrecisionLossWarning`` so the loss is explicit and named.
        """
        if target_precision < 1 or target_precision > MAX_WORKING_PRECISION:
            raise ValueError(
                f"target_precision must be in [1, {MAX_WORKING_PRECISION}]"
            )
        if self.working_precision == target_precision:
            return self
        if self.is_zero:
            return QpElement.zero(self.p, target_precision)
        if self.working_precision > target_precision:
            # Truncate the higher-order digits.  Keep the lowest ``target_precision``
            # base-p coefficients.
            new_digits = self.unit_digits[:target_precision]
            if new_digits and new_digits[0] == 0:
                # The truncated precision cannot resolve the unit; the value is
                # zero modulo p^{valuation_offset + target_precision}.
                result = QpElement.zero(self.p, target_precision)
            else:
                result = QpElement(
                    self.p, self.valuation_offset, new_digits, target_precision
                )
            if _settings.QP_PRECISION_LOSS_WARNING:
                warnings.warn(
                    f"QpElement precision reduced from {self.working_precision} "
                    f"to {target_precision} for p={self.p}",
                    PrecisionLossWarning,
                    stacklevel=2,
                )
            return result
        # Extend precision by padding higher-order digits with zeros.
        pad = target_precision - self.working_precision
        new_digits = self.unit_digits + (0,) * pad
        return QpElement(
            self.p, self.valuation_offset, new_digits, target_precision
        )

    @classmethod
    def promote(
        cls, a: "QpElement", b: "QpElement"
    ) -> tuple["QpElement", "QpElement"]:
        """Return ``a`` and ``b`` promoted to the higher working precision.

        The shorter expansion is zero-padded on the right (higher-order digits).
        """
        return _promote(a, b)

    def __neg__(self) -> "QpElement":
        """Return the additive inverse."""
        if self.is_zero:
            return self
        unit_int = _digits_to_int(self.unit_digits, self.p)
        mod = self.p ** self.working_precision
        neg_unit = (-unit_int) % mod
        return _from_unit_int(self.p, self.valuation_offset, neg_unit, self.working_precision)

    def __add__(self, other: "QpElement") -> "QpElement":
        """Return the sum, promoted to the higher working precision."""
        if not isinstance(other, QpElement):
            return NotImplemented
        a, b = _promote(self, other)
        if a.is_zero:
            return b
        if b.is_zero:
            return a
        v_a = a.valuation_offset
        v_b = b.valuation_offset
        assert isinstance(v_a, int) and isinstance(v_b, int)
        v_min = min(v_a, v_b)
        p = a.p
        u_a = _digits_to_int(a.unit_digits, p)
        u_b = _digits_to_int(b.unit_digits, p)
        scale_a = p ** (v_a - v_min)
        scale_b = p ** (v_b - v_min)
        total = scale_a * u_a + scale_b * u_b
        return _from_unit_int(p, v_min, total, a.working_precision)

    def __sub__(self, other: "QpElement") -> "QpElement":
        """Return the difference."""
        if not isinstance(other, QpElement):
            return NotImplemented
        return self.__add__(-other)

    def __mul__(self, other: "QpElement") -> "QpElement":
        """Return the product, promoted to the higher working precision."""
        if not isinstance(other, QpElement):
            return NotImplemented
        a, b = _promote(self, other)
        if a.is_zero or b.is_zero:
            return QpElement.zero(a.p, a.working_precision)
        v_a = a.valuation_offset
        v_b = b.valuation_offset
        assert isinstance(v_a, int) and isinstance(v_b, int)
        p = a.p
        u_a = _digits_to_int(a.unit_digits, p)
        u_b = _digits_to_int(b.unit_digits, p)
        mod = p ** a.working_precision
        prod = (u_a * u_b) % mod
        return _from_unit_int(p, v_a + v_b, prod, a.working_precision)

    def inverse(self) -> "QpElement":
        """Return the multiplicative inverse."""
        if self.is_zero:
            raise ZeroDivisionError("cannot invert zero in Q_p")
        v = self.valuation_offset
        assert isinstance(v, int)
        p = self.p
        unit_int = _digits_to_int(self.unit_digits, p)
        mod = p ** self.working_precision
        inv_unit = pow(unit_int, -1, mod)
        return _from_unit_int(p, -v, inv_unit, self.working_precision)

    def __truediv__(self, other: "QpElement") -> "QpElement":
        """Return the quotient."""
        if not isinstance(other, QpElement):
            return NotImplemented
        return self.__mul__(other.inverse())

    def p_adic_norm(self) -> float:
        """Return the p-adic absolute value ``|self|_p = p^{-v_p(self)}``.

        Returns ``0.0`` for the zero element.
        """
        if self.is_zero:
            return 0.0
        v = self.valuation_offset
        assert isinstance(v, int)
        return float(self.p ** (-v))

    def value_mod(self, k: int) -> int:
        """Return the p-adic residue of this element modulo ``p^k``.

        The residue is an integer in ``[0, p^k)`` representing the element
        truncated to precision ``k``.
        """
        if k <= 0:
            raise ValueError(f"k must be positive, got {k}")
        if self.is_zero:
            return 0
        v = self.valuation_offset
        assert isinstance(v, int)
        if v >= k:
            return 0
        unit_int = _digits_to_int(self.unit_digits, self.p)
        # unit_int is defined modulo p^working_precision.  We need it modulo p^{k-v}.
        mod_unit = self.p ** (k - v)
        unit_int = unit_int % mod_unit
        return (unit_int * (self.p**v)) % (self.p**k)

    def distance(self, other: "QpElement") -> float:
        """Return the p-adic distance ``|self - other|_p``."""
        return qp_distance(self, other)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of this element."""
        return {
            "p": self.p,
            "valuation_offset": (
                "inf" if self.valuation_offset == math.inf else self.valuation_offset
            ),
            "unit_digits": list(self.unit_digits),
            "working_precision": self.working_precision,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QpElement":
        """Reconstruct a ``QpElement`` from ``as_dict`` output."""
        v = payload["valuation_offset"]
        if v == "inf":
            v = math.inf
        else:
            v = parse_bigint(v)
        return cls(
            p=parse_bigint(payload["p"]),
            valuation_offset=v,
            unit_digits=tuple(parse_bigint(d) for d in payload["unit_digits"]),
            working_precision=parse_bigint(payload["working_precision"]),
        )


def qp_distance(a: QpElement, b: QpElement) -> float:
    """Return the p-adic distance ``|a - b|_p = p^{-v_p(a-b)}``.

    Returns ``0.0`` when ``a`` and ``b`` are equal within their shared working
    precision.  Raises ``ValueError`` when the primes differ.
    """
    if a.p != b.p:
        raise ValueError(f"qp_distance requires the same prime, got {a.p} and {b.p}")
    a, b = _promote(a, b)
    diff = a - b
    if diff.is_zero:
        return 0.0
    v = diff.valuation_offset
    assert isinstance(v, int)
    return float(a.p ** (-v))


def qp_score(distance: float, p: int, N: int) -> float:
    """Map a p-adic distance to a retrieval score in ``[0, 1]``.

    The mapping is monotonically decreasing with distance:

    - ``distance == 0`` (identical coordinates) → ``1.0``.
    - ``distance == p^{-N}`` (separated by the working precision) → ``0.0``.
    - Distances larger than ``1`` clamp to ``0.0``.

    ``N`` is the working precision (number of unit digits).  The score is
    ``v / N`` where ``v = -log_p(distance)``.
    """
    if distance <= 0.0:
        return 1.0
    if N <= 0:
        return 0.0
    v = -math.log(distance, p)
    return max(0.0, min(1.0, v / N))


def hensel_lift(
    f: Callable[[int], int],
    f_prime: Callable[[int], int],
    a0: int,
    p: int,
    N: int,
) -> int:
    """Lift a simple root ``a0`` of ``f`` modulo ``p`` to a root modulo ``p^N``.

    Uses Newton iteration.  ``a0`` must satisfy ``f(a0) == 0 (mod p)`` and
    ``f'(a0) != 0 (mod p)``.  The returned integer is the unique root
    modulo ``p^N`` congruent to ``a0`` modulo ``p``.

    Raises ``ValueError`` when ``f'(a0)`` is divisible by ``p`` (degenerate root)
    or when ``N < 1``.
    """
    if N < 1:
        raise ValueError("N must be a positive integer")
    if not _is_prime(p):
        raise ValueError(f"p must be prime, got {p}")
    a0 = int(a0) % p
    if f(a0) % p != 0:
        raise ValueError(f"a0={a0} is not a root modulo {p}")
    if f_prime(a0) % p == 0:
        raise ValueError(f"f'(a0) is divisible by {p}; root is not simple")

    current = a0
    current_mod = p
    target_modulus = p**N

    while current_mod < target_modulus:
        next_mod = min(current_mod * current_mod, target_modulus)
        denom = f_prime(current)
        inv_denom = pow(denom, -1, next_mod)
        correction = (f(current) * inv_denom) % next_mod
        current = (current - correction) % next_mod
        current_mod = next_mod

    return current % target_modulus


def newton_lift(
    g: Callable[[int], int],
    g_prime: Callable[[int], int],
    x0: int,
    p: int,
    N: int,
) -> int:
    """Lift an approximate fixed point ``x0`` of ``g`` to precision ``p^N``.

    Solves ``g(x) - x == 0 (mod p^N)`` using Newton iteration on ``h(x) = g(x) - x``.
    Requires ``g'(x0) - 1 != 0 (mod p)``.
    """
    if N < 1:
        raise ValueError("N must be a positive integer")
    if not _is_prime(p):
        raise ValueError(f"p must be prime, got {p}")

    def h(x: int) -> int:
        return g(x) - x

    def h_prime(x: int) -> int:
        return g_prime(x) - 1

    return hensel_lift(h, h_prime, x0 % p, p, N)


def _promote(a: QpElement, b: QpElement) -> tuple[QpElement, QpElement]:
    """Return two elements promoted to a common working precision.

    The common precision is the maximum of the two working precisions.  The
    shorter expansion is zero-padded on the right (lower-significance digits).
    """
    if a.p != b.p:
        raise ValueError("QpElement operands must share the same prime")
    if a.working_precision == b.working_precision:
        return a, b
    target = max(a.working_precision, b.working_precision)
    return a.to_precision(target), b.to_precision(target)


def _from_unit_int(
    p: int,
    valuation_offset: int | float,
    unit_int: int,
    working_precision: int,
) -> QpElement:
    """Create a QpElement from a possibly scaled unit integer.

    ``unit_int`` is interpreted modulo ``p^working_precision`` and the result is
    normalised so that factors of ``p`` are moved from the unit into
    ``valuation_offset``.  Each stripped factor reduces the working precision by
    one because the corresponding digit is known to be zero.
    """
    requested_precision = working_precision
    if math.isinf(valuation_offset) or unit_int == 0:
        return QpElement.zero(p, requested_precision)
    assert isinstance(valuation_offset, int)
    if working_precision < 1:
        raise ValueError("working_precision must be positive")
    mod = p**working_precision
    unit_int = int(unit_int) % mod
    if unit_int == 0:
        return QpElement.zero(p, requested_precision)
    # Strip trailing zero digits (lowest powers of p) and move the valuation.
    while unit_int % p == 0:
        unit_int //= p
        valuation_offset += 1
        working_precision -= 1
        if working_precision <= 0:
            return QpElement.zero(p, requested_precision)
    digits = tuple(_base_p_digits(unit_int, p, working_precision))
    return QpElement(p, valuation_offset, digits, working_precision)


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


def _integer_valuation(p: int, n: int) -> int:
    """Return ``v_p(n)`` for a non-zero integer ``n``."""
    if n == 0:
        raise ValueError("v_p(0) is undefined (infinite)")
    v = 0
    nn = abs(int(n))
    while nn % p == 0:
        nn //= p
        v += 1
    return v


def _base_p_digits(value: int, p: int, length: int) -> list[int]:
    """Return the base-``p`` expansion of ``value`` as ``length`` digits, LSB first."""
    digits: list[int] = []
    remaining = int(value)
    for _ in range(length):
        digits.append(remaining % p)
        remaining //= p
    return digits


def _digits_to_int(digits: tuple[int, ...], p: int) -> int:
    """Convert a base-``p`` digit tuple (LSB first) to an integer."""
    total = 0
    place = 1
    for d in digits:
        total += d * place
        place *= p
    return total


def _rational_reconstruction(x: int, p: int, N: int) -> Fraction:
    """Reconstruct a small rational from its p-adic residue modulo ``p^N``.

    This is the standard extended-Euclidean / continued-fraction rational
    reconstruction. It returns the unique fraction ``a/b`` (with ``b > 0``,
    ``gcd(a,b) = 1``, and ``b`` not divisible by ``p``) such that
    ``a/b ≡ x (mod p^N)`` whenever ``|a|`` and ``b`` are both ``< sqrt(p^N)``.
    """
    if N == 0:
        return Fraction(0, 1)
    modulus = p**N
    x = int(x) % modulus
    if x == 0:
        return Fraction(0, 1)

    # Extended Euclidean algorithm on (modulus, x).
    # Maintain:
    #   a = s0*modulus + t0*x
    #   b = s1*modulus + t1*x
    a, b = modulus, x
    s0, s1 = 1, 0
    t0, t1 = 0, 1
    bound = int(math.isqrt(modulus))

    while b > bound:
        q = a // b
        a, b = b, a - q * b
        s0, s1 = s1, s0 - q * s1
        t0, t1 = t1, t0 - q * t1

    # At loop exit, b is the first remainder <= sqrt(modulus) and
    # b ≡ t1*x (mod modulus), so x ≡ b/t1 (mod modulus).
    num = b
    den = t1
    if den == 0:
        return Fraction(x, 1)

    if den < 0:
        num, den = -num, -den

    g = math.gcd(num, den)
    if g:
        num //= g
        den //= g

    return Fraction(num, den)
