from __future__ import annotations

import pytest
from fastapi import FastAPI

import backend.fieldx_kernel.substrate.ledger_store_v2 as ledger_module
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.p_adic import PrimeLatticeState
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.search.token_index import TokenPrimeIndex


class _CountingDict(dict):
    """Dict that counts writes/deletes for index keys."""

    def __init__(self) -> None:
        super().__init__()
        self.index_writes = 0
        self.index_deletes = 0

    def __setitem__(self, key, value):
        if isinstance(key, str) and key.startswith("ix:prime:"):
            self.index_writes += 1
        super().__setitem__(key, value)

    def __delitem__(self, key):
        if isinstance(key, str) and key.startswith("ix:prime:"):
            self.index_deletes += 1
        super().__delitem__(key)


@pytest.fixture
def low_body_prime(monkeypatch):
    """Lower the body-prime filter so short test tokens are indexed."""
    monkeypatch.setattr(ledger_module, "MIN_BODY_PRIME", 2)
    monkeypatch.setattr(ledger_module, "FLOW_PRIMES", frozenset())


def test_prime_lattice_state_delta() -> None:
    a = PrimeLatticeState({2: 1, 3: 1, 5: 1})
    b = PrimeLatticeState({3: 1, 5: 1, 7: 1})

    delta = b.delta(a)
    assert delta[2] == -1
    assert delta[7] == 1
    assert 3 not in delta
    assert 5 not in delta


def test_token_index_delta_adds_and_removes_entries() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)

    prime_a = token_index.get_or_assign_prime("alpha")
    prime_b = token_index.get_or_assign_prime("beta")

    token_index.update_inverted_index_delta({prime_a: 1, prime_b: 1}, "entry-1")
    assert "entry-1" in token_index.entries_for_prime(prime_a)
    assert "entry-1" in token_index.entries_for_prime(prime_b)

    token_index.update_inverted_index_delta({prime_a: -1}, "entry-1")
    assert "entry-1" not in token_index.entries_for_prime(prime_a)
    assert "entry-1" in token_index.entries_for_prime(prime_b)


def test_ledger_store_write_uses_sparse_delta_indexing(low_body_prime) -> None:
    app = FastAPI()
    app.state.db = _CountingDict()
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    entry = LedgerEntry(
        LedgerKey("ns", "1"),
        ContinuousState({}, "phase", {}),
    )
    entry.text = "alpha beta gamma"
    store.write(entry)

    initial_writes = app.state.db.index_writes
    initial_deletes = app.state.db.index_deletes
    assert initial_writes > 0  # New entry: all primes indexed.

    # Update the entry to drop "alpha" and add "delta".
    updated = LedgerEntry(
        LedgerKey("ns", "1"),
        ContinuousState({}, "phase", {}),
    )
    updated.text = "beta gamma delta"
    store.write(updated)

    # Only the changed primes should trigger index writes/removes.
    delta_writes = app.state.db.index_writes - initial_writes
    delta_deletes = app.state.db.index_deletes - initial_deletes
    assert delta_writes <= 2  # "delta" prime added; possibly one re-write
    assert delta_deletes == 1  # "alpha" prime removed


def test_ledger_store_write_indexes_all_primes_for_new_entry(low_body_prime) -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    entry = LedgerEntry(
        LedgerKey("ns", "2"),
        ContinuousState({}, "phase", {}),
    )
    entry.text = "one two three"
    store.write(entry)

    # Every token prime should appear in the inverted index.
    for token in ["one", "two", "three"]:
        prime = token_index.get_or_assign_prime(token)
        assert entry.key.as_path() in token_index.entries_for_prime(prime)
