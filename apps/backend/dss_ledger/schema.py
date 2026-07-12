"""Config schema loader for the dual-layer ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LedgerSchema:
    """Runtime view of the generated ledger config."""

    ontology: dict[str, dict[str, Any]]
    slots: dict[str, dict[str, Any]]
    relations: dict[str, dict[str, Any]]
    weights: dict[str, Any]

    @classmethod
    def from_config_dir(cls, config_dir: str | Path | None = None) -> "LedgerSchema":
        if config_dir is None:
            config_dir = Path(__file__).parent / "config"
        config_dir = Path(config_dir)

        def _load(name: str) -> dict[str, Any]:
            path = config_dir / name
            if not path.exists():
                raise FileNotFoundError(f"Missing ledger config: {path}")
            return json.loads(path.read_text())

        ontology_data = _load("ontology.json")
        slots_data = _load("slots.json")
        relations_data = _load("relations.json")
        weights_data = _load("weights.json")

        ontology = dict(ontology_data["concepts"])
        slots = dict(slots_data["slots"])
        relations = dict(relations_data["relations"])

        cls._validate(ontology, slots, relations)

        return cls(
            ontology=ontology,
            slots=slots,
            relations=relations,
            weights=weights_data,
        )

    @staticmethod
    def _validate(
        ontology: dict[str, dict[str, Any]],
        slots: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> None:
        slot_bases = {s["base"] for s in slots.values()}
        ontology_primes = {c["prime"] for c in ontology.values()}
        relation_primes = {r["prime"] for r in relations.values()}

        if len(ontology_primes) != len(ontology):
            raise ValueError("Ontology contains duplicate process primes")
        if slot_bases & ontology_primes:
            raise ValueError("Ontology prime collides with a slot base")
        if slot_bases & relation_primes:
            raise ValueError("Relation prime collides with a slot base")
        if ontology_primes & relation_primes:
            raise ValueError("Ontology prime collides with a relation prime")

    def concept_prime(self, name: str) -> int:
        if name not in self.ontology:
            raise ValueError(f"Unknown concept: {name}")
        return int(self.ontology[name]["prime"])

    def concept_name(self, prime: int) -> str:
        for name, meta in self.ontology.items():
            if int(meta["prime"]) == prime:
                return name
        raise ValueError(f"Unknown process prime: {prime}")

    def slot_base(self, slot: str) -> int:
        if slot not in self.slots:
            raise ValueError(f"Unknown slot: {slot}")
        return int(self.slots[slot]["base"])

    def relation_prime(self, relation: str) -> int:
        if relation not in self.relations:
            raise ValueError(f"Unknown relation: {relation}")
        return int(self.relations[relation]["prime"])
