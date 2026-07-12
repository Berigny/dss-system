from __future__ import annotations

import pytest

from backend.fieldx_kernel.p_adic import PAdicInteger
from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore


@pytest.fixture
def store() -> PAdicLedgerStore:
    return PAdicLedgerStore(db={}, p=5, N=4)


def test_write_and_read_exact_payload(store: PAdicLedgerStore) -> None:
    state = PAdicInteger.from_int(p=5, n=123, N=4)
    payload = b"entry-123"
    store.write("chat-demo", state, payload)

    assert store.read("chat-demo", state) == payload


def test_read_requires_exact_match(store: PAdicLedgerStore) -> None:
    state = PAdicInteger.from_int(p=5, n=123, N=4)
    store.write("chat-demo", state, b"entry-123")

    other = PAdicInteger.from_int(p=5, n=124, N=4)
    assert store.read("chat-demo", other) is None


def test_nearest_falls_back_to_coarser_ball(store: PAdicLedgerStore) -> None:
    # Write a state and query a different state that shares the same
    # low-order digit (same residue mod 5) but diverges at higher precision.
    written = PAdicInteger.from_int(p=5, n=123, N=4)  # digits (3,4,4,0)
    store.write("chat-demo", written, b"entry-123")

    query = PAdicInteger.from_int(p=5, n=128, N=4)  # digits (3,0,1,0)
    # Exact (k=4) differs, but k=1 residue 3 matches.
    assert store.nearest("chat-demo", query, min_k=1) == b"entry-123"
    # If we require exact match, it should miss.
    assert store.nearest("chat-demo", query, min_k=4) is None


def test_nearest_returns_none_when_no_ball_matches(store: PAdicLedgerStore) -> None:
    state = PAdicInteger.from_int(p=5, n=123, N=4)
    store.write("chat-demo", state, b"entry-123")

    query = PAdicInteger.from_int(p=5, n=124, N=4)
    assert store.nearest("chat-demo", query, min_k=1) is None


def test_write_at_multiple_precisions_is_retrievable(store: PAdicLedgerStore) -> None:
    state = PAdicInteger.from_int(p=5, n=123, N=4)
    store.write("chat-demo", state, b"entry-123")

    # The same state can be retrieved at every coarser precision.
    for k in range(1, 5):
        coarse = PAdicInteger.from_int(p=5, n=123 % (5**k), N=k)
        # Construct a query state at full precision N=4 but with the same residue mod 5**k.
        query = PAdicInteger.from_int(p=5, n=123, N=4)
        assert store.nearest("chat-demo", query, min_k=k) == b"entry-123"


def test_ball_prefix_scan_enumerates_ball(store: PAdicLedgerStore) -> None:
    # States with distinct k=1 residues are stored under distinct k=1 keys.
    a = PAdicInteger.from_int(p=5, n=3, N=4)
    b = PAdicInteger.from_int(p=5, n=4, N=4)
    c = PAdicInteger.from_int(p=5, n=12, N=4)  # residue 2 mod 5

    store.write("ns", a, b"a")
    store.write("ns", b, b"b")
    store.write("ns", c, b"c")

    k1_ball = store.ball_prefix_scan("ns", k=1, residue=3)
    assert k1_ball == [b"a"]

    # Exact sub-ball for residue 3 mod 5**4 contains only a.
    exact_ball = store.ball_prefix_scan("ns", k=4, residue=3)
    assert exact_ball == [b"a"]


def test_ball_prefix_scan_returns_all_at_precision(store: PAdicLedgerStore) -> None:
    a = PAdicInteger.from_int(p=5, n=3, N=4)
    b = PAdicInteger.from_int(p=5, n=4, N=4)
    store.write("ns", a, b"a")
    store.write("ns", b, b"b")

    all_at_k1 = store.ball_prefix_scan("ns", k=1)
    assert set(all_at_k1) == {b"a", b"b"}


def test_store_rejects_incompatible_state(store: PAdicLedgerStore) -> None:
    wrong_p = PAdicInteger.from_int(p=3, n=1, N=4)
    wrong_N = PAdicInteger.from_int(p=5, n=1, N=3)

    with pytest.raises(ValueError):
        store.write("ns", wrong_p, b"x")
    with pytest.raises(ValueError):
        store.nearest("ns", wrong_N)


def test_nearest_rejects_invalid_min_k(store: PAdicLedgerStore) -> None:
    state = PAdicInteger.from_int(p=5, n=1, N=4)
    with pytest.raises(ValueError):
        store.nearest("ns", state, min_k=0)
    with pytest.raises(ValueError):
        store.nearest("ns", state, min_k=5)
