"""Lattice utilities for mapping high dimensional states to a grid.

MMF projection helpers
----------------------
The functions below implement the reusable projection transform protocol
(``phi_d`` / ``psi_d``) between token-prime sequences and per-domain
``PrimeLatticeState`` cubes.  They are intentionally generic: the prime cubes
are imported from the core informational unit, but the transform logic itself
does not depend on a specific entry class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Sequence, Tuple

from backend.fieldx_kernel.informational_unit import (
    KERNEL_PRIMES,
    MMF_DOMAINS,
    build_mmf_projection_exponents,
)
from backend.fieldx_kernel.p_adic import PrimeLatticeState


@dataclass(frozen=True)
class LatticePoint:
    """Represents a discrete point on an n-dimensional lattice."""

    coordinates: Tuple[int, ...]

    def neighbors(self) -> Iterable["LatticePoint"]:
        """Yield adjacent lattice points using Manhattan distance."""

        for idx, value in enumerate(self.coordinates):
            delta = list(self.coordinates)
            delta[idx] = value + 1
            yield LatticePoint(tuple(delta))
            delta[idx] = value - 1
            yield LatticePoint(tuple(delta))


class Lattice:
    """Minimal lattice wrapper to construct and trace coordinates."""

    def __init__(self, dimensions: int) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def origin(self) -> LatticePoint:
        """Return the zeroed origin for the lattice size."""

        return LatticePoint(tuple(0 for _ in range(self.dimensions)))

    def trace_path(self, start: LatticePoint, steps: List[int]) -> LatticePoint:
        """Walk along a path of increments from a start point."""

        if len(steps) != self.dimensions:
            raise ValueError("step count must match lattice dimensions")
        coords = tuple(coord + step for coord, step in zip(start.coordinates, steps))
        return LatticePoint(coords)


def mmf_projection_for_entry(
    entry_class: str,
    token_primes: Sequence[int],
    content_type: str | None = None,
) -> dict[str, PrimeLatticeState]:
    """Return per-domain ``PrimeLatticeState`` projections for an entry.

    This is the canonical reusable wrapper around the entry-class-specific
    domain mapping in ``informational_unit.py``.  It returns lattice states
    rather than raw exponent dicts so downstream code can use meet/join/delta
    operations directly.
    """
    exponents = build_mmf_projection_exponents(entry_class, token_primes, content_type)
    return {
        domain: PrimeLatticeState(domain_exponents)
        for domain, domain_exponents in exponents.items()
    }


def phi_d(primes: Sequence[int], domain: str) -> PrimeLatticeState:
    """Project a sequence of token primes onto a single MMF domain cube.

    ``phi_d`` is deterministic and idempotent: applying it twice yields the
    same lattice state as applying it once.
    """
    if domain not in MMF_DOMAINS:
        raise ValueError(f"unknown MMF domain: {domain}")
    allowed = set(MMF_DOMAINS[domain])
    filtered = [int(p) for p in primes if int(p) in allowed]
    return PrimeLatticeState.from_primes(filtered)


def psi_d(state: PrimeLatticeState, domain: str) -> dict[int, int]:
    """Extract the exponent vector of a lattice state restricted to one domain.

    Together, ``phi_d`` and ``psi_d`` form a reversible round-trip for the
    domain-restricted component of a prime lattice:

        psi_d(phi_d(primes, domain), domain) == Counter(primes filtered to domain)
    """
    if domain not in MMF_DOMAINS:
        raise ValueError(f"unknown MMF domain: {domain}")
    return {
        prime: state.valuation(prime)
        for prime in MMF_DOMAINS[domain]
        if state.valuation(prime) > 0
    }


def validate_domain_isolation(projections: Mapping[str, Mapping[int, Any]]) -> bool:
    """Return True iff no prime is shared across two MMF domain projections.

    The Domain Isolation Invariant requires that each MMF domain owns a
    disjoint 8-prime namespace.  A prime appearing in more than one projection
    (or a prime outside the declared domain cube) violates the invariant.

    Kernel primes are allowed to coexist with domain primes in separate fields
    because kernel and MMF namespaces are semantically distinct.  The two
    kernel bridge primes (137 and 139) are intentionally shared with the
    auditory and olfactory MMF domains and do not violate this helper, which
    focuses on cross-domain isolation.
    """
    seen: set[int] = set()
    for domain, exponents in projections.items():
        allowed = set(MMF_DOMAINS.get(domain, ()))
        if not allowed:
            return False
        for prime in exponents:
            p = int(prime)
            if p not in allowed:
                return False
            if p in seen:
                return False
            seen.add(p)
    return True
