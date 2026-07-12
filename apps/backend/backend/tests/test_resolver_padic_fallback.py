from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.resolver import router as resolver_router
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.p_adic import PAdicInteger
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(resolver_router)
    return TestClient(app)


def _write_exact(client: TestClient, namespace: str, identifier: str, content: str) -> None:
    db = client.app.state.db
    store = LedgerStoreV2(db)
    entry = LedgerEntry(
        key=LedgerKey(namespace=namespace, identifier=identifier),
        state=ContinuousState(metadata={"content": content}),
        created_at=datetime.now(timezone.utc),
    )
    store.write(entry)


def _write_padic_index(
    client: TestClient,
    namespace: str,
    n: int,
    path: str,
    p: int = 5,
    N: int = 4,
) -> None:
    db = client.app.state.db
    padic_store = PAdicLedgerStore(db, p=p, N=N)
    state = PAdicInteger.from_int(p=p, n=n, N=N)
    padic_store.write(namespace, state, path.encode())


def test_resolve_exact_without_precision() -> None:
    client = _make_client()
    _write_exact(client, "ns", "42", "hello")

    resp = client.post("/resolve", json={"namespace": "ns", "identifier": "42"})
    assert resp.status_code == 200
    assert resp.json()["state"]["metadata"]["content"] == "hello"


def test_resolve_padic_fallback_returns_nearest_state() -> None:
    client = _make_client()
    _write_exact(client, "ns", "42", "hello")
    _write_padic_index(client, "ns", 42, "ns:42")

    # 47 has the same k=1 residue (2) as 42 under p=5, but is not an exact entry.
    resp = client.post(
        "/resolve",
        json={"namespace": "ns", "identifier": "47", "precision": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["state"]["metadata"]["content"] == "hello"


def test_resolve_padic_fallback_honours_min_precision() -> None:
    client = _make_client()
    _write_exact(client, "ns", "42", "hello")
    _write_padic_index(client, "ns", 42, "ns:42")

    # 42 and 47 share residue 2 mod 5 (k=1) but not mod 25 (k=2).
    # With precision=2 the fallback must not match.
    resp = client.post(
        "/resolve",
        json={"namespace": "ns", "identifier": "47", "precision": 2},
    )
    assert resp.status_code == 404


def test_resolve_true_miss_still_404() -> None:
    client = _make_client()
    _write_exact(client, "ns", "42", "hello")
    _write_padic_index(client, "ns", 42, "ns:42")

    # 43 has a different k=1 residue (3) from 42 (2).
    resp = client.post(
        "/resolve",
        json={"namespace": "ns", "identifier": "43", "precision": 1},
    )
    assert resp.status_code == 404


def test_resolve_non_integer_identifier_ignores_precision() -> None:
    client = _make_client()
    _write_exact(client, "ns", "WX-42", "hello")

    resp = client.post(
        "/resolve",
        json={"namespace": "ns", "identifier": "WX-42", "precision": 1},
    )
    assert resp.status_code == 200


def test_resolve_error_message_does_not_claim_valuation_zero() -> None:
    client = _make_client()
    resp = client.post(
        "/resolve",
        json={"namespace": "ns", "identifier": "missing", "precision": 1},
    )
    assert resp.status_code == 404
    detail = resp.json().get("detail", "")
    assert "valuation is 0" not in str(detail).lower()
    assert "valuation=0" not in str(detail).lower()
