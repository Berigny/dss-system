"""Circulation-aware p-adic coordinate placeholder.

This module defines the symbolic digit alphabet and the ``QpCoordinate`` dataclass that
carries the circulation-hysteresis trace described in
``backlog_reqs/paper/DSS_Qp_Circulation_Extension_Spec.md``.

A ``QpCoordinate`` is *not* a raw p-adic field element. Its ``unit_digits`` are
``DigitSymbol`` enum values encoding structural attributes (0-9 phases and the ``inf``
pass-boundary operator). The underlying mathematical value used for ultrametric distance
is stored in ``rational_representative`` as a ``Fraction`` or ``QpElement``.

Code-level claims (see Epic 22 / Epic 23 claim registers):

- CLAIM(analogy): ``QpCoordinate`` is a placeholder for the circulation-hysteresis
  coordinate described in the circulation-extension spec. Full dual-tetrahedron
  synchronization and circulation pass machinery are added in DS-REVIEW-192 Phase 1.
  EVIDENCE: runs/ds-review-192/tasks/192-P0-02-module-boundary.md

- CLAIM(definite): ``QpCoordinate`` is immutable after creation (``frozen=True``,
  ``sealed=True``).
  EVIDENCE: this module and ``backend/tests/test_qp_coordinate.py``
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from fractions import Fraction
from typing import Any, Mapping

from shared_types.coord_schema import bigint_str, parse_bigint

from .qp_arithmetic import QpElement, _integer_valuation, qp_distance


class DigitSymbol(IntEnum):
    """Symbolic digit alphabet for the 0-9 Computational Lattice circulation trace.

    The integer values are stable structural aliases (0-9) plus ``INF`` as the
    pass-boundary operator. They are *not* used for arithmetic; they encode which
    structural attribute was active during a circulation pass.
    """

    ORIGIN = 0          # Eq0 / Null State / S1
    BOUNDARY = 1        # Eq1 / Boundary Operator / S1
    TEMPORALIZATION = 2 # Eq2 / Temporalization / S1
    CLOSURE = 3         # Eq3 / Geometric Closure / S1
    COUPLING = 4        # Eq4 / Coupling Constant / S2
    PERSISTENCE = 5     # Eq5 / Persistence Cost / S2
    AUDIT = 6           # Eq6 / State Auditor / S2
    COHERENCE = 7       # Eq7 / Coherence Norm / S2
    LAW = 8             # Eq8 / Admissibility Gate / C
    GRACE = 9           # Eq9 / Terminal Optimizer / C
    INF = 10            # Pass-boundary / recursion operator

    def __repr__(self) -> str:  # pragma: no cover
        return f"DigitSymbol.{self.name}"


# -----------------------------------------------------------------------------
# Dual-tetrahedron overlay geometry
# -----------------------------------------------------------------------------

_DUAL_COMPLEMENT: dict[str, str] = {
    "Eq0": "Eq4",
    "Eq1": "Eq5",
    "Eq2": "Eq6",
    "Eq3": "Eq7",
    "Eq4": "Eq0",
    "Eq5": "Eq1",
    "Eq6": "Eq2",
    "Eq7": "Eq3",
    "Eq8": "Eq9",
    "Eq9": "Eq8",
}

_METRIC_PRIME: dict[str, int] = {
    "Eq0": 2,
    "Eq1": 3,
    "Eq2": 5,
    "Eq3": 7,
    "Eq4": 11,
    "Eq5": 13,
    "Eq6": 17,
    "Eq7": 19,
    "Eq8": 137,
    "Eq9": 139,
}

_TETRAHEDRON: dict[str, str] = {
    "Eq0": "S1",
    "Eq1": "S1",
    "Eq2": "S1",
    "Eq3": "S1",
    "Eq4": "S2",
    "Eq5": "S2",
    "Eq6": "S2",
    "Eq7": "S2",
    "Eq8": "C",
    "Eq9": "C",
}

# Mapping from kernel node to the symbolic digit active at that node.
_NODE_DIGIT: dict[str, DigitSymbol] = {
    "Eq0": DigitSymbol.ORIGIN,
    "Eq1": DigitSymbol.BOUNDARY,
    "Eq2": DigitSymbol.TEMPORALIZATION,
    "Eq3": DigitSymbol.CLOSURE,
    "Eq4": DigitSymbol.COUPLING,
    "Eq5": DigitSymbol.PERSISTENCE,
    "Eq6": DigitSymbol.AUDIT,
    "Eq7": DigitSymbol.COHERENCE,
    "Eq8": DigitSymbol.LAW,
    "Eq9": DigitSymbol.GRACE,
}


@dataclass(frozen=True, slots=True)
class QpCoordinate:
    """A circulation-aware, immutable p-adic coordinate.

    This is the structural layer on top of the mathematical ``QpElement`` field. It
    carries the digit trace, dual-tetrahedron references, mediator state, and
    circulation pass metadata required by the circulation-hysteresis specification.
    """

    # Identity
    coordinate_id: str
    kernel_node: str
    metric_prime: int
    tetrahedron: str  # "S1" | "S2" | "C" | "B"
    dual_complement: str

    # P-adic expansion (the hysteresis trace)
    unit_digits: tuple[DigitSymbol, ...]
    valuation_offset: int
    working_precision: int

    # Rational embedding (for cross-domain bridge)
    rational_representative: Fraction | QpElement | None = None

    # Circulation metadata
    circulation_pass: int = 0
    pass_entry_node: str | None = None
    pass_exit_node: str | None = None

    # Hysteresis engine state
    hysteresis_depth: float = 0.0
    last_shift_map: str | None = None

    # Dual-tetrahedron overlay state
    dual_state: QpCoordinate | None = None
    mediator_state: QpCoordinate | None = None
    coherence_threshold: float = 0.98

    # Composition provenance
    composition_history: tuple[QpCoordinate, ...] = ()
    parent_coordinate_id: str | None = None

    # Diagnostics
    p_adic_write_cost: float = 0.0
    padic_ball_hit_count: int = 0

    # Immutability seal
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sealed: bool = True

    def __post_init__(self) -> None:
        # Consistency checks on the frozen object.
        if not isinstance(self.metric_prime, int) or self.metric_prime < 2:
            raise ValueError(
                f"metric_prime must be a prime >= 2, got {self.metric_prime!r}"
            )
        if self.valuation_offset < 0:
            raise ValueError(
                f"valuation_offset must be non-negative, got {self.valuation_offset}"
            )
        if self.valuation_offset != len(self.unit_digits):
            raise ValueError(
                f"valuation_offset {self.valuation_offset} must equal "
                f"len(unit_digits) {len(self.unit_digits)}"
            )
        if not all(isinstance(d, DigitSymbol) for d in self.unit_digits):
            raise ValueError(
                f"unit_digits must be DigitSymbol values, got {self.unit_digits}"
            )
        if self.working_precision < 0:
            raise ValueError(
                f"working_precision must be non-negative, got {self.working_precision}"
            )
        if self.coherence_threshold < 0.0 or self.coherence_threshold > 1.0:
            raise ValueError(
                f"coherence_threshold must be in [0, 1], got {self.coherence_threshold}"
            )
        expected_id = _coordinate_hash(
            self.metric_prime, self.valuation_offset, self.unit_digits
        )
        if self.coordinate_id != expected_id:
            raise ValueError(
                f"coordinate_id {self.coordinate_id!r} does not match expected "
                f"hash {expected_id!r}"
            )

    @classmethod
    def origin(
        cls,
        metric_prime: int,
        working_precision: int,
        kernel_node: str = "Eq0",
        tetrahedron: str = "S1",
        dual_complement: str = "Eq4",
    ) -> "QpCoordinate":
        """Return an origin coordinate at the given metric prime and precision."""
        return cls(
            coordinate_id=_coordinate_hash(metric_prime, 0, ()),
            kernel_node=kernel_node,
            metric_prime=metric_prime,
            tetrahedron=tetrahedron,
            dual_complement=dual_complement,
            unit_digits=(),
            valuation_offset=0,
            working_precision=working_precision,
            pass_entry_node=kernel_node,
        )

    def append(self, digit: DigitSymbol) -> "QpCoordinate":
        """Return a new coordinate with ``digit`` appended and valuation incremented."""
        if digit == DigitSymbol.INF:
            raise ValueError(
                "INF is the pass-boundary operator and is not stored as a digit"
            )
        new_digits = self.unit_digits + (digit,)
        new_valuation = self.valuation_offset + 1
        if new_valuation > self.working_precision:
            raise ValueError(
                f"append would exceed working_precision {self.working_precision}"
            )
        result = QpCoordinate(
            coordinate_id=_coordinate_hash(self.metric_prime, new_valuation, new_digits),
            kernel_node=f"Eq{digit.value}",
            metric_prime=self.metric_prime,
            tetrahedron=self.tetrahedron,
            dual_complement=self.dual_complement,
            unit_digits=new_digits,
            valuation_offset=new_valuation,
            working_precision=self.working_precision,
            rational_representative=self.rational_representative,
            circulation_pass=self.circulation_pass,
            pass_entry_node=self.pass_entry_node,
            hysteresis_depth=self.hysteresis_depth,
            dual_state=self.dual_state,
            mediator_state=self.mediator_state,
            coherence_threshold=self.coherence_threshold,
            composition_history=(self,) + self.composition_history,
            parent_coordinate_id=self.coordinate_id,
            p_adic_write_cost=self.p_adic_write_cost,
            padic_ball_hit_count=self.padic_ball_hit_count,
            sealed=True,
        )
        if result.dual_state is not None and not pair_valid(result, result.dual_state):
            raise ValueError(
                "append breaks dual synchronization: |Δv| > 1 or pair mismatch"
            )
        return result

    def with_dual_state(self, dual_state: QpCoordinate | None) -> "QpCoordinate":
        """Return a new coordinate with an updated dual reference."""
        return QpCoordinate(
            coordinate_id=self.coordinate_id,
            kernel_node=self.kernel_node,
            metric_prime=self.metric_prime,
            tetrahedron=self.tetrahedron,
            dual_complement=self.dual_complement,
            unit_digits=self.unit_digits,
            valuation_offset=self.valuation_offset,
            working_precision=self.working_precision,
            rational_representative=self.rational_representative,
            circulation_pass=self.circulation_pass,
            pass_entry_node=self.pass_entry_node,
            pass_exit_node=self.pass_exit_node,
            hysteresis_depth=self.hysteresis_depth,
            last_shift_map=self.last_shift_map,
            dual_state=dual_state,
            mediator_state=self.mediator_state,
            coherence_threshold=self.coherence_threshold,
            composition_history=self.composition_history,
            parent_coordinate_id=self.parent_coordinate_id,
            p_adic_write_cost=self.p_adic_write_cost,
            padic_ball_hit_count=self.padic_ball_hit_count,
            created_at=self.created_at,
            sealed=self.sealed,
        )

    def with_mediator_state(
        self, mediator_state: QpCoordinate | None
    ) -> "QpCoordinate":
        """Return a new coordinate with an updated mediator reference."""
        return QpCoordinate(
            coordinate_id=self.coordinate_id,
            kernel_node=self.kernel_node,
            metric_prime=self.metric_prime,
            tetrahedron=self.tetrahedron,
            dual_complement=self.dual_complement,
            unit_digits=self.unit_digits,
            valuation_offset=self.valuation_offset,
            working_precision=self.working_precision,
            rational_representative=self.rational_representative,
            circulation_pass=self.circulation_pass,
            pass_entry_node=self.pass_entry_node,
            pass_exit_node=self.pass_exit_node,
            hysteresis_depth=self.hysteresis_depth,
            last_shift_map=self.last_shift_map,
            dual_state=self.dual_state,
            mediator_state=mediator_state,
            coherence_threshold=self.coherence_threshold,
            composition_history=self.composition_history,
            parent_coordinate_id=self.parent_coordinate_id,
            p_adic_write_cost=self.p_adic_write_cost,
            padic_ball_hit_count=self.padic_ball_hit_count,
            created_at=self.created_at,
            sealed=self.sealed,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        return {
            "coordinate_id": self.coordinate_id,
            "kernel_node": self.kernel_node,
            "metric_prime": self.metric_prime,
            "tetrahedron": self.tetrahedron,
            "dual_complement": self.dual_complement,
            "unit_digits": [int(d) for d in self.unit_digits],
            "valuation_offset": self.valuation_offset,
            "working_precision": self.working_precision,
            "rational_representative": _serialize_rational(self.rational_representative),
            "circulation_pass": self.circulation_pass,
            "pass_entry_node": self.pass_entry_node,
            "pass_exit_node": self.pass_exit_node,
            "hysteresis_depth": self.hysteresis_depth,
            "last_shift_map": self.last_shift_map,
            "dual_state": self.dual_state.as_dict() if self.dual_state else None,
            "mediator_state": self.mediator_state.as_dict()
            if self.mediator_state
            else None,
            "coherence_threshold": self.coherence_threshold,
            "composition_history": [c.as_dict() for c in self.composition_history],
            "parent_coordinate_id": self.parent_coordinate_id,
            "p_adic_write_cost": self.p_adic_write_cost,
            "padic_ball_hit_count": self.padic_ball_hit_count,
            "created_at": self.created_at.isoformat(),
            "sealed": self.sealed,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QpCoordinate":
        """Reconstruct a ``QpCoordinate`` from ``as_dict`` output."""
        return cls(
            coordinate_id=str(payload["coordinate_id"]),
            kernel_node=str(payload["kernel_node"]),
            metric_prime=parse_bigint(payload["metric_prime"]),
            tetrahedron=str(payload["tetrahedron"]),
            dual_complement=str(payload["dual_complement"]),
            unit_digits=tuple(DigitSymbol(parse_bigint(d)) for d in payload["unit_digits"]),
            valuation_offset=parse_bigint(payload["valuation_offset"]),
            working_precision=parse_bigint(payload["working_precision"]),
            rational_representative=_deserialize_rational(payload.get("rational_representative")),
            circulation_pass=parse_bigint(payload.get("circulation_pass", 0)),
            pass_entry_node=payload.get("pass_entry_node"),
            pass_exit_node=payload.get("pass_exit_node"),
            hysteresis_depth=float(payload.get("hysteresis_depth", 0.0)),
            last_shift_map=payload.get("last_shift_map"),
            dual_state=cls.from_dict(payload["dual_state"]) if payload.get("dual_state") else None,
            mediator_state=cls.from_dict(payload["mediator_state"])
            if payload.get("mediator_state")
            else None,
            coherence_threshold=float(payload.get("coherence_threshold", 0.98)),
            composition_history=tuple(
                cls.from_dict(c) for c in payload.get("composition_history", [])
            ),
            parent_coordinate_id=payload.get("parent_coordinate_id"),
            p_adic_write_cost=float(payload.get("p_adic_write_cost", 0.0)),
            padic_ball_hit_count=parse_bigint(payload.get("padic_ball_hit_count", 0)),
            created_at=datetime.fromisoformat(payload["created_at"]),
            sealed=bool(payload.get("sealed", True)),
        )


# -----------------------------------------------------------------------------
# Dual-tetrahedron overlay helpers
# -----------------------------------------------------------------------------

def dual_complement(kernel_node: str) -> str:
    """Return the paired kernel node for ``kernel_node``."""
    if kernel_node not in _DUAL_COMPLEMENT:
        raise ValueError(f"unknown kernel node: {kernel_node!r}")
    return _DUAL_COMPLEMENT[kernel_node]


def metric_prime(kernel_node: str) -> int:
    """Return the metric prime associated with ``kernel_node``."""
    if kernel_node not in _METRIC_PRIME:
        raise ValueError(f"unknown kernel node: {kernel_node!r}")
    return _METRIC_PRIME[kernel_node]


def tetrahedron(kernel_node: str) -> str:
    """Return the tetrahedron (S1, S2, or C) for ``kernel_node``."""
    if kernel_node not in _TETRAHEDRON:
        raise ValueError(f"unknown kernel node: {kernel_node!r}")
    return _TETRAHEDRON[kernel_node]


def pair_valid(a: QpCoordinate, b: QpCoordinate) -> bool:
    """Return True if ``a`` and ``b`` form a valid dual pair.

    A valid pair respects the 0↔4, 1↔5, 2↔6, 3↔7, 8↔9 mapping and keeps the
    valuation gap within one.
    """
    if b.kernel_node != dual_complement(a.kernel_node):
        return False
    if a.metric_prime != metric_prime(a.kernel_node):
        return False
    if b.metric_prime != metric_prime(b.kernel_node):
        return False
    return abs(a.valuation_offset - b.valuation_offset) <= 1


def synchronize_audit(
    audit_state: QpCoordinate, awareness_state: QpCoordinate
) -> QpCoordinate:
    """Return a new Eq6 audit coordinate synchronized with an Eq2 awareness coordinate.

    This implements the 2↔6 dual pair rule: audit verifies the hysteresis trace
    left by awareness accumulation.
    """
    if audit_state.kernel_node != "Eq6":
        raise ValueError("synchronize_audit requires an Eq6 audit state")
    if awareness_state.kernel_node != "Eq2":
        raise ValueError("synchronize_audit requires an Eq2 awareness state")
    if awareness_state.valuation_offset < audit_state.valuation_offset:
        raise ValueError("awareness valuation must be >= audit valuation")
    if awareness_state.hysteresis_depth < audit_state.hysteresis_depth:
        raise ValueError("awareness hysteresis must be >= audit hysteresis")

    new_digits = audit_state.unit_digits + (DigitSymbol.AUDIT,)
    new_valuation = audit_state.valuation_offset + 1
    if new_valuation > audit_state.working_precision:
        raise ValueError("synchronize_audit would exceed working_precision")

    return QpCoordinate(
        coordinate_id=_coordinate_hash(
            audit_state.metric_prime, new_valuation, new_digits
        ),
        kernel_node="Eq6",
        metric_prime=audit_state.metric_prime,
        tetrahedron="S2",
        dual_complement="Eq2",
        unit_digits=new_digits,
        valuation_offset=new_valuation,
        working_precision=audit_state.working_precision,
        rational_representative=audit_state.rational_representative,
        circulation_pass=awareness_state.circulation_pass,
        pass_entry_node=audit_state.pass_entry_node,
        hysteresis_depth=awareness_state.hysteresis_depth,
        last_shift_map="audit_sync",
        dual_state=awareness_state,
        mediator_state=audit_state.mediator_state,
        coherence_threshold=audit_state.coherence_threshold,
        composition_history=(audit_state,) + audit_state.composition_history,
        parent_coordinate_id=audit_state.coordinate_id,
        p_adic_write_cost=audit_state.p_adic_write_cost,
        padic_ball_hit_count=audit_state.padic_ball_hit_count,
        sealed=True,
    )


# -----------------------------------------------------------------------------
# Retrieval-law compatibility helpers (DS-REVIEW-193 P2-01)
# -----------------------------------------------------------------------------

def _coordinate_to_qp_element(
    coord: QpCoordinate,
    p: int,
    working_precision: int,
) -> QpElement:
    """Return a ``QpElement`` for ``coord`` at the given prime and precision."""
    rep = coord.rational_representative
    if isinstance(rep, QpElement):
        if rep.p != p:
            raise ValueError(
                f"coordinate rational representative has prime {rep.p}, expected {p}"
            )
        return rep.to_precision(working_precision)
    if isinstance(rep, Fraction):
        return QpElement.from_rational(
            p, rep.numerator, rep.denominator, working_precision
        )
    raise ValueError(
        "coordinate has no rational representative for distance computation"
    )


def qp_coordinate_distance(a: QpCoordinate, b: QpCoordinate) -> float:
    """Return the p-adic distance between two coordinates.

    The distance is computed on the coordinates' ``rational_representative`` values
    embedded into their shared ``metric_prime``.  Raises ``ValueError`` when the
    primes differ or when either coordinate lacks a rational representative.
    """
    if a.metric_prime != b.metric_prime:
        raise ValueError(
            f"qp_coordinate_distance requires the same prime, got {a.metric_prime} "
            f"and {b.metric_prime}"
        )
    p = a.metric_prime
    working_precision = max(a.working_precision, b.working_precision)
    qa = _coordinate_to_qp_element(a, p, working_precision)
    qb = _coordinate_to_qp_element(b, p, working_precision)
    return qp_distance(qa, qb)


def circulation_depth_compatible(
    query: QpCoordinate,
    candidate: QpCoordinate,
    *,
    max_pass_delta: int = 1,
    max_depth_delta: float = 1.0,
) -> bool:
    """Return True if the candidate's circulation depth is close enough to the query's.

    This is an explicit filter, not part of the distance function.
    """
    if abs(query.circulation_pass - candidate.circulation_pass) > max_pass_delta:
        return False
    if abs(query.hysteresis_depth - candidate.hysteresis_depth) > max_depth_delta:
        return False
    return True


def dual_state_compatible(query: QpCoordinate, candidate: QpCoordinate) -> bool:
    """Return True if the candidate's dual state matches the query's dual state.

    An S1 query's S2 dual must be compatible with the candidate's S2 dual:
    same kernel node, same metric prime, and ``|Δv| <= 1``.  If the query carries
    no dual state, the filter is vacuously satisfied.
    """
    qd = query.dual_state
    if qd is None:
        return True
    cd = candidate.dual_state
    if cd is None:
        return False
    if qd.kernel_node != cd.kernel_node:
        return False
    if qd.metric_prime != cd.metric_prime:
        return False
    return abs(qd.valuation_offset - cd.valuation_offset) <= 1


def mediator_state_compatible(query: QpCoordinate, candidate: QpCoordinate) -> bool:
    """Return True if the candidate's mediator state matches the query's.

    Law/Grace context must match when the query carries a mediator state.
    """
    qm = query.mediator_state
    if qm is None:
        return True
    cm = candidate.mediator_state
    if cm is None:
        return False
    if qm.kernel_node != cm.kernel_node:
        return False
    if qm.metric_prime != cm.metric_prime:
        return False
    return abs(qm.valuation_offset - cm.valuation_offset) <= 1


def _serialize_rational(value: Fraction | QpElement | None) -> dict[str, Any] | None:
    """Serialize a rational representative for JSON storage."""
    if value is None:
        return None
    if isinstance(value, Fraction):
        return {
            "type": "fraction",
            "numerator": bigint_str(value.numerator),
            "denominator": bigint_str(value.denominator),
        }
    if isinstance(value, QpElement):
        return {"type": "qp", "value": value.as_dict()}
    raise TypeError(f"cannot serialize rational representative of type {type(value)}")


def _deserialize_rational(payload: Mapping[str, Any] | None) -> Fraction | QpElement | None:
    """Deserialize a rational representative from JSON storage."""
    if payload is None:
        return None
    kind = payload.get("type")
    if kind == "fraction":
        return Fraction(parse_bigint(payload["numerator"]), parse_bigint(payload["denominator"]))
    if kind == "qp":
        return QpElement.from_dict(payload["value"])
    raise ValueError(f"unknown rational representative type: {kind!r}")


def derive_p_adic_coordinate(
    metadata: Mapping[str, Any],
    working_precision: int = 16,
) -> QpCoordinate | None:
    """Derive a canonical ``QpCoordinate`` from entry metadata.

    The coordinate is built from the kernel-prime exponents stored in
    ``metadata["kernel_prime_exponents"]``.  The active kernel node is the one
    with the largest exponent; ties are broken by the lowest Eq index.  The
    digit trace records every active kernel node in Eq order.
    """
    from backend.fieldx_kernel.informational_unit import (
        KERNEL_EQ_TO_PRIME,
        KERNEL_PRIME_TO_EQ,
    )

    kernel_exponents = metadata.get("kernel_prime_exponents") if isinstance(metadata, Mapping) else None
    if not isinstance(kernel_exponents, Mapping):
        return None

    # Select only kernel primes (ignore body-tier and MMF-domain primes).
    kernel_items = [
        (prime, exp)
        for prime, exp in kernel_exponents.items()
        if prime in KERNEL_PRIME_TO_EQ
    ]
    if not kernel_items:
        return None

    # Sort by exponent descending, then Eq index ascending for determinism.
    sorted_items = sorted(
        kernel_items,
        key=lambda item: (-item[1], KERNEL_PRIME_TO_EQ[item[0]]),
    )
    active_prime, active_exp = sorted_items[0]
    active_eq = KERNEL_PRIME_TO_EQ[active_prime]
    kernel_node = f"Eq{active_eq}"
    metric_prime = _METRIC_PRIME.get(kernel_node)
    if metric_prime is None:
        return None

    # Build the digit trace from all active kernel nodes in Eq order.
    active_eqs = sorted(
        {KERNEL_PRIME_TO_EQ[p] for p, _ in kernel_items},
    )
    unit_digits = tuple(DigitSymbol(eq) for eq in active_eqs)
    valuation_offset = len(unit_digits)

    # Rational representative: product of kernel primes raised to their exponents,
    # embedded into the metric prime's p-adic field.
    numerator = 1
    denominator = 1
    for prime, exp in kernel_items:
        if exp > 0:
            numerator *= prime**exp
        else:
            denominator *= prime**(-exp)
    rational_representative = QpElement.from_rational(
        metric_prime, numerator, denominator, working_precision
    )

    # Dual complement string from the kernel geometry.
    dual_complement = _DUAL_COMPLEMENT.get(kernel_node, "")

    # Mediator state: S1 nodes -> Law (Eq8), S2 nodes -> Grace (Eq9), C nodes -> none.
    mediator_state: QpCoordinate | None = None
    if kernel_node.startswith("Eq") and kernel_node in _TETRAHEDRON:
        tetra = _TETRAHEDRON[kernel_node]
        if tetra == "S1":
            mediator_state = QpCoordinate.origin(
                _METRIC_PRIME["Eq8"], working_precision, kernel_node="Eq8"
            )
        elif tetra == "S2":
            mediator_state = QpCoordinate.origin(
                _METRIC_PRIME["Eq9"], working_precision, kernel_node="Eq9"
            )

    return QpCoordinate(
        coordinate_id=_coordinate_hash(metric_prime, valuation_offset, unit_digits),
        kernel_node=kernel_node,
        metric_prime=metric_prime,
        tetrahedron=_TETRAHEDRON.get(kernel_node, "S1"),
        dual_complement=dual_complement,
        unit_digits=unit_digits,
        valuation_offset=valuation_offset,
        working_precision=working_precision,
        rational_representative=rational_representative,
        mediator_state=mediator_state,
    )


# -----------------------------------------------------------------------------
# Circulation engine
# -----------------------------------------------------------------------------

def _apply_shift_map(qp: QpElement) -> QpElement:
    """Apply the Eq2 hysteresis shift ``x_{t+1} = x_t + p^{v_p(x_t)}``.

    For the zero element the shift is ``+1`` (``p^0``).
    """
    p = qp.p
    v = qp.valuation_offset
    if math.isinf(v):
        increment = QpElement.from_int(p, 1, qp.working_precision)
    elif isinstance(v, int):
        if v >= 0:
            increment = QpElement.from_int(p, p**v, qp.working_precision)
        else:
            increment = QpElement.from_rational(p, 1, p ** (-v), qp.working_precision)
    else:
        raise ValueError("unexpected valuation type")
    return qp + increment


def _node_symbol(node: str) -> DigitSymbol:
    """Return the structural digit symbol for ``node``."""
    if node not in _NODE_DIGIT:
        raise ValueError(f"unknown kernel node: {node!r}")
    return _NODE_DIGIT[node]


def eq2_temporalization_qp(
    state: QpCoordinate,
    hysteresis: float = 0.1,
    cycle_automorphism: str = "digit_rotation",
) -> QpCoordinate:
    """Apply the Eq2 temporalization shift map with dual-aware audit sync.

    Implements the hysteresis engine from the circulation spec:
    ``x_{t+1} = x_t + p^{v_p(x_t)}``.  The symbolic digit trace is rotated
    according to ``cycle_automorphism`` and ``DigitSymbol.TEMPORALIZATION`` is
    appended.  If a dual Eq6 audit coordinate exists, it is synchronized.
    """
    if state.metric_prime != 5:
        raise ValueError("Eq2 temporalization requires metric_prime == 5")
    if state.dual_complement != "Eq6":
        raise ValueError("Eq2 temporalization requires dual_complement == 'Eq6'")

    digits = list(state.unit_digits)
    if cycle_automorphism == "digit_rotation":
        if digits:
            shifted = digits[1:] + [digits[0]]
        else:
            shifted = []
    elif cycle_automorphism == "block_rotation":
        mid = len(digits) // 2
        shifted = digits[mid:] + digits[:mid]
    elif cycle_automorphism == "identity":
        shifted = digits
    else:
        raise ValueError(f"unsupported cycle_automorphism: {cycle_automorphism!r}")

    new_digits = tuple(shifted) + (DigitSymbol.TEMPORALIZATION,)
    new_valuation = state.valuation_offset + 1
    if new_valuation > state.working_precision:
        raise ValueError("temporalization would exceed working_precision")

    new_hysteresis = state.hysteresis_depth + hysteresis

    # Update the rational representative (Qp-native; no modular wrap).
    new_rep: Fraction | QpElement | None = state.rational_representative
    if isinstance(new_rep, QpElement):
        new_rep = _apply_shift_map(new_rep)
    elif isinstance(new_rep, Fraction):
        qp_rep = QpElement.from_rational(
            state.metric_prime,
            new_rep.numerator,
            new_rep.denominator,
            state.working_precision,
        )
        new_rep = _apply_shift_map(qp_rep)

    result = QpCoordinate(
        coordinate_id=_coordinate_hash(state.metric_prime, new_valuation, new_digits),
        kernel_node="Eq2",
        metric_prime=state.metric_prime,
        tetrahedron="S1",
        dual_complement="Eq6",
        unit_digits=new_digits,
        valuation_offset=new_valuation,
        working_precision=state.working_precision,
        rational_representative=new_rep,
        circulation_pass=state.circulation_pass,
        pass_entry_node=state.pass_entry_node,
        hysteresis_depth=new_hysteresis,
        last_shift_map=cycle_automorphism,
        dual_state=state.dual_state,
        mediator_state=state.mediator_state,
        coherence_threshold=state.coherence_threshold,
        composition_history=(state,) + state.composition_history,
        parent_coordinate_id=state.coordinate_id,
        p_adic_write_cost=hysteresis,
        padic_ball_hit_count=0,
        sealed=True,
    )

    if result.dual_state is not None:
        audit = synchronize_audit(result.dual_state, result)
        result = result.with_dual_state(audit)

    return result


class PassLifecycle:
    """Helper that walks one circulation pass through Eq0 → Eq9.

    The pass appends the correct structural digit at each node and enforces
    dual-tetrahedron pair checks when a ``dual_state`` is present.
    """

    _PASS_NODES = [
        "Eq0",
        "Eq1",
        "Eq2",
        "Eq3",
        "Eq4",
        "Eq5",
        "Eq6",
        "Eq7",
        "Eq8",
        "Eq9",
    ]

    def __init__(
        self,
        state: QpCoordinate,
        mode: int = 0,
        coherence: float = 0.0,
    ) -> None:
        if state.kernel_node not in self._PASS_NODES:
            raise ValueError(f"pass must start at a lattice node, got {state.kernel_node!r}")
        self.state = state
        self.mode = mode
        self.coherence = coherence
        self.gates_passed = True

    def step(self) -> "PassLifecycle":
        """Append the digit for the current node and advance to the next node."""
        node = self.state.kernel_node
        digit = _node_symbol(node)
        new_state = self.state.append(digit)
        idx = self._PASS_NODES.index(node)
        next_node = self._PASS_NODES[idx + 1] if idx + 1 < len(self._PASS_NODES) else node
        new_state = QpCoordinate(
            coordinate_id=new_state.coordinate_id,
            kernel_node=next_node,
            metric_prime=new_state.metric_prime,
            tetrahedron=new_state.tetrahedron,
            dual_complement=new_state.dual_complement,
            unit_digits=new_state.unit_digits,
            valuation_offset=new_state.valuation_offset,
            working_precision=new_state.working_precision,
            rational_representative=new_state.rational_representative,
            circulation_pass=new_state.circulation_pass,
            pass_entry_node=new_state.pass_entry_node,
            pass_exit_node=new_state.pass_exit_node,
            hysteresis_depth=new_state.hysteresis_depth,
            last_shift_map=new_state.last_shift_map,
            dual_state=new_state.dual_state,
            mediator_state=new_state.mediator_state,
            coherence_threshold=new_state.coherence_threshold,
            composition_history=new_state.composition_history,
            parent_coordinate_id=new_state.parent_coordinate_id,
            p_adic_write_cost=new_state.p_adic_write_cost,
            padic_ball_hit_count=new_state.padic_ball_hit_count,
            created_at=new_state.created_at,
            sealed=new_state.sealed,
        )
        self.state = new_state
        return self

    def run(self) -> "PassLifecycle":
        """Run the remaining nodes of the current pass."""
        start_idx = self._PASS_NODES.index(self.state.kernel_node)
        for _ in range(len(self._PASS_NODES) - start_idx):
            self.step()
        return self


def commit_decision(lifecycle: PassLifecycle) -> bool:
    """Return True if the pass may commit.

    Commit requires mode >= 2, all gates passed, and dual synchronization.
    """
    if lifecycle.mode < 2:
        return False
    if not lifecycle.gates_passed:
        return False
    state = lifecycle.state
    if state.dual_state is not None and not pair_valid(state, state.dual_state):
        return False
    return True


def recurse(state: QpCoordinate) -> QpCoordinate:
    """Begin a new circulation pass without mutating ``state``.

    Increments ``circulation_pass``, resets the kernel node to Eq0, and clears
    the exit node.  The accumulated digit trace is preserved.
    """
    return QpCoordinate(
        coordinate_id=state.coordinate_id,
        kernel_node="Eq0",
        metric_prime=state.metric_prime,
        tetrahedron=state.tetrahedron,
        dual_complement=state.dual_complement,
        unit_digits=state.unit_digits,
        valuation_offset=state.valuation_offset,
        working_precision=state.working_precision,
        rational_representative=state.rational_representative,
        circulation_pass=state.circulation_pass + 1,
        pass_entry_node="Eq0",
        pass_exit_node=None,
        hysteresis_depth=state.hysteresis_depth,
        last_shift_map="recursion",
        dual_state=state.dual_state,
        mediator_state=state.mediator_state,
        coherence_threshold=state.coherence_threshold,
        composition_history=(state,) + state.composition_history,
        parent_coordinate_id=state.coordinate_id,
        p_adic_write_cost=state.p_adic_write_cost,
        padic_ball_hit_count=state.padic_ball_hit_count,
        created_at=state.created_at,
        sealed=state.sealed,
    )


def hensel_lift_coordinate(
    state: QpCoordinate,
    target_precision: int,
    _visited: set[int] | None = None,
) -> QpCoordinate:
    """Lift ``state``'s underlying rational representative to ``target_precision``.

    The symbolic digit trace, tetrahedron, and circulation metadata are preserved.
    Dual and mediator references are lifted recursively (with cycle protection) so
    the overlay remains consistent.
    """
    if target_precision < 1:
        raise ValueError("target_precision must be a positive integer")
    if _visited is None:
        _visited = set()
    if id(state) in _visited:
        return state
    _visited.add(id(state))

    rep = state.rational_representative
    if isinstance(rep, Fraction):
        lifted_rep = QpElement.from_rational(
            state.metric_prime, rep.numerator, rep.denominator, target_precision
        )
    elif isinstance(rep, QpElement):
        lifted_rep = rep.to_precision(target_precision)
    else:
        lifted_rep = rep

    lifted_dual = (
        hensel_lift_coordinate(state.dual_state, target_precision, _visited)
        if state.dual_state is not None
        else None
    )
    lifted_mediator = (
        hensel_lift_coordinate(state.mediator_state, target_precision, _visited)
        if state.mediator_state is not None
        else None
    )

    return QpCoordinate(
        coordinate_id=state.coordinate_id,
        kernel_node=state.kernel_node,
        metric_prime=state.metric_prime,
        tetrahedron=state.tetrahedron,
        dual_complement=state.dual_complement,
        unit_digits=state.unit_digits,
        valuation_offset=state.valuation_offset,
        working_precision=target_precision,
        rational_representative=lifted_rep,
        circulation_pass=state.circulation_pass,
        pass_entry_node=state.pass_entry_node,
        pass_exit_node=state.pass_exit_node,
        hysteresis_depth=state.hysteresis_depth,
        last_shift_map=state.last_shift_map,
        dual_state=lifted_dual,
        mediator_state=lifted_mediator,
        coherence_threshold=state.coherence_threshold,
        composition_history=state.composition_history,
        parent_coordinate_id=state.parent_coordinate_id,
        p_adic_write_cost=state.p_adic_write_cost,
        padic_ball_hit_count=state.padic_ball_hit_count,
        created_at=state.created_at,
        sealed=state.sealed,
    )


def _coordinate_hash(
    metric_prime: int, valuation_offset: int, unit_digits: tuple[DigitSymbol, ...]
) -> str:
    """Return a deterministic content-addressed hash for a coordinate.

    The hash is taken over the metric prime, valuation offset, and the symbolic digit
    values. This makes the coordinate identity stable across processes.
    """
    payload: dict[str, Any] = {
        "metric_prime": metric_prime,
        "valuation_offset": valuation_offset,
        "unit_digits": [d.value for d in unit_digits],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "qp:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
