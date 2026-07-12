"""Constrained slot-filling parser for the dual-layer ledger."""

from __future__ import annotations

import re
from typing import Any

from dss_ledger.schema import LedgerSchema


class ConstrainedParser:
    """Parse natural language into structured process slots.

    The parser is deliberately constrained: it only recognises known ontology
    concepts and assigns them positionally. It does not hallucinate concepts.
    """

    def __init__(self, schema: LedgerSchema) -> None:
        self._schema = schema
        self._concept_index = {
            name.lower(): name for name in schema.ontology
        }

    def _extract_concepts(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z_]+", text)
        found: list[str] = []
        seen = set()
        for token in tokens:
            canonical = self._concept_index.get(token.lower())
            if canonical and canonical not in seen:
                found.append(canonical)
                seen.add(canonical)
        return found

    def parse(self, text: str) -> dict[str, Any]:
        concepts = self._extract_concepts(text)

        if len(concepts) < 3:
            return {
                "status": "REJECT",
                "reason": "INSUFFICIENT_CONCEPTS",
                "message": (
                    f"Found {len(concepts)} known concept(s); "
                    "need at least agent, verb, patient."
                ),
                "original": text,
            }

        slots: dict[str, str] = {
            "agent": concepts[0],
            "verb": concepts[1],
            "patient": concepts[2],
        }
        if len(concepts) >= 4:
            slots["result"] = concepts[3]
        if len(concepts) >= 5:
            slots["context"] = concepts[4]

        return {
            "status": "PARSED",
            "slots": slots,
            "original": text,
        }
