from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pytest
from fastapi import FastAPI

import backend.fieldx_kernel.substrate.ledger_store_v2 as ledger_module
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.orchestrator import fuzzy_retrieve
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.search.token_index import TokenPrimeIndex


@pytest.fixture
def low_body_prime(monkeypatch):
    """Lower the body-prime filter so short test tokens create token balls."""
    monkeypatch.setattr(ledger_module, "MIN_BODY_PRIME", 2)
    monkeypatch.setattr(ledger_module, "FLOW_PRIMES", frozenset())


class _FakeMemoryService:
    def __init__(self, memories: Sequence[Mapping[str, Any]]) -> None:
        self._memories = memories

    def get_all_memories(self, entity: str | None = None) -> Sequence[Mapping[str, Any]]:
        return list(self._memories)

    def anchor(self, text: str, entity: str | None = None) -> Mapping[str, Any]:
        return {}


def _zero_embeddings(texts: list[str]) -> list[list[float]]:
    dim = 1536
    return [[0.0] * dim for _ in texts]


def test_fuzzy_retrieve_prefilters_with_padic_ball_scan(low_body_prime, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.fieldx_kernel.orchestrator._get_embeddings",
        _zero_embeddings,
    )

    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    # Write two entries with distinct token sets and integer identifiers.
    entry_a = LedgerEntry(LedgerKey("chat-demo", "1"), ContinuousState({}, "phase", {}))
    entry_a.text = "alpha beta"
    store.write(entry_a)

    entry_b = LedgerEntry(LedgerKey("chat-demo", "2"), ContinuousState({}, "phase", {}))
    entry_b.text = "gamma delta"
    store.write(entry_b)

    memories = [
        {"coord": entry_a.key.as_path(), "text": entry_a.text},
        {"coord": entry_b.key.as_path(), "text": entry_b.text},
    ]
    service = _FakeMemoryService(memories)

    from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore

    padic_store = PAdicLedgerStore(app.state.db, p=5, N=4)

    results = fuzzy_retrieve(
        "alpha beta",
        entity="chat-demo",
        memory_service=service,
        token_index=token_index,
        padic_store=padic_store,
    )

    # The ball pre-filter should narrow candidates to the entry whose
    # token-product residue matches the query.
    result_coords = [r.get("coord") for r in results]
    assert entry_a.key.as_path() in result_coords
    assert entry_b.key.as_path() not in result_coords


def test_fuzzy_retrieve_falls_back_when_no_ball_matches(low_body_prime, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.fieldx_kernel.orchestrator._get_embeddings",
        _zero_embeddings,
    )

    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    entry_a = LedgerEntry(LedgerKey("chat-demo", "1"), ContinuousState({}, "phase", {}))
    entry_a.text = "alpha beta"
    store.write(entry_a)

    memories = [
        {"coord": entry_a.key.as_path(), "text": entry_a.text},
    ]
    service = _FakeMemoryService(memories)

    from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore

    padic_store = PAdicLedgerStore(app.state.db, p=5, N=4)

    results = fuzzy_retrieve(
        "gamma delta",  # no token-product ball match
        entity="chat-demo",
        memory_service=service,
        token_index=token_index,
        padic_store=padic_store,
    )

    # No ball match -> fallback to full candidate list; the single memory
    # should still be returned.
    assert len(results) == 1


def test_ledger_store_v2_populates_token_padic_ball_store(low_body_prime) -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    entry = LedgerEntry(LedgerKey("chat-demo", "42"), ContinuousState({}, "phase", {}))
    entry.text = "alpha beta"
    store.write(entry)

    from backend.fieldx_kernel.p_adic import PAdicInteger
    from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore

    padic = PAdicLedgerStore(app.state.db, p=5, N=4)
    primes = token_index.primes_for_tokens(["alpha", "beta"])
    from backend.fieldx_kernel.substrate.ledger_store_v2 import _token_product_residue

    residue = _token_product_residue(primes, 5, 4)
    state = PAdicInteger.from_int(5, residue, 4)

    payloads = padic.ball_prefix_scan("tp:chat-demo", k=4, residue=state.value_mod(4))
    assert any(p.decode() == "42" for p in payloads)
