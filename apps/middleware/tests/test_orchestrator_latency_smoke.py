import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")

from app import app
import routes.orchestrator as orchestrator_module


client = TestClient(app)


async def _fake_assemble(**_kwargs):
    return {"retrieved": [], "decoded_context": []}


async def _fake_decode_coordinate(_coord: str, *, entity: str | None = None, session_id: str | None = None):
    return {
        "coord": "chat-latency:WX-123",
        "type": "WX",
        "skim": {"one_line": "candidate"},
        "walk": None,
        "refs": {},
        "payload": {"parts": []},
        "interpretation": {},
        "governance": {},
        "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
    }


async def _fake_emit_telemetry(**_kwargs):
    return None


async def _fake_stream_response(**_kwargs):
    async def _gen():
        yield "ok"

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 1, "output": 1}})
    return _gen(), fut


def _stream_events(payload: dict) -> list[dict]:
    with client.stream("POST", "/api/orchestrator", json=payload) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    return [json.loads(line) for line in lines]


def _patch_common(monkeypatch):
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)


def test_latency_policy_applies_when_prior_rolling_exceeds_threshold(monkeypatch):
    session_id = "latency-policy-trigger"
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "latency_rolling_ms": float(orchestrator_module.LATENCY_ROUTE_THRESHOLD_MS + 1200),
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-latency:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {}

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "LATENCY_ALLOW_S1_FALLBACK", True)
    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    events = _stream_events(
        {
            "session_id": session_id,
            "message": "latency route check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 5,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    latency_diag = meta.get("latency_diagnostics") if isinstance(meta.get("latency_diagnostics"), dict) else {}
    policy = latency_diag.get("policy") if isinstance(latency_diag.get("policy"), dict) else {}

    assert policy.get("enabled") is True
    assert policy.get("allow_s1_fallback") is True
    assert policy.get("applied") is True
    assert int(policy.get("k_after")) <= max(int(orchestrator_module.LATENCY_ROUTE_K_LIMIT), 1)


def test_latency_policy_does_not_downgrade_without_opt_in(monkeypatch):
    session_id = "latency-policy-no-downgrade-default"
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "latency_rolling_ms": float(orchestrator_module.LATENCY_ROUTE_THRESHOLD_MS + 1200),
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-latency:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {}

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "LATENCY_ALLOW_S1_FALLBACK", False)
    monkeypatch.setattr(orchestrator_module, "S_MODE_DEFAULT", "s2")
    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    events = _stream_events(
        {
            "session_id": session_id,
            "message": "latency route no downgrade",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 5,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    latency_diag = meta.get("latency_diagnostics") if isinstance(meta.get("latency_diagnostics"), dict) else {}
    policy = latency_diag.get("policy") if isinstance(latency_diag.get("policy"), dict) else {}
    governance_path = meta.get("governance_path") if isinstance(meta.get("governance_path"), dict) else {}

    assert policy.get("enabled") is True
    assert policy.get("allow_s1_fallback") is False
    assert policy.get("applied") is False
    assert governance_path.get("s_mode") == "s2"


def test_post_introspect_cache_path_marks_cache_hit(monkeypatch):
    session_id = "latency-cache-path"
    entity = f"chat-{session_id}"
    coordinate = "chat-latency:WX-cache"
    cache_key = orchestrator_module._post_introspect_cache_key(entity, session_id, coordinate)
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "post_introspect_cache": {
            cache_key: {
                "expires_at": time.time() + 600,
                "eq9_eval": {"on_track": True, "checks": {}},
                "introspect_snapshot": {"governance_metrics": {"L": 1.0}},
            }
        },
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": coordinate,
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    events = _stream_events(
        {
            "session_id": session_id,
            "message": "cache route check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 3,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    assert meta.get("eq9_eval_source") == "post_commit_cache"
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    assert metadata.get("post_introspect_cache_hit") is True
    patch_events = [event for event in events if event.get("type") == "meta_patch"]
    assert not patch_events


def test_break_glass_profile_marker_emitted(monkeypatch):
    session_id = "break-glass-marker"
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-latency:WX-break-glass",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {}

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "BREAK_GLASS_UNSAFE_PROFILE", True)
    monkeypatch.setattr(orchestrator_module, "_RUNTIME_PROFILE_MARKERS", ["break_glass_profile"])
    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    events = _stream_events(
        {
            "session_id": session_id,
            "message": "break glass marker check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    policy_controls = meta.get("policy_controls") if isinstance(meta.get("policy_controls"), dict) else {}
    markers = policy_controls.get("runtime_profile_markers") if isinstance(policy_controls.get("runtime_profile_markers"), list) else []

    assert policy_controls.get("break_glass_profile_active") is True
    assert "break_glass_profile" in markers


def test_break_glass_authorized_override_has_audit_trace(monkeypatch):
    session_id = "break-glass-authorized-override"
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-latency:WX-break-glass-auth",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {}

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "BREAK_GLASS_UNSAFE_PROFILE", True)
    monkeypatch.setattr(orchestrator_module, "_RUNTIME_PROFILE_MARKERS", ["break_glass_profile"])
    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    with client.stream(
        "POST",
        "/api/orchestrator",
        json={
            "session_id": session_id,
            "message": "break glass authorized override",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": False,
            "s_mode": "s1",
            "principal_did": "did:key:test-user",
            "session_jti": "sess-123",
        },
        headers={"Authorization": "Bearer opaque-session-token"},
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    events = [json.loads(line) for line in lines]
    meta = [event for event in events if event.get("type") == "meta"][-1]
    policy_controls = meta.get("policy_controls") if isinstance(meta.get("policy_controls"), dict) else {}
    rejected = policy_controls.get("rejected_overrides") if isinstance(policy_controls.get("rejected_overrides"), list) else []

    assert policy_controls.get("break_glass_profile_active") is True
    assert policy_controls.get("override_authorized") is True
    assert policy_controls.get("requested_enable_ledger") is False
    assert policy_controls.get("effective_enable_ledger") is False
    assert policy_controls.get("requested_s_mode") == "s1"
    assert policy_controls.get("effective_s_mode") == "s1"
    assert rejected == []
