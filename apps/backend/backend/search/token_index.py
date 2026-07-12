"""Token-to-prime index stored in RocksDB."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from functools import lru_cache, reduce
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence

from fastapi import FastAPI

from backend.fieldx_kernel.p_adic import PrimeLatticeState

# Deterministic list of prime numbers for token assignment.
# Generated at import time but deterministic because no randomness is used.

_DEFAULT_PRIME_COUNT = 10_000
_PRIMES: List[int] = []


@lru_cache(maxsize=None)
def _ensure_primes(count: int) -> List[int]:
    """Return at least ``count`` deterministic primes, caching the result.

    The cache is safe because the generated list only ever grows and is
    deterministic for a given ``count``.
    """
    primes = _PRIMES
    if len(primes) >= count:
        return primes

    candidate = primes[-1] + 1 if primes else 2
    while len(primes) < count:
        is_prime = True
        limit = int(math.sqrt(candidate)) + 1
        for prime in primes:
            if prime > limit:
                break
            if candidate % prime == 0:
                is_prime = False
                break
        if is_prime:
            primes.append(candidate)
        candidate += 1
    return primes


def normalise_tokens(text: str) -> List[str]:
    """Return lowercase ``[a-zA-Z0-9]+`` tokens extracted from ``text``."""

    return [token.lower() for token in re.findall(r"[a-zA-Z0-9]+", text)]


# Backward-compatible alias (deprecated)
def normalise_text(text: str) -> List[str]:
    return normalise_tokens(text)


class TokenPrimeIndex:
    """Manage token→prime assignments and an inverted prime index.

    The index keeps in-process LRU-style caches for token→prime and
    prime→token lookups.  Assignments are still persisted to RocksDB so that
    the mapping survives process restarts, but hot lookups avoid repeated
    database reads.
    """

    def __init__(self, app: FastAPI):
        self.app = app
        self.db = app.state.db
        self._token_prime_cache: Dict[str, int] = {}
        self._prime_token_cache: Dict[int, str] = {}

    @staticmethod
    def _token_key(token: str) -> str:
        return f"tp:token:{token}"

    @staticmethod
    def _prime_key(prime: int) -> str:
        return f"ix:prime:{prime}"

    @staticmethod
    def _prime_token_key(prime: int) -> str:
        return f"tp:prime:{prime}"

    @staticmethod
    def _keyword_prime_key(prime: int) -> str:
        return f"ix:kw:{prime}"

    @staticmethod
    def _next_index_key() -> str:
        return "tp:next_index"

    @staticmethod
    def _product(primes: Sequence[int]) -> int:
        return reduce(lambda acc, value: acc * int(value), primes, 1)

    @staticmethod
    def _decode_value(raw):
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, (bytes, bytearray)) else raw

    def _current_prime_target(self) -> int:
        next_index_raw = self.db.get(self._next_index_key())
        next_index = int(next_index_raw) if next_index_raw is not None else 0
        return max(next_index, _DEFAULT_PRIME_COUNT)

    def _multi_get(self, keys: List[str]) -> List[object]:
        """Fetch multiple keys in one round-trip if the backing store supports it."""
        if not keys:
            return []
        multi_get = getattr(self.db, "multi_get", None)
        if multi_get is not None:
            try:
                return list(multi_get(keys))
            except Exception:
                pass
        return [self.db.get(key) for key in keys]

    def get_or_assign_prime(self, token: str) -> int:
        """Return the assigned prime for ``token`` or allocate a new one."""

        normalised_token = token.strip().lower()
        if not normalised_token:
            raise ValueError("token cannot be empty")

        if normalised_token in self._token_prime_cache:
            return self._token_prime_cache[normalised_token]

        token_key = self._token_key(normalised_token)
        existing = self.db.get(token_key)
        if existing is not None:
            prime = int(existing)
            self._token_prime_cache[normalised_token] = prime
            self._prime_token_cache[prime] = normalised_token
            return prime

        next_index_raw = self.db.get(self._next_index_key())
        next_index = int(next_index_raw) if next_index_raw is not None else 0
        primes = _ensure_primes(next_index + 1)
        prime = primes[next_index]
        self.db[token_key] = str(prime)
        self.db[self._prime_token_key(prime)] = normalised_token
        self.db[self._next_index_key()] = str(next_index + 1)
        self._token_prime_cache[normalised_token] = prime
        self._prime_token_cache[prime] = normalised_token
        return prime

    def primes_for_tokens_batch(self, tokens: Iterable[str]) -> Dict[str, int]:
        """Return a mapping ``normalised_token -> prime`` for ``tokens``.

        New primes are allocated in a single batch, reducing RocksDB round-trips.
        """

        # Preserve first-seen order while deduplicating.
        unique_tokens: List[str] = []
        seen: set[str] = set()
        for token in tokens:
            normalised = token.strip().lower()
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            unique_tokens.append(normalised)

        result: Dict[str, int] = {}
        missing: List[str] = []

        for token in unique_tokens:
            cached = self._token_prime_cache.get(token)
            if cached is not None:
                result[token] = cached
            else:
                missing.append(token)

        if missing:
            keys = [self._token_key(token) for token in missing]
            values = self._multi_get(keys)
            still_missing: List[str] = []
            for token, value in zip(missing, values):
                if value is not None:
                    prime = int(value)
                    result[token] = prime
                    self._token_prime_cache[token] = prime
                    self._prime_token_cache[prime] = token
                else:
                    still_missing.append(token)

            if still_missing:
                next_index_raw = self.db.get(self._next_index_key())
                next_index = int(next_index_raw) if next_index_raw is not None else 0
                count = len(still_missing)
                primes = _ensure_primes(next_index + count)
                for offset, token in enumerate(still_missing):
                    prime = primes[next_index + offset]
                    self.db[self._token_key(token)] = str(prime)
                    self.db[self._prime_token_key(prime)] = token
                    self.db[self._next_index_key()] = str(next_index + offset + 1)
                    result[token] = prime
                    self._token_prime_cache[token] = prime
                    self._prime_token_cache[prime] = token

        return result

    def primes_for_tokens(self, tokens: Iterable[str]) -> List[int]:
        """Return primes for ``tokens``, allocating new ones as needed."""

        mapping = self.primes_for_tokens_batch(tokens)
        seen: set[str] = set()
        primes: List[int] = []
        for token in tokens:
            normalised = token.strip().lower()
            if not normalised or normalised in seen:
                continue
            seen.add(normalised)
            primes.append(mapping[normalised])
        return primes

    def lattice_state_for_tokens(self, tokens: Iterable[str]) -> PrimeLatticeState:
        """Return a ``PrimeLatticeState`` for ``tokens``, allocating primes as needed."""

        return PrimeLatticeState.from_primes(self.primes_for_tokens(tokens))

    def update_inverted_index(self, primes: Iterable[int], entry_id: str) -> None:
        """Add ``entry_id`` to the inverted index for each ``prime``."""

        for prime in primes:
            key = self._prime_key(prime)
            existing_raw = self.db.get(key)
            if existing_raw:
                try:
                    entries = set(json.loads(existing_raw))
                except (TypeError, json.JSONDecodeError):
                    entries = set()
            else:
                entries = set()

            if entry_id not in entries:
                entries.add(entry_id)
                self.db[key] = json.dumps(sorted(entries))

    def update_inverted_index_delta(
        self,
        deltas: Mapping[int, int],
        entry_id: str,
    ) -> None:
        """Apply sparse exponent deltas to the inverted index for ``entry_id``.

        Positive deltas add ``entry_id`` to the prime's posting set; negative
        deltas remove it.  A posting set is deleted when it becomes empty.
        """

        for prime, delta in deltas.items():
            if delta == 0:
                continue

            key = self._prime_key(prime)
            existing_raw = self.db.get(key)
            if existing_raw:
                try:
                    entries = set(json.loads(existing_raw))
                except (TypeError, json.JSONDecodeError):
                    entries = set()
            else:
                entries = set()

            if delta > 0:
                if entry_id not in entries:
                    entries.add(entry_id)
                    self.db[key] = json.dumps(sorted(entries))
            else:
                if entry_id in entries:
                    entries.remove(entry_id)
                    if entries:
                        self.db[key] = json.dumps(sorted(entries))
                    else:
                        del self.db[key]

    def update_keyword_index(self, prime_weights: Mapping[int, float], entry_id: str) -> None:
        """Add weighted keyword postings for ``entry_id`` by ``prime``."""

        for prime, weight in prime_weights.items():
            key = self._keyword_prime_key(int(prime))
            existing_raw = self.db.get(key)
            if existing_raw:
                try:
                    entries = json.loads(existing_raw)
                except (TypeError, json.JSONDecodeError):
                    entries = {}
            else:
                entries = {}

            current = entries.get(entry_id)
            if current is None or float(weight) > float(current):
                entries[entry_id] = float(weight)
                self.db[key] = json.dumps(entries)

    def keyword_weights_for_primes(self, primes: Iterable[int]) -> dict[int, dict[str, float]]:
        """Return mapping of prime->entry_id->weight for keyword postings."""

        results: dict[int, dict[str, float]] = {}
        for prime in primes:
            raw = self._decode_value(self.db.get(self._keyword_prime_key(int(prime))))
            if raw is None:
                continue
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                results[int(prime)] = {
                    str(entry_id): float(weight)
                    for entry_id, weight in payload.items()
                }
        return results

    def token_for_prime(self, prime: int) -> str | None:
        """Return the token associated with ``prime`` if recorded."""

        prime = int(prime)
        cached = self._prime_token_cache.get(prime)
        if cached is not None:
            return cached

        raw = self.db.get(self._prime_token_key(prime))
        decoded = self._decode_value(raw)
        token = str(decoded) if decoded is not None else None
        if token is not None:
            self._prime_token_cache[prime] = token
        return token

    def factor_coordinate(self, coordinate: int) -> list[int]:
        """Prime-factorise ``coordinate`` into known primes."""

        if coordinate is None or int(coordinate) < 1:
            raise ValueError("Coordinate must be a positive integer")

        remaining = int(coordinate)
        factors: list[int] = []

        primes = _ensure_primes(self._current_prime_target())
        for prime in primes:
            if prime * prime > remaining:
                break
            while remaining % prime == 0:
                factors.append(prime)
                remaining //= prime
            if remaining == 1:
                break

        if remaining > 1:
            if remaining not in primes:
                raise ValueError("Coordinate contains prime factors outside the index range")
            factors.append(remaining)

        return factors

    def unique_prime_factors(self, coordinate: int) -> list[int]:
        """Return unique sorted factors for ``coordinate``."""

        return sorted(set(self.factor_coordinate(coordinate)))

    def product_for_primes(self, primes: Sequence[int]) -> int:
        """Return the multiplicative coordinate for ``primes``."""

        if not primes:
            raise ValueError("No primes supplied for coordinate generation")
        return self._product(primes)

    def entries_for_prime(self, prime: int) -> set[str]:
        """Return entry identifiers associated with ``prime``."""

        raw = self._decode_value(self.db.get(self._prime_key(int(prime))))
        if raw is None:
            return set()
        try:
            payload = json.loads(raw)
            return {str(item) for item in payload}
        except (TypeError, json.JSONDecodeError):
            return set()

    def entries_for_primes(self, primes: Iterable[int]) -> dict[int, set[str]]:
        """Return mapping of prime→entry_ids for provided ``primes``."""

        result: dict[int, set[str]] = {}
        for prime in primes:
            entries = self.entries_for_prime(int(prime))
            if entries:
                result[int(prime)] = entries
        return result

    def resolve_entries_for_primes(
        self,
        primes: Iterable[int],
        store: MutableMapping | Mapping,
        limit: int | None = None,
    ) -> list[dict]:
        """Resolve ledger entries for ``primes`` with metadata context."""

        from backend.fieldx_kernel.substrate.ledger_store_v2 import _collect_text_fragments

        entries_by_prime = self.entries_for_primes(primes)
        reverse_index: dict[str, set[int]] = {}
        max_entries = None
        if limit is not None:
            max_entries = max(int(limit), 0)
            if max_entries == 0:
                return []

        for prime in sorted(entries_by_prime):
            for entry_id in sorted(entries_by_prime[prime]):
                if max_entries is not None and len(reverse_index) >= max_entries:
                    break
                reverse_index.setdefault(entry_id, set()).add(prime)
            if max_entries is not None and len(reverse_index) >= max_entries:
                break

        resolved: list[dict] = []
        for entry_id, prime_set in reverse_index.items():
            if max_entries is not None and len(resolved) >= max_entries:
                break
            try:
                entry = store.read(entry_id) if hasattr(store, "read") else None
            except Exception:
                entry = None
            if entry is None:
                continue

            metadata = entry.state.metadata or {}
            text = metadata.get("full_text")
            if not text:
                fragments = list(_collect_text_fragments(metadata))
                text = " ".join(str(fragment) for fragment in fragments if fragment)

            resolved.append(
                {
                    "entry_id": entry_id,
                    "key": {
                        "namespace": entry.key.namespace,
                        "identifier": entry.key.identifier,
                    },
                    "primes": sorted(prime_set),
                    "tokens": [token for prime in sorted(prime_set) if (token := self.token_for_prime(prime))],
                    "metadata": metadata,
                    "text": text,
                }
            )

        return sorted(resolved, key=lambda row: row["entry_id"])
