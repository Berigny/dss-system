"""Shadow Codex agent acting as an interpretability companion."""

from __future__ import annotations

from typing import Dict, List

from backend.coherence_layer import PolicyEngine
from backend.fieldx_kernel import ContinuousState, LedgerEntry, LedgerKey


class ShadowCodex:
    """Lightweight agent that annotates ledger entries for transparency."""

    def __init__(self, policy_engine: PolicyEngine) -> None:
        self.policy_engine = policy_engine
        self.annotations: Dict[str, List[str]] = {}

    def annotate(self, key: LedgerKey, note: str) -> LedgerEntry | None:
        """Attach a note to an existing ledger entry and re-evaluate policy."""

        entry = self.policy_engine.ledger_store.get(key)
        if entry is None:
            return None

        updated_state = ContinuousState(
            coordinates=entry.state.coordinates,
            phase=entry.state.phase,
            metadata=dict(entry.state.metadata),
        )
        annotated_entry = LedgerEntry(
            key=key,
            state=updated_state,
            created_at=entry.created_at,
            notes=note,
        )
        self.policy_engine.record(annotated_entry)
        self.annotations.setdefault(key.as_path(), []).append(note)
        return annotated_entry

    def summarize(self, key: LedgerKey) -> Dict[str, List[str]]:
        """Return accumulated annotations for the key."""

        return {key.as_path(): self.annotations.get(key.as_path(), [])}
