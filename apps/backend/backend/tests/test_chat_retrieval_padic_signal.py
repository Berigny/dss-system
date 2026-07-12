from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import chat as chat_module
from backend.api.agent_writes import record_message
from backend.api.assemble import assemble_payload
from backend.api.chat import router as chat_router
from backend.fieldx_kernel import assemble_context
from backend.fieldx_kernel.ledger import MemoryLedger
from backend.fieldx_kernel.substrate import LedgerStoreV2, MemorySubstrate
from backend.search.token_index import TokenPrimeIndex
from backend.services.ledger_service import LedgerService


# Push target tokens past small body-prime filtering (MIN_BODY_PRIME=23) so the
# query primes overlap the persisted factor list.
_FILLER = "zero one two three four five six seven eight nine eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
_ENERGY_TEST_TEXT = _FILLER + " xylophone zebra quartz"
_QUERY_TEXT = "xylophone zebra quartz"


def _make_index_and_store() -> tuple[TokenPrimeIndex, LedgerStoreV2, MemorySubstrate, MemoryLedger]:
    app = FastAPI()
    db: dict = {}
    app.state.db = db
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(db, token_index=token_index)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)
    return token_index, store, substrate, ledger


def _write_seed_record(
    store: LedgerStoreV2,
    substrate: MemorySubstrate,
    ledger: MemoryLedger,
    web4_key: str,
) -> str:
    result = record_message(
        entity="chat-demo",
        role="assistant",
        content=_ENERGY_TEST_TEXT,
        kind="chat",
        metadata={"web4_key": web4_key, "created_at": datetime.now(timezone.utc).isoformat()},
        substrate=substrate,
        ledger=ledger,
        store=store,
    )
    return str(result["coordinate"])


@pytest.mark.anyio
async def test_assemble_context_surfaces_padic_score(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PADIC_WRITE_COST_LAMBDA", "0.1")
    monkeypatch.setenv("P_ADIC_DISTANCE_PRIME", "5")

    token_index, store, substrate, ledger = _make_index_and_store()
    _write_seed_record(store, substrate, ledger, "WX-PADIC-SEED-1")

    result = await assemble_context(
        entity="chat-demo",
        query=_QUERY_TEXT,
        k=3,
        ledger=ledger,
        substrate=substrate,
        store=store,
        token_index=token_index,
        padic_store=store._padic_store,
    )

    diagnostics = result.get("padic_diagnostics")
    assert isinstance(diagnostics, dict)
    assert int(diagnostics.get("query_prime_count") or 0) > 0
    assert int(diagnostics.get("ball_hit_count") or 0) > 0
    assert float(diagnostics.get("top_p_adic_score") or 0.0) > 0.0

    catalog = result.get("candidate_catalog") or []
    assert isinstance(catalog, list)
    assert any(
        isinstance(item, dict) and float(item.get("p_adic_score") or 0.0) > 0.0
        for item in catalog
    )


@pytest.mark.anyio
async def test_assemble_endpoint_surfaces_padic_score(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PADIC_WRITE_COST_LAMBDA", "0.1")
    monkeypatch.setenv("P_ADIC_DISTANCE_PRIME", "5")

    app = FastAPI()
    db: dict = {}
    app.state.db = db
    token_index = TokenPrimeIndex(app)
    store = LedgerStoreV2(db, token_index=token_index)
    substrate = MemorySubstrate(db)
    ledger = MemoryLedger(db)
    _write_seed_record(store, substrate, ledger, "WX-PADIC-SEED-2")

    payload = await assemble_payload(
        entity="chat-demo",
        query=_QUERY_TEXT,
        k=3,
        quote_safe=False,
        since=None,
        until=None,
        ledger=ledger,
        substrate=substrate,
        store=store,
        token_index=token_index,
    )

    diagnostics = payload.get("padic_diagnostics")
    assert isinstance(diagnostics, dict)
    assert float(diagnostics.get("top_p_adic_score") or 0.0) > 0.0
    catalog = payload.get("candidate_catalog") or []
    assert any(
        isinstance(item, dict) and float(item.get("p_adic_score") or 0.0) > 0.0
        for item in catalog
    )


def _events_from_ndjson(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def test_chat_stream_candidate_trace_has_padic_score(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PADIC_WRITE_COST_LAMBDA", "0.1")
    monkeypatch.setenv("P_ADIC_DISTANCE_PRIME", "5")

    app = FastAPI()
    db: dict = {}
    app.state.db = db
    app.include_router(chat_router)
    client = TestClient(app)

    token_index = TokenPrimeIndex(app)
    service = LedgerService(db, token_index=token_index)
    substrate = service.memory_substrate()
    ledger = service.memory_ledger()
    _write_seed_record(service.store, substrate, ledger, "WX-PADIC-SEED-3")

    async def _fake_stream(**_kwargs):
        async def _tokens():
            for token in ("Hello", " world"):
                yield token

        fut: asyncio.Future[str] = asyncio.Future()
        fut.set_result("stop")
        return _tokens(), fut

    async def _fake_enrich_turn(**_kwargs):
        return {
            "coordinate": "chat-demo:WX-PADIC-STREAM-1",
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

    monkeypatch.setattr(chat_module, "yield_chat_stream", _fake_stream)
    monkeypatch.setattr(chat_module, "enrich_turn", _fake_enrich_turn)
    monkeypatch.setattr(chat_module, "_compute_loop_risk", _fake_loop_risk)
    monkeypatch.setattr(chat_module, "PRE_EMISSION_DENY_STRICT", False)

    resp = client.post(
        "/chat/stream",
        json={
            "session_id": "s-padic",
            "entity": "chat-demo",
            "ledger_id": "chat-demo",
            "message": _QUERY_TEXT,
            "provider": "openai",
            "history": [],
        },
        headers={"x-ledger-id": "chat-demo"},
    )
    assert resp.status_code == 200
    events = _events_from_ndjson(resp.text)

    candidate_trace_evt = next((evt for evt in events if evt.get("type") == "candidate_trace"), {})
    candidate_trace = candidate_trace_evt.get("payload", {}).get("top_k") or []
    assert any(
        isinstance(row, dict) and float(row.get("p_adic_score") or 0.0) > 0.0
        for row in candidate_trace
    )

    context_meta = next((evt for evt in events if evt.get("type") == "context_meta"), {})
    assert isinstance(context_meta.get("padic_diagnostics"), dict)
    assert float(context_meta["padic_diagnostics"].get("top_p_adic_score") or 0.0) > 0.0

    meta = next((evt for evt in events if evt.get("type") == "meta"), {})
    assert isinstance(meta.get("padic_diagnostics"), dict)
    meta_trace = meta.get("candidate_trace") or []
    assert any(
        isinstance(row, dict) and float(row.get("p_adic_score") or 0.0) > 0.0
        for row in meta_trace
    )
