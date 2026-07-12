from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from backend.fieldx_kernel.p_adic import PrimeLatticeState
from backend.search.token_index import TokenPrimeIndex


@dataclass
class _FakeKey:
    namespace: str
    identifier: str


@dataclass
class _FakeState:
    metadata: dict


@dataclass
class _FakeEntry:
    key: _FakeKey
    state: _FakeState


class _FakeStore:
    def __init__(self, entries: dict[str, _FakeEntry]) -> None:
        self._entries = entries
        self.read_calls: list[str] = []

    def read(self, entry_id: str) -> _FakeEntry | None:
        self.read_calls.append(entry_id)
        return self._entries.get(entry_id)


def test_resolve_entries_for_primes_respects_limit() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)

    prime_alpha = token_index.get_or_assign_prime("alpha")
    prime_beta = token_index.get_or_assign_prime("beta")

    token_index.update_inverted_index([prime_alpha], "entry-b")
    token_index.update_inverted_index([prime_alpha], "entry-a")
    token_index.update_inverted_index([prime_beta], "entry-c")

    store = _FakeStore(
        {
            "entry-a": _FakeEntry(_FakeKey("ns", "a"), _FakeState({"full_text": "alpha"})),
            "entry-b": _FakeEntry(_FakeKey("ns", "b"), _FakeState({"full_text": "beta"})),
            "entry-c": _FakeEntry(_FakeKey("ns", "c"), _FakeState({"full_text": "gamma"})),
        }
    )

    resolved = token_index.resolve_entries_for_primes(
        [prime_alpha, prime_beta],
        store,
        limit=1,
    )

    assert [item["entry_id"] for item in resolved] == ["entry-a"]
    assert store.read_calls == ["entry-a"]


def test_lattice_state_for_tokens_uses_assigned_primes() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)

    state = token_index.lattice_state_for_tokens(["alpha", "beta", "alpha"])

    assert isinstance(state, PrimeLatticeState)
    # Deduplication: "alpha" appears twice but should contribute one prime.
    assert len(state.exponents) == 2
    assert state.value() == token_index.product_for_primes(
        token_index.primes_for_tokens(["alpha", "beta"])
    )


def test_lattice_state_round_trips_through_factor_coordinate() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)

    state = token_index.lattice_state_for_tokens(["hello", "world"])
    factors = token_index.factor_coordinate(state.value())

    assert sorted(factors) == sorted(token_index.primes_for_tokens(["hello", "world"]))


class _CountingDict(dict):
    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    def get(self, key):
        self.get_calls += 1
        return super().get(key)


def test_token_prime_cache_avoids_repeated_db_reads() -> None:
    app = FastAPI()
    app.state.db = _CountingDict()
    token_index = TokenPrimeIndex(app)

    prime = token_index.get_or_assign_prime("cached-token")

    # Second lookup should hit the in-process cache and not touch the DB.
    app.state.db.get_calls = 0
    assert token_index.get_or_assign_prime("cached-token") == prime
    assert app.state.db.get_calls == 0

    # Reverse lookup should also hit the cache.
    app.state.db.get_calls = 0
    assert token_index.token_for_prime(prime) == "cached-token"
    assert app.state.db.get_calls == 0


def test_token_for_prime_uses_cache() -> None:
    app = FastAPI()
    app.state.db = _CountingDict()
    token_index = TokenPrimeIndex(app)

    prime = token_index.get_or_assign_prime("reverse-lookup")

    app.state.db.get_calls = 0
    assert token_index.token_for_prime(prime) == "reverse-lookup"
    assert app.state.db.get_calls == 0


def test_primes_for_tokens_batch_assigns_and_deduplicates() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)

    tokens = ["alpha", "beta", "alpha", "gamma", "beta"]
    mapping = token_index.primes_for_tokens_batch(tokens)

    assert len(mapping) == 3
    assert mapping["alpha"] == mapping["alpha"]
    assert mapping["beta"] == mapping["beta"]
    assert mapping["alpha"] != mapping["beta"]
    assert mapping["alpha"] != mapping["gamma"]

    # Re-running should return the same assignments from cache/DB.
    mapping2 = token_index.primes_for_tokens_batch(tokens)
    assert mapping2 == mapping


def test_primes_for_tokens_uses_batch_and_preserves_order() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)

    primes = token_index.primes_for_tokens(["one", "two", "one", "three"])
    assert len(primes) == 3
    assert primes[0] == token_index.get_or_assign_prime("one")
    assert primes[1] == token_index.get_or_assign_prime("two")
    assert primes[2] == token_index.get_or_assign_prime("three")


def test_batch_prime_assignment_reduces_individual_gets() -> None:
    app = FastAPI()
    app.state.db = _CountingDict()
    token_index = TokenPrimeIndex(app)

    # All tokens are new; the implementation may read next_index once and
    # perform a multi_get / individual gets for the missing token keys.
    mapping = token_index.primes_for_tokens_batch(["a", "b", "c", "d", "e"])
    assert len(mapping) == 5
    assert len(set(mapping.values())) == 5

    # After assignment, a second batch should not read the DB for these tokens.
    app.state.db.get_calls = 0
    mapping2 = token_index.primes_for_tokens_batch(["a", "b", "c", "d", "e"])
    assert mapping2 == mapping
    # Only the next_index key may be read if the implementation checks it;
    # no token assignment keys should be read because they are cached.
    assert app.state.db.get_calls <= 1
