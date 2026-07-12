from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.compat import router as compat_router
from backend.fieldx_kernel.kernel_origin_equations import calculate_persistence_cost
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.search.token_index import TokenPrimeIndex


# A sentence with enough unique tokens that the deterministic token→prime
# assignment crosses ``MIN_BODY_PRIME`` (23) and produces a non-empty lattice.
_ENERGY_TEST_TEXT = "one two three four five six seven eight nine ten eleven"


def _make_token_index(db: dict | None = None) -> tuple[TokenPrimeIndex, dict]:
    app = FastAPI()
    storage: dict = db if db is not None else {}
    app.state.db = storage
    return TokenPrimeIndex(app), storage


def _make_entry(namespace: str, identifier: str, text: str | None = None) -> LedgerEntry:
    metadata: dict = {}
    if text is not None:
        metadata["text"] = text
    return LedgerEntry(
        key=LedgerKey(namespace=namespace, identifier=identifier),
        state=ContinuousState(metadata=metadata),
        created_at=datetime.now(timezone.utc),
    )


# --- Unit-level cost function -------------------------------------------------


def test_persistence_cost_without_lattice_delta_unchanged() -> None:
    base = calculate_persistence_cost(0.01, 0.95, 100)
    with_delta_no_lambda = calculate_persistence_cost(
        0.01, 0.95, 100, lattice_delta={2: 1, 3: -1}, lambda_p=0.0
    )
    assert with_delta_no_lambda == pytest.approx(base)


def test_persistence_cost_adds_discrete_lattice_term() -> None:
    base = calculate_persistence_cost(0.01, 0.95, 100)
    lattice_delta = {2: 3, 3: -2, 5: 0}
    lambda_p = 0.05
    expected_delta_cost = lambda_p * (3 + 2 + 0)

    cost = calculate_persistence_cost(
        0.01, 0.95, 100, lattice_delta=lattice_delta, lambda_p=lambda_p
    )

    assert cost == pytest.approx(base + expected_delta_cost)


# --- LedgerStoreV2 integration ------------------------------------------------


def test_ledger_store_v2_records_padic_write_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PADIC_WRITE_COST_LAMBDA", "0.1")

    token_index, db = _make_token_index()
    store = LedgerStoreV2(db, token_index=token_index)
    entry = _make_entry("ns", "1001", _ENERGY_TEST_TEXT)

    store.write(entry)

    written = store.read(entry.key.as_path())
    assert written is not None
    cost = written.state.metadata.get("p_adic_write_cost")
    assert isinstance(cost, float)
    assert cost > 0.0


def test_ledger_store_v2_zero_cost_when_lambda_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PADIC_WRITE_COST_LAMBDA", raising=False)

    token_index, db = _make_token_index()
    store = LedgerStoreV2(db, token_index=token_index)
    entry = _make_entry("ns", "1002", _ENERGY_TEST_TEXT)

    store.write(entry)

    written = store.read(entry.key.as_path())
    assert written is not None
    assert written.state.metadata.get("p_adic_write_cost") == 0.0


# --- Compatibility route (/anchor) --------------------------------------------


def _make_compat_client(db: dict | None = None) -> tuple[TestClient, dict]:
    app = FastAPI()
    storage: dict = db if db is not None else {}
    app.state.db = storage
    app.include_router(compat_router)
    return TestClient(app), storage


def test_compat_anchor_returns_energy_when_lambda_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PADIC_WRITE_COST_LAMBDA", "0.1")

    client, _db = _make_compat_client()
    payload = {
        "entity": "compat-ns",
        "factors": [{"prime": 29, "delta": 1}],
        "text": _ENERGY_TEST_TEXT,
    }

    resp = client.post("/anchor", json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "anchored"
    assert body["energy"] is not None
    assert body["energy"] > 0.0


def test_compat_anchor_energy_zero_when_lambda_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PADIC_WRITE_COST_LAMBDA", raising=False)

    client, _db = _make_compat_client()
    payload = {
        "entity": "compat-ns",
        "factors": [{"prime": 29, "delta": 1}],
        "text": _ENERGY_TEST_TEXT,
    }

    resp = client.post("/anchor", json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "anchored"
    assert body["energy"] == 0.0
