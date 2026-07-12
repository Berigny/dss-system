from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from backend.api.resolver import _try_padic_nearest
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.p_adic import PAdicInteger
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore
from backend.search.token_index import TokenPrimeIndex


@dataclass
class _FakeRequest:
    app: FastAPI


def test_ledger_store_v2_writes_integer_id_to_padic_ball_store() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    entry = LedgerEntry(
        LedgerKey("chat-demo", "123"),
        ContinuousState({}, "phase", {"text": "alpha beta"}),
    )
    entry.text = "alpha beta"
    store.write(entry)

    padic = PAdicLedgerStore(app.state.db, p=5, N=4)
    query = PAdicInteger.from_int(5, 123, 4)
    payload = padic.read("chat-demo", query)
    assert payload is not None
    assert payload.decode() == entry.key.as_path()


def test_padic_store_uses_reference_keys_to_reduce_amplification() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    entry = LedgerEntry(
        LedgerKey("chat-demo", "456"),
        ContinuousState({}, "phase", {}),
    )
    entry.text = "gamma"
    store.write(entry)

    padic = PAdicLedgerStore(app.state.db, p=5, N=4)
    query = PAdicInteger.from_int(5, 456, 4)
    payload_key_count = sum(
        1 for key in app.state.db.keys()
        if isinstance(key, bytes) and b"padic:chat-demo:p=5:payload:" in key
    )
    # Payload stored exactly once.
    assert payload_key_count == 1

    # All ball keys should resolve to the same payload.
    payload = padic.read("chat-demo", query)
    assert payload is not None
    assert payload.decode() == entry.key.as_path()


def test_resolver_returns_padic_resolution_metadata() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    entry = LedgerEntry(
        LedgerKey("chat-demo", "789"),
        ContinuousState({}, "phase", {}),
    )
    entry.text = "delta"
    store.write(entry)

    request = _FakeRequest(app)
    resolved = _try_padic_nearest(request, store, "chat-demo", "789", precision=4)
    assert resolved is not None
    metadata = resolved.state.metadata or {}
    resolution = metadata.get("p_adic_resolution")
    assert isinstance(resolution, dict)
    assert resolution["mode"] == "nearest_ball"
    assert resolution["precision_found"] == 4
    assert resolution["distance"] is not None
    assert resolution["confidence"] is not None


def test_ledger_store_v2_skips_non_integer_identifiers() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    entry = LedgerEntry(
        LedgerKey("chat-demo", "wx-uuid"),
        ContinuousState({}, "phase", {}),
    )
    entry.text = "epsilon"
    store.write(entry)

    padic = PAdicLedgerStore(app.state.db, p=5, N=4)
    # No p-adic keys should have been written for a non-digit identifier.
    padic_keys = [k for k in app.state.db.keys() if isinstance(k, bytes) and k.startswith(b"padic:")]
    assert len(padic_keys) == 0


def test_padic_nearest_falls_back_after_production_write() -> None:
    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(app.state.db, token_index=token_index)

    # Write an entry with identifier 1000.
    entry = LedgerEntry(
        LedgerKey("chat-demo", "1000"),
        ContinuousState({}, "phase", {}),
    )
    entry.text = "zeta"
    store.write(entry)

    # Query a different integer that shares the same low-order residue.
    padic = PAdicLedgerStore(app.state.db, p=5, N=4)
    query = PAdicInteger.from_int(5, 1000 + 5**3, 4)  # same mod 5, 25, 125
    payload, k_found, distance = padic.nearest_with_distance("chat-demo", query, min_k=1)
    assert payload is not None
    assert payload.decode() == entry.key.as_path()
    assert k_found is not None
    assert k_found >= 1
    assert distance is not None
