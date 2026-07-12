"""Multi-Reading Kernel Output (MRKO) formatter.

Provides dataclasses for a single ``UnitReading`` and an aggregate
``LatticeReadingOutput`` plus JSON round-trip helpers.  This is an additive
patch; no existing kernel constants or mappings are modified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from backend.kernel.coord_enrichment import COORD_REGISTRY, CoordEnrichmentCard


@dataclass(frozen=True)
class UnitReading:
    """A single input unit resolved into a lattice coordinate path."""

    source_type: str
    source_label: str
    coordinate_path: Tuple[str, ...]
    raw_input: str
    semantic_tags: Tuple[str, ...] = field(default_factory=tuple)
    confidence_score: float = 1.0
    prose: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_label": self.source_label,
            "coordinate_path": list(self.coordinate_path),
            "raw_input": self.raw_input,
            "semantic_tags": list(self.semantic_tags),
            "confidence_score": self.confidence_score,
            "prose": self.prose,
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "UnitReading":
        return cls.from_dict(json.loads(payload))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UnitReading":
        return cls(
            source_type=str(data["source_type"]),
            source_label=str(data["source_label"]),
            coordinate_path=tuple(data["coordinate_path"]),
            raw_input=str(data["raw_input"]),
            semantic_tags=tuple(data.get("semantic_tags", [])),
            confidence_score=float(data.get("confidence_score", 1.0)),
            prose=str(data.get("prose", "")),
        )


@dataclass(frozen=True)
class LatticeReadingOutput:
    """Aggregate output for a multi-source lattice reading."""

    source_type: str
    source_label: str
    coordinates: Tuple[str, ...]
    unit_readings: Tuple[UnitReading, ...]
    layer_distribution: Mapping[str, int]
    mode_distribution: Mapping[str, int]
    breath_distribution: Mapping[str, int]
    element_presence: Mapping[str, int]
    kernel_presence: Mapping[str, int]
    centroid_present: bool
    cross_substrate_transitions: int
    prose: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_label": self.source_label,
            "coordinates": list(self.coordinates),
            "unit_readings": [u.to_dict() for u in self.unit_readings],
            "layer_distribution": dict(self.layer_distribution),
            "mode_distribution": dict(self.mode_distribution),
            "breath_distribution": dict(self.breath_distribution),
            "element_presence": dict(self.element_presence),
            "kernel_presence": dict(self.kernel_presence),
            "centroid_present": self.centroid_present,
            "cross_substrate_transitions": self.cross_substrate_transitions,
            "prose": self.prose,
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LatticeReadingOutput":
        return cls(
            source_type=str(data["source_type"]),
            source_label=str(data["source_label"]),
            coordinates=tuple(data["coordinates"]),
            unit_readings=tuple(
                UnitReading.from_dict(u) for u in data.get("unit_readings", [])
            ),
            layer_distribution=dict(data.get("layer_distribution", {})),
            mode_distribution=dict(data.get("mode_distribution", {})),
            breath_distribution=dict(data.get("breath_distribution", {})),
            element_presence=dict(data.get("element_presence", {})),
            kernel_presence=dict(data.get("kernel_presence", {})),
            centroid_present=bool(data.get("centroid_present", False)),
            cross_substrate_transitions=int(
                data.get("cross_substrate_transitions", 0)
            ),
            prose=str(data.get("prose", "")),
        )

    @classmethod
    def from_json(cls, payload: str) -> "LatticeReadingOutput":
        return cls.from_dict(json.loads(payload))


def _get_card(coord: str) -> Optional[CoordEnrichmentCard]:
    return COORD_REGISTRY.get(coord)


def _tetra_class(tetra: str) -> str:
    if tetra in {"S1"}:
        return "S1"
    if tetra in {"S2"}:
        return "S2"
    return "C"


def _count_transitions(coords: Sequence[str]) -> int:
    """Count adjacent coordinate pairs that cross between S1 and S2."""
    transitions = 0
    for a, b in zip(coords, coords[1:]):
        card_a = _get_card(a)
        card_b = _get_card(b)
        if card_a is None or card_b is None:
            continue
        class_a = _tetra_class(card_a.tetrahedron)
        class_b = _tetra_class(card_b.tetrahedron)
        if class_a != class_b and class_a in {"S1", "S2"} and class_b in {"S1", "S2"}:
            transitions += 1
    return transitions


def _generate_lattice_prose(output: LatticeReadingOutput) -> str:
    """Produce a compact prose summary from an aggregate reading."""
    lines: List[str] = [
        f"Lattice reading for {output.source_label} ({output.source_type}).",
        f"Coordinates ({len(output.coordinates)}): {', '.join(output.coordinates)}.",
    ]
    if output.centroid_present:
        lines.append("Centroid (111) is present — mediation axis active.")
    if output.cross_substrate_transitions:
        lines.append(
            f"Cross-substrate transitions: {output.cross_substrate_transitions}."
        )
    if output.kernel_presence:
        lines.append(
            "Kernel presence: "
            + ", ".join(f"{k}({v})" for k, v in output.kernel_presence.items())
            + "."
        )
    if output.element_presence:
        lines.append(
            "Elemental presence: "
            + ", ".join(f"{k}({v})" for k, v in output.element_presence.items())
            + "."
        )
    lines.append(
        "Layer distribution: "
        + ", ".join(f"{k}={v}" for k, v in output.layer_distribution.items())
        + "."
    )
    return " ".join(lines)


def build_lattice_reading_output(
    units: Sequence[UnitReading],
    source_type: str = "multi_reading",
    source_label: str = "aggregate",
) -> LatticeReadingOutput:
    """Build a ``LatticeReadingOutput`` from a sequence of unit readings."""
    coordinates: List[str] = []
    layer_dist: Dict[str, int] = {}
    mode_dist: Dict[str, int] = {}
    breath_dist: Dict[str, int] = {}
    element_presence: Dict[str, int] = {}
    kernel_presence: Dict[str, int] = {}
    centroid_present = False

    for unit in units:
        for coord in unit.coordinate_path:
            card = _get_card(coord)
            if card is None:
                continue
            coordinates.append(coord)
            layer_dist[card.layer_name] = layer_dist.get(card.layer_name, 0) + 1
            mode_dist[card.mode_name] = mode_dist.get(card.mode_name, 0) + 1
            breath_dist[card.breath_name] = breath_dist.get(card.breath_name, 0) + 1
            if card.element:
                element_presence[card.element] = (
                    element_presence.get(card.element, 0) + 1
                )
            if card.kernel_label:
                kernel_presence[card.kernel_label] = (
                    kernel_presence.get(card.kernel_label, 0) + 1
                )
            if card.coord_id == "111":
                centroid_present = True

    output = LatticeReadingOutput(
        source_type=source_type,
        source_label=source_label,
        coordinates=tuple(coordinates),
        unit_readings=tuple(units),
        layer_distribution=layer_dist,
        mode_distribution=mode_dist,
        breath_distribution=breath_dist,
        element_presence=element_presence,
        kernel_presence=kernel_presence,
        centroid_present=centroid_present,
        cross_substrate_transitions=_count_transitions(coordinates),
        prose="",
    )
    object.__setattr__(output, "prose", _generate_lattice_prose(output))
    return output


__all__ = (
    "UnitReading",
    "LatticeReadingOutput",
    "build_lattice_reading_output",
)
