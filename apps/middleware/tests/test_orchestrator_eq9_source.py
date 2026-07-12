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
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)


@pytest.mark.parametrize(
    "source",
    {
        "post_commit_metadata",
        "post_commit_cache",
        "pending_post_commit_introspect",
        "post_commit_introspect",
    },
)
def test_eq9_source_enum_is_known(source: str):
    # Sanity guard to keep expected source enum explicit.
    assert source in {
        "post_commit_metadata",
        "post_commit_cache",
        "pending_post_commit_introspect",
        "post_commit_introspect",
    }


def test_eq9_source_post_commit_metadata(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-eq9:WX-commit",
            "metadata": {
                "governance": {
                    "metrics": {
                        "L": 1.0,
                        "H": 0.0,
                        "U": 1.0,
                        "V": 0.0,
                        "I1": 0.0,
                        "I2": 0.0,
                    }
                }
            },
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {}

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    events = _stream_events(
        {
            "session_id": "eq9-source-meta",
            "message": "check source",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    assert meta.get("eq9_eval_source") == "post_commit_metadata"
    assert meta.get("eq9_eval_pending") is False


def test_eq9_source_post_commit_cache_sets_cache_hit(monkeypatch):
    session_id = "eq9-source-cache"
    entity = f"chat-{session_id}"
    coordinate = "chat-eq9:WX-cache"
    cache_key = orchestrator_module._post_introspect_cache_key(entity, session_id, coordinate)
    cached_eq9 = {
        "on_track": True,
        "checks": {},
    }

    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "post_introspect_cache": {
            cache_key: {
                "expires_at": time.time() + 600,
                "eq9_eval": cached_eq9,
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
        return {}

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    events = _stream_events(
        {
            "session_id": session_id,
            "message": "check cache",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    assert meta.get("eq9_eval_source") == "post_commit_cache"
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    assert metadata.get("post_introspect_cache_hit") is True


def test_eq9_meta_patch_applied_clears_pending(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-eq9:WX-live",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {
            "governance_metrics": {
                "L": 1.0,
                "H": 0.0,
                "U": 1.0,
                "V": 0.0,
                "I1": 0.0,
                "I2": 0.0,
            }
        }

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    events = _stream_events(
        {
            "session_id": "eq9-live-patch",
            "message": "check live patch",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    patch_events = [event for event in events if event.get("type") == "meta_patch"]
    assert patch_events, "Expected meta_patch for live post-commit introspection"
    patch = patch_events[-1]
    assert patch.get("status") == "applied"
    assert patch.get("eq9_eval_pending") is False
    assert patch.get("eq9_eval_source") == "post_commit_introspect"



def test_telemetry_payload_includes_eq9_and_meta_patch_fields(monkeypatch):
    captured: dict = {}

    async def fake_emit_telemetry(payload, **_kwargs):
        if isinstance(payload, dict):
            captured.update(payload)
        return None

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-eq9:WX-telemetry",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {
            "governance_metrics": {
                "L": 1.0,
                "H": 0.0,
                "U": 1.0,
                "V": 0.0,
                "I1": 0.0,
                "I2": 0.0,
            }
        }

    _patch_common(monkeypatch)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)

    _stream_events(
        {
            "session_id": "eq9-telemetry-fields",
            "message": "check telemetry parity",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    assert captured.get("eq9_eval_source") == "post_commit_introspect"
    assert captured.get("meta_patch_status") == "applied"
    assert "meta_patch_reason" in captured
