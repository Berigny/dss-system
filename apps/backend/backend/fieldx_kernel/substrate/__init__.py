"""Substrate-bound storage utilities."""

from __future__ import annotations

import json
import math
from datetime import datetime
from threading import RLock
from typing import MutableMapping, Optional

from .ledger_store_v2 import LedgerStoreV2
from backend.fieldx_kernel.schema import FLOW_PRIMES, MIN_BODY_PRIME
from backend.fieldx_kernel.state import (
    GRACE_PRIME,
    LAW_PRIME,
    MEDIATOR_PRIMES as _MEDIATOR_PRIMES,
    TIER_SCHEMA,
)

MEDIATOR_PRIMES: tuple[int, ...] = tuple(_MEDIATOR_PRIMES)

_RESERVED_BODY_PRIMES = set(MEDIATOR_PRIMES)

_NON_ALLOCATABLE_PRIMES = {
    prime
    for tier in TIER_SCHEMA.values()
    if not bool(tier.get("allocatable", True))
    for prime in tier.get("primes", [])
}

_BODY_LOCK = RLock()


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value in {2, 3}:
        return True
    if value % 2 == 0:
        return False

    limit = int(math.sqrt(value)) + 1
    for candidate in range(3, limit, 2):
        if value % candidate == 0:
            return False
    return True


def _next_prime(value: int) -> int:
    candidate = max(2, value)
    while not _is_prime(candidate):
        candidate += 1
    return candidate


class MemorySubstrate:
    """Manage immutable body primes stored alongside normalised payloads."""

    def __init__(self, db: MutableMapping[bytes, bytes]):
        self._db = db

    @staticmethod
    def _encode_key(key: str) -> bytes:
        return key.encode()

    def _next_key(self, entity: str) -> bytes:
        return self._encode_key(f"entity:{entity}:body:next")

    def _body_key(self, entity: str, prime: int) -> bytes:
        return self._encode_key(f"entity:{entity}:body:{prime}")

    def allocate_body_prime(self, entity: str) -> int:
        """Allocate the next free body prime (>=23) for ``entity``."""

        with _BODY_LOCK:
            next_key = self._next_key(entity)
            existing_raw = self._db.get(next_key)
            starting_point = int(existing_raw.decode()) if existing_raw else MIN_BODY_PRIME

            prime = _next_prime(starting_point)
            reserved_primes = _NON_ALLOCATABLE_PRIMES | _RESERVED_BODY_PRIMES | set(FLOW_PRIMES)
            while prime in reserved_primes or self._db.get(
                self._body_key(entity, prime)
            ) is not None:
                prime = _next_prime(prime + 1)

            self._db[next_key] = str(prime + 1).encode()
            return prime

    def write_body_prime(self, entity: str, prime: int, raw: str, norm: dict) -> dict:
        """Persist ``raw``/``norm`` text under ``prime`` immutably."""

        encoded_key = self._body_key(entity, prime)
        with _BODY_LOCK:
            if self._db.get(encoded_key) is not None:
                raise ValueError(f"Body prime {prime} already exists for {entity}")

            payload = {
                "prime": prime,
                "entity": entity,
                "raw": raw,
                "norm": norm,
                "created_at": datetime.utcnow().isoformat(),
            }
            self._db[encoded_key] = json.dumps(payload).encode()
            return payload

    def get_body_prime(self, entity: str, prime: int) -> Optional[dict]:
        encoded_key = self._body_key(entity, prime)
        raw = self._db.get(encoded_key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None


def non_allocatable_primes() -> frozenset[int]:
    """Return all primes that cannot be allocated for bodies."""

    return frozenset(_NON_ALLOCATABLE_PRIMES)


def reserved_body_primes() -> frozenset[int]:
    """Return primes reserved for mediator metadata."""

    return frozenset(_RESERVED_BODY_PRIMES)


__all__ = [
    "GRACE_PRIME",
    "LAW_PRIME",
    "MEDIATOR_PRIMES",
    "LedgerStoreV2",
    "MemorySubstrate",
    "non_allocatable_primes",
    "reserved_body_primes",
]
