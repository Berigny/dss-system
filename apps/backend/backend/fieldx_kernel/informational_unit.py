"""Core informational unit for ledger entries aligned to the 0-9 Computational Lattice.

The unit is intentionally split into reusable schema/components and entry-specific
state:

* Reusable across all entry classes: the kernel attribute schema, lattice
  operations, flow-rule tag structure, relationship-link structure, and
  serialization format.
* Entry-specific / not reusable: actual exponent values, activated MMF domains,
  content-derived token primes, and concrete coordinate relationships.

Prime namespaces
----------------
* Kernel primes (shared): 2, 3, 5, 7, 11, 13, 17, 19, 137, 139.
* Kernel bridge primes (shared with MMF): 137, 139. These mediate between the
  kernel central axis (EQ8/9) and the auditory/olfactory MMF domains.
* Body-tier recall primes (shared): 23, 29, 31, ...
* MMF domain primes (isolated per domain): verbal, visual, auditory, olfactory,
  spatial, behavioral cubes from Computational-Lattice.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from backend.fieldx_kernel.p_adic import PrimeLatticeState
from backend.fieldx_kernel.qp_coordinate import derive_p_adic_coordinate

if TYPE_CHECKING:
    from backend.fieldx_kernel.models import LedgerEntry


# ---------------------------------------------------------------------------
# 0-9 Computational Lattice prime assignments
# ---------------------------------------------------------------------------

KERNEL_EQ_TO_PRIME: dict[int, int] = {
    0: 2,    # Null State
    1: 3,    # Boundary Operator
    2: 5,    # Temporalization
    3: 7,    # Geometric Closure
    4: 11,   # Coupling Constant
    5: 13,   # Persistence Cost
    6: 17,   # State Auditor
    7: 19,   # Coherence Norm
    8: 137,  # Admissibility Gate
    9: 139,  # Terminal Optimizer
}

KERNEL_PRIME_TO_EQ: dict[int, int] = {p: eq for eq, p in KERNEL_EQ_TO_PRIME.items()}

KERNEL_PRIMES: tuple[int, ...] = tuple(KERNEL_EQ_TO_PRIME.values())

# Bridge primes are shared between the kernel central axis (EQ8/9) and selected
# MMF domain cubes. They are intentionally exempt from the strict S1/S2 node
# isolation rule because they mediate between the kernel and sensory domains.
KERNEL_BRIDGE_PRIMES: frozenset[int] = frozenset({137, 139})

BODY_TIER_START: int = 23
BODY_TIER_PRIMES: tuple[int, ...] = (BODY_TIER_START,)

# Multi-Modal Foundation domain cubes from Computational-Lattice.md Section 12.
MMF_DOMAINS: dict[str, tuple[int, ...]] = {
    "verbal": (29, 31, 37, 41, 43, 47, 53, 59),
    "visual": (61, 67, 71, 73, 79, 83, 89, 97),
    "auditory": (101, 103, 107, 109, 113, 127, 131, 137),
    "olfactory": (139, 149, 151, 157, 163, 167, 173, 179),
    "spatial": (181, 191, 193, 197, 199, 211, 223, 227),
    "behavioral": (229, 233, 239, 241, 251, 257, 263, 269),
}

MMF_DOMAIN_FACES: dict[str, tuple[str, str, str, str]] = {
    "verbal": ("S1-N0", "S1-N1", "S2-N1", "S2-N0"),
    "visual": ("S1-N1", "S1-N2", "S2-N2", "S2-N1"),
    "auditory": ("S1-N2", "S1-N3", "S2-N3", "S2-N2"),
    "olfactory": ("S1-N3", "S1-N0", "S2-N0", "S2-N3"),
    "spatial": ("S1-N0", "S1-N1", "S1-N2", "S1-N3"),
    "behavioral": ("S2-N0", "S2-N1", "S2-N2", "S2-N3"),
}

ALL_RESERVED_PRIMES: frozenset[int] = frozenset(
    list(KERNEL_PRIMES)
    + list(BODY_TIER_PRIMES)
    + [p for primes in MMF_DOMAINS.values() for p in primes]
)

# Core informational unit metadata keys.
CIU_KERNEL_EXPONENTS = "kernel_prime_exponents"
CIU_MMF_PROJECTIONS = "mmf_projection_exponents"
CIU_FLOW_RULE_TAGS = "flow_rule_tags"
CIU_RELATIONSHIP_LINKS = "relationship_links"
CIU_FACTORS = "factors"
CIU_ENTRY_CLASS = "core_info_entry_class"
CIU_P_ADIC_COORDINATE = "p_adic_coordinate"


# ---------------------------------------------------------------------------
# Entry-class inference and mapping
# ---------------------------------------------------------------------------

def entry_class_for_metadata(metadata: Mapping[str, Any], phase: str | None = None) -> str:
    """Infer a coarse entry class from existing metadata and optional phase."""
    if phase == "attachment":
        return "attachment"
    kind = str(metadata.get("kind") or "").lower()
    if kind in {"turn", "chat"}:
        return "turn"
    if kind in {"topic", "taxon", "claim"}:
        return "topic"
    if kind in {"event", "walk"}:
        return "event"
    if kind in {"attachment", "attachment_part"}:
        return "attachment"
    if phase == "chat":
        return "turn"
    return "turn"


def default_mmf_domains_for_entry(entry_class: str, content_type: str | None = None) -> list[str]:
    """Return the canonical MMF domains activated for an entry class.

    The mapping is intentionally conservative: text-heavy entries activate the
    verbal domain; other domains are added as multimodal inputs become
    available.
    """
    if entry_class == "attachment":
        ctype = str(content_type or "text").lower()
        if ctype.startswith("image"):
            return ["visual"]
        if ctype.startswith("audio"):
            return ["auditory"]
        return ["verbal"]
    if entry_class == "topic":
        return ["verbal", "spatial"]
    if entry_class == "event":
        return ["behavioral"]
    # Default for turns and unknown classes.
    return ["verbal"]


def default_kernel_exponents_for_entry(entry_class: str) -> dict[int, int]:
    """Return a conservative kernel activation vector for an entry class.

    All written entries activate the Persistence Cost node (EQ5 / prime 13)
    because they represent state that must be sustained, and the Recall node
    (body tier / prime 23) because they are archived for later retrieval.
    """
    exponents: dict[int, int] = {}
    exponents[KERNEL_EQ_TO_PRIME[5]] = 1  # Persistence Cost
    exponents[BODY_TIER_START] = 1         # Recall
    if entry_class == "turn":
        exponents[KERNEL_EQ_TO_PRIME[6]] = 1  # State Auditor
    elif entry_class == "event":
        exponents[KERNEL_EQ_TO_PRIME[2]] = 1  # Temporalization
    return exponents


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _as_exponent_dict(value: Any) -> dict[int, int]:
    """Coerce a mapping into a prime -> exponent dict with int keys."""
    result: dict[int, int] = {}
    if not isinstance(value, Mapping):
        return result
    for k, v in value.items():
        try:
            prime = int(k)
            exp = int(v)
        except Exception:
            continue
        if prime > 1 and exp > 0:
            result[prime] = result.get(prime, 0) + exp
    return result


def build_mmf_projection_exponents(
    entry_class: str,
    token_primes: Sequence[int],
    content_type: str | None = None,
) -> dict[str, dict[int, int]]:
    """Map token primes into the canonical MMF domain cubes for the entry class.

    Token primes that do not belong to an activated domain are ignored. This
    keeps domain cubes isolated and avoids leaking kernel/body primes into MMF
    projections.
    """
    projections: dict[str, dict[int, int]] = {}
    domains = default_mmf_domains_for_entry(entry_class, content_type)
    for domain in domains:
        domain_primes = set(MMF_DOMAINS[domain])
        exponents: dict[int, int] = {}
        for prime in token_primes:
            if prime in domain_primes:
                exponents[prime] = exponents.get(prime, 0) + 1
        if exponents:
            projections[domain] = exponents
    return projections


def build_core_informational_unit(
    entry_class: str,
    token_primes: Sequence[int],
    content_type: str | None = None,
    relationships: Sequence[Mapping[str, Any]] | None = None,
    flow_rule_tags: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build the core informational unit payload for a ledger entry.

    The returned dict is intended to be merged into `state.metadata`.
    """
    kernel_exponents = dict(default_kernel_exponents_for_entry(entry_class))
    # Token primes that belong to the kernel or body tier also contribute to
    # the shared lattice. Domain primes are routed separately.
    for prime in token_primes:
        if prime in KERNEL_PRIME_TO_EQ or prime >= BODY_TIER_START:
            kernel_exponents[prime] = kernel_exponents.get(prime, 0) + 1

    mmf_projections = build_mmf_projection_exponents(entry_class, token_primes, content_type)

    # Flat factor list for retrieval paths that use p_adic_distance_for_factors.
    factors = [{"prime": int(p), "delta": int(e)} for p, e in sorted(kernel_exponents.items())]
    for domain_exponents in mmf_projections.values():
        for prime, exp in sorted(domain_exponents.items()):
            factors.append({"prime": int(prime), "delta": int(exp)})

    links: list[dict[str, Any]] = []
    for rel in relationships or []:
        if not isinstance(rel, Mapping):
            continue
        coord = rel.get("coord") or rel.get("coordinate")
        if not coord:
            continue
        links.append({
            "coord": str(coord),
            "relation": str(rel.get("relation") or "related"),
            "shared_primes": list(rel.get("shared_primes", [])),
        })

    return {
        CIU_ENTRY_CLASS: entry_class,
        CIU_KERNEL_EXPONENTS: kernel_exponents,
        CIU_MMF_PROJECTIONS: mmf_projections,
        CIU_FACTORS: factors,
        CIU_FLOW_RULE_TAGS: list(flow_rule_tags or []),
        CIU_RELATIONSHIP_LINKS: links,
    }


def attach_core_informational_unit(
    entry: "LedgerEntry",
    relationships: Sequence[Mapping[str, Any]] | None = None,
    flow_rule_tags: Sequence[str] | None = None,
) -> None:
    """Populate core informational unit fields on a ledger entry's metadata.

    Existing fields are overwritten. The function derives the entry class from
    the entry phase/kind and uses the entry's already-computed token primes.
    """
    metadata = dict(entry.state.metadata)
    entry_class = entry_class_for_metadata(metadata, phase=entry.state.phase)
    token_primes = metadata.get("token_primes", [])
    if not isinstance(token_primes, list):
        token_primes = []
    content_type = metadata.get("content_type") or metadata.get("mime_type")

    # Derive relationships from known metadata fields when not supplied.
    if relationships is None:
        relationships = []
        parent = metadata.get("parent_coord") or metadata.get("in_reply_to")
        if parent:
            relationships.append({"coord": parent, "relation": "parent"})
        for part in metadata.get("attachment_parts", []) or []:
            if isinstance(part, str):
                relationships.append({"coord": part, "relation": "attachment_part"})
            elif isinstance(part, Mapping) and part.get("coord"):
                relationships.append({"coord": part["coord"], "relation": "attachment_part"})

    unit = build_core_informational_unit(
        entry_class=entry_class,
        token_primes=token_primes,
        content_type=content_type,
        relationships=relationships,
        flow_rule_tags=flow_rule_tags,
    )
    metadata.update(unit)

    # Derive the canonical QpCoordinate from the kernel exponents for retrieval,
    # but do not overwrite an explicitly supplied coordinate. This lets callers
    # anchor entries with retrieval probes that do not match the lexical content.
    if CIU_P_ADIC_COORDINATE not in metadata:
        coord = derive_p_adic_coordinate(metadata)
        if coord is not None:
            metadata[CIU_P_ADIC_COORDINATE] = coord.as_dict()

    entry.state.metadata = metadata


# ---------------------------------------------------------------------------
# Readers / validators
# ---------------------------------------------------------------------------

def validate_core_informational_unit(metadata: Mapping[str, Any]) -> list[str]:
    """Return a list of validation errors for the core informational unit."""
    errors: list[str] = []
    kernel = _as_exponent_dict(metadata.get(CIU_KERNEL_EXPONENTS))
    for prime in kernel:
        if prime not in ALL_RESERVED_PRIMES:
            errors.append(f"kernel exponent uses unknown prime {prime}")

    projections = metadata.get(CIU_MMF_PROJECTIONS)
    if isinstance(projections, Mapping):
        for domain, exponents in projections.items():
            if domain not in MMF_DOMAINS:
                errors.append(f"unknown MMF domain {domain}")
                continue
            allowed = set(MMF_DOMAINS[domain])
            exp_dict = _as_exponent_dict(exponents)
            for prime in exp_dict:
                if prime not in allowed:
                    errors.append(f"domain {domain} contains non-domain prime {prime}")
        # Domain isolation: no prime may appear in two domains.
        seen: dict[int, str] = {}
        for domain, exponents in projections.items():
            if domain not in MMF_DOMAINS:
                continue
            exp_dict = _as_exponent_dict(exponents)
            for prime in exp_dict:
                if prime in seen:
                    errors.append(
                        f"prime {prime} appears in both {seen[prime]} and {domain}"
                    )
                else:
                    seen[prime] = domain
    return errors


def kernel_state_from_metadata(metadata: Mapping[str, Any]) -> PrimeLatticeState:
    """Return the kernel + body-tier lattice state from metadata."""
    return PrimeLatticeState(_as_exponent_dict(metadata.get(CIU_KERNEL_EXPONENTS)))


def mmf_state_from_metadata(
    metadata: Mapping[str, Any], domain: str
) -> PrimeLatticeState | None:
    """Return the lattice state for a single MMF domain, or None if absent."""
    projections = metadata.get(CIU_MMF_PROJECTIONS)
    if not isinstance(projections, Mapping):
        return None
    exponents = projections.get(domain)
    if not isinstance(exponents, Mapping):
        return None
    return PrimeLatticeState(_as_exponent_dict(exponents))


def all_mmf_states_from_metadata(
    metadata: Mapping[str, Any]
) -> dict[str, PrimeLatticeState]:
    """Return all present MMF domain lattice states keyed by domain name."""
    result: dict[str, PrimeLatticeState] = {}
    projections = metadata.get(CIU_MMF_PROJECTIONS)
    if not isinstance(projections, Mapping):
        return result
    for domain, exponents in projections.items():
        if domain in MMF_DOMAINS and isinstance(exponents, Mapping):
            result[domain] = PrimeLatticeState(_as_exponent_dict(exponents))
    return result


def information_unit_distance(
    a_metadata: Mapping[str, Any],
    b_metadata: Mapping[str, Any],
    *,
    metric_prime: int = 5,
) -> float:
    """Return the p-adic distance between two kernel lattice states.

    The kernel lattice captures the shared computational-lattice structure;
    MMF projections are compared separately per domain.
    """
    from backend.fieldx_kernel.p_adic import p_adic_distance_for_factors

    a_state = kernel_state_from_metadata(a_metadata)
    b_state = kernel_state_from_metadata(b_metadata)
    return p_adic_distance_for_factors(
        [{"prime": p, "delta": e} for p, e in a_state.exponents.items()],
        [{"prime": p, "delta": e} for p, e in b_state.exponents.items()],
        metric_prime=metric_prime,
    )[0]


__all__ = [
    "KERNEL_EQ_TO_PRIME",
    "KERNEL_PRIME_TO_EQ",
    "KERNEL_PRIMES",
    "BODY_TIER_START",
    "BODY_TIER_PRIMES",
    "MMF_DOMAINS",
    "MMF_DOMAIN_FACES",
    "CIU_KERNEL_EXPONENTS",
    "CIU_MMF_PROJECTIONS",
    "CIU_FLOW_RULE_TAGS",
    "CIU_RELATIONSHIP_LINKS",
    "CIU_FACTORS",
    "CIU_ENTRY_CLASS",
    "entry_class_for_metadata",
    "default_mmf_domains_for_entry",
    "default_kernel_exponents_for_entry",
    "build_core_informational_unit",
    "attach_core_informational_unit",
    "validate_core_informational_unit",
    "kernel_state_from_metadata",
    "mmf_state_from_metadata",
    "all_mmf_states_from_metadata",
    "information_unit_distance",
]
