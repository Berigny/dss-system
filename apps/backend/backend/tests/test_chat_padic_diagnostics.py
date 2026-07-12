"""Offline validation that /chat/stream surfaces p-adic diagnostics."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import chat as chat_module
from backend.api.chat import router as chat_router


def _events_from_ndjson(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


async def _fake_assemble_context(**kwargs):
    include_diagnostics = bool(kwargs.get("include_padic_diagnostics", True))
    result: dict = {
        "recent": [],
        "claims": [],
        "retrieved": [
            {
                "coordinate": "chat-demo:WX-PADIC-1",
                "relevance_score": 0.92,
                "p_adic_score": 0.85,
                "p_adic_write_cost": 0.12,
                "snippet": "factor-bearing prior turn",
                "source": "retrieved",
            }
        ],
        "assessments": {},
    }
    if include_diagnostics:
        result["padic_diagnostics"] = {
            "query_prime_count": 3,
            "ball_hit_count": 1,
            "top_p_adic_score": 0.85,
            "top_p_adic_write_cost": 0.12,
            "hardening_level": kwargs.get("hardening_level", 0),
        }
    return result


async def _fake_stream(**_kwargs):
    async def _tokens():
        yield "Hello"
        yield " world"

    fut: asyncio.Future[str] = asyncio.Future()
    fut.set_result("stop")
    return _tokens(), fut


async def _fake_enrich_turn(**_kwargs):
    return {
        "coordinate": "chat-demo:WX-PADIC-2",
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


def test_chat_stream_surfaces_padic_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PADIC_WRITE_COST_LAMBDA", "0.1")

    app = FastAPI()
    app.state.db = {}
    app.include_router(chat_router)
    client = TestClient(app)

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
            "message": "hello factor bearing query",
            "provider": "openai",
            "history": [],
            "include_padic_diagnostics": True,
            "hardening_level": 2,
        },
        headers={"x-ledger-id": "chat-demo"},
    )
    assert resp.status_code == 200
    events = _events_from_ndjson(resp.text)

    context_meta = next((evt for evt in events if evt.get("type") == "context_meta"), {})
    meta = next((evt for evt in events if evt.get("type") == "meta"), {})
    candidate_trace = next((evt for evt in events if evt.get("type") == "candidate_trace"), {})

    # Retrieval signal
    payload = candidate_trace.get("payload") if isinstance(candidate_trace.get("payload"), dict) else {}
    rows = payload.get("top_k") if isinstance(payload.get("top_k"), list) else []
    assert rows, "expected candidate_trace top_k rows"
    assert any(float(row.get("p_adic_score", 0.0)) > 0.0 for row in rows), "expected non-zero p_adic_score"

    # Diagnostics requested and forwarded
    ctx_diagnostics = context_meta.get("padic_diagnostics")
    meta_diagnostics = meta.get("padic_diagnostics")
    assert isinstance(ctx_diagnostics, dict) or isinstance(meta_diagnostics, dict)
    diagnostics = ctx_diagnostics if isinstance(ctx_diagnostics, dict) else meta_diagnostics
    assert diagnostics.get("query_prime_count") == 3
    assert diagnostics.get("hardening_level") == 2

    # Write cost
    assert float(context_meta.get("p_adic_write_cost", 0.0)) > 0.0
    assert float(meta.get("p_adic_write_cost", 0.0)) > 0.0


def test_chat_stream_omits_padic_diagnostics_when_not_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.state.db = {}
    app.include_router(chat_router)
    client = TestClient(app)

    monkeypatch.setattr(chat_module, "assemble_context", _fake_assemble_context)
    monkeypatch.setattr(chat_module, "yield_chat_stream", _fake_stream)
    monkeypatch.setattr(chat_module, "enrich_turn", _fake_enrich_turn)
    monkeypatch.setattr(chat_module, "_compute_loop_risk", _fake_loop_risk)
    monkeypatch.setattr(chat_module, "PRE_EMISSION_DENY_STRICT", False)

    resp = client.post(
        "/chat/stream",
        json={
            "session_id": "s2",
            "entity": "chat-demo",
            "ledger_id": "chat-demo",
            "message": "hello",
            "provider": "openai",
            "history": [],
            "include_padic_diagnostics": False,
        },
        headers={"x-ledger-id": "chat-demo"},
    )
    assert resp.status_code == 200
    events = _events_from_ndjson(resp.text)

    meta = next((evt for evt in events if evt.get("type") == "meta"), {})
    assert meta.get("padic_diagnostics") is None
