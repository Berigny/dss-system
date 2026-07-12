"""Data classes describing the core Field-X state and ledger records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class LedgerKey:
    """Key identifying a logical ledger partition and a unique entry."""

    namespace: str
    identifier: str

    def as_path(self) -> str:
        """Return a stable string path for the key, useful for stores."""

        return f"{self.namespace}:{self.identifier}"


@dataclass
class ContinuousState:
    """Represents a smoothly varying set of parameters for the substrate."""

    coordinates: Dict[str, float] = field(default_factory=dict)
    phase: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def update(self, updates: Mapping[str, float]) -> None:
        """Merge new coordinate values into the state in place."""

        self.coordinates.update(updates)


@dataclass
class LedgerEntry:
    """Ledger record pairing a key with the most recent continuous state."""

    key: LedgerKey
    state: ContinuousState
    created_at: datetime = field(default_factory=datetime.utcnow)
    notes: str | None = None
    pinned: bool = False

    def as_dict(self) -> Dict[str, Any]:
        """Serialize the entry into a dictionary suitable for APIs."""

        return {
            "key": self.key.as_path(),
            "state": {
                "coordinates": dict(self.state.coordinates),
                "phase": self.state.phase,
                "metadata": dict(self.state.metadata),
            },
            "created_at": self.created_at.isoformat(),
            "notes": self.notes,
            "pinned": self.pinned,
        }
