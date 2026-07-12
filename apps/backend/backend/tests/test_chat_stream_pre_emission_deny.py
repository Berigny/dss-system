from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import chat as chat_module
from backend.api.chat import router as chat_router
from backend.fieldx_kernel.governance_engine import CoherenceException


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(chat_router)
    return TestClient(app)


def _patch_common_stream(monkeypatch) -> None:
    async def _fake_assemble_context(**_kwargs):
        return {"recent": [], "claims": [], "retrieved": [], "assessments": {}}

    async def _fake_stream(**_kwargs):
        async def _tokens():
            for token in ("Hello", " world"):
                yield token

        fut: asyncio.Future[str] = asyncio.Future()
        fut.set_result("stop")
        return _tokens(), fut

    monkeypatch.setattr(chat_module, "assemble_context", _fake_assemble_context)
    monkeypatch.setattr(chat_module, "yield_chat_stream", _fake_stream)
    monkeypatch.setattr(chat_module, "PRE_EMISSION_DENY_STRICT", True)


def _events_from_ndjson(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def test_stream_pre_emission_deny_blocks_token_leak_on_blocked_turn(monkeypatch) -> None:
    _patch_common_stream(monkeypatch)

    async def _raise_enrich(**_kwargs):
        raise CoherenceException("genesis_ladder_blocked")

    monkeypatch.setattr(chat_module, "enrich_turn", _raise_enrich)

    client = _make_client()
    resp = client.post(
        "/chat/stream",
        json={
            "session_id": "s1",
            "entity": "chat-team-a",
            "ledger_id": "chat-team-a",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert resp.status_code == 200
    events = _events_from_ndjson(resp.text)
    assert not any(evt.get("type") == "token" for evt in events)
    assert any(evt.get("type") == "pre_emission_deny" for evt in events)
    assert any(evt.get("type") == "policy_envelope" for evt in events)
    meta = next((evt for evt in events if evt.get("type") == "meta"), {})
    posture = meta.get("posture_policy") if isinstance(meta, dict) else {}
    assert isinstance(posture, dict)
    assert posture.get("policy_decision") == "deny"


def test_stream_pre_emission_deny_flushes_tokens_when_not_blocked(monkeypatch) -> None:
    _patch_common_stream(monkeypatch)

    async def _ok_enrich(**_kwargs):
        return {
            "coordinate": "chat-team-a:WX-1",
            "metadata": {"appraisal": {"law_score": 1.0, "grace_score": 1.0}},
            "flow_enrich": {},
        }

    monkeypatch.setattr(chat_module, "enrich_turn", _ok_enrich)

    client = _make_client()
    resp = client.post(
        "/chat/stream",
        json={
            "session_id": "s1",
            "entity": "chat-team-a",
            "ledger_id": "chat-team-a",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert resp.status_code == 200
    events = _events_from_ndjson(resp.text)
    token_contents = [str(evt.get("content") or "") for evt in events if evt.get("type") == "token"]
    assert "".join(token_contents) == "Hello world"
    assert not any(evt.get("type") == "pre_emission_deny" for evt in events)
    assert any(evt.get("type") == "policy_envelope" for evt in events)
    meta = next((evt for evt in events if evt.get("type") == "meta"), {})
    posture = meta.get("posture_policy") if isinstance(meta, dict) else {}
    assert isinstance(posture, dict)
    assert posture.get("policy_decision") in {"allow", "degrade"}
