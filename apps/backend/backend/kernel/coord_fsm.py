"""COORD derivation validity finite-state machine.

COORDs are hierarchical semantic addresses of the form:

    dimension/category/action/variant/v-level

The FSM enforces:
- Topology membership (known dimensions and valid depth).
- Hierarchical derivation (a child extends a parent by one semantic level
  or changes the v-level of the same leaf).
- Novel-territory elevation (new COORDs carry a novelty flag and a
  topology-membership proof).
- Supercession validity (new state COORD must derive from old state COORD).
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Sequence

from backend.kernel import constants


# Valid semantic dimensions from the quaternary gate registry plus the telos branch.
DEFAULT_DIMENSIONS: frozenset[str] = frozenset(
    constants.QUATERNARY_GATE_TO_DIMENSION.values()
) | {"telos"}

# Expected COORD depth: dimension/category/action/variant/v-level
EXPECTED_DEPTH: int = 5


class CoordFSM:
    """Finite-state machine for COORD topology and derivation validity."""

    def __init__(
        self,
        *,
        topology: Iterable[str] | None = None,
        allowed_dimensions: Iterable[str] | None = None,
    ) -> None:
        self.allowed_dimensions = set(allowed_dimensions or DEFAULT_DIMENSIONS)
        self.topology: set[str] = set()
        for coord in topology or ():
            self.topology.add(self._normalize(coord))

    @staticmethod
    def _normalize(coord: str) -> str:
        return "/".join(part.strip() for part in coord.lower().split("/") if part.strip())

    @staticmethod
    def _parts(coord: str) -> tuple[str, ...]:
        return tuple(part for part in coord.lower().split("/") if part.strip())

    def is_wellformed(self, coord: str) -> bool:
        """Return True if ``coord`` has the expected depth and dimension."""
        parts = self._parts(coord)
        if len(parts) != EXPECTED_DEPTH:
            return False
        if parts[0] not in self.allowed_dimensions:
            return False
        return True

    def is_topology_member(self, coord: str) -> bool:
        """Return True if ``coord`` is in the known topology or is well-formed."""
        normalized = self._normalize(coord)
        if normalized in self.topology:
            return True
        return self.is_wellformed(coord)

    def is_derivation_valid(self, parent: str, child: str) -> bool:
        """Return True if ``child`` is a valid derivation from ``parent``.

        Valid derivations:
        - identical COORD;
        - child extends parent hierarchically (parent is a prefix);
        - child shares the same parent path and differs only in the trailing
          segment(s) (sibling derivation).
        """
        parent_parts = self._parts(parent)
        child_parts = self._parts(child)

        # Full COORD children must be well-formed; partial parents are allowed.
        if len(child_parts) >= EXPECTED_DEPTH and not self.is_wellformed(child):
            return False

        # Identical coordinate or hierarchical extension.
        if parent_parts == child_parts:
            return True
        if child_parts[: len(parent_parts)] == parent_parts:
            return True

        # Sibling derivation: same immediate parent or same grand-parent path.
        if len(child_parts) == len(parent_parts) and len(child_parts) >= 2:
            if child_parts[:-1] == parent_parts[:-1]:
                return True
            if len(child_parts) >= 3 and child_parts[:-2] == parent_parts[:-2]:
                return True

        return False

    def derivation_path(self, coord: str) -> list[str]:
        """Return the ordered list of COORDs from dimension down to ``coord``."""
        parts = self._parts(coord)
        return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]

    def novelty_proof(self, coord: str) -> dict[str, Any]:
        """Return a novelty proof bundle for a new COORD.

        Raises:
            ValueError: if the COORD is already in the topology or is malformed.
        """
        normalized = self._normalize(coord)
        if normalized in self.topology:
            raise ValueError(f"COORD {coord} is already in the topology")
        if not self.is_wellformed(coord):
            raise ValueError(f"COORD {coord} is malformed")

        path = self.derivation_path(coord)
        payload = "|".join(path).encode("utf-8")
        return {
            "novelty_flag": True,
            "topology_membership_proof": hashlib.sha256(payload).hexdigest(),
            "derivation_path": path,
        }

    def supercession_valid(
        self,
        old_coord: str,
        new_coord: str,
        *,
        require_topology_membership: bool = True,
    ) -> bool:
        """Return True if ``new_coord`` validly supercedes ``old_coord``.

        Supercession requires hierarchical derivation and topology membership
        for the new coordinate.
        """
        if require_topology_membership and not self.is_topology_member(new_coord):
            return False
        return self.is_derivation_valid(old_coord, new_coord)

    def register(self, coord: str) -> None:
        """Add ``coord`` to the known topology."""
        normalized = self._normalize(coord)
        if self.is_wellformed(normalized):
            self.topology.add(normalized)
