"""Coordinate enrichment cards for the 27-node kernel lattice.

This module provides an additive registry (`COORD_REGISTRY`) that enriches the
strict engineering constants in `backend.kernel.constants` with compact,
cross-domain coordinate metadata used by translation, formatting, embedding,
and reverse-parsing patches.  It does **not** modify existing constants.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Final, FrozenSet, List, Mapping, Optional, Tuple

from backend.kernel import constants


@dataclass(frozen=True)
class CoordEnrichmentCard:
    """Compact, structurally-derived metadata for a single lattice coordinate.

    All numeric fields are derived from the canonical 3-digit ternary address
    ``(L, M, B)`` and the neutral lattice constants in
    `backend.kernel.constants`.  Cross-domain fields are optional and are
    populated only when the coordinate has a well-attested mapping.
    """

    coord_id: str
    day_index: int
    hebrew_letter: str
    hebrew_name: str
    layer: int
    layer_name: str
    mode: int
    mode_name: str
    breath: int
    breath_name: str
    node_type: str
    kernel_label: Optional[str]
    tetrahedron: str
    structural_role: str
    structural_role_short: str
    prime: int
    element: Optional[str]
    iching_trigram: Optional[str]
    iching_name: Optional[str]
    adjacent_coords: FrozenSet[str] = field(default_factory=frozenset)
    embedding_vector: Tuple[float, ...] = field(default_factory=tuple)
    narrative_fragments: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dictionary representation (JSON-serializable)."""
        return {
            "coord_id": self.coord_id,
            "day_index": self.day_index,
            "hebrew_letter": self.hebrew_letter,
            "hebrew_name": self.hebrew_name,
            "layer": self.layer,
            "layer_name": self.layer_name,
            "mode": self.mode,
            "mode_name": self.mode_name,
            "breath": self.breath,
            "breath_name": self.breath_name,
            "node_type": self.node_type,
            "kernel_label": self.kernel_label,
            "tetrahedron": self.tetrahedron,
            "structural_role": self.structural_role,
            "structural_role_short": self.structural_role_short,
            "prime": self.prime,
            "element": self.element,
            "iching_trigram": self.iching_trigram,
            "iching_name": self.iching_name,
            "adjacent_coords": sorted(self.adjacent_coords),
            "embedding_vector": list(self.embedding_vector),
            "narrative_fragments": list(self.narrative_fragments),
        }


# ---------------------------------------------------------------------------
# Canonical lattice source data
# ---------------------------------------------------------------------------

_COORD_TO_HEBREW: Final[Mapping[str, Tuple[str, str]]] = {
    "000": ("Aleph", "א"),
    "001": ("Bet", "ב"),
    "002": ("Gimel", "ג"),
    "010": ("Dalet", "ד"),
    "011": ("He", "ה"),
    "012": ("Vav", "ו"),
    "020": ("Zayin", "ז"),
    "021": ("Chet", "ח"),
    "022": ("Tet", "ט"),
    "100": ("Yod", "י"),
    "101": ("Kaf", "כ"),
    "102": ("Lamed", "ל"),
    "110": ("Mem", "מ"),
    "111": ("Nun", "נ"),
    "112": ("Samekh", "ס"),
    "120": ("Ayin", "ע"),
    "121": ("Pe", "פ"),
    "122": ("Tsade", "צ"),
    "200": ("Qof", "ק"),
    "201": ("Resh", "ר"),
    "202": ("Shin", "ש"),
    "210": ("Tav", "ת"),
    "211": ("Kaf Sofit", "ך"),
    "212": ("Mem Sofit", "ם"),
    "220": ("Nun Sofit", "ן"),
    "221": ("Pe Sofit", "ף"),
    "222": ("Tsade Sofit", "ץ"),
}

_COORD_TO_PRIME: Final[Mapping[str, int]] = {
    "000": 1,
    "001": 2,
    "002": 3,
    "010": 5,
    "011": 7,
    "012": 11,
    "020": 13,
    "021": 17,
    "022": 19,
    "100": 23,
    "101": 29,
    "102": 31,
    "110": 37,
    "111": 41,
    "112": 43,
    "120": 47,
    "121": 53,
    "122": 59,
    "200": 61,
    "201": 67,
    "202": 71,
    "210": 73,
    "211": 79,
    "212": 83,
    "220": 89,
    "221": 97,
    "222": 101,
}

_FACE_ELEMENTS: Final[Mapping[str, str]] = {
    "011": "Fire",
    "101": "Air",
    "110": "Water",
    "112": "Earth",
    "121": "Aether",
    "211": "Matter",
}

_ICHING_CORNER_MAP: Final[Mapping[str, Tuple[str, str]]] = {
    "000": ("☷", "Kun"),
    "022": ("☱", "Dui"),
    "202": ("☲", "Li"),
    "220": ("☴", "Xun"),
    "002": ("☳", "Zhen"),
    "020": ("☵", "Kan"),
    "200": ("☶", "Gen"),
    "222": ("☰", "Qian"),
}

_MODE_NAMES: Final[Mapping[int, str]] = {
    0: "Distinction",
    1: "Integration",
    2: "Mastery",
}

_BREATH_NAMES: Final[Mapping[int, str]] = {
    0: "Origin",
    1: "Transition",
    2: "Terminal",
}

_TETRA_CODE: Final[Mapping[str, int]] = {
    "S1": 0,
    "S2": 1,
    "C": 2,
    "bridge": 3,
    "face": 4,
    "reset": 5,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_adjacent(coord: str) -> FrozenSet[str]:
    """Return all ternary cube neighbours one unit away along a single axis."""
    digits = [int(c) for c in coord]
    adjacent: List[str] = []
    for axis, d in enumerate(digits):
        for delta in (-1, 1):
            nd = d + delta
            if 0 <= nd <= 2:
                neighbour = list(digits)
                neighbour[axis] = nd
                adjacent.append("".join(str(x) for x in neighbour))
    return frozenset(adjacent)


def _classify_node(coord: str) -> Tuple[str, Optional[str], str, str, str]:
    """Return (node_type, kernel_label, tetrahedron, role, role_short)."""
    corner = constants.LATTICE_CORNER_MAP.get(coord)
    if corner is not None:
        kernel_label = corner["kernel"]
        eq_node = corner["eq_node"]
        tetra = constants.NODE_TO_TETRAHEDRON.get(eq_node, "S1")
        role = corner["role"]
        short = {
            "S1_sink": "void",
            "S1_branch": "branch",
            "S1_terminal": "terminal",
            "S2_sink": "seed",
            "S2_branch": "branch",
            "S2_terminal": "terminal",
        }.get(role, "corner")
        return "corner", kernel_label, tetra, role, short

    if coord == constants.LATTICE_CENTROID_COORDINATE:
        return "body", "C", "C", "mediator_centroid", "mediator"

    for edge in constants.LATTICE_BRIDGE_EDGES:
        if edge["coordinate"] == coord:
            return "edge", None, "bridge", f"bridge_{edge['axis']}", "bridge"

    for face in constants.LATTICE_FACE_CENTERS:
        if face["coordinate"] == coord:
            element = _FACE_ELEMENTS.get(coord, "unknown")
            return "face", None, "face", f"face_{element.lower()}", element.lower()

    return "unknown", None, "unknown", "unknown", "unknown"


def _layer_name(layer: int, coord: str) -> str:
    if coord == constants.LATTICE_CENTROID_COORDINATE:
        return "Centroid"
    return {0: "Surface", 1: "Mediation", 2: "Synthesis"}.get(layer, "Unknown")


def _build_embedding_vector(card: CoordEnrichmentCard) -> Tuple[float, ...]:
    """Return a 5-dimensional structural vector for downstream embeddings."""
    tetra_code = _TETRA_CODE.get(card.tetrahedron, 6)
    log_prime = math.log(card.prime) if card.prime > 0 else 0.0
    return (
        float(card.layer),
        float(card.mode),
        float(card.breath),
        log_prime,
        float(tetra_code),
    )


def _narrative_fragments(card: CoordEnrichmentCard) -> Tuple[str, ...]:
    """Return a small set of structurally-derived narrative tags."""
    fragments: List[str] = [
        f"{card.hebrew_name} ({card.hebrew_letter}) at day {card.day_index}",
        f"{card.layer_name} layer, {card.mode_name} mode, {card.breath_name} breath",
    ]
    if card.kernel_label:
        fragments.append(f"kernel corner {card.kernel_label}")
    if card.tetrahedron in {"S1", "S2"}:
        fragments.append(f"{card.tetrahedron} tetrahedron")
    if card.element:
        fragments.append(f"{card.element} elemental face")
    if card.iching_trigram:
        fragments.append(f"Bagua {card.iching_trigram} {card.iching_name}")
    fragments.append(f"structural role: {card.structural_role}")
    return tuple(fragments)


def _build_card(coord: str, day_index: int) -> CoordEnrichmentCard:
    hebrew_name, hebrew_letter = _COORD_TO_HEBREW[coord]
    layer, mode, breath = (int(c) for c in coord)
    node_type, kernel_label, tetra, role, role_short = _classify_node(coord)
    prime = _COORD_TO_PRIME[coord]
    element = _FACE_ELEMENTS.get(coord)
    iching_trigram, iching_name = _ICHING_CORNER_MAP.get(coord, (None, None))

    card = CoordEnrichmentCard(
        coord_id=coord,
        day_index=day_index,
        hebrew_letter=hebrew_letter,
        hebrew_name=hebrew_name,
        layer=layer,
        layer_name=_layer_name(layer, coord),
        mode=mode,
        mode_name=_MODE_NAMES[mode],
        breath=breath,
        breath_name=_BREATH_NAMES[breath],
        node_type=node_type,
        kernel_label=kernel_label,
        tetrahedron=tetra,
        structural_role=role,
        structural_role_short=role_short,
        prime=prime,
        element=element,
        iching_trigram=iching_trigram,
        iching_name=iching_name,
        adjacent_coords=_compute_adjacent(coord),
        embedding_vector=(),
        narrative_fragments=(),
    )

    # Fill derived fields that depend on the card itself.
    object.__setattr__(card, "embedding_vector", _build_embedding_vector(card))
    object.__setattr__(card, "narrative_fragments", _narrative_fragments(card))
    return card


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------

def _build_registry() -> Mapping[str, CoordEnrichmentCard]:
    """Build the 27 coordinate cards plus the centroid reset card."""
    cards: Dict[str, CoordEnrichmentCard] = {}
    for day_index, coord in enumerate(constants.LATTICE_TRAVERSAL_SEQUENCE):
        if coord not in cards:
            cards[coord] = _build_card(coord, day_index)

    # The 28th card represents the Day-27 centroid reset closure.
    reset = _build_card(constants.LATTICE_RESET_COORDINATE, day_index=27)
    object.__setattr__(reset, "coord_id", "000_reset")
    object.__setattr__(reset, "kernel_label", "C")
    object.__setattr__(reset, "tetrahedron", "reset")
    object.__setattr__(reset, "structural_role", "centroid_reset")
    object.__setattr__(reset, "structural_role_short", "reset")
    object.__setattr__(reset, "layer_name", "Reset")
    object.__setattr__(reset, "embedding_vector", _build_embedding_vector(reset))
    object.__setattr__(reset, "narrative_fragments", _narrative_fragments(reset))
    cards["000_reset"] = reset
    return cards


COORD_REGISTRY: Final[Mapping[str, CoordEnrichmentCard]] = _build_registry()

__all__ = (
    "CoordEnrichmentCard",
    "COORD_REGISTRY",
)
