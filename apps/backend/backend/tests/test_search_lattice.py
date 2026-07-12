from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI

from backend.fieldx_kernel.models import ContinuousState
from backend.search.service import search
from backend.search.token_index import TokenPrimeIndex


@dataclass
class _FakeKey:
    namespace: str
    identifier: str


@dataclass
class _FakeEntry:
    key: _FakeKey
    state: ContinuousState
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str = ""


class _FakeStore:
    def __init__(self, entries: dict[str, _FakeEntry]) -> None:
        self._entries = entries

    def read(self, entry_id: str) -> _FakeEntry | None:
        return self._entries.get(entry_id)


def _make_index() -> tuple[TokenPrimeIndex, dict]:
    app = FastAPI()
    app.state.db = {}
    return TokenPrimeIndex(app), app.state.db


def test_search_uses_prime_lattice_exponents() -> None:
    token_index, _db = _make_index()

    # Assign primes for known tokens so we can build deterministic lattices.
    alpha = token_index.get_or_assign_prime("alpha")
    beta = token_index.get_or_assign_prime("beta")

    entries = {
        "entry-a": _FakeEntry(
            _FakeKey("ns", "a"),
            ContinuousState({}, "phase", {"full_text": "alpha", "prime_lattice_exponents": {alpha: 1}}),
        ),
        "entry-b": _FakeEntry(
            _FakeKey("ns", "b"),
            ContinuousState({}, "phase", {"full_text": "beta", "prime_lattice_exponents": {beta: 1}}),
        ),
    }
    token_index.update_inverted_index([alpha], "entry-a")
    token_index.update_inverted_index([beta], "entry-b")
    store = _FakeStore(entries)

    results = search("alpha", store=store, token_index=token_index)
    assert len(results) == 1
    assert results[0]["entry_id"] == "entry-a"


def test_search_discards_orthogonal_entries() -> None:
    token_index, _db = _make_index()

    alpha = token_index.get_or_assign_prime("alpha")
    gamma = token_index.get_or_assign_prime("gamma")

    entries = {
        "entry-a": _FakeEntry(
            _FakeKey("ns", "a"),
            ContinuousState({}, "phase", {"full_text": "alpha", "prime_lattice_exponents": {alpha: 1}}),
        ),
        "entry-b": _FakeEntry(
            _FakeKey("ns", "b"),
            ContinuousState({}, "phase", {"full_text": "gamma", "prime_lattice_exponents": {gamma: 1}}),
        ),
    }
    token_index.update_inverted_index([alpha], "entry-a")
    token_index.update_inverted_index([gamma], "entry-b")
    store = _FakeStore(entries)

    results = search("alpha", store=store, token_index=token_index)
    assert [r["entry_id"] for r in results] == ["entry-a"]


def test_search_boosts_multiple_shared_primes() -> None:
    token_index, _db = _make_index()

    alpha = token_index.get_or_assign_prime("alpha")
    beta = token_index.get_or_assign_prime("beta")

    entries = {
        "entry-a": _FakeEntry(
            _FakeKey("ns", "a"),
            ContinuousState({}, "phase", {"full_text": "alpha", "prime_lattice_exponents": {alpha: 1}}),
        ),
        "entry-b": _FakeEntry(
            _FakeKey("ns", "b"),
            ContinuousState({}, "phase", {"full_text": "alpha beta", "prime_lattice_exponents": {alpha: 1, beta: 1}}),
        ),
    }
    token_index.update_inverted_index([alpha], "entry-a")
    token_index.update_inverted_index([alpha, beta], "entry-b")
    store = _FakeStore(entries)

    results = search("alpha beta", store=store, token_index=token_index)
    assert len(results) == 2
    # entry-b shares both primes and should outrank entry-a.
    assert results[0]["entry_id"] == "entry-b"
    assert results[0]["p_adic_overlap"] == 2
    assert results[1]["p_adic_overlap"] == 1


def test_search_fallback_to_legacy_token_primes() -> None:
    token_index, _db = _make_index()

    alpha = token_index.get_or_assign_prime("alpha")

    entries = {
        "entry-a": _FakeEntry(
            _FakeKey("ns", "a"),
            ContinuousState({}, "phase", {"full_text": "alpha", "token_primes": [alpha]}),
        ),
    }
    token_index.update_inverted_index([alpha], "entry-a")
    store = _FakeStore(entries)

    results = search("alpha", store=store, token_index=token_index)
    assert len(results) == 1
    assert results[0]["entry_id"] == "entry-a"


def test_ledger_store_writes_prime_lattice_exponents() -> None:
    from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2

    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    from backend.fieldx_kernel.models import LedgerEntry, LedgerKey

    entry = LedgerEntry(
        LedgerKey("ns", "entry-1"),
        ContinuousState({}, "phase", {}),
    )
    entry.text = "alpha beta"
    store.write(entry)

    metadata = entry.state.metadata or {}
    assert "prime_lattice_exponents" in metadata
    assert isinstance(metadata["prime_lattice_exponents"], dict)
    # token_prime_product should no longer be stored.
    assert "token_prime_product" not in metadata
