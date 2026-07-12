"""Shared S1/S2 state scaffolding for the substrate memory model."""

from __future__ import annotations

from typing import Dict, List

from backend.fieldx_kernel.schema import LAW_PRIME, GRACE_PRIME, S_PRIMES, A_PRIMES


S1_PRIMES: List[int] = list(S_PRIMES)
S2_PRIMES: List[int] = list(A_PRIMES)
MEDIATOR_PRIMES: List[int] = [LAW_PRIME, GRACE_PRIME]

# Tier metadata describing prime roles and allocation eligibility.
TIER_SCHEMA: Dict[str, Dict[str, List[int] | bool]] = {
    "S1": {"primes": S1_PRIMES, "allocatable": False},
    "S2": {"primes": S2_PRIMES, "allocatable": False},
    "C": {"primes": MEDIATOR_PRIMES, "allocatable": False},
    # Body primes live in tier B (allocated dynamically starting at 23+).
    # The list is kept empty because these primes are discovered at runtime.
    "B": {"primes": [], "allocatable": True},
}


def default_S1() -> Dict[str, dict]:
    """Return an empty S1 state structure.

    S1 holds references to body primes and lightweight metadata only; it never
    stores raw text. Each tier is keyed by its prime and contains a ``refs``
    list plus an optional ``metadata`` dictionary for auxiliary signals.
    """

    return {str(prime): {"refs": [], "metadata": {}} for prime in S1_PRIMES}


def default_S2() -> Dict[str, dict]:
    """Return an empty S2 state structure.

    S2 captures higher-level metadata. Keys are stringified primes to preserve
    stability when serialised to JSON.
    """

    return {
        "11": {"summary_ref": None, "metadata": {}},
        "13": {"taxonomy": [], "metadata": {}},
        "17": {"linkmap": [], "metadata": {}},
        "19": {"claims": [], "metadata": {}},
    }


def default_mediators() -> Dict[str, dict]:
    """Return an empty mediator tier state structure.

    Mediator primes (tier C) are reserved and store metadata such as ethics
    diagnostics. They do not hold flow-enforced links to body primes.
    """

    return {str(prime): {"metadata": {}} for prime in MEDIATOR_PRIMES}


__all__ = [
    "S1_PRIMES",
    "S2_PRIMES",
    "LAW_PRIME",
    "GRACE_PRIME",
    "MEDIATOR_PRIMES",
    "TIER_SCHEMA",
    "default_S1",
    "default_S2",
    "default_mediators",
]
