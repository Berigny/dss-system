"""Placeholder coordinate and ledger-entry schemas.

Populated in DSS-240.
"""

from __future__ import annotations

from pydantic import BaseModel


class Coordinate(BaseModel):
    namespace: str
    identifier: str

    def as_path(self) -> str:
        return f"{self.namespace}:{self.identifier}"


class LedgerEntrySchema(BaseModel):
    coord: Coordinate
    metadata: dict
