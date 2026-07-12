from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Protocol, Sequence

from backend.fieldx_kernel.e6_packet import pack_header_v0, unpack_header_v0


class MMFDomain(str, Enum):
    VERBAL = "verbal"
    VISUAL = "visual"
    AUDITORY = "auditory"
    OLFACTORY = "olfactory"
    SPATIAL = "spatial"
    BEHAVIORAL = "behavioral"


MMF_DOMAIN_SET: tuple[str, ...] = tuple(domain.value for domain in MMFDomain)

MMF_KERNEL_NODES: tuple[str, ...] = (
    "S1-N0",
    "S1-N1",
    "S1-N2",
    "S1-N3",
    "S2-N0",
    "S2-N1",
    "S2-N2",
    "S2-N3",
)

MMF_CANONICAL_FACES: tuple[tuple[str, str, str, str], ...] = (
    ("S1-N0", "S1-N1", "S2-N1", "S2-N0"),
    ("S1-N1", "S1-N2", "S2-N2", "S2-N1"),
    ("S1-N2", "S1-N3", "S2-N3", "S2-N2"),
    ("S1-N3", "S1-N0", "S2-N0", "S2-N3"),
    ("S1-N0", "S1-N1", "S1-N2", "S1-N3"),
    ("S2-N0", "S2-N1", "S2-N2", "S2-N3"),
)

MMF_DOMAIN_FACE_MAP: Mapping[str, tuple[str, str, str, str]] = {
    MMFDomain.VERBAL.value: MMF_CANONICAL_FACES[0],
    MMFDomain.VISUAL.value: MMF_CANONICAL_FACES[1],
    MMFDomain.AUDITORY.value: MMF_CANONICAL_FACES[2],
    MMFDomain.OLFACTORY.value: MMF_CANONICAL_FACES[3],
    MMFDomain.SPATIAL.value: MMF_CANONICAL_FACES[4],
    MMFDomain.BEHAVIORAL.value: MMF_CANONICAL_FACES[5],
}

# Canonical kernel ring primes from topology schema/flow rules.
MMF_KERNEL_PRIME_MAP: Mapping[str, int] = {
    "S1-N0": 2,
    "S1-N1": 3,
    "S1-N2": 5,
    "S1-N3": 7,
    "S2-N0": 11,
    "S2-N1": 13,
    "S2-N2": 17,
    "S2-N3": 19,
}

# DS-REVIEW-035 invariant: each domain owns its own 8-prime cube.
MMF_DOMAIN_PRIME_MAP: Mapping[str, tuple[int, int, int, int, int, int, int, int]] = {
    MMFDomain.VERBAL.value: (29, 31, 37, 41, 43, 47, 53, 59),
    MMFDomain.VISUAL.value: (61, 67, 71, 73, 79, 83, 89, 97),
    MMFDomain.AUDITORY.value: (101, 103, 107, 109, 113, 127, 131, 137),
    MMFDomain.OLFACTORY.value: (139, 149, 151, 157, 163, 167, 173, 179),
    MMFDomain.SPATIAL.value: (181, 191, 193, 197, 199, 211, 223, 227),
    MMFDomain.BEHAVIORAL.value: (229, 233, 239, 241, 251, 257, 263, 269),
}

# Backward-compatible alias; values now encode the full domain cube rather than extension-only primes.
MMF_DOMAIN_EXTENSION_PRIME_MAP: Mapping[str, tuple[int, int, int, int, int, int, int, int]] = MMF_DOMAIN_PRIME_MAP


@dataclass(frozen=True)
class MMFPrimeTopology:
    topology: str
    anchor_nodes: tuple[str, ...]
    anchor_primes: tuple[int, ...]
    extension_primes: tuple[int, ...]
    cube_primes: tuple[int, ...]


def validate_domain_set(domains: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(str(item).strip().lower() for item in domains)
    if len(normalized) != 6:
        raise ValueError("mmf domain cardinality must be exactly six")
    if len(set(normalized)) != 6:
        raise ValueError("mmf domain set must not contain duplicates")

    unknown = sorted(set(normalized) - set(MMF_DOMAIN_SET))
    if unknown:
        raise ValueError(f"unknown mmf domains: {', '.join(unknown)}")

    missing = sorted(set(MMF_DOMAIN_SET) - set(normalized))
    if missing:
        raise ValueError(f"missing mmf domains: {', '.join(missing)}")

    return normalized


def validate_domain_face_map(face_map: Mapping[str, Sequence[str]] = MMF_DOMAIN_FACE_MAP) -> Dict[str, tuple[str, str, str, str]]:
    domains = validate_domain_set(tuple(face_map.keys()))

    canonical_faces = {frozenset(face) for face in MMF_CANONICAL_FACES}
    assigned_faces: set[frozenset[str]] = set()
    normalized: Dict[str, tuple[str, str, str, str]] = {}
    for domain in domains:
        face_raw = tuple(str(node).strip() for node in face_map[domain])
        if len(face_raw) != 4:
            raise ValueError(f"domain {domain} must map to exactly 4 face nodes")
        if len(set(face_raw)) != 4:
            raise ValueError(f"domain {domain} face contains duplicate nodes")

        unknown_nodes = sorted(set(face_raw) - set(MMF_KERNEL_NODES))
        if unknown_nodes:
            raise ValueError(f"domain {domain} face has unknown kernel nodes: {', '.join(unknown_nodes)}")

        face_set = frozenset(face_raw)
        if face_set not in canonical_faces:
            raise ValueError(f"domain {domain} face is not a canonical kernel cube face")
        if face_set in assigned_faces:
            raise ValueError(f"duplicate face assignment detected for domain {domain}")

        assigned_faces.add(face_set)
        normalized[domain] = face_raw

    if assigned_faces != canonical_faces:
        raise ValueError("domain face assignments must cover all six canonical faces exactly once")

    return normalized


def validate_kernel_prime_map(kernel_primes: Mapping[str, int] = MMF_KERNEL_PRIME_MAP) -> Dict[str, int]:
    if set(kernel_primes.keys()) != set(MMF_KERNEL_NODES):
        raise ValueError("kernel prime map must define all 8 canonical kernel nodes")

    normalized = {str(node): int(prime) for node, prime in kernel_primes.items()}
    primes = tuple(normalized.values())
    if len(set(primes)) != len(primes):
        raise ValueError("kernel prime map must contain unique primes")
    if any(prime <= 1 for prime in primes):
        raise ValueError("kernel prime map contains invalid prime value")

    return normalized


def validate_domain_extension_prime_map(
    extension_map: Mapping[str, Sequence[int]] = MMF_DOMAIN_EXTENSION_PRIME_MAP,
    *,
    kernel_primes: Mapping[str, int] = MMF_KERNEL_PRIME_MAP,
) -> Dict[str, tuple[int, int, int, int, int, int, int, int]]:
    validate_domain_set(tuple(extension_map.keys()))
    kernel_prime_values = set(validate_kernel_prime_map(kernel_primes).values())

    normalized: Dict[str, tuple[int, int, int, int, int, int, int, int]] = {}
    all_extensions: list[int] = []
    for domain in MMF_DOMAIN_SET:
        ext = tuple(int(value) for value in extension_map[domain])
        if len(ext) != 8:
            raise ValueError(f"domain {domain} must define exactly 8 domain primes")
        if len(set(ext)) != 8:
            raise ValueError(f"domain {domain} domain primes must be unique")
        if any(prime in kernel_prime_values for prime in ext):
            raise ValueError(f"domain {domain} domain primes overlap kernel primes")
        normalized[domain] = ext
        all_extensions.extend(ext)

    if len(set(all_extensions)) != len(all_extensions):
        raise ValueError("domain prime set must be globally unique across all domains")

    expected = mmf_required_extension_prime_count(domains=6, anchors_per_domain=0, cube_size=8)
    if len(all_extensions) != expected:
        raise ValueError(f"expected {expected} domain primes, got {len(all_extensions)}")

    return normalized


def build_mmf_prime_topologies(
    *,
    face_map: Mapping[str, Sequence[str]] = MMF_DOMAIN_FACE_MAP,
    kernel_primes: Mapping[str, int] = MMF_KERNEL_PRIME_MAP,
    extension_map: Mapping[str, Sequence[int]] = MMF_DOMAIN_EXTENSION_PRIME_MAP,
) -> Dict[str, MMFPrimeTopology]:
    normalized_faces = validate_domain_face_map(face_map)
    normalized_kernel = validate_kernel_prime_map(kernel_primes)
    normalized_extensions = validate_domain_extension_prime_map(extension_map, kernel_primes=normalized_kernel)

    topologies: Dict[str, MMFPrimeTopology] = {
        "kernel": MMFPrimeTopology(
            topology="kernel",
            anchor_nodes=tuple(MMF_KERNEL_NODES),
            anchor_primes=tuple(normalized_kernel[node] for node in MMF_KERNEL_NODES),
            extension_primes=(),
            cube_primes=tuple(normalized_kernel[node] for node in MMF_KERNEL_NODES),
        )
    }

    for domain in MMF_DOMAIN_SET:
        face_nodes = tuple(normalized_faces[domain])
        domain_cube_primes = tuple(normalized_extensions[domain])
        face_primes = domain_cube_primes[:4]
        extension_primes = domain_cube_primes[4:]
        topologies[domain] = MMFPrimeTopology(
            topology=domain,
            anchor_nodes=face_nodes,
            anchor_primes=face_primes,
            extension_primes=extension_primes,
            cube_primes=domain_cube_primes,
        )

    return topologies


def mmf_required_extension_prime_count(*, domains: int = 6, anchors_per_domain: int = 4, cube_size: int = 8) -> int:
    """DS-REVIEW-035: each domain owns its full cube of unique primes."""
    if domains <= 0:
        raise ValueError("domains must be positive")
    if anchors_per_domain < 0 or cube_size <= 0 or anchors_per_domain > cube_size:
        raise ValueError("invalid anchor/cube size inputs")
    return domains * cube_size


@lru_cache(maxsize=1)
def canonical_mmf_prime_topologies() -> Mapping[str, MMFPrimeTopology]:
    topologies = build_mmf_prime_topologies(
        face_map=MMF_DOMAIN_FACE_MAP,
        kernel_primes=MMF_KERNEL_PRIME_MAP,
        extension_map=MMF_DOMAIN_PRIME_MAP,
    )
    return MappingProxyType(topologies)


# Import-time validation makes the taxonomy structural rather than advisory.
CANONICAL_MMF_PRIME_TOPOLOGIES = canonical_mmf_prime_topologies()


@dataclass(frozen=True)
class KernelTopology:
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]


class ProjectionTransform(Protocol):
    def phi_d(self, node_state: Mapping[str, Any]) -> Mapping[str, Any]:
        ...

    def psi_d(self, edge_flow: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class DomainProjection:
    domain: str
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    node_state: Mapping[str, Mapping[str, Any]]
    edge_flow: tuple[Mapping[str, Any], ...]


class ProjectionEngine:
    """Thin MMF projection boundary using per-domain phi_d/psi_d transforms."""

    def __init__(self, transforms: Mapping[str, ProjectionTransform]):
        validated = validate_domain_set(tuple(transforms.keys()))
        self._transforms = dict(transforms)
        self._domains = validated

    @property
    def domains(self) -> tuple[str, ...]:
        return self._domains

    def project(
        self,
        *,
        topology: KernelTopology,
        domain: str,
        node_state: Mapping[str, Mapping[str, Any]],
        edge_flow: Iterable[Mapping[str, Any]],
    ) -> DomainProjection:
        domain_key = str(domain).strip().lower()
        if domain_key not in self._transforms:
            raise ValueError(f"unsupported mmf domain: {domain}")

        transform = self._transforms[domain_key]
        projected_node_state: Dict[str, Mapping[str, Any]] = {}
        for node in topology.nodes:
            source_state = dict(node_state.get(node, {}))
            projected_node_state[node] = dict(transform.phi_d(source_state))

        projected_edge_flow = tuple(dict(transform.psi_d(dict(edge))) for edge in edge_flow)

        return DomainProjection(
            domain=domain_key,
            nodes=tuple(topology.nodes),
            edges=tuple(topology.edges),
            node_state=projected_node_state,
            edge_flow=projected_edge_flow,
        )


def evaluate_e6_decision(*, mode: int, K: int, P: int, E: int, V_q: int, momentum_min: int = 0) -> Dict[str, Any]:
    mode_i = int(mode)
    if mode_i < 0 or mode_i > 3:
        raise ValueError("mode must be base-4 (0..3)")

    k_i = 1 if int(K) else 0
    p_i = 1 if int(P) else 0
    e_i = 1 if int(E) else 0
    v_i = int(V_q)

    route_by_mode = {0: 0, 1: 1, 2: 2, 3: 3}
    ptype_by_mode = {0: 1, 1: 2, 2: 3, 3: 0}

    commit_allowed = bool(k_i and p_i and e_i and mode_i >= 2 and v_i >= int(momentum_min))

    return {
        "mode": mode_i,
        "ptype": ptype_by_mode[mode_i],
        "law": 2 if commit_allowed else 1,
        "route": route_by_mode[mode_i],
        "K": k_i,
        "P": p_i,
        "E": e_i,
        "valid": 1,
        "V_q": v_i,
        "commit": commit_allowed,
    }


@dataclass(frozen=True)
class PacketBoundary:
    """Stable pack/unpack boundary for MMF over E6 header v0."""

    default_node: int = 0

    def pack(
        self,
        *,
        decision: Mapping[str, Any],
        seq: int,
        t_ms: int,
        dW: int = 0,
        node: int | None = None,
    ) -> bytes:
        return pack_header_v0(
            mode=int(decision.get("mode", 0)),
            ptype=int(decision.get("ptype", 1)),
            law=int(decision.get("law", 1)),
            route=int(decision.get("route", 0)),
            node=int(self.default_node if node is None else node),
            K=int(decision.get("K", 0)),
            P=int(decision.get("P", 0)),
            E=int(decision.get("E", 0)),
            valid=int(decision.get("valid", 1)),
            dW=int(dW),
            seq=int(seq),
            t_ms=int(t_ms),
            V_q=int(decision.get("V_q", 0)),
        )

    def unpack(self, data: bytes) -> Dict[str, Any]:
        return unpack_header_v0(data)


__all__ = [
    "MMFDomain",
    "MMF_DOMAIN_SET",
    "MMF_KERNEL_NODES",
    "MMF_CANONICAL_FACES",
    "MMF_DOMAIN_FACE_MAP",
    "MMF_KERNEL_PRIME_MAP",
    "MMF_DOMAIN_PRIME_MAP",
    "MMF_DOMAIN_EXTENSION_PRIME_MAP",
    "MMFPrimeTopology",
    "KernelTopology",
    "DomainProjection",
    "ProjectionTransform",
    "ProjectionEngine",
    "PacketBoundary",
    "validate_domain_set",
    "validate_domain_face_map",
    "validate_kernel_prime_map",
    "validate_domain_extension_prime_map",
    "build_mmf_prime_topologies",
    "mmf_required_extension_prime_count",
    "canonical_mmf_prime_topologies",
    "CANONICAL_MMF_PRIME_TOPOLOGIES",
    "evaluate_e6_decision",
]
