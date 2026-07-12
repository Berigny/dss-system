from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import chat as chat_module
from backend.api.agent_writes import record_message
from backend.api.chat import router as chat_router
from backend.fieldx_kernel.ledger import MemoryLedger
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate import LedgerStoreV2, MemorySubstrate
from backend.search.token_index import TokenPrimeIndex


_ENERGY_TEST_TEXT = "one two three four five six seven eight nine ten eleven twelve thirteen"


def _make_token_index(db: dict | None = None) -> tuple[TokenPrimeIndex, dict]:
    app = FastAPI()
    storage: dict = db if db is not None else {}
    app.state.db = storage
    return TokenPrimeIndex(app), storage


def _make_record_message_deps() -> tuple[MemorySubstrate, MemoryLedger, LedgerStoreV2]:
    token_index, db = _make_token_index()
    store = LedgerStoreV2(db, token_index=token_index)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)
    return substrate, ledger, store


def test_record_message_persists_factors_and_padic_write_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PADIC_WRITE_COST_LAMBDA", "0.1")
    substrate, ledger, store = _make_record_message_deps()

    result = record_message(
        entity="chat-demo",
        role="assistant",
        content=_ENERGY_TEST_TEXT,
        kind="chat",
        metadata={"web4_key": "WX-TEST-1"},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )

    meta = result["metadata"]
    assert isinstance(meta.get("factors"), list)
    assert len(meta["factors"]) > 0
    assert meta["core_info_entry_class"] == "turn"
    assert isinstance(meta.get("kernel_prime_exponents"), dict)
    assert isinstance(meta.get("p_adic_write_cost"), (int, float))
    assert float(meta["p_adic_write_cost"]) > 0.0

    coordinate = result["coordinate"]
    entry = store.read(coordinate)
    assert entry is not None
    stored_meta = entry.state.metadata
    assert isinstance(stored_meta.get("factors"), list)
    assert len(stored_meta["factors"]) > 0
    assert isinstance(stored_meta.get("p_adic_write_cost"), (int, float))
    assert float(stored_meta["p_adic_write_cost"]) > 0.0


def test_record_message_zero_cost_when_lambda_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PADIC_WRITE_COST_LAMBDA", raising=False)
    substrate, ledger, store = _make_record_message_deps()

    result = record_message(
        entity="chat-demo",
        role="assistant",
        content=_ENERGY_TEST_TEXT,
        kind="chat",
        metadata={"web4_key": "WX-TEST-2"},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )

    assert result["metadata"]["p_adic_write_cost"] == 0.0
    entry = store.read(result["coordinate"])
    assert entry is not None
    assert entry.state.metadata["p_adic_write_cost"] == 0.0
    # Factors are still attached regardless of cost lambda.
    assert entry.state.metadata.get("factors")


def _events_from_ndjson(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def test_chat_stream_emits_padic_write_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PADIC_WRITE_COST_LAMBDA", "0.1")

    app = FastAPI()
    app.state.db = {}
    app.include_router(chat_router)
    client = TestClient(app)

    async def _fake_assemble_context(**_kwargs):
        return {"recent": [], "claims": [], "retrieved": [], "assessments": {}}

    async def _fake_stream(**_kwargs):
        async def _tokens():
            for token in ("Hello", " world"):
                yield token

        fut: asyncio.Future[str] = asyncio.Future()
        fut.set_result("stop")
        return _tokens(), fut

    async def _fake_enrich_turn(**_kwargs):
        return {
            "coordinate": "chat-demo:WX-STREAM-1",
            "metadata": {
                "appraisal": {"law_score": 1.0, "grace_score": 1.0},
                "p_adic_write_cost": 0.42,
                "factors": [{"prime": 13, "delta": 1}, {"prime": 23, "delta": 1}],
            },
            "flow_enrich": {},
        }

    def _fake_loop_risk(**_kwargs):
        return {
            "loop_risk": 0.0,
            "hard_threshold": 1.0,
            "warn_threshold": 1.0,
            "grounding_gap": 0.0,
            "closure_pressure": 0.0,
        }

    monkeypatch.setattr(chat_module, "assemble_context", _fake_assemble_context)
    monkeypatch.setattr(chat_module, "yield_chat_stream", _fake_stream)
    monkeypatch.setattr(chat_module, "enrich_turn", _fake_enrich_turn)
    monkeypatch.setattr(chat_module, "_compute_loop_risk", _fake_loop_risk)
    monkeypatch.setattr(chat_module, "PRE_EMISSION_DENY_STRICT", False)

    resp = client.post(
        "/chat/stream",
        json={
            "session_id": "s1",
            "entity": "chat-demo",
            "ledger_id": "chat-demo",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
        headers={"x-ledger-id": "chat-demo"},
    )
    assert resp.status_code == 200
    events = _events_from_ndjson(resp.text)

    context_meta = next((evt for evt in events if evt.get("type") == "context_meta"), {})
    meta = next((evt for evt in events if evt.get("type") == "meta"), {})

    assert isinstance(context_meta.get("p_adic_write_cost"), (int, float))
    assert float(context_meta["p_adic_write_cost"]) > 0.0
    assert isinstance(meta.get("p_adic_write_cost"), (int, float))
    assert float(meta["p_adic_write_cost"]) > 0.0
