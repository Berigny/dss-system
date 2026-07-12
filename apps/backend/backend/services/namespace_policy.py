"""Namespace policy helpers for staged compatibility rollouts."""

from __future__ import annotations

import os


def resolve_write_namespace(*, ledger_id: str, entity: str | None) -> str:
    """Resolve persisted namespace according to rollout policy."""
    mode = os.getenv("LEDGER_NAMESPACE_SOURCE", "ledger_id").strip().lower()
    if mode == "entity_compat":
        candidate = (entity or "").strip()
        if candidate:
            return candidate
    return str(ledger_id).strip()

