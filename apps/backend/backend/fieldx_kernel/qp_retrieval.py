"""Pure Qp retrieval helpers (DS-REVIEW-193 P2-04).

This module provides the shared plumbing for ranking retrieval candidates by
``qp_distance`` on ``QpElement`` rational representatives.  It is kept separate
from the mixed (legacy) retrieval paths so that pure and non-pure modes are
explicitly distinguishable.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Mapping, Sequence

from backend.fieldx_kernel.informational_unit import KERNEL_PRIME_TO_EQ
from backend.fieldx_kernel.qp_arithmetic import qp_score
from backend.fieldx_kernel.qp_coordinate import (
    QpCoordinate,
    circulation_depth_compatible,
    derive_p_adic_coordinate,
    dual_state_compatible,
    mediator_state_compatible,
    qp_coordinate_distance,
)

logger = logging.getLogger(__name__)


def extract_qp_coordinate(payload: Mapping[str, Any] | None) -> QpCoordinate | None:
    """Return a ``QpCoordinate`` stored in ``payload`` or its metadata, if any."""
    if not isinstance(payload, Mapping):
        return None
    for source in (payload, payload.get("metadata"), payload.get("state", {}).get("metadata")):
        if not isinstance(source, Mapping):
            continue
        coord_data = source.get("p_adic_coordinate")
        if isinstance(coord_data, Mapping):
            try:
                return QpCoordinate.from_dict(coord_data)
            except Exception as exc:
                logger.debug("Failed to deserialize QpCoordinate: %s", exc)
                continue
    return None


def _kernel_exponents_from_factors(
    factors: Sequence[Mapping[str, Any]] | None,
) -> dict[int, int]:
    """Build ``kernel_prime_exponents`` from core-info style factor lists."""
    exponents: Counter = Counter()
    if not factors:
        return {}
    for factor in factors:
        if not isinstance(factor, Mapping):
            continue
        prime = factor.get("prime")
        delta = factor.get("delta", 1)
        try:
            prime_int = int(prime)
            delta_int = int(delta)
        except (TypeError, ValueError):
            continue
        if prime_int in KERNEL_PRIME_TO_EQ:
            exponents[prime_int] += delta_int
    return dict(exponents)


def derive_query_coordinate_from_factors(
    factors: Sequence[Mapping[str, Any]] | None,
    working_precision: int = 16,
) -> QpCoordinate | None:
    """Derive a query ``QpCoordinate`` from a factor list.

    Only kernel primes are used; non-kernel primes are ignored so the query
    coordinate lives in the same metric prime as the stored candidate
    coordinates.
    """
    kernel_exponents = _kernel_exponents_from_factors(factors)
    if not kernel_exponents:
        return None
    return derive_p_adic_coordinate(
        {"kernel_prime_exponents": kernel_exponents},
        working_precision=working_precision,
    )


def derive_query_coordinate_from_primes(
    primes: Sequence[int],
    working_precision: int = 16,
) -> QpCoordinate | None:
    """Derive a query ``QpCoordinate`` from a list of token/kernel primes."""
    factors = [{"prime": int(p), "delta": 1} for p in primes if isinstance(p, int) and p > 1]
    return derive_query_coordinate_from_factors(factors, working_precision=working_precision)


def qp_pure_compatible(
    query: QpCoordinate | None,
    candidate: QpCoordinate | None,
    *,
    max_pass_delta: int = 1,
    max_depth_delta: float = 1.0,
) -> bool:
    """Return True when ``candidate`` passes the pure-Qp compatibility filters.

    The filters are applied separately from the distance function, as required
    by the retrieval-law design.
    """
    if query is None or candidate is None:
        return False
    if query.metric_prime != candidate.metric_prime:
        return False
    return (
        circulation_depth_compatible(
            query, candidate, max_pass_delta=max_pass_delta, max_depth_delta=max_depth_delta
        )
        and dual_state_compatible(query, candidate)
        and mediator_state_compatible(query, candidate)
    )


def qp_pure_distance(query: QpCoordinate, candidate: QpCoordinate) -> float | None:
    """Return ``qp_coordinate_distance`` or ``None`` on incompatibility/error."""
    try:
        return float(qp_coordinate_distance(query, candidate))
    except Exception as exc:
        logger.debug("Qp distance computation failed: %s", exc)
        return None


def qp_pure_rank_score(distance: float, metric_prime: int, working_precision: int) -> float:
    """Map an ultrametric distance to a retrieval score in ``[0, 1]``."""
    return float(qp_score(distance, metric_prime, working_precision))


__all__ = [
    "derive_query_coordinate_from_factors",
    "derive_query_coordinate_from_primes",
    "extract_qp_coordinate",
    "qp_pure_compatible",
    "qp_pure_distance",
    "qp_pure_rank_score",
]
