import asyncio
import json

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")

from app import app
import routes.orchestrator as orchestrator_module


client = TestClient(app)


def _patch_permissive_runtime_actor(monkeypatch) -> None:
    def fake_resolve_runtime_actor(*, payload, auth_claims=None, provider=None, agent=None):
        actor_resolution = {
            "actor_did": str((payload or {}).get("principal_did") or "did:key:z6MkWalkTest"),
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "binding_candidates": [],
            "principal_status": "active",
            "tenant_id": "tenant:test",
            "session_jti": str((payload or {}).get("session_jti") or "") or None,
            "auth_method": "claims" if auth_claims else None,
            "verification_state": "claims_only" if auth_claims else "unverified",
            "resolution_reason": "test_fixture",
        }
        standing_envelope = {
            "standing_envelope_version": "se-v1",
            "standing_envelope_ref": "env:test",
            "actor_did": actor_resolution["actor_did"],
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "verification_state": actor_resolution["verification_state"],
            "trust_class": "T2",
            "posture_class": "P2",
            "active_sanctions": [],
            "probation_status": None,
            "tool_scope": "full",
            "retrieval_scope": "tenant",
            "max_output_tokens": 4096,
            "write_commit_allowed": True,
            "credential_ref": None,
            "reason_code": "test_fixture",
            "resolved_at": "2026-03-15T00:00:00Z",
        }
        return actor_resolution, standing_envelope

    monkeypatch.setattr(orchestrator_module, "_resolve_runtime_actor", fake_resolve_runtime_actor)


def test_orchestrator_propagates_walk_flow_diagnostic(monkeypatch):
    flow_diag = "FLOW VIOLATION (C-cross): C cannot route to S2 odd 5 from S1 context."

    async def fake_assemble(**_kwargs):
        return {"retrieved": []}

    async def fake_decode_coordinate(_coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": "chat-demo-session:WX-123",
            "type": "WX",
            "skim": {"one_line": "candidate"},
            "walk": None,
            "refs": {},
            "payload": {"parts": []},
            "interpretation": {},
            "governance": {},
            "meta": {
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_cw": 1,
            },
        }

    async def fake_coord_walk(**_kwargs):
        return {
            "status": "success",
            "path": ["chat-demo-session:WX-123"],
            "steps": [
                {
                    "from": "chat-demo-session:WX-123",
                    "to": "chat-demo-session:WX-123",
                    "score": -0.4,
                    "lawfulness_level": 0,
                    "flow_diagnostic": flow_diag,
                    "candidates": [
                        {
                            "coord": "chat-demo-session:WX-123",
                            "score": -0.4,
                            "eq6_lawfulness_level": 0,
                            "flow_diagnostic": flow_diag,
                        }
                    ],
                }
            ],
            "flow_diagnostic": flow_diag,
        }

    async def fake_write_walk(_payload: dict, **_kwargs):
        return {"status": "success", "walk_id": "EV-WALK-test", "coordinate": "chat-demo-session:EV-WALK-test"}

    async def fake_track_telemetry(**_kwargs):
        return None

    async def fake_commit_answer(**_kwargs):
        return {"status": "success", "coordinate": "chat-demo-session:WX-commit"}

    async def fake_introspect_runtime(**_kwargs):
        return {}

    async def fake_stream_response(**_kwargs):
        async def _gen():
            yield "ok"

        fut: asyncio.Future = asyncio.Future()
        fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 1, "output": 1}})
        return _gen(), fut

    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", fake_track_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    with client.stream(
        "POST",
        "/api/orchestrator",
        json={
            "session_id": "walk-flow-test",
            "message": "walk this",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "_stream_passthrough": True,
            "context_coords": ["chat-demo-session:WX-123"],
            "k": 1,
        },
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    events = [json.loads(line) for line in lines]
    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events, "Expected at least one meta event in orchestrator stream"

    meta = meta_events[-1]
    router_decision = meta.get("router_decision") if isinstance(meta.get("router_decision"), dict) else {}
    coord_walk = meta.get("coord_walk") if isinstance(meta.get("coord_walk"), dict) else {}

    assert router_decision.get("walk_flow_diagnostic") == flow_diag
    assert coord_walk.get("flow_diagnostic") == flow_diag
