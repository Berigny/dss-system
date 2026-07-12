# DSS-CP-GOV-v1.0.0-alpha
"""YAML ontology loader and validation helpers for the connection governance engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class OntologyError(ValueError):
    """Raised when an ontology constraint is violated."""


@dataclass(frozen=True)
class RelationshipType:
    name: str
    from_types: frozenset[str]
    to_types: frozenset[str]
    cardinality: str = "many_to_many"
    bidirectional: bool = False
    meaning: str = ""
    requires_subtype: str | None = None


@dataclass(frozen=True)
class EntityType:
    name: str
    subtypes: frozenset[str] = field(default_factory=frozenset)
    can_hold: frozenset[str] = field(default_factory=frozenset)
    can_be_held_by: frozenset[str] = field(default_factory=frozenset)
    cannot_hold: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class LegacyMapping:
    maps_to: str
    reverse_direction: bool = False
    meaning: str = ""


@dataclass(frozen=True)
class Ontology:
    entity_types: dict[str, EntityType]
    relationship_types: dict[str, RelationshipType]
    legacy_types: dict[str, LegacyMapping]

    def is_subtype(self, entity_type: str, subtype: str) -> bool:
        et = self.entity_types.get(entity_type)
        if et is None:
            return False
        return subtype in et.subtypes or subtype == entity_type

    def relationship_type(self, name: str) -> RelationshipType | LegacyMapping | None:
        normalized = str(name or "").strip().lower()
        if normalized in self.relationship_types:
            return self.relationship_types[normalized]
        return self.legacy_types.get(normalized)


def _default_ontology() -> dict[str, Any]:
    return {
        "entity_types": {
            "principal": {
                "subtypes": ["human", "runtime_model", "organisation"],
                "can_hold": ["principal"],
                "can_be_held_by": ["ledger", "surface", "principal"],
            },
            "ledger": {
                "subtypes": ["governed_memory_boundary"],
                "can_hold": ["principal", "surface", "ledger"],
                "can_be_held_by": ["ledger"],
            },
            "surface": {
                "subtypes": ["chat", "portal", "api"],
                "can_hold": ["principal", "ledger"],
                "can_be_held_by": ["ledger"],
                "constraints": ["CANNOT_HOLD: surface"],
            },
        },
        "relationship_types": {
            "member_of": {
                "from": ["principal", "surface"],
                "to": ["ledger"],
                "cardinality": "many_to_many",
            },
            "links_to": {
                "from": ["ledger"],
                "to": ["ledger"],
                "cardinality": "many_to_many",
                "bidirectional": True,
            },
            "belongs_to": {
                "from": ["principal"],
                "to": ["principal"],
                "cardinality": "many_to_many",
            },
            "holds": {
                "from": ["principal"],
                "to": ["principal", "ledger", "surface"],
                "cardinality": "one_to_many",
                "requires_subtype": "organisation",
            },
            "hosts": {
                "from": ["surface"],
                "to": ["principal", "ledger"],
                "cardinality": "many_to_many",
            },
            "access_grant": {
                "from": ["surface", "principal"],
                "to": ["ledger"],
                "cardinality": "many_to_many",
            },
        },
        "legacy_relationship_types": {
            "related_to": {"maps_to": "member_of"},
            "member_of_ledger": {"maps_to": "member_of"},
            "surface_bound_to_ledger": {"maps_to": "member_of"},
            "can_access_surface": {"maps_to": "hosts", "reverse_direction": True},
            "writes_to_ledger": {"maps_to": "access_grant"},
            "administered_by": {"maps_to": "holds"},
        },
    }


def _load_yaml(path: Path) -> Any:
    import yaml

    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse_ontology(data: dict[str, Any]) -> Ontology:
    entity_types: dict[str, EntityType] = {}
    for name, raw in (data.get("entity_types") or {}).items():
        constraints = raw.get("constraints") or []
        cannot_hold = set()
        for c in constraints:
            if isinstance(c, str) and c.startswith("CANNOT_HOLD:"):
                cannot_hold.add(c.split(":", 1)[1].strip())
        entity_types[str(name)] = EntityType(
            name=str(name),
            subtypes=frozenset(str(s).strip().lower() for s in raw.get("subtypes") or []),
            can_hold=frozenset(str(s).strip().lower() for s in raw.get("can_hold") or []),
            can_be_held_by=frozenset(str(s).strip().lower() for s in raw.get("can_be_held_by") or []),
            cannot_hold=frozenset(str(s).strip().lower() for s in cannot_hold),
        )

    relationship_types: dict[str, RelationshipType] = {}
    for name, raw in (data.get("relationship_types") or {}).items():
        relationship_types[str(name)] = RelationshipType(
            name=str(name),
            from_types=frozenset(str(s).strip().lower() for s in raw.get("from") or []),
            to_types=frozenset(str(s).strip().lower() for s in raw.get("to") or []),
            cardinality=str(raw.get("cardinality") or "many_to_many").strip().lower(),
            bidirectional=bool(raw.get("bidirectional")),
            meaning=str(raw.get("meaning") or "").strip(),
            requires_subtype=str(raw.get("requires_subtype") or "").strip() or None,
        )

    legacy_types: dict[str, LegacyMapping] = {}
    for name, raw in (data.get("legacy_relationship_types") or {}).items():
        legacy_types[str(name)] = LegacyMapping(
            maps_to=str(raw.get("maps_to") or "").strip().lower(),
            reverse_direction=bool(raw.get("reverse_direction")),
            meaning=str(raw.get("meaning") or "").strip(),
        )

    return Ontology(
        entity_types=entity_types,
        relationship_types=relationship_types,
        legacy_types=legacy_types,
    )


def load_ontology(config_path: str | os.PathLike[str] | None = None) -> Ontology:
    """Load ontology from YAML, falling back to a built-in default."""
    if config_path is not None:
        path = Path(config_path)
        if path.exists():
            return _parse_ontology(_load_yaml(path))

    default_paths = [
        Path(__file__).resolve().parent.parent / "config" / "ontology.yaml",
        Path.cwd() / "backend" / "config" / "ontology.yaml",
        Path.cwd() / "config" / "ontology.yaml",
    ]
    for path in default_paths:
        if path.exists():
            return _parse_ontology(_load_yaml(path))

    return _parse_ontology(_default_ontology())


ONTOLOGY = load_ontology()


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def validate_relationship_type(
    relationship_type: str,
    subject_type: str,
    object_type: str,
    *,
    subject_subtype: str | None = None,
) -> None:
    """Validate a relationship against the loaded ontology.

    Raises OntologyError if the combination is not allowed.
    Legacy types are accepted without strict subject/object checks.
    """
    rel_key = _normalize(relationship_type)
    if not rel_key:
        raise OntologyError("relationship_type is required")

    if rel_key in ONTOLOGY.legacy_types:
        return

    rel = ONTOLOGY.relationship_types.get(rel_key)
    if rel is None:
        # Unknown / free-form relationship types are accepted for backward compatibility.
        return

    sub_type = _normalize(subject_type)
    obj_type = _normalize(object_type)

    if sub_type not in rel.from_types:
        raise OntologyError(
            f"relationship '{relationship_type}' cannot originate from '{subject_type}'"
        )
    if obj_type not in rel.to_types:
        raise OntologyError(
            f"relationship '{relationship_type}' cannot target '{object_type}'"
        )

    sub_entity = ONTOLOGY.entity_types.get(sub_type)
    if sub_entity and obj_type in sub_entity.cannot_hold:
        raise OntologyError(
            f"entity type '{subject_type}' cannot hold '{object_type}'"
        )

    if rel.requires_subtype and subject_subtype:
        if not ONTOLOGY.is_subtype("principal", _normalize(subject_subtype)):
            raise OntologyError(
                f"relationship '{relationship_type}' requires subject subtype '{rel.requires_subtype}'"
            )


def is_typed_relationship(relationship_type: str) -> bool:
    """Return True if the relationship type is part of the typed ontology (not legacy)."""
    return _normalize(relationship_type) in ONTOLOGY.relationship_types


def map_legacy_relationship(
    relationship_type: str,
    subject_type: str,
    subject_id: str,
    object_type: str,
    object_id: str,
) -> tuple[str, str, str, str, str]:
    """Map a legacy relationship into canonical ontology form.

    Returns (relationship_type, subject_type, subject_id, object_type, object_id).
    """
    rel_key = _normalize(relationship_type)
    mapping = ONTOLOGY.legacy_types.get(rel_key)
    if mapping is None:
        return relationship_type, subject_type, subject_id, object_type, object_id

    if mapping.reverse_direction:
        return mapping.maps_to, object_type, object_id, subject_type, subject_id
    return mapping.maps_to, subject_type, subject_id, object_type, object_id
