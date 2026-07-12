"""PID factorisation decoder for the dual-layer ledger."""

from __future__ import annotations

from typing import Any

from dss_ledger.schema import LedgerSchema

from .encoder import SLOT_ORDER


class LedgerDecoder:
    """Factor a ProcessID back into its structured slots."""

    def __init__(self, schema: LedgerSchema) -> None:
        self._schema = schema

    def factor(self, pid: int) -> dict[str, Any]:
        """Return slot assignments and canonical text for a PID."""
        pid = int(pid)
        if pid <= 0:
            raise ValueError("PID must be a positive integer")

        slots: dict[str, int] = {}
        remaining = pid
        for slot_name in SLOT_ORDER:
            base = self._schema.slot_base(slot_name)
            exponent = 0
            while remaining % base == 0:
                remaining //= base
                exponent += 1
            if exponent:
                slots[slot_name] = exponent

        if remaining != 1:
            raise ValueError(f"PID {pid} contains factors outside slot bases")

        canonical_parts: list[str] = []
        for slot_name in ("agent", "verb", "patient"):
            if slot_name not in slots:
                raise ValueError(f"PID {pid} is missing required slot {slot_name}")
            canonical_parts.append(self._schema.concept_name(slots[slot_name]))

        canonical = ".".join(canonical_parts)
        if "result" in slots:
            canonical += f"→{self._schema.concept_name(slots['result'])}"
        if "context" in slots:
            canonical += f"@{self._schema.concept_name(slots['context'])}"

        return {
            "pid": pid,
            "slots": slots,
            "canonical": canonical,
        }
