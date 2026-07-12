"""Gödel-style PID encoder for the dual-layer ledger."""

from __future__ import annotations

from typing import Any

from dss_ledger.schema import LedgerSchema


SLOT_ORDER = ("agent", "verb", "patient", "result", "context")
COMPOUND_BASES = (2, 3, 5, 7, 11, 13, 17, 19)


class LedgerEncoder:
    """Encode process and compound ontology entries as deterministic integers."""

    def __init__(self, schema: LedgerSchema) -> None:
        self._schema = schema

    def encode_process(
        self,
        agent: str,
        verb: str,
        patient: str,
        result: str | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Return a non-commutative ProcessID from structured slots."""
        slots: dict[str, int] = {
            "agent": self._schema.concept_prime(agent),
            "verb": self._schema.concept_prime(verb),
            "patient": self._schema.concept_prime(patient),
        }
        if result is not None:
            slots["result"] = self._schema.concept_prime(result)
        if context is not None:
            slots["context"] = self._schema.concept_prime(context)

        pid = 1
        for slot_name in SLOT_ORDER:
            if slot_name not in slots:
                continue
            base = self._schema.slot_base(slot_name)
            pid *= base ** slots[slot_name]

        canonical = f"{agent}.{verb}.{patient}"
        if result is not None:
            canonical += f"→{result}"
        if context is not None:
            canonical += f"@{context}"

        return {
            "pid": pid,
            "slots": slots,
            "canonical": canonical,
        }

    def encode_compound(self, relation: str, *concepts: str) -> dict[str, Any]:
        """Encode a Layer-1 ontology entry with a relation marker."""
        rel_prime = self._schema.relation_prime(relation)
        concept_primes = [self._schema.concept_prime(c) for c in concepts]

        compound = rel_prime
        for i, cp in enumerate(concept_primes):
            compound *= COMPOUND_BASES[i] ** cp

        return {
            "compound_id": compound,
            "relation": relation,
            "components": list(concepts),
        }
