"""Dual-process memory sketch inspired by System 1 / System 2 reasoning."""

from __future__ import annotations

from typing import List, Optional

from .substrate import LedgerStoreV2
from .models import ContinuousState, LedgerEntry, LedgerKey


class DualProcessMemory:
    """Maintains short-term and deliberative memories backed by the ledger."""

    def __init__(self, store: LedgerStoreV2) -> None:
        self.store = store
        self._working_memory: List[ContinuousState] = []

    def remember(self, key: LedgerKey, state: ContinuousState, notes: str | None = None) -> LedgerEntry:
        """Record a memory snapshot into the ledger and working buffer."""

        entry = LedgerEntry(key=key, state=state, notes=notes)
        self._working_memory.append(state)
        self.store.upsert(entry)
        return entry

    def recall_recent(self) -> Optional[ContinuousState]:
        """Return the most recent working memory state if available."""

        if not self._working_memory:
            return None
        return self._working_memory[-1]

    def clear_working_memory(self) -> None:
        """Drop accumulated working memory to force System 2 refresh."""

        self._working_memory.clear()
