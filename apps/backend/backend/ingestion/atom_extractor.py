"""Semantic atom extraction mapped to COORD branches and prime gates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from backend.kernel import constants


@dataclass(frozen=True)
class SemanticAtom:
    """A semantic atom extracted from a chunk.

    Each atom maps to a COORD branch and triggers exactly one of the three
    core prime gates (awareness=5, unity=7, ethics=2).
    """

    coord: str  # Full COORD path including v-level, e.g. ethics/lawfulness/refusal/v3
    branch: str  # Branch path without v-level, e.g. ethics/lawfulness/refusal
    prime: int
    v: int
    keywords: tuple[str, ...] = field(default_factory=tuple)


# Core prime → gate dimension mapping from HENGE-001.
_PRIME_TO_DIMENSION: dict[int, str] = {
    constants.QUATERNARY_GATE_TO_PRIME["awareness"]: "awareness",
    constants.QUATERNARY_GATE_TO_PRIME["unity"]: "unity",
    constants.QUATERNARY_GATE_TO_PRIME["ethics"]: "ethics",
}

# Heuristic keyword rules. Each rule maps a regex to a COORD branch. The branch's
# primary dimension determines the prime gate; telos atoms are expanded to all
# three primes because purpose binds awareness, unity, and ethics.
_BRANCH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(refuse|decline|reject|violate|unethical|ethics|ethical|wrong)\b", re.IGNORECASE), "ethics/lawfulness/refusal"),
    (re.compile(r"\b(harm|hurt|damage|safety|protect|avoid|risk)\b", re.IGNORECASE), "ethics/lawfulness/harm_avoidance"),
    (re.compile(r"\b(attention|focus|aware|notice|observe|recognize|conscious)\b", re.IGNORECASE), "awareness/attention/focus"),
    (re.compile(r"\b(signal|detect|pattern|anomaly|alert|sense)\b", re.IGNORECASE), "awareness/attention/signal_detection"),
    (re.compile(r"\b(together|align|unity|coherent|consistent|integrate|harmonize)\b", re.IGNORECASE), "unity/coherence/alignment"),
    (re.compile(r"\b(collaborate|cooperate|shared|collective|team|mutual)\b", re.IGNORECASE), "unity/coherence/collaboration"),
    (re.compile(r"\b(goal|purpose|aim|objective|target|mission)\b", re.IGNORECASE), "telos/purpose/goal"),
    (re.compile(r"\b(intent|intend|plan|desire|will|resolve)\b", re.IGNORECASE), "telos/purpose/intent"),
]


def _dimension_for_branch(branch: str) -> str:
    """Return the primary dimension (awareness/unity/ethics/telos) for a branch."""
    return branch.split("/", 1)[0]


def _prime_for_dimension(dimension: str) -> int | None:
    """Return the core prime for an awareness/unity/ethics dimension."""
    return constants.QUATERNARY_GATE_TO_PRIME.get(dimension)


def _make_atom(branch: str, prime: int, matched_keywords: Iterable[str]) -> SemanticAtom:
    v = 3  # Default fertile-pending exponent; keeps individual chunks in LOAM unless reinforced.
    keywords = tuple(sorted(set(matched_keywords)))
    return SemanticAtom(
        coord=f"{branch}/v{v}",
        branch=branch,
        prime=prime,
        v=v,
        keywords=keywords,
    )


def extract_atoms(text: str) -> list[SemanticAtom]:
    """Extract semantic atoms from ``text``.

    Each atom triggers at least one prime gate (5, 7, or 2). The returned list
    is ordered by appearance in the text.
    """
    atoms: list[SemanticAtom] = []
    seen: set[tuple[str, int]] = set()

    for pattern, branch in _BRANCH_RULES:
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        matched_keywords = [m.group(0).lower() for m in matches]
        dimension = _dimension_for_branch(branch)

        if dimension == "telos":
            # Purpose atoms bind all three core primes.
            for prime in (5, 7, 2):
                key = (branch, prime)
                if key in seen:
                    continue
                seen.add(key)
                atoms.append(_make_atom(branch, prime, matched_keywords))
        else:
            prime = _prime_for_dimension(dimension)
            if prime is None:
                continue
            key = (branch, prime)
            if key in seen:
                continue
            seen.add(key)
            atoms.append(_make_atom(branch, prime, matched_keywords))

    return atoms
