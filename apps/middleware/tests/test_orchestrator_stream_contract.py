import asyncio
import json

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")

from app import app
import routes.orchestrator as orchestrator_module


client = TestClient(app)


_REQUIRED_META_FIELDS = {
    "eq9_eval",
    "eq9_eval_pre_commit",
    "eq9_eval_post_commit",
    "eq9_eval_source",
    "eq9_eval_pending",
}


async def _fake_assemble(**_kwargs):
    return {"retrieved": [], "decoded_context": []}


async def _fake_assemble_with_candidates(**_kwargs):
    return {
        "retrieved": [
            {
                "coordinate": "chat-demo:WX-1772505927152",
                "relevance_score": 0.96,
                "snippet": "resolved payload candidate",
                "source": "retrieved",
            },
            {
                "coordinate": "chat-demo:WX-1772505000000",
                "relevance_score": 0.55,
                "source": "recent",
            },
        ],
        "decoded_context": [],
    }


async def _fake_assemble_with_recent_generic_candidate(**_kwargs):
    return {
        "retrieved": [
            {
                "coordinate": "chat-demo:WX-recent-generic-1",
                "relevance_score": 0.82,
                "snippet": "This is a two-sentence check that the chat surface retains a streamed answer after completion.",
                "source": "recent",
            },
        ],
        "decoded_context": [],
    }


async def _fake_assemble_with_skip_recommended_attachment_candidate(**_kwargs):
    return {
        "retrieved": [
            {
                "coordinate": "chat-demo:ATT-physics-1-T012",
                "relevance_score": 0.88,
                "snippet": "Aharonov-Bohm oscillations and flux quantization in mesoscopic rings.",
                "source": "retrieved",
                "state": {
                    "metadata": {
                        "summary": "Physics attachment about flux quantization",
                        "topics": ["physics", "oscillations"],
                        "tags": ["mesoscopic", "flux"],
                        "recommended": ["skip"],
                        "reasons": ["cross_domain_weak_match"],
                        "eq6_commit_allowed": True,
                        "eq6_lawfulness_level": 2,
                        "eq6_cw": 1,
                    }
                },
            }
        ],
        "decoded_context": [],
    }


async def _fake_assemble_with_rich_attachment_summary(**_kwargs):
    return {
        "retrieved": [
            {
                "coordinate": "chat-demo:ATT-parent-001",
                "relevance_score": 0.96,
                "snippet": "attachment parent",
                "source": "retrieved",
            }
        ],
        "decoded_context": [],
        "summary": {
            "text": "Part 4 - There Was Only Ever One Tree. Genesis, the Ring, and the difference between descent and return. The attachment grounds the answer in a richer historical reading rather than a thin excerpt."
        },
    }


async def _fake_search_any_with_genesis(**_kwargs):
    return {
        "results": [
            {
                "entry_id": "chat-demo:WX-genesis-search-1",
                "score": 6.8,
                "snippet": "Genesis and AI design connect through ordered differentiation, stewardship, alignment, and purpose.",
                "p_adic_overlap": 3,
                "entry": {
                    "key": {"namespace": "chat-demo", "identifier": "WX-genesis-search-1"},
                    "state": {
                        "metadata": {
                            "summary": "Genesis, AI design, stewardship, and alignment",
                            "topics": ["genesis", "ai", "design", "alignment"],
                            "tags": ["stewardship", "purpose", "creation"],
                            "eq6_commit_allowed": True,
                            "eq6_lawfulness_level": 2,
                            "eq6_cw": 1,
                        }
                    },
                },
            }
        ]
    }


async def _fake_search_any_with_irrelevant_attachment_and_relevant_wx(**_kwargs):
    return {
        "results": [
            {
                "entry_id": "chat-demo:ATT-physics-1-T012",
                "score": 7.6,
                "snippet": "Aharonov-Bohm oscillations and flux quantization in mesoscopic rings.",
                "p_adic_overlap": 2,
                "entry": {
                    "key": {"namespace": "chat-demo", "identifier": "ATT-physics-1-T012"},
                    "state": {
                        "metadata": {
                            "summary": "Physics attachment about flux quantization",
                            "topics": ["physics", "oscillations"],
                            "tags": ["mesoscopic", "flux"],
                            "claims": ["Aharonov-Bohm oscillations"],
                            "recommended": ["skip"],
                            "reasons": ["cross_domain_weak_match"],
                            "eq6_commit_allowed": True,
                            "eq6_lawfulness_level": 2,
                            "eq6_cw": 1,
                        }
                    },
                },
            },
            {
                "entry_id": "chat-demo:WX-genesis-bible-1",
                "score": 6.1,
                "snippet": "Genesis as biblical creation narrative linked to stewardship, naming, order, and human purpose.",
                "p_adic_overlap": 2,
                "entry": {
                    "key": {"namespace": "chat-demo", "identifier": "WX-genesis-bible-1"},
                    "state": {
                        "metadata": {
                            "summary": "Biblical Genesis and AI design reflections",
                            "topics": ["genesis", "bible", "creation", "ai design"],
                            "tags": ["stewardship", "naming", "order"],
                            "claims": ["Genesis as biblical creation narrative"],
                            "eq6_commit_allowed": True,
                            "eq6_lawfulness_level": 2,
                            "eq6_cw": 1,
                        }
                    },
                },
            },
        ]
    }


async def _fake_search_any_with_only_skip_recommended_attachment(**_kwargs):
    return {
        "results": [
            {
                "entry_id": "chat-demo:ATT-physics-1-T012",
                "score": 7.6,
                "snippet": "Aharonov-Bohm oscillations and flux quantization in mesoscopic rings.",
                "p_adic_overlap": 2,
                "entry": {
                    "key": {"namespace": "chat-demo", "identifier": "ATT-physics-1-T012"},
                    "state": {
                        "metadata": {
                            "summary": "Physics attachment about flux quantization",
                            "topics": ["physics", "oscillations"],
                            "tags": ["mesoscopic", "flux"],
                            "claims": ["Aharonov-Bohm oscillations"],
                            "recommended": ["skip"],
                            "reasons": ["cross_domain_weak_match"],
                            "eq6_commit_allowed": True,
                            "eq6_lawfulness_level": 2,
                            "eq6_cw": 1,
                        }
                    },
                },
            }
        ]
    }


async def _fake_decode_coordinate(_coord: str, *, entity: str | None = None, session_id: str | None = None):
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
            "prime_multiplicative_value": 2310,
            "body_prime": 11,
            "token_primes": [2, 3, 5, 7, 11],
            "taxonomy_topology_ref": "mmf:test-topology",
            "taxonomy_mode": "indefeasible",
        },
    }


async def _fake_emit_telemetry(**_kwargs):
    return None


async def _fake_stream_response(**_kwargs):
    async def _gen():
        yield "ok"

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 1, "output": 1}})
    return _gen(), fut


async def _fake_stream_response_contradiction(**_kwargs):
    async def _gen():
        yield "I currently cannot access that thread content."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 2, "output": 8}})
    return _gen(), fut


async def _fake_stream_response_empty(**_kwargs):
    async def _gen():
        if False:
            yield ""

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 2, "output": 0}, "text": ""})
    return _gen(), fut


async def _fake_stream_response_fabricated_walk(**_kwargs):
    async def _gen():
        yield "I'll attempt a walk and call introspection_signal with kind=\"walk\" now."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 12}})
    return _gen(), fut


async def _fake_stream_response_thin_attachment_preview(**_kwargs):
    async def _gen():
        yield "Yes. chat-demo:ATT-child-001 is accessible and was resolved in this turn."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 16}})
    return _gen(), fut


async def _fake_stream_response_attachment_denial(**_kwargs):
    async def _gen():
        yield "I cannot open, retrieve, or read attachment payloads from the system."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 16}})
    return _gen(), fut


async def _fake_stream_response_attachment_not_opened_denial(**_kwargs):
    async def _gen():
        yield "The attachment chat-demo:ATT-target-parent-T015 has not been opened, and only skim preview fragments are available."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 18}})
    return _gen(), fut


async def _fake_stream_response_attachment_attempt_placeholder(**_kwargs):
    async def _gen():
        yield "I acknowledge your direct request. Let me attempt to open the specified attachment coordinate."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 16}})
    return _gen(), fut


async def _fake_stream_response_live_attachment_open_placeholder(**_kwargs):
    async def _gen():
        yield "I'll open the attachment coordinate to retrieve the payload content."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 17}})
    return _gen(), fut


async def _fake_stream_response_attachment_check_placeholder(**_kwargs):
    async def _gen():
        yield "I appreciate the question. Let me check available evidence coordinates first."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 16}})
    return _gen(), fut


async def _fake_stream_response_ledger_check_placeholder(**_kwargs):
    async def _gen():
        yield "I'll check the ledger for historically relevant evidence on this topic."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 12}})
    return _gen(), fut


async def _fake_stream_response_governance_signal_placeholder(**_kwargs):
    async def _gen():
        yield "I need to signal the governance context and then answer from available evidence."

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 14}})
    return _gen(), fut


async def _fake_generate_response_grounded(**_kwargs):
    return {
        "text": "Grounded summary from resolved context.",
        "cost": 0.001,
        "tokens": {"input": 4, "output": 6},
        "model": "mock",
        "finish_reason": "stop",
    }


async def _fake_generate_response_attachment_repair(**_kwargs):
    system_prompt = str(_kwargs.get("system_prompt") or "")
    if "PAYLOAD READ ATTESTATION" in system_prompt:
        return {
            "text": (
                '{"payload_delivered_to_model": true, '
                '"delivered_coords_seen": ["chat-demo:ATT-target-parent-T015"], '
                '"model_acknowledged_read": true, '
                '"used_coords": ["chat-demo:ATT-target-parent-T015"], '
                '"insufficient_payload": false, '
                '"notes": "Attachment payload text for the target coord was present in context."}'
            ),
            "cost": 0.001,
            "tokens": {"input": 4, "output": 24},
            "model": "mock",
            "finish_reason": "stop",
        }
    return {
        "text": (
            "The opened attachment describes stored quantities, stress-like constraints, "
            "and transport thresholds as coupled structural descriptors relevant to consciousness modeling."
        ),
        "cost": 0.001,
        "tokens": {"input": 4, "output": 20},
        "model": "mock",
        "finish_reason": "stop",
    }


async def _fake_generate_response_attachment_wrapper_then_synthesis(**_kwargs):
    system_prompt = str(_kwargs.get("system_prompt") or "")
    if "PAYLOAD SYNTHESIS RETRY" in system_prompt:
        return {
            "text": (
                "The attachment frames consciousness through coupled structural variables: stored quantity, "
                "stress-like resistance, and transport thresholds. It treats these not as isolated metrics but as "
                "interdependent constraints that shape when a system remains coherent or crosses into regime change. "
                "That makes the text relevant to consciousness because it models awareness as a dynamic balance of "
                "containment, pressure, and transition rather than a single scalar state. The passage suggests that "
                "what matters is how a region holds, resists, and transmits organization over time. In that sense, "
                "consciousness is approached as patterned stability under changing conditions."
            ),
            "cost": 0.001,
            "tokens": {"input": 5, "output": 64},
            "model": "mock",
            "finish_reason": "stop",
        }
    if "PAYLOAD READ ATTESTATION" in system_prompt:
        return {
            "text": (
                '{"payload_delivered_to_model": true, '
                '"delivered_coords_seen": ["chat-demo:ATT-target-parent-T015"], '
                '"model_acknowledged_read": true, '
                '"used_coords": ["chat-demo:ATT-target-parent-T015"], '
                '"insufficient_payload": false, '
                '"notes": "Payload text for the target coord was present in context."}'
            ),
            "cost": 0.001,
            "tokens": {"input": 4, "output": 24},
            "model": "mock",
            "finish_reason": "stop",
        }
    return {
        "text": (
            "Yes. `chat-demo:ATT-target-parent-T015` is accessible and was resolved in this turn.\n\n"
            "Observed excerpt: consciousness and structural coupling in the attachment\n\n"
            "If you want, I can extract key claims or walk one level deeper from this COORD."
        ),
        "cost": 0.001,
        "tokens": {"input": 4, "output": 28},
        "model": "mock",
        "finish_reason": "stop",
    }


async def _fake_generate_response_attachment_unread_attestation(**_kwargs):
    system_prompt = str(_kwargs.get("system_prompt") or "")
    if "PAYLOAD READ ATTESTATION" in system_prompt:
        return {
            "text": (
                '{"payload_delivered_to_model": false, '
                '"delivered_coords_seen": [], '
                '"model_acknowledged_read": false, '
                '"used_coords": [], '
                '"insufficient_payload": true, '
                '"notes": "The attachment was not delivered to the model; only skim preview fragments were visible."}'
            ),
            "cost": 0.001,
            "tokens": {"input": 4, "output": 24},
            "model": "mock",
            "finish_reason": "stop",
        }
    return {
        "text": "I acknowledge your direct request. Let me attempt to open the specified attachment coordinate.",
        "cost": 0.001,
        "tokens": {"input": 4, "output": 16},
        "model": "mock",
        "finish_reason": "stop",
    }


async def _fake_coord_walk(**_kwargs):
    return {"status": "success", "path": [], "steps": []}


async def _fake_coord_walk_with_attachment_guided_path(**_kwargs):
    return {
        "status": "success",
        "path": [],
        "steps": [
            {
                "candidates": [
                    {
                        "coord": "chat-demo:ATT-physics-1-T012",
                        "score": 0.96,
                        "flow_diagnostic": "ok",
                        "eq6_lawfulness_level": 2,
                    },
                    {
                        "coord": "chat-demo:WX-genesis-bible-1",
                        "score": 0.82,
                        "flow_diagnostic": "ok",
                        "eq6_lawfulness_level": 2,
                    },
                ]
            }
        ],
    }


async def _fake_write_walk(_payload: dict, **_kwargs):
    return {"status": "success", "walk_id": "EV-WALK-test", "coordinate": "chat-demo-session:EV-WALK-test"}


def _patch_permissive_runtime_actor(monkeypatch) -> None:
    def fake_resolve_runtime_actor(*, payload, auth_claims=None, provider=None, agent=None):
        actor_resolution = {
            "actor_did": str((payload or {}).get("principal_did") or "did:key:z6MkTestActor"),
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


def _stream_events(payload: dict) -> list[dict]:
    payload = dict(payload)
    payload.setdefault("_stream_passthrough", True)
    with client.stream("POST", "/api/orchestrator", json=payload) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    return [json.loads(line) for line in lines]


def _stream_events_with_cookie(payload: dict, *, cookie_name: str, cookie_value: str) -> list[dict]:
    payload = dict(payload)
    payload.setdefault("_stream_passthrough", True)
    with client.stream(
        "POST",
        "/api/orchestrator",
        json=payload,
        cookies={cookie_name: cookie_value},
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    return [json.loads(line) for line in lines]


def _assert_meta_contract(meta: dict) -> None:
    assert _REQUIRED_META_FIELDS.issubset(set(meta.keys()))


@pytest.mark.parametrize("include_snapshot", [False, True])
def test_meta_patch_contract_and_snapshot_toggle(monkeypatch, include_snapshot: bool):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
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

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)

    events = _stream_events(
        {
            "session_id": f"meta-patch-contract-{int(include_snapshot)}",
            "message": "contract check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "include_post_introspect_snapshot": include_snapshot,
            "k": 1,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events, "Expected at least one meta event"
    meta = meta_events[-1]
    _assert_meta_contract(meta)

    patch_events = [event for event in events if event.get("type") == "meta_patch"]
    assert patch_events, "Expected meta_patch event when post-introspect is pending"
    patch = patch_events[-1]
    assert patch.get("status") in {"applied", "skipped"}
    if patch.get("status") == "skipped":
        assert isinstance(patch.get("reason"), str) and patch.get("reason")
    if include_snapshot and patch.get("status") == "applied":
        assert "introspect_snapshot_post" in patch
    if not include_snapshot:
        assert "introspect_snapshot_post" not in patch


def test_meta_exposes_phase_timing_milestones(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
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

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)

    events = _stream_events(
        {
            "session_id": "phase-timing-contract",
            "message": "contract check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    timing_ms = meta.get("timing_ms") if isinstance(meta.get("timing_ms"), dict) else {}
    for key in ("first_token_emitted_ms", "visible_answer_complete_ms", "assess_complete_ms", "commit_complete_ms"):
        assert isinstance(timing_ms.get(key), int), key
    assert timing_ms["first_token_emitted_ms"] <= timing_ms["visible_answer_complete_ms"] <= timing_ms["total_ms"]
    assert timing_ms["visible_answer_complete_ms"] <= timing_ms["assess_complete_ms"] <= timing_ms["commit_complete_ms"] <= timing_ms["total_ms"]

    patch = [event for event in events if event.get("type") == "meta_patch"][-1]
    assert patch.get("status") == "applied"
    patch_timing = patch.get("timing_ms") if isinstance(patch.get("timing_ms"), dict) else {}
    assert isinstance(patch_timing.get("post_commit_introspect_complete_ms"), int)


def test_meta_metadata_carries_authoritative_live_turn_surface(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "authoritative-live-turn-contract",
            "message": "contract check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "principal_did": "did:key:z6MkExample",
            "session_jti": "sess-123",
            "k": 1,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    authoritative = metadata.get("authoritative_live_turn") if isinstance(metadata.get("authoritative_live_turn"), dict) else {}
    assert authoritative
    assert metadata.get("runtime_actor") == meta.get("runtime_actor")
    assert metadata.get("standing_envelope") == meta.get("standing_envelope")
    assert metadata.get("policy_controls") == meta.get("policy_controls")
    assert authoritative.get("runtime_actor") == meta.get("runtime_actor")
    assert authoritative.get("standing_envelope") == meta.get("standing_envelope")
    assert authoritative.get("policy_controls") == meta.get("policy_controls")


def test_meta_patch_absent_when_post_commit_metadata_available(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
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

    called = {"introspect": 0}

    async def fake_introspect_runtime(**_kwargs):
        called["introspect"] += 1
        return {}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)

    events = _stream_events(
        {
            "session_id": "meta-patch-not-needed",
            "message": "contract check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events, "Expected at least one meta event"
    meta = meta_events[-1]
    _assert_meta_contract(meta)
    assert meta.get("eq9_eval_source") == "post_commit_metadata"

    patch_events = [event for event in events if event.get("type") == "meta_patch"]
    assert not patch_events
    assert called["introspect"] >= 0


def test_candidate_trace_and_autonomy_decision_contract(monkeypatch):
    captured_kwargs: dict[str, object] = {}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_stream_response_capture(**kwargs):
        captured_kwargs.update(kwargs)
        return await _fake_stream_response(**kwargs)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "test_open"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", fake_stream_response_capture)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "candidate-trace-contract",
            "message": "resolve latest coord",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    context_meta_events = [event for event in events if event.get("type") == "context_meta"]
    assert context_meta_events
    context_meta = context_meta_events[-1].get("payload")
    if not isinstance(context_meta, dict):
        context_meta = context_meta_events[-1]
    assert isinstance(context_meta.get("candidate_trace"), list)
    assert isinstance(context_meta.get("autonomy_decision"), dict)

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    assert isinstance(top_k, list) and top_k
    assert top_k[0].get("coord") == "chat-demo:WX-1772505927152"

    decision_events = [event for event in events if event.get("type") == "autonomy_decision"]
    assert decision_events
    decision = decision_events[-1].get("payload") or {}
    assert decision.get("action") in {"resolve", "reuse_path", "answer_from_priors"}
    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    assert action_plan_events
    assert (action_plan_events[-1].get("payload") or {}).get("action") in {"open", "stop", "use_priors"}
    coord_opened_events = [event for event in events if event.get("type") == "coord_opened"]
    assert coord_opened_events
    assert (coord_opened_events[0].get("payload") or {}).get("coord") == "chat-demo:WX-1772505927152"
    admitted_events = [event for event in events if event.get("type") == "coord_context_admitted"]
    assert admitted_events
    assert (admitted_events[0].get("payload") or {}).get("coord") == "chat-demo:WX-1772505927152"
    ui_status_events = [event for event in events if event.get("type") == "ui_status"]
    assert ui_status_events
    action_status = next(
        (event for event in ui_status_events if ((event.get("payload") or {}).get("stage") == "coord_action_plan")),
        None,
    )
    assert action_status is not None
    action_status_payload = action_status.get("payload") or {}
    assert action_status_payload.get("channel") == "loading_overlay"
    assert action_status_payload.get("action") in {"open", "stop", "use_priors"}
    catalog_status = next(
        (event for event in ui_status_events if ((event.get("payload") or {}).get("stage") == "coord_catalog")),
        None,
    )
    assert catalog_status is not None
    catalog_status_payload = catalog_status.get("payload") or {}
    coord_meta = catalog_status_payload.get("coord_meta") or {}
    assert coord_meta.get("prime_multiplicative_value") == 2310

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    meta = meta_events[-1]
    assert isinstance(meta.get("candidate_trace"), list)
    assert isinstance(meta.get("coord_catalog"), list)
    assert isinstance(meta.get("coord_action_trace"), list)
    assert isinstance(meta.get("opened_action_trace"), list)
    assert isinstance(meta.get("admitted_context_trace"), list)
    assert isinstance(meta.get("coord_chain_trace"), list)
    assert isinstance(meta.get("autonomy_decision"), dict)
    assert isinstance(meta.get("consistency_check"), dict)
    assert isinstance(meta.get("epistemic_status"), dict)
    meta_walk = meta.get("coord_walk") if isinstance(meta.get("coord_walk"), dict) else {}
    if meta_walk:
        assert isinstance(meta_walk.get("coord_chain_trace"), list)
        assert isinstance(meta_walk.get("opened_action_trace"), list)
        assert isinstance(meta_walk.get("admitted_context_trace"), list)
    candidates = meta_walk.get("candidates") if isinstance(meta_walk.get("candidates"), list) else []
    if candidates:
        assert "topology_signal" in candidates[0]
    llm_context = captured_kwargs.get("context")
    assert isinstance(llm_context, list)
    llm_signals = captured_kwargs.get("signals")
    assert isinstance(llm_signals, list)
    assert any(isinstance(item, dict) and item.get("kind") == "coord_catalog" for item in llm_signals)
    assert (
        any(isinstance(item, dict) and "COORD_CATALOG_JSON:" in str(item.get("text") or "") for item in llm_context)
        or any(isinstance(item, dict) and item.get("kind") == "coord_catalog" for item in llm_signals)
    )

    epistemic_events = [event for event in events if event.get("type") == "epistemic_status"]
    assert epistemic_events
    ep = epistemic_events[-1].get("payload") or {}
    assert ep.get("status") in {"observed", "derived", "unknown"}


def test_empty_assemble_falls_back_to_session_continuity_candidate(monkeypatch):
    session_obj = {
        "turn_count": 1,
        "last_agent": "mock",
        "last_coordinate": "chat-demo:WX-1772505927152",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "continuity_open"

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "continuity-candidate-contract",
            "message": "what do you know from our conversation history",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    assert len(top_k) == 1
    assert top_k[0].get("coord") == "chat-demo:WX-1772505927152"
    assert top_k[0].get("source") == "recent"

    decision_events = [event for event in events if event.get("type") == "autonomy_decision"]
    assert decision_events
    decision = decision_events[-1].get("payload") or {}
    assert decision.get("action") == "reuse_path"


def test_empty_assemble_uses_introspect_continuity_candidates_for_history_query(monkeypatch):
    session_obj = {
        "turn_count": 1,
        "last_agent": "mock",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {
            "latest_turn_coordinate": "chat-demo:WX-1772505927152",
            "latest_attachment_coordinate": "chat-demo:ATT-1772505927000",
        }

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "continuity-introspect-contract",
            "message": "what do you know from our conversation history and attachments",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    coords = [item.get("coord") for item in top_k if isinstance(item, dict)]
    assert "chat-demo:WX-1772505927152" in coords
    assert "chat-demo:ATT-1772505927000" in coords
    sources = {
        str(item.get("coord")): str(item.get("continuity_source"))
        for item in top_k
        if isinstance(item, dict)
    }
    assert sources.get("chat-demo:WX-1772505927152") == "introspect_latest_turn"
    assert sources.get("chat-demo:ATT-1772505927000") == "introspect_latest_attachment"
    decision_events = [event for event in events if event.get("type") == "autonomy_decision"]
    assert decision_events
    decision = decision_events[-1].get("payload") or {}
    assert decision.get("chosen_coord") == "chat-demo:WX-1772505927152"

    context_meta_events = [event for event in events if event.get("type") == "context_meta"]
    assert context_meta_events
    context_meta = context_meta_events[-1].get("payload")
    if not isinstance(context_meta, dict):
        context_meta = context_meta_events[-1]
    queued_coords = context_meta.get("queued_coords") if isinstance(context_meta.get("queued_coords"), list) else []
    assert "chat-demo:WX-1772505927152" in queued_coords
    ui_status_events = [event for event in events if event.get("type") == "ui_status"]
    queue_status = next(
        (
            event
            for event in ui_status_events
            if ((event.get("payload") or {}).get("stage") == "coord_queue")
        ),
        None,
    )
    candidate_status = next(
        (
            event
            for event in ui_status_events
            if ((event.get("payload") or {}).get("stage") == "coord_candidate")
        ),
        None,
    )
    assert queue_status is not None
    assert candidate_status is not None
    assert "chat-demo:WX-1772505927152" in str((queue_status.get("payload") or {}).get("message") or "")
    assert "chat-demo:WX-1772505927152" in str((candidate_status.get("payload") or {}).get("message") or "")

    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    assert action_plan_events


def test_empty_assemble_uses_introspect_continuity_candidates_for_coord_decision_query(monkeypatch):
    session_obj = {
        "turn_count": 1,
        "last_agent": "mock",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {
            "latest_turn_coordinate": "chat-demo:WX-1772505927152",
            "latest_attachment_coordinate": "chat-demo:ATT-1772505927000",
        }

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "continuity-coord-decision-contract",
            "message": "What COORD candidates arise and can they be decoded? Select which to decode.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    coords = [item.get("coord") for item in top_k if isinstance(item, dict)]
    assert "chat-demo:WX-1772505927152" in coords
    assert "chat-demo:ATT-1772505927000" in coords


def test_empty_client_history_reuses_server_session_messages_between_turns(monkeypatch):
    session_obj = {
        "turn_count": 0,
        "last_agent": "mock",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
        "messages": [],
    }
    observed_histories: list[list[dict] | None] = []

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": f"chat-demo:WX-commit-{len(observed_histories)}",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_stream_response(**kwargs):
        history = kwargs.get("history")
        observed_histories.append(list(history) if isinstance(history, list) else None)

        async def _gen():
            yield "ok"

        fut: asyncio.Future = asyncio.Future()
        fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 1, "output": 1}})
        return _gen(), fut

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    base_payload = {
        "session_id": "server-session-history-contract",
        "history": [],
        "provider": "openai",
        "agent": "mock",
        "enable_ledger": True,
        "k": 2,
    }
    _stream_events({**base_payload, "message": "Please remember marker EPIC12-CARRY-FORWARD-TEST."})
    _stream_events({**base_payload, "message": "What marker did I ask you to remember last turn?"})

    assert observed_histories[0] is None
    assert observed_histories[1] == [
        {"role": "user", "content": "Please remember marker EPIC12-CARRY-FORWARD-TEST."},
        {"role": "assistant", "content": "ok"},
    ]
    assert session_obj["messages"][-2:] == [
        {"role": "user", "content": "What marker did I ask you to remember last turn?"},
        {"role": "assistant", "content": "ok"},
    ]


def test_single_continuity_candidate_query_forces_open_instead_of_stop(monkeypatch):
    session_obj = {
        "turn_count": 1,
        "last_agent": "mock",
        "last_coordinate": "chat-demo:WX-1772505927152",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_generate_response(**_kwargs):
        return {
            "text": json.dumps(
                {
                    "action": "stop",
                    "coord": None,
                    "reason": "No suitable COORD candidates to decode.",
                }
            )
        }

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", fake_generate_response)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "single-continuity-force-open",
            "message": "What COORD candidates arise and can they be decoded? Select which to decode.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    assert action_plan_events
    payloads = [event.get("payload") or {} for event in action_plan_events]
    assert any(
        payload.get("action") == "open" and payload.get("coord") == "chat-demo:WX-1772505927152"
        for payload in payloads
    )
    opened_events = [event for event in events if event.get("type") == "coord_opened"]
    assert opened_events
    assert any(
        (event.get("payload") or {}).get("coord") == "chat-demo:WX-1772505927152"
        for event in opened_events
    )


def test_predecode_honors_reuse_path_autonomy_choice(monkeypatch):
    session_obj = {
        "turn_count": 1,
        "last_agent": "mock",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {
            "latest_turn_coordinate": "chat-demo:WX-1772505927152",
            "latest_attachment_coordinate": "chat-demo:ATT-1772505927000",
        }

    async def fake_select_choice_coord(**kwargs):
        catalog = kwargs.get("catalog") or []
        coords = [item.get("coord") for item in catalog if isinstance(item, dict)]
        if "chat-demo:ATT-1772505927000-T001" in coords:
            return "open", "chat-demo:ATT-1772505927000-T001", "catalog_rank"
        return "open", "chat-demo:ATT-1772505927000-T001", "catalog_rank"

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-1772505927000":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 2},
            }
        if coord == "chat-demo:ATT-1772505927000-T001":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "attachment part"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "predecode-reuse-path-alignment",
            "message": "What COORD candidates arise and can they be decoded? Select which to decode.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    decision_events = [event for event in events if event.get("type") == "autonomy_decision"]
    assert decision_events
    decision = decision_events[-1].get("payload") or {}
    assert decision.get("action") == "reuse_path"
    assert decision.get("chosen_coord") == "chat-demo:WX-1772505927152"

    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    assert action_plan_events
    payloads = [event.get("payload") or {} for event in action_plan_events]
    assert any(
        payload.get("phase") == "predecode"
        and payload.get("action") == "open"
        and payload.get("coord") == "chat-demo:WX-1772505927152"
        and payload.get("reason") == "autonomy_reuse_path_override"
        for payload in payloads
    )

    opened_events = [event for event in events if event.get("type") == "coord_opened"]
    assert any(
        (event.get("payload") or {}).get("coord") == "chat-demo:WX-1772505927152"
        for event in opened_events
    )

def test_predecode_single_candidate_cannot_stop_on_coord_path(monkeypatch):
    session_obj = {
        "turn_count": 1,
        "last_agent": "mock",
        "last_coordinate": "chat-demo:WX-1772505927152",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_select_choice_coord(**kwargs):
        catalog = kwargs.get("catalog") or []
        hop_index = kwargs.get("hop_index")
        if hop_index == 0 and len(catalog) == 1:
            return "stop", None, "No relevant repository found to open."
        return "open", "chat-demo:WX-1772505927152", "single_candidate"

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "predecode-single-candidate-no-stop",
            "message": "What does this repo discuss about the benefits of a dual substrate system of AI?",
            "history": [],
            "context_coords": ["chat-demo:WX-1772505927152"],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    assert action_plan_events
    payloads = [event.get("payload") or {} for event in action_plan_events]
    assert any(
        payload.get("phase") == "predecode"
        and payload.get("action") == "open"
        and payload.get("coord") == "chat-demo:WX-1772505927152"
        and payload.get("reason") == "single_candidate_coord_override"
        for payload in payloads
    )


def test_attachment_context_contract_surfaces_part_walk_and_skip_state(monkeypatch):
    session_obj = {
        "turn_count": 1,
        "last_agent": "mock",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-1772505927000":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {"coord": "chat-demo:ATT-1772505927000-T001"},
                        {"coord": "chat-demo:ATT-1772505927000-T002"},
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 2},
            }
        if coord in {"chat-demo:ATT-1772505927000-T001", "chat-demo:ATT-1772505927000-T002"}:
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "attachment part"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-context-contract",
            "message": "Use this attachment to answer.",
            "history": [],
            "context_coords": ["chat-demo:ATT-1772505927000"],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    context_meta_events = [event for event in events if event.get("type") == "context_meta"]
    assert context_meta_events
    context_meta = context_meta_events[-1].get("payload")
    if not isinstance(context_meta, dict):
        context_meta = context_meta_events[-1]
    attachment_context = context_meta.get("attachment_context") if isinstance(context_meta.get("attachment_context"), dict) else {}
    assert attachment_context.get("attachment_focus") is True
    assert attachment_context.get("part_walk_required") is True
    assert int(attachment_context.get("attachment_parts_added") or 0) == 0
    assert attachment_context.get("skipped") is True
    assert attachment_context.get("skip_reason") == "attachment_parts_unavailable"
    queued = attachment_context.get("queued_coords") if isinstance(attachment_context.get("queued_coords"), list) else []
    assert "chat-demo:ATT-1772505927000" in queued
    assert all(not coord.endswith("-T001") for coord in queued)

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    meta_payload = meta_events[-1].get("payload")
    if not isinstance(meta_payload, dict):
        meta_payload = meta_events[-1]
    final_attachment_context = meta_payload.get("attachment_context") if isinstance(meta_payload.get("attachment_context"), dict) else {}
    assert final_attachment_context.get("attachment_focus") is True
    assert final_attachment_context.get("part_walk_required") is True
    assert final_attachment_context.get("skipped") is True
    assert final_attachment_context.get("skip_reason") == "attachment_parts_unavailable"


def test_explicit_attachment_target_beats_recent_attachment_family(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-recent-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "recent attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {"parts": [{"coord": "chat-demo:ATT-recent-parent-T001", "topics": ["other"]}]},
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {"parts": [{"coord": "chat-demo:ATT-target-parent-T015", "topics": ["consciousness"]}]},
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord in {"chat-demo:ATT-target-parent-T015", "chat-demo:ATT-recent-parent-T001"}:
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "attachment part"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        if call_state["select_calls"] == 2:
            return "open", "chat-demo:ATT-target-parent-T015", "open_target_child"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-explicit-target-contract",
            "message": "Use the specified attachment content.",
            "history": [],
            "context_coords": ["chat-demo:ATT-recent-parent"],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    meta_payload = meta_events[-1].get("payload")
    if not isinstance(meta_payload, dict):
        meta_payload = meta_events[-1]
    resolved_coords = meta_payload.get("resolved_coords") if isinstance(meta_payload.get("resolved_coords"), list) else []
    inputs = meta_payload.get("inputs") if isinstance(meta_payload.get("inputs"), dict) else {}
    parts_used = inputs.get("parts_used") if isinstance(inputs.get("parts_used"), list) else []

    assert "chat-demo:ATT-target-parent" in resolved_coords
    assert "chat-demo:ATT-target-parent-T015" in resolved_coords or "chat-demo:ATT-target-parent-T015" in parts_used
    assert "chat-demo:ATT-recent-parent" not in resolved_coords

    attachment_context = meta_payload.get("attachment_context") if isinstance(meta_payload.get("attachment_context"), dict) else {}
    queued = attachment_context.get("queued_coords") if isinstance(attachment_context.get("queued_coords"), list) else []
    assert queued
    assert queued[0] == "chat-demo:ATT-target-parent"


def test_explicit_attachment_part_survives_parent_only_queue(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 0},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "target attachment explicit part"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-explicit-part-queue-survives",
            "message": "Use the specified attachment part content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    meta_payload = meta_events[-1].get("payload")
    if not isinstance(meta_payload, dict):
        meta_payload = meta_events[-1]
    resolved_coords = meta_payload.get("resolved_coords") if isinstance(meta_payload.get("resolved_coords"), list) else []
    assert "chat-demo:ATT-target-parent" in resolved_coords
    assert "chat-demo:ATT-target-parent-T015" in resolved_coords


def test_single_candidate_override_rewrites_stop_for_recursive_catalogs():
    catalog = [
        {
            "coord": "chat-demo:WX-1772505000000",
            "type": "WX",
            "skim": "",
            "refs": {},
            "walk": None,
            "governance": {},
            "claims": [],
            "topics": [],
            "tags": [],
        }
    ]

    action, coord, reason = orchestrator_module._fail_open_single_coord_candidate(
        catalog=catalog,
        action="stop",
        coord=None,
        reason="No relevant repo found to discuss benefits of a dual substrate system of AI.",
    )

    assert action == "open"
    assert coord == "chat-demo:WX-1772505000000"
    assert reason == "single_candidate_coord_override"


def test_resolve_autonomy_rewrites_predecode_stop_for_chosen_candidate():
    action, coord, reason = orchestrator_module._align_predecode_with_autonomy(
        autonomy_decision={
            "action": "resolve",
            "chosen_coord": "chat-demo:WX-1772505000000",
        },
        query="Return runtime metadata for the chosen coord path.",
        candidate_coords=["chat-demo:WX-1772505000000", "chat-demo:WX-1772504000000"],
        plan_action="stop",
        plan_coord=None,
        plan_reason="No relevant coordinates to open based on current metadata.",
    )

    assert action == "open"
    assert coord == "chat-demo:WX-1772505000000"
    assert reason == "autonomy_resolve_override"


def test_resolve_autonomy_rewrites_wrong_open_choice_to_top_candidate():
    action, coord, reason = orchestrator_module._align_predecode_with_autonomy(
        autonomy_decision={
            "action": "resolve",
            "chosen_coord": "chat-demo:WX-1772505000000",
        },
        query="Use current runtime metadata.",
        candidate_coords=["chat-demo:WX-1772505000000", "chat-demo:WX-1772504000000"],
        plan_action="open",
        plan_coord="chat-demo:WX-1772504000000",
        plan_reason="catalog_rank",
    )

    assert action == "open"
    assert coord == "chat-demo:WX-1772505000000"
    assert reason == "autonomy_resolve_override"


def test_normalize_open_without_coord_uses_first_catalog_coord():
    action, coord, reason = orchestrator_module._normalize_open_without_coord(
        catalog=[
            {"coord": "chat-demo:ATT-1772505000000"},
            {"coord": "chat-demo:ATT-1772504000000"},
        ],
        action="open",
        coord=None,
        reason="no_relevant_coord",
    )

    assert action == "open"
    assert coord == "chat-demo:ATT-1772505000000"
    assert reason == "catalog_first_coord_fallback"


def test_normalize_open_without_coord_stops_when_catalog_empty():
    action, coord, reason = orchestrator_module._normalize_open_without_coord(
        catalog=[],
        action="open",
        coord=None,
        reason="no_relevant_coord",
    )

    assert action == "stop"
    assert coord is None
    assert reason == "no_relevant_coord"


def test_consistency_retry_applies_on_contradiction(monkeypatch):
    committed = {"reply": None}

    async def fake_commit_answer(**kwargs):
        committed["reply"] = kwargs.get("reply")
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "test_open"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_contradiction)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "consistency-retry-contract",
            "message": "resolve and quote the coord",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    consistency_events = [event for event in events if event.get("type") == "consistency_check"]
    assert consistency_events
    payload = consistency_events[-1].get("payload") or {}
    assert payload.get("retried") is True
    assert payload.get("retry_count") == 1

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    meta = meta_events[-1]
    assert isinstance(meta.get("consistency_check"), dict)
    assert committed["reply"] == "Grounded summary from resolved context."


def test_empty_stream_reply_falls_back_to_grounded_context(monkeypatch):
    committed = {"reply": None}

    async def fake_commit_answer(**kwargs):
        committed["reply"] = kwargs.get("reply")
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "test_open"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_empty)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "empty-stream-grounded-fallback",
            "message": "What COORD candidates arise and can they be decoded? Select which to decode.",
            "history": [],
            "context_coords": ["chat-demo:WX-1772505927152"],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    consistency_events = [event for event in events if event.get("type") == "consistency_check"]
    assert consistency_events
    payload = consistency_events[-1].get("payload") or {}
    assert payload.get("retried") is True
    assert payload.get("retry_status") == "fallback_empty_response"
    assert committed["reply"]
    assert "resolved in this turn" in committed["reply"]

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    metadata = meta_events[-1].get("metadata") if isinstance(meta_events[-1].get("metadata"), dict) else {}
    assert metadata.get("consistency_check", {}).get("retry_status") == "fallback_empty_response"


def test_session_auth_envelope_reused_when_request_auth_missing(monkeypatch):
    captured: dict[str, object] = {}
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "auth_envelope": {
            "headers": {
                "authorization": "Bearer sticky-token",
                "x-principal-did": "did:key:z6MkSticky",
                "x-session-jti": "sess-sticky",
                "x-context-id": "ctx-sticky",
            },
            "claims": {
                "principal_did": "did:key:z6MkSticky",
                "session_jti": "sess-sticky",
                "context_id": "ctx-sticky",
            },
        },
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_assemble(**kwargs):
        captured["assemble_auth_headers"] = kwargs.get("auth_headers")
        captured["assemble_auth_claims"] = kwargs.get("auth_claims")
        return {"retrieved": []}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-auth-sticky",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**kwargs):
        captured["introspect_auth_headers"] = kwargs.get("auth_headers")
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

    def fake_resolve_runtime_actor(*, payload, auth_claims=None, provider=None, agent=None):
        captured["resolved_auth_claims"] = auth_claims
        actor_resolution = {
            "actor_did": str((auth_claims or {}).get("principal_did") or "did:key:missing"),
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "binding_candidates": [],
            "principal_status": "active",
            "tenant_id": "tenant:test",
            "session_jti": str((auth_claims or {}).get("session_jti") or "") or None,
            "auth_method": "claims",
            "verification_state": "verified",
            "resolution_reason": "session_auth_fixture",
        }
        standing_envelope = {
            "standing_envelope_version": "se-v1",
            "standing_envelope_ref": "env:test-session-auth",
            "actor_did": actor_resolution["actor_did"],
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "verification_state": actor_resolution["verification_state"],
            "trust_class": "T3",
            "posture_class": "P3",
            "active_sanctions": [],
            "probation_status": None,
            "tool_scope": "full",
            "retrieval_scope": "tenant",
            "max_output_tokens": 4096,
            "write_commit_allowed": True,
            "credential_ref": None,
            "reason_code": "session_auth_fixture",
            "resolved_at": "2026-03-17T00:00:00Z",
        }
        return actor_resolution, standing_envelope

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "_resolve_runtime_actor", fake_resolve_runtime_actor)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)

    events = _stream_events(
        {
            "session_id": "sticky-auth-contract",
            "message": "check sticky auth",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    runtime_actor = meta_events[-1].get("runtime_actor") if isinstance(meta_events[-1].get("runtime_actor"), dict) else {}
    assert runtime_actor.get("actor_did") == "did:key:z6MkSticky"
    assert captured.get("resolved_auth_claims") == {
        "principal_did": "did:key:z6MkSticky",
        "session_jti": "sess-sticky",
        "context_id": "ctx-sticky",
    }
    assemble_headers = captured.get("assemble_auth_headers") if isinstance(captured.get("assemble_auth_headers"), dict) else {}
    assert assemble_headers.get("authorization") == "Bearer sticky-token"
    assert assemble_headers.get("x-principal-did") == "did:key:z6MkSticky"
    introspect_headers = captured.get("introspect_auth_headers") if isinstance(captured.get("introspect_auth_headers"), dict) else {}
    assert introspect_headers.get("authorization") == "Bearer sticky-token"
    assert session_obj.get("auth_envelope", {}).get("claims", {}).get("principal_did") == "did:key:z6MkSticky"


def test_session_auth_headers_synthesize_claims_when_session_claims_missing(monkeypatch):
    captured: dict[str, object] = {}
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "auth_envelope": {
            "headers": {
                "authorization": "Bearer sticky-token",
                "x-principal-did": "did:key:z6MkHeaderOnly",
                "x-session-jti": "sess-header-only",
                "x-context-id": "ctx-header-only",
                "x-auth-method": "header-forwarded",
            },
            "claims": {},
        },
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-auth-header-only",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    def fake_resolve_runtime_actor(*, payload, auth_claims=None, provider=None, agent=None):
        captured["resolved_auth_claims"] = auth_claims
        actor_resolution = {
            "actor_did": str((auth_claims or {}).get("principal_did") or "did:key:missing"),
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "binding_candidates": [],
            "principal_status": "active",
            "tenant_id": "tenant:test",
            "session_jti": str((auth_claims or {}).get("session_jti") or "") or None,
            "auth_method": str((auth_claims or {}).get("auth_method") or "") or None,
            "verification_state": "verified",
            "resolution_reason": "header_only_session_fixture",
        }
        standing_envelope = {
            "standing_envelope_version": "se-v1",
            "standing_envelope_ref": "env:test-session-header-only",
            "actor_did": actor_resolution["actor_did"],
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "verification_state": actor_resolution["verification_state"],
            "trust_class": "T3",
            "posture_class": "P3",
            "active_sanctions": [],
            "probation_status": None,
            "tool_scope": "full",
            "retrieval_scope": "tenant",
            "max_output_tokens": 4096,
            "write_commit_allowed": True,
            "credential_ref": None,
            "reason_code": "header_only_session_fixture",
            "resolved_at": "2026-03-17T00:00:00Z",
        }
        return actor_resolution, standing_envelope

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "_resolve_runtime_actor", fake_resolve_runtime_actor)

    events = _stream_events(
        {
            "session_id": "sticky-auth-header-only-contract",
            "message": "check sticky auth from headers",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    runtime_actor = meta_events[-1].get("runtime_actor") if isinstance(meta_events[-1].get("runtime_actor"), dict) else {}
    assert runtime_actor.get("actor_did") == "did:key:z6MkHeaderOnly"
    assert captured.get("resolved_auth_claims") == {
        "principal_did": "did:key:z6MkHeaderOnly",
        "session_jti": "sess-header-only",
        "context_id": "ctx-header-only",
        "auth_method": "header-forwarded",
    }
    assert session_obj.get("auth_envelope", {}).get("claims", {}).get("principal_did") == "did:key:z6MkHeaderOnly"


def test_session_auth_envelope_does_not_mix_stale_delegated_claims_into_direct_operator() -> None:
    operator_did = "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"
    session = {
        "auth_envelope": {
            "headers": {
                "authorization": "Bearer delegated-session-token",
                "x-principal-did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                "x-principal-key-id": "openai:agent:codex",
                "x-session-jti": "sess-codex",
                "x-context-id": "ctx-delegated",
                "x-auth-method": "delegated_cli_request",
            },
            "claims": {
                "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                "principal_key_id": "openai:agent:codex",
                "session_jti": "sess-codex",
                "context_id": "ctx-delegated",
                "auth_method": "delegated_cli_request",
            },
        }
    }
    current_envelope = {
        "headers": {
            "x-principal-did": operator_did,
            "x-context-id": "ctx-direct",
        },
        "claims": {
            "principal_did": operator_did,
            "context_id": "ctx-direct",
        },
        "token_present": False,
        "token_type": "none",
    }

    merged, headers, claims = orchestrator_module._merge_session_auth_envelope(
        auth_envelope=current_envelope,
        session=session,
    )

    assert claims == {
        "principal_did": operator_did,
        "context_id": "ctx-direct",
    }
    assert headers.get("x-principal-did") == operator_did
    assert "x-principal-key-id" not in headers
    assert "x-auth-method" not in headers
    assert "x-session-jti" not in headers
    assert merged.get("token_present") is False
    assert session.get("auth_envelope", {}).get("claims") == claims


def test_delegated_codex_prompt_path_overrides_principal_for_current_turn_only(monkeypatch):
    captured: dict[str, object] = {}
    codex_did = "did:web:id.dualsubstrate.com:principals:agent:openai:codex"
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "auth_envelope": {
            "headers": {
                "authorization": "Bearer sticky-token",
                "x-principal-did": "did:key:z6MkOperator",
                "x-session-jti": "sess-operator",
                "x-context-id": "ctx-operator",
            },
            "claims": {
                "principal_did": "did:key:z6MkOperator",
                "session_jti": "sess-operator",
                "context_id": "ctx-operator",
            },
        },
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_assemble(**kwargs):
        captured["assemble_auth_headers"] = kwargs.get("auth_headers")
        captured["assemble_auth_claims"] = kwargs.get("auth_claims")
        return {"retrieved": []}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-codex-delegated",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    def fake_resolve_runtime_actor(*, payload, auth_claims=None, provider=None, agent=None):
        captured["resolved_auth_claims"] = auth_claims
        actor_resolution = {
            "actor_did": str((auth_claims or {}).get("principal_did") or "did:key:missing"),
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "binding_candidates": [],
            "principal_status": "active",
            "tenant_id": "tenant:test",
            "session_jti": str((auth_claims or {}).get("session_jti") or "") or None,
            "auth_method": str((auth_claims or {}).get("auth_method") or "") or None,
            "verification_state": "verified",
            "resolution_reason": "delegated_codex_fixture",
        }
        standing_envelope = {
            "standing_envelope_version": "se-v1",
            "standing_envelope_ref": "env:test-delegated-codex",
            "actor_did": actor_resolution["actor_did"],
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "verification_state": actor_resolution["verification_state"],
            "trust_class": "T3",
            "posture_class": "P3",
            "active_sanctions": [],
            "probation_status": None,
            "tool_scope": "full",
            "retrieval_scope": "tenant",
            "max_output_tokens": 4096,
            "write_commit_allowed": True,
            "credential_ref": None,
            "reason_code": "delegated_codex_fixture",
            "resolved_at": "2026-05-04T00:00:00Z",
        }
        return actor_resolution, standing_envelope

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "_resolve_runtime_actor", fake_resolve_runtime_actor)

    events = _stream_events(
        {
            "session_id": "delegated-codex-contract",
            "message": "run delegated codex prompt",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
            "delegated_principal": {
                "principal_did": codex_did,
                "principal_key_id": "openai:agent:codex",
                "principal_id": "openai:codex",
                "principal_type": "agent",
                "explicit_cli_request": True,
                "delegation_mode": "delegated_only",
                "delegated_by_principal_did": "did:key:z6MkOperator",
                "delegated_by_principal_id": "operator:david",
                "ledger_scope": ["chat-demo"],
                "surface_scope": ["surface:chat:primary"],
                "surface_id": "surface:chat:primary",
                "expires_at": "2026-05-05T00:00:00Z",
            },
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    runtime_actor = meta_events[-1].get("runtime_actor") if isinstance(meta_events[-1].get("runtime_actor"), dict) else {}
    assert runtime_actor.get("actor_did") == codex_did
    assert captured.get("resolved_auth_claims") == {
        "principal_did": codex_did,
        "principal_key_id": "openai:agent:codex",
        "session_jti": "sess-operator",
        "context_id": "ctx-operator",
        "auth_method": "delegated_cli_request",
    }
    assemble_headers = captured.get("assemble_auth_headers") if isinstance(captured.get("assemble_auth_headers"), dict) else {}
    assert assemble_headers.get("x-principal-did") == codex_did
    assert assemble_headers.get("x-principal-type") == "agent"
    assert assemble_headers.get("x-delegated-cli-request") == "true"
    assert assemble_headers.get("x-delegated-by-principal-did") == "did:key:z6MkOperator"
    assert assemble_headers.get("x-delegated-by-principal-id") == "operator:david"
    assert assemble_headers.get("x-delegated-ledger-scope") == "chat-demo"
    assert assemble_headers.get("x-delegated-surface-scope") == "surface:chat:primary"
    assert assemble_headers.get("x-surface-id") == "surface:chat:primary"
    assert session_obj.get("auth_envelope", {}).get("claims", {}).get("principal_did") == "did:key:z6MkOperator"


def test_resolve_runtime_actor_accepts_delegated_codex_claims_without_local_registry(monkeypatch):
    class _Registry:
        def get(self, principal_did: str):
            return None

        def find_by_key_ref(self, principal_key_ref: str, *, tenant_id: str | None = None):
            return None

    monkeypatch.setattr(orchestrator_module, "_principal_registry", lambda: _Registry())

    actor_resolution, standing_envelope = orchestrator_module._resolve_runtime_actor(
        payload={
            "principal_did": "did:web:ds-backend-new.fly.dev:principals:agent:openai:codex",
            "principal_key_id": "openai:agent:codex",
            "auth_method": "delegated_cli_request",
            "tenant_id": "tenant:demo",
        },
        auth_claims={
            "principal_did": "did:web:ds-backend-new.fly.dev:principals:agent:openai:codex",
            "principal_key_id": "openai:agent:codex",
            "auth_method": "delegated_cli_request",
        },
        provider="anthropic/claude-haiku-4.5",
        agent="anthropic/claude-haiku-4.5",
    )

    assert actor_resolution["actor_did"] == "did:web:ds-backend-new.fly.dev:principals:agent:openai:codex"
    assert actor_resolution["principal_status"] == "active"
    assert actor_resolution["resolution_reason"] == "delegated_principal_claims"
    assert standing_envelope["write_commit_allowed"] is True
    assert standing_envelope["tool_scope"] == "standard"
    assert standing_envelope["retrieval_scope"] == "tenant"


def test_continuity_introspect_uses_sticky_auth_headers(monkeypatch):
    captured: dict[str, object] = {}
    session_obj = {
        "turn_count": 1,
        "last_agent": "mock",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
        "auth_envelope": {
            "headers": {
                "authorization": "Bearer sticky-token",
                "x-principal-did": "did:key:z6MkSticky",
                "x-session-jti": "sess-sticky",
                "x-context-id": "ctx-sticky",
            },
            "claims": {
                "principal_did": "did:key:z6MkSticky",
                "session_jti": "sess-sticky",
                "context_id": "ctx-sticky",
            },
        },
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**kwargs):
        captured["introspect_auth_headers"] = kwargs.get("auth_headers")
        return {
            "latest_turn_coordinate": "chat-demo:WX-1772505927152",
            "latest_attachment_coordinate": "chat-demo:ATT-1772505927000",
        }

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "continuity-sticky-auth",
            "message": "What COORD candidates arise and can they be decoded? Select which to decode.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    assert [event for event in events if event.get("type") == "candidate_trace"]
    introspect_headers = captured.get("introspect_auth_headers") if isinstance(captured.get("introspect_auth_headers"), dict) else {}
    assert introspect_headers.get("authorization") == "Bearer sticky-token"
    assert introspect_headers.get("x-principal-did") == "did:key:z6MkSticky"


def test_cookie_session_token_forwarded_to_backend_calls(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_assemble(**kwargs):
        captured["assemble_auth_headers"] = kwargs.get("auth_headers")
        return {"retrieved": [], "decoded_context": []}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-cookie-auth",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**kwargs):
        captured["introspect_auth_headers"] = kwargs.get("auth_headers")
        return {"governance_metrics": {"L": 1.0, "H": 0.0, "U": 1.0, "V": 0.0, "I1": 0.0, "I2": 0.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events_with_cookie(
        {
            "session_id": "cookie-session-token-forwarding",
            "message": "reply with exactly test",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        },
        cookie_name="ds_backend_session_token",
        cookie_value="cookie-session-token",
    )

    assert [event for event in events if event.get("type") == "meta"]
    assemble_headers = captured.get("assemble_auth_headers") if isinstance(captured.get("assemble_auth_headers"), dict) else {}
    introspect_headers = captured.get("introspect_auth_headers") if isinstance(captured.get("introspect_auth_headers"), dict) else {}
    assert assemble_headers.get("authorization") == "Bearer cookie-session-token"
    assert introspect_headers.get("authorization") == "Bearer cookie-session-token"


def test_backend_stream_cookie_session_token_forwarded_to_backend_stream(monkeypatch):
    captured: dict[str, object] = {}

    class DummyUpstreamResponse:
        def __init__(self, status_code: int, lines: list[str]):
            self.status_code = status_code
            self._lines = lines
            self.text = ""

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            lines = [
                '{"type":"status","message":"Initializing Quantum Kernel..."}',
                '{"type":"meta","coordinate":"chat-demo:WX-backend-stream-cookie"}',
            ]
            return DummyUpstreamResponse(200, lines)

    monkeypatch.setattr(orchestrator_module.httpx, "AsyncClient", DummyAsyncClient)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events_with_cookie(
        {
            "session_id": "cookie-backend-stream-forwarding",
            "message": "reply with exactly test",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "backend_stream": True,
            "_stream_passthrough": True,
            "k": 1,
        },
        cookie_name="ds_backend_session_token",
        cookie_value="cookie-session-token",
    )

    assert [event for event in events if event.get("type") == "meta"]
    outbound_headers = captured.get("headers") if isinstance(captured.get("headers"), dict) else {}
    outbound_payload = captured.get("json") if isinstance(captured.get("json"), dict) else {}
    assert str(captured.get("url") or "").endswith("/chat/stream")
    assert outbound_headers.get("authorization") == "Bearer cookie-session-token"
    assert outbound_payload.get("session_id") == "cookie-backend-stream-forwarding"
    assert outbound_payload.get("enable_ledger") is True


def test_commit_answer_forwards_auth_headers_and_claims(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_commit_answer(**kwargs):
        captured.update(kwargs)
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-auth-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    _stream_events_with_cookie(
        {
            "session_id": "commit-auth-forwarding",
            "message": "reply with exactly auth-forwarding",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "principal_did": "did:key:z6MkCommitForward",
            "principal_key_id": "did:key:z6MkCommitForward#k1",
            "session_jti": "sess-commit-forward",
            "k": 1,
        },
        cookie_name="ds_backend_session_token",
        cookie_value="cookie-session-token",
    )

    auth_headers = captured.get("auth_headers") if isinstance(captured.get("auth_headers"), dict) else {}
    auth_claims = captured.get("auth_claims") if isinstance(captured.get("auth_claims"), dict) else {}
    assert auth_headers.get("authorization") == "Bearer cookie-session-token"
    assert auth_claims.get("principal_did") == "did:key:z6MkCommitForward"
    assert auth_claims.get("principal_key_id") == "did:key:z6MkCommitForward#k1"
    assert auth_claims.get("session_jti") == "sess-commit-forward"


def test_meta_contract_uses_top_level_blocked_signal(monkeypatch):
    async def fake_commit_answer(**kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": kwargs.get("appraisal"),
            "blocked": True,
        }

    async def fake_assess_chat(**_kwargs):
        return {
            "appraisal": {
                "score": 0.1,
                "law_score": 0.2,
                "grace_score": 0.2,
                "drift": 0.9,
            }
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "assess_chat", fake_assess_chat)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "S1_GUARDIAN_FAST_DEFAULT", False)

    events = _stream_events(
        {
            "session_id": "meta-blocked-contract",
            "message": "contract check blocked",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "s_mode": "s2",
            "k": 1,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    meta = meta_events[-1]
    assert meta.get("blocked") is True


def test_unprivileged_policy_overrides_are_rejected(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "POLICY_ALLOW_CLIENT_OVERRIDES", False)
    monkeypatch.setattr(orchestrator_module, "S_MODE_DEFAULT", "s2")
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "policy-reject-contract",
            "message": "policy contract check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": False,
            "s_mode": "s1",
            "k": 1,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    meta = meta_events[-1]

    governance_path = meta.get("governance_path") if isinstance(meta.get("governance_path"), dict) else {}
    assert governance_path.get("s_mode") == "s2"

    policy_controls = meta.get("policy_controls") if isinstance(meta.get("policy_controls"), dict) else {}
    assert policy_controls.get("override_authorized") is False
    assert policy_controls.get("requested_enable_ledger") is False
    assert policy_controls.get("effective_enable_ledger") is True
    rejected = policy_controls.get("rejected_overrides") if isinstance(policy_controls.get("rejected_overrides"), list) else []
    assert "enable_ledger_disabled_by_client" in rejected
    assert "s1_mode_requested_by_client" in rejected


def test_privileged_policy_overrides_are_allowed(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "POLICY_ALLOW_CLIENT_OVERRIDES", False)
    _patch_permissive_runtime_actor(monkeypatch)

    with client.stream(
        "POST",
        "/api/orchestrator",
        json={
            "session_id": "policy-allow-contract",
            "message": "policy allow check",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": False,
            "s_mode": "s1",
            "principal_did": "did:key:z6MkExample",
            "session_jti": "sess-allow-1",
            "k": 1,
        },
        headers={"Authorization": "Bearer opaque-session-token"},
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    events = [json.loads(line) for line in lines]
    meta = [event for event in events if event.get("type") == "meta"][-1]

    governance_path = meta.get("governance_path") if isinstance(meta.get("governance_path"), dict) else {}
    assert governance_path.get("s_mode") == "s1"

    policy_controls = meta.get("policy_controls") if isinstance(meta.get("policy_controls"), dict) else {}
    assert policy_controls.get("override_authorized") is True
    assert policy_controls.get("requested_enable_ledger") is False
    assert policy_controls.get("effective_enable_ledger") is False
    rejected = policy_controls.get("rejected_overrides") if isinstance(policy_controls.get("rejected_overrides"), list) else []
    assert rejected == []



def test_thinking_trace_lifecycle_contract(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "thinking-trace-lifecycle",
            "request_id": "req-thinking-trace-lifecycle",
            "message": "trace contract",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    trace_events = [event.get("payload") for event in events if event.get("type") == "thinking_trace"]
    trace_events = [event for event in trace_events if isinstance(event, dict)]
    assert trace_events
    assert trace_events[0].get("type") == "process_started"
    assert trace_events[-1].get("type") in {"process_completed", "process_failed"}

    seq_values = [int(event.get("trace_seq")) for event in trace_events if isinstance(event.get("trace_seq"), int)]
    assert seq_values == sorted(seq_values)
    assert len(seq_values) >= 4
    ctx_start = next((event for event in trace_events if event.get("step_code") == "CTX_ASSEMBLY_START"), {})
    ctx_done = next((event for event in trace_events if event.get("step_code") == "CTX_ASSEMBLY_DONE"), {})
    assert isinstance(ctx_start.get("details"), dict)
    assert isinstance(ctx_done.get("details"), dict)
    assert "queued_coords" in ctx_start["details"]
    assert "resolved_coords" in ctx_done["details"]
    assert "coord_count" in ctx_done["details"]


def test_coord_catalog_path_reports_resolver_cache_stats(monkeypatch):
    session_obj = {
        "turn_count": 1,
        "last_agent": "different-agent",
        "last_coordinate": "chat-demo:WX-1772505927152",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
    }
    decode_calls = {"count": 0}

    async def fake_decode_coordinate(_coord: str, *, entity: str | None = None, session_id: str | None = None):
        decode_calls["count"] += 1
        return {
            "coord": "chat-demo:WX-1772505927152",
            "type": "WX",
            "skim": {"one_line": "candidate"},
            "walk": None,
            "refs": {},
            "payload": {"parts": []},
            "interpretation": {"claims": [{"label": "cached"}]},
            "governance": {},
            "meta": {
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_cw": 1,
            },
        }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "test_open"

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "resolver-cache-contract",
            "message": "resolve latest coord",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    meta = meta_events[-1]
    resolver_cache = meta.get("resolver_cache") if isinstance(meta.get("resolver_cache"), dict) else {}
    assert resolver_cache.get("misses", 0) >= 1
    assert decode_calls["count"] == 1


def test_multi_hop_open_path_reuses_resolver_cache(monkeypatch):
    decode_calls = {"count": 0}
    call_state = {"select_calls": 0}

    async def fake_assemble_with_attachment_parent(**_kwargs):
        return {
            "retrieved": [
                {
                    "coordinate": "chat-demo:ATT-parent-001",
                    "relevance_score": 0.96,
                    "snippet": "attachment parent",
                    "source": "retrieved",
                }
            ],
            "decoded_context": [],
        }

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        decode_calls["count"] += 1
        if coord == "chat-demo:ATT-parent-001":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "parent summary"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-child-001",
                            "topics": ["child"],
                            "tags": ["part"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {"claims": [{"label": "parent"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        if coord == "chat-demo:ATT-child-001":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "child summary"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {"claims": [{"label": "child"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-parent-001", "open_parent"
        if call_state["select_calls"] == 2:
            return "open", "chat-demo:ATT-child-001", "open_child"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble_with_attachment_parent)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "multi-hop-cache-proof",
            "message": "open parent then child",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    resolver_cache = meta.get("resolver_cache") if isinstance(meta.get("resolver_cache"), dict) else {}
    assert resolver_cache.get("hits", 0) >= 1
    assert resolver_cache.get("misses", 0) >= 2
    assert decode_calls["count"] == 2


def test_coord_action_plan_can_stop_opening(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_select_choice_coord(**_kwargs):
        return "use_priors", None, "catalog_not_needed"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "coord-action-stop-contract",
            "message": "answer from priors if enough",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    assert action_plan_events
    payload = action_plan_events[-1].get("payload") or {}
    assert payload.get("action") == "use_priors"
    meta = [event for event in events if event.get("type") == "meta"][-1]
    assert meta.get("resolved_coords") == []


def test_recursive_child_candidates_do_not_become_context_without_open(monkeypatch):
    call_state = {"select_calls": 0, "child_decode_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:WX-1772505927152":
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "parent candidate"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-child-001",
                            "topics": ["child"],
                            "tags": ["part"],
                            "tokens_est": 120,
                        }
                    ]
                },
                "interpretation": {"claims": [{"label": "parent"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        if coord == "chat-demo:ATT-child-001":
            call_state["child_decode_calls"] += 1
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "child candidate"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {"claims": [{"label": "child"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:WX-1772505927152", "open_parent"
        return "use_priors", None, "do_not_open_child"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "recursive-child-catalog-only",
            "message": "open parent but do not recurse",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    child_context_events = [
        event for event in events
        if event.get("type") == "context_item" and event.get("coord") == "chat-demo:ATT-child-001"
    ]
    assert not child_context_events
    assert call_state["child_decode_calls"] == 0
    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    assert action_plan_events
    assert (action_plan_events[0].get("payload") or {}).get("action") == "open"


def test_recursive_ref_candidates_do_not_decode_without_open(monkeypatch):
    call_state = {"select_calls": 0, "ref_decode_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:WX-1772505927152":
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "parent with refs"},
                "walk": None,
                "refs": {"context": ["chat-demo:WX-ref-001"]},
                "payload": {"parts": []},
                "interpretation": {"claims": [{"label": "parent"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        if coord == "chat-demo:WX-ref-001":
            call_state["ref_decode_calls"] += 1
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "ref child"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {"claims": [{"label": "ref"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:WX-1772505927152", "open_parent"
        return "use_priors", None, "do_not_open_ref"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "recursive-ref-catalog-only",
            "message": "open parent but do not open refs",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    ref_context_events = [
        event for event in events
        if event.get("type") == "context_item" and event.get("coord") == "chat-demo:WX-ref-001"
    ]
    assert not ref_context_events
    assert call_state["ref_decode_calls"] == 0
    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    assert action_plan_events
    assert (action_plan_events[0].get("payload") or {}).get("action") == "open"


def test_current_turn_runtime_prompt_suppresses_recursive_wx_ref_walks(monkeypatch):
    call_state = {"select_calls": 0, "ref_decode_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:WX-1772505927152":
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "parent with refs"},
                "walk": None,
                "refs": {"context": ["chat-demo:WX-ref-001"]},
                "payload": {"parts": []},
                "interpretation": {"claims": [{"label": "parent"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        if coord == "chat-demo:WX-ref-001":
            call_state["ref_decode_calls"] += 1
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "ref child"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {"claims": [{"label": "ref"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        return "open", "chat-demo:WX-1772505927152", "open_parent"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "current-turn-runtime-only",
            "message": "Current-turn runtime only. Do not use prior answers or prior WX summaries.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    recursive_action_events = [
        event
        for event in events
        if event.get("type") == "coord_action_plan" and ((event.get("payload") or {}).get("phase") == "recursive")
    ]
    assert not recursive_action_events
    assert call_state["ref_decode_calls"] == 0
    resolved_meta = [event for event in events if event.get("type") == "meta"][-1]
    assert resolved_meta.get("resolved_coords") == ["chat-demo:WX-1772505927152"]


def test_opened_coord_admits_skim_summary_before_full_payload(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:WX-1772505927152":
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "compact summary"},
                "walk": None,
                "refs": {},
                "payload": {
                    "segments": [{"id": "ANS-1", "kind": "answer", "blob_ref": "BLOB:ANS-1"}],
                    "blobs": {"BLOB:ANS-1": "full payload body that should not be admitted first"},
                    "parts": [],
                },
                "interpretation": {"claims": [{"label": "summary-first"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "open_parent"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "skim-summary-admission",
            "message": "open parent with summary first",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    context_items = [event for event in events if event.get("type") == "context_item" and event.get("coord") == "chat-demo:WX-1772505927152"]
    assert context_items
    assert (context_items[-1].get("text") or "") == "full payload body that should not be admitted first"
    admitted_events = [event for event in events if event.get("type") == "coord_context_admitted"]
    assert admitted_events
    admitted_payload = admitted_events[-1].get("payload") or {}
    assert admitted_payload.get("admission") == "opened_payload"


def test_explicit_attachment_part_admits_full_payload_before_skim_summary(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {
                    "segments": [{"id": "ANS-01", "kind": "answer", "blob_ref": "BLOB:ATT-T:ANS-01"}],
                    "blobs": {
                        "BLOB:ATT-T:ANS-01": "Full payload body on consciousness and coupled structural variables."
                    },
                    "parts": [],
                },
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_attempt_placeholder)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "explicit-attachment-part-admission",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    context_items = [
        event
        for event in events
        if event.get("type") == "context_item" and event.get("coord") == "chat-demo:ATT-target-parent-T015"
    ]
    assert context_items
    assert (context_items[-1].get("text") or "") == "Full payload body on consciousness and coupled structural variables."
    admitted_events = [
        event
        for event in events
        if event.get("type") == "coord_context_admitted"
        and ((event.get("payload") or {}).get("coord") == "chat-demo:ATT-target-parent-T015")
    ]
    assert admitted_events
    admitted_payload = admitted_events[-1].get("payload") or {}
    assert admitted_payload.get("admission") == "attachment_payload"


def test_blocked_coord_exposes_block_reason_and_gated_preview_without_full_payload(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:WX-1772505927152":
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "catalog skim only"},
                "walk": None,
                "refs": {},
                "payload": {
                    "segments": [{"id": "ANS-1", "kind": "answer", "blob_ref": "BLOB:ANS-1"}],
                    "blobs": {"BLOB:ANS-1": "full payload body should stay hidden"},
                    "parts": [],
                },
                "interpretation": {},
                "governance": {
                    "policy_decision": "block",
                    "reason_code": "governance_gate",
                    "failed_eq": "eq6_awareness",
                    "repair_actions": ["provide_provenance", "replay_evidence"],
                    "enforced_controls": ["emit_block_envelope_only"],
                    "trust_class": "T3",
                    "eq9_posture_class": "P0",
                },
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "open_parent"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "blocked-state-admission",
            "message": "open blocked coord",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    context_items = [event for event in events if event.get("type") == "context_item" and event.get("coord") == "chat-demo:WX-1772505927152"]
    assert context_items
    text = context_items[-1].get("text") or ""
    assert "policy_decision=block" in text
    assert "block_reason=governance_gate" in text
    assert "preview_state=skim_only_preview" in text
    assert "failed_eq=eq6_awareness" in text
    assert "trust_class=T3" in text
    assert "eq9_posture_class=P0" in text
    assert "repair_actions=provide_provenance,replay_evidence" in text
    assert "enforced_controls=emit_block_envelope_only" in text
    assert "catalog skim only" in text
    assert "full payload body should stay hidden" not in text
    admitted_events = [event for event in events if event.get("type") == "coord_context_admitted"]
    assert admitted_events
    admitted_payload = admitted_events[-1].get("payload") or {}
    assert admitted_payload.get("admission") == "governance_block_state"
    assert admitted_payload.get("block_reason") == "governance_gate"
    assert admitted_payload.get("preview_state") == "skim_only_preview"
    assert admitted_payload.get("failed_eq") == "eq6_awareness"
    assert admitted_payload.get("trust_class") == "T3"
    assert admitted_payload.get("eq9_posture_class") == "P0"
    assert admitted_payload.get("repair_actions") == ["provide_provenance", "replay_evidence"]
    assert admitted_payload.get("enforced_controls") == ["emit_block_envelope_only"]


def test_blocked_coord_distinguishes_payload_missing_from_not_yet_opened(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    decode_counter = {"count": 0}

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        decode_counter["count"] += 1
        if coord == "chat-demo:WX-1772505927152":
            if decode_counter["count"] == 1:
                return {
                    "coord": coord,
                    "type": "WX",
                    "skim": {},
                    "walk": None,
                    "refs": {},
                    "payload": {
                        "segments": [{"id": "ANS-1", "kind": "answer", "blob_ref": "BLOB:ANS-1"}],
                        "blobs": {"BLOB:ANS-1": "hidden answer body"},
                        "parts": [],
                    },
                    "interpretation": {},
                    "governance": {"policy_decision": "block"},
                    "meta": {"governance_error": {"reason": "materialization_pending"}},
                }
            return {
                "coord": coord,
                "type": "WX",
                "skim": {},
                "walk": None,
                "refs": {},
                "payload": {},
                "interpretation": {},
                "governance": {"policy_decision": "block"},
                "meta": {"governance_error": {"reason": "payload_unavailable"}},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "open_parent"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    first_events = _stream_events(
        {
            "session_id": "blocked-preview-state-present",
            "message": "open blocked coord once",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )
    first_item = [event for event in first_events if event.get("type") == "context_item" and event.get("coord") == "chat-demo:WX-1772505927152"][-1]
    assert "preview_state=payload_present_not_opened" in (first_item.get("text") or "")

    second_events = _stream_events(
        {
            "session_id": "blocked-preview-state-missing",
            "message": "open blocked coord twice",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )
    second_item = [event for event in second_events if event.get("type") == "context_item" and event.get("coord") == "chat-demo:WX-1772505927152"][-1]
    assert "preview_state=payload_missing" in (second_item.get("text") or "")


def test_current_turn_runtime_only_skips_session_continuity_fallback(monkeypatch):
    session_obj = {
        "turn_count": 0,
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "last_coordinate": "chat-demo:WX-last-turn",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_assemble(**_kwargs):
        return {"retrieved": [], "decoded_context": []}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        raise AssertionError("current-turn/runtime-only prompts should not introspect for continuity fallback")

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "current-turn-no-continuity-fallback",
            "message": "Current-turn runtime only. Do not use prior answers or prior WX summaries.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    candidate_event = next((event for event in events if event.get("type") == "candidate_trace"), {})
    top_k = (((candidate_event.get("payload") or {}) if isinstance(candidate_event, dict) else {}).get("top_k") or [])
    assert top_k == []
    decision_event = next((event for event in events if event.get("type") == "autonomy_decision"), {})
    decision = (decision_event.get("payload") or {}) if isinstance(decision_event, dict) else {}
    assert decision.get("action") == "answer_from_priors"
    opened_coords = [
        ((event.get("payload") or {}).get("coord"))
        for event in events
        if event.get("type") == "coord_action_plan"
    ]
    assert "chat-demo:WX-last-turn" not in opened_coords


def test_packed_live_review_skips_agent_change_last_turn_seeding(monkeypatch):
    session_obj = {
        "turn_count": 2,
        "last_agent": "previous-model",
        "last_coordinate": "chat-demo:WX-last-turn",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "packed-live-review-no-last-turn-seed",
            "message": "Assess live DSS Epic 17 on chat-demo under these headings only: payload opacity, continuity and consolidation, retention and gravity, and claim-to-evidence grounding. Cite observable fields only.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    status_messages = [
        str(event.get("message") or "")
        for event in events
        if event.get("type") == "status"
    ]
    assert not any("Seeding last turn chat-demo:WX-last-turn" in message for message in status_messages)
    resolved_meta = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    resolved_coords = resolved_meta.get("resolved_coords") if isinstance(resolved_meta, dict) else []
    assert "chat-demo:WX-last-turn" not in (resolved_coords or [])


def test_governance_block_explainer_prompt_uses_packed_live_review_lane(monkeypatch):
    session_obj = {
        "turn_count": 2,
        "last_agent": "previous-model",
        "last_coordinate": "chat-demo:WX-last-turn",
        "ledger_id": orchestrator_module.settings.DEFAULT_LEDGER_ID,
        "entity": "chat-demo",
    }

    def fake_get_session(_session_id: str):
        return session_obj

    def fake_update_session(_session_id: str, session: dict):
        session_obj.update(session)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_assemble(**_kwargs):
        return {"retrieved": [], "decoded_context": []}

    async def fake_introspect_runtime(**_kwargs):
        return {"entity": "chat-demo"}

    monkeypatch.setattr(orchestrator_module, "get_session", fake_get_session)
    monkeypatch.setattr(orchestrator_module, "update_session", fake_update_session)
    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(
        orchestrator_module,
        "_build_packed_review_runtime_witness",
        lambda _snapshot, *, message="", **_kwargs: {
            "coord": "runtime:introspect:chat-demo",
            "text": (
                "Current-turn runtime witness evidence object:\n"
                "- evidence_access_state=payload_opened\n"
                "- resolved_for_answer=true\n"
                "- reason_code=eq_blocked:eq6_awareness\n"
                "- failed_eq=eq6_awareness\n"
                "- repair_actions=Provide complete provenance inputs and replay evidence.\n"
                "- enforced_controls=emit_block_envelope_only\n"
            ),
            "evidence_access_state": "payload_opened",
            "payload_opened": True,
            "resolved_for_answer": True,
            "grounding_eligible": True,
            "source": "current_turn_runtime_introspect",
        },
    )
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "governance-block-packed-review",
            "message": "Please explain the governance block on the last blocked turn. Use only the admitted evidence. If the block reason is present, name the reason code, failed eq, repair actions, and enforced controls briefly.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    meta = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    admitted = meta.get("admitted_context_trace") if isinstance(meta, dict) else None
    assert isinstance(admitted, list)
    witness = next((entry for entry in admitted if (entry or {}).get("coord") == "runtime:introspect:chat-demo"), None)
    assert witness is not None
    assert witness.get("admission") == "current_turn_runtime_witness"
    compact_context = meta.get("context") if isinstance(meta, dict) else None
    assert isinstance(compact_context, list)
    joined = "\n".join(str(item.get("text") or "") for item in compact_context if isinstance(item, dict))
    assert "Current-turn runtime witness evidence object:" in joined
    assert "reason_code=eq_blocked:eq6_awareness" in joined
    assert "failed_eq=eq6_awareness" in joined


def test_meta_includes_autonomy_evidence_for_single_prior_decode(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "autonomy-evidence-single-prior",
            "message": "Open the most recent coord and answer briefly.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )
    meta = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta.get("metadata") if isinstance(meta, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("used_prior_coordinates") is True
    assert autonomy.get("traversal_state") == "single_coord_decode"
    assert autonomy.get("coord_access_state") == "payload_opened"


def test_empty_assemble_uses_subject_history_candidates_for_ordinary_prompt(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_thread(**_kwargs):
        return [
            {
                "coord": "chat-demo:WX-genesis-1",
                "role": "assistant",
                "content": "Genesis gives a meta-narrative for AI design through ordered differentiation, alignment, and stewardship.",
                "metadata": {
                    "summary": "Genesis and AI design in a nutshell",
                    "topics": ["genesis", "ai", "design"],
                    "tags": ["alignment", "creation", "nutshell"],
                    "eq6_commit_allowed": True,
                    "eq6_lawfulness_level": 2,
                    "eq6_cw": 1,
                },
            },
            {
                "coord": "chat-demo:WX-unrelated-1",
                "role": "assistant",
                "content": "A note about weather and breakfast.",
                "metadata": {
                    "summary": "Weather and breakfast",
                    "topics": ["weather"],
                    "tags": ["breakfast"],
                    "eq6_commit_allowed": True,
                    "eq6_lawfulness_level": 1,
                    "eq6_cw": 2,
                },
            },
        ]

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": coord,
            "type": "WX",
            "skim": {"one_line": f"decoded {coord}"},
            "walk": None,
            "refs": {},
            "payload": {"parts": []},
            "interpretation": {},
            "governance": {},
            "meta": {
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_cw": 1,
                "topics": ["genesis", "ai", "design"],
                "tags": ["alignment", "creation"],
            },
        }

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "thread", fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-history-fallback",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    assert top_k
    assert top_k[0].get("coord") == "chat-demo:WX-genesis-1"
    assert top_k[0].get("source") == "history_subject"

    decision_events = [event for event in events if event.get("type") == "autonomy_decision"]
    assert decision_events
    decision = decision_events[-1].get("payload") or {}
    assert decision.get("chosen_coord") == "chat-demo:WX-genesis-1"

    meta = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta.get("metadata") if isinstance(meta, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("used_prior_coordinates") is True
    assert autonomy.get("traversal_state") != "no_traversal"
    assert "chat-demo:WX-genesis-1" in (autonomy.get("resolved_coords") or [])


def test_subject_history_candidates_outrank_recent_generic_coord_for_ordinary_prompt(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_thread(**_kwargs):
        return [
            {
                "coord": "chat-demo:WX-genesis-deep-1",
                "role": "assistant",
                "content": "Genesis and AI design connect through ordered differentiation, alignment drift, stewardship, and purpose.",
                "metadata": {
                    "summary": "Genesis, alignment, and AI design",
                    "topics": ["genesis", "ai", "design", "alignment"],
                    "tags": ["stewardship", "purpose", "creation"],
                    "eq6_commit_allowed": True,
                    "eq6_lawfulness_level": 2,
                    "eq6_cw": 1,
                },
            },
        ]

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": coord,
            "type": "WX",
            "skim": {"one_line": f"decoded {coord}"},
            "walk": None,
            "refs": {},
            "payload": {"parts": []},
            "interpretation": {},
            "governance": {},
            "meta": {
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_cw": 1,
                "topics": ["genesis", "ai", "design", "alignment"],
                "tags": ["stewardship", "purpose"],
            },
        }

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_recent_generic_candidate)
    monkeypatch.setattr(orchestrator_module.api, "thread", fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-history-outranks-recent-generic",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    assert top_k
    assert top_k[0].get("coord") == "chat-demo:WX-genesis-deep-1"
    assert top_k[0].get("source") == "history_subject"

    decision_events = [event for event in events if event.get("type") == "autonomy_decision"]
    assert decision_events
    decision = decision_events[-1].get("payload") or {}
    assert decision.get("chosen_coord") == "chat-demo:WX-genesis-deep-1"

    meta = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta.get("metadata") if isinstance(meta, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("used_prior_coordinates") is True
    assert "chat-demo:WX-genesis-deep-1" in (autonomy.get("resolved_coords") or [])


def test_subject_search_candidates_outrank_recent_generic_coord_for_ordinary_prompt(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": coord,
            "type": "WX",
            "skim": {"one_line": f"decoded {coord}"},
            "walk": None,
            "refs": {},
            "payload": {"parts": []},
            "interpretation": {},
            "governance": {},
            "meta": {
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_cw": 1,
                "topics": ["genesis", "ai", "design", "alignment"],
                "tags": ["stewardship", "purpose"],
            },
        }

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_recent_generic_candidate)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any_with_genesis)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-search-outranks-recent-generic",
            "entity": "chat-demo",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    assert top_k
    assert top_k[0].get("coord") == "chat-demo:WX-genesis-search-1"
    assert top_k[0].get("source") == "history_search"
    assert top_k[0].get("ancestry_score") == 0.75

    decision_events = [event for event in events if event.get("type") == "autonomy_decision"]
    assert decision_events
    decision = decision_events[-1].get("payload") or {}
    assert decision.get("chosen_coord") == "chat-demo:WX-genesis-search-1"


def test_subject_search_penalizes_irrelevant_attachment_hit_for_ordinary_prompt(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": coord,
            "type": "WX" if coord.endswith("WX-genesis-bible-1") else "ATT-PART",
            "skim": {"one_line": f"decoded {coord}"},
            "walk": None,
            "refs": {},
            "payload": {"parts": []},
            "interpretation": {},
            "governance": {},
            "meta": {
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_cw": 1,
                "topics": ["genesis", "bible", "creation"] if coord.endswith("WX-genesis-bible-1") else ["physics", "oscillations"],
                "tags": ["stewardship", "order"] if coord.endswith("WX-genesis-bible-1") else ["flux"],
            },
        }

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(
        orchestrator_module.api,
        "search_any",
        _fake_search_any_with_irrelevant_attachment_and_relevant_wx,
    )
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-search-penalizes-attachment-false-positive",
            "entity": "chat-demo",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    assert top_k
    assert top_k[0].get("coord") == "chat-demo:WX-genesis-bible-1"
    assert top_k[0].get("source") == "history_search"
    assert top_k[0].get("semantic_score", 0) > 0

    decision_events = [event for event in events if event.get("type") == "autonomy_decision"]
    assert decision_events
    decision = decision_events[-1].get("payload") or {}
    assert decision.get("chosen_coord") == "chat-demo:WX-genesis-bible-1"


def test_subject_search_attachment_only_still_falls_back_to_thread_history(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_thread(**_kwargs):
        return [
            {
                "coord": "chat-demo:WX-genesis-thread-1",
                "role": "assistant",
                "content": "Genesis as biblical creation narrative connects to AI design through stewardship, ordered naming, and alignment.",
                "metadata": {
                    "summary": "Biblical Genesis, stewardship, and AI alignment",
                    "topics": ["genesis", "bible", "ai", "design", "alignment"],
                    "tags": ["stewardship", "creation", "order"],
                    "eq6_commit_allowed": True,
                    "eq6_lawfulness_level": 2,
                    "eq6_cw": 1,
                },
            },
        ]

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": coord,
            "type": "WX" if ":WX-" in coord else "ATT-PART",
            "skim": {"one_line": f"decoded {coord}"},
            "walk": None,
            "refs": {},
            "payload": {"parts": []},
            "interpretation": {},
            "governance": {},
            "meta": {
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_cw": 1,
                "topics": ["genesis", "bible", "ai", "design"] if ":WX-" in coord else ["physics", "oscillations"],
                "tags": ["stewardship", "creation"] if ":WX-" in coord else ["flux"],
            },
        }

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any_with_only_skip_recommended_attachment)
    monkeypatch.setattr(orchestrator_module.api, "thread", fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-search-attachment-only-thread-fallback",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events
    top_k = ((trace_events[-1].get("payload") or {}).get("top_k")) or []
    assert top_k
    assert top_k[0].get("coord") == "chat-demo:WX-genesis-thread-1"
    assert top_k[0].get("source") == "history_subject"

    meta = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta.get("metadata") if isinstance(meta, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert "chat-demo:WX-genesis-thread-1" in (autonomy.get("resolved_coords") or [])


def test_ordinary_subject_weak_attachment_candidates_can_be_suppressed():
    candidate_trace = [
        {
            "coord": "chat-demo:ATT-physics-1-T012",
            "relevance_score": 0.83,
            "semantic_score": 0.0,
            "ancestry_score": 0.5,
            "source": "retrieved",
        },
        {
            "coord": "chat-demo:WX-genesis-bible-1",
            "relevance_score": 0.79,
            "semantic_score": 0.33,
            "ancestry_score": 0.5,
            "source": "history_search",
        },
    ]

    weak = orchestrator_module._ordinary_subject_weak_attachment_coords(candidate_trace)
    assert "chat-demo:ATT-physics-1-T012" in weak
    assert "chat-demo:WX-genesis-bible-1" not in weak


def test_ordinary_subject_branch_exploration_detects_ambiguous_cross_domain_top_hit():
    candidate_trace = [
        {
            "coord": "chat-demo:ATT-physics-1-T012",
            "relevance_score": 0.83,
            "semantic_score": 0.12,
            "source": "retrieved",
        },
        {
            "coord": "chat-demo:WX-genesis-bible-1",
            "relevance_score": 0.79,
            "semantic_score": 0.33,
            "source": "history_search",
        },
    ]

    assert orchestrator_module._ordinary_subject_should_explore_branches(candidate_trace) is True


def test_ordinary_subject_skip_recommended_attachment_candidates_can_be_suppressed():
    queued_coords = [
        "chat-demo:ATT-physics-1-T012",
        "chat-demo:WX-genesis-bible-1",
    ]
    preview_map = {
        "chat-demo:ATT-physics-1-T012": {
            "summary": "Physics attachment about flux quantization",
            "recommended": ["skip"],
        },
        "chat-demo:WX-genesis-bible-1": {
            "summary": "Biblical Genesis and AI design reflections",
            "recommended": ["open:answer"],
        },
    }

    skipped = orchestrator_module._ordinary_subject_skip_recommended_attachment_coords(
        queued_coords,
        preview_map,
    )
    assert "chat-demo:ATT-physics-1-T012" in skipped
    assert "chat-demo:WX-genesis-bible-1" not in skipped


def test_subject_search_candidates_preserve_skip_recommendations():
    candidates = orchestrator_module._build_subject_search_candidates(
        message="Explain flux quantization in mesoscopic rings briefly.",
        search_result=asyncio.run(_fake_search_any_with_only_skip_recommended_attachment()),
        entity="chat-demo",
    )

    physics = next(
        candidate for candidate in candidates
        if candidate.get("coord") == "chat-demo:ATT-physics-1-T012"
    )
    metadata = physics.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("recommended") == ["skip"]
    assert metadata.get("reasons") == ["cross_domain_weak_match"]


def test_subject_branch_exploration_skips_predecode_single_open_for_skip_recommended_attachment(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": coord,
            "type": "WX" if ":WX-" in coord else "ATT",
            "skim": {"one_line": f"decoded {coord}"},
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

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any_with_irrelevant_attachment_and_relevant_wx)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-branch-skip-predecode",
            "entity": "chat-demo",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    predecode_actions = [
        (event.get("payload") or {})
        for event in action_plan_events
        if ((event.get("payload") or {}).get("phase") == "predecode")
    ]
    assert predecode_actions
    assert all(action.get("coord") != "chat-demo:ATT-physics-1-T012" for action in predecode_actions)

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events
    payload_meta = meta_events[-1].get("metadata")
    if not isinstance(payload_meta, dict):
        payload_meta = meta_events[-1].get("payload")
    if not isinstance(payload_meta, dict):
        payload_meta = meta_events[-1]
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    resolved_coords = autonomy.get("resolved_coords") or []
    assert "chat-demo:ATT-physics-1-T012" not in resolved_coords


def test_ordinary_subject_does_not_open_only_skip_recommended_attachment(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(
        orchestrator_module.api,
        "search_any",
        _fake_search_any_with_only_skip_recommended_attachment,
    )
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-skip-only-attachment",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("payload")
    if not isinstance(payload_meta, dict):
        payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("resolved_coords") == []
    assert autonomy.get("traversal_state") == "no_traversal"


def test_ordinary_subject_drops_skip_recommended_attachment_from_retrieved_queue(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_skip_recommended_attachment_candidate)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any_with_only_skip_recommended_attachment)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-retrieved-skip-attachment",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("payload")
    if not isinstance(payload_meta, dict):
        payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("resolved_coords") == []
    assert autonomy.get("traversal_state") == "no_traversal"


def test_ordinary_subject_decode_loop_skips_skip_recommended_attachment(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(
        orchestrator_module.api,
        "search_any",
        _fake_search_any_with_only_skip_recommended_attachment,
    )
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-decode-skip-attachment",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("payload")
    if not isinstance(payload_meta, dict):
        payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("resolved_coords") == []
    assert autonomy.get("opened_payload_coord_count") == 0


def test_ordinary_subject_decode_loop_skips_attachment_even_without_skip_metadata(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_assemble_attachment_only(**_kwargs):
        return {
            "retrieved": [
                {
                    "coordinate": "chat-demo:ATT-physics-1-T012",
                    "relevance_score": 0.88,
                    "snippet": "Aharonov-Bohm oscillations and flux quantization in mesoscopic rings.",
                    "source": "retrieved",
                    "state": {
                        "metadata": {
                            "summary": "Physics attachment about flux quantization",
                            "topics": ["physics", "oscillations"],
                            "tags": ["mesoscopic", "flux"],
                            "eq6_commit_allowed": True,
                            "eq6_lawfulness_level": 2,
                            "eq6_cw": 1,
                        }
                    },
                }
            ],
            "decoded_context": [],
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble_attachment_only)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any_with_only_skip_recommended_attachment)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-decode-attachment-no-skip-meta",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("resolved_coords") == []
    assert autonomy.get("opened_payload_coord_count") == 0


def test_ordinary_subject_walk_guidance_cannot_reintroduce_attachment_hit(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": coord,
            "type": "WX" if ":WX-" in coord else "ATT",
            "skim": {"one_line": f"decoded {coord}"},
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

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any_with_irrelevant_attachment_and_relevant_wx)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk_with_attachment_guided_path)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-walk-guidance-filter",
            "message": "What has the story of Genesis got to do with AI design in a nutshell?",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    resolved_coords = autonomy.get("resolved_coords") or []
    assert "chat-demo:ATT-physics-1-T012" not in resolved_coords


def test_ordinary_subject_generic_evidence_request_does_not_authorize_attachment_hit(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": coord,
            "type": "WX" if ":WX-" in coord else "ATT",
            "skim": {"one_line": f"decoded {coord}"},
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

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any_with_irrelevant_attachment_and_relevant_wx)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk_with_attachment_guided_path)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-generic-evidence-no-attachment",
            "message": "What has the story of Genesis got to do with AI design in a nutshell? Prefer historically relevant ledger evidence if available.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    resolved_coords = autonomy.get("resolved_coords") or []
    assert "chat-demo:ATT-physics-1-T012" not in resolved_coords


def test_explicit_traversal_request_preserves_bounded_walk_queue(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {"governance_metrics": {"L": 1.0}}

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "explicit-traversal-walk",
            "message": "Walk the most relevant prior coordinate chain up to 1 hop and report the strongest historical fact.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )
    meta = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta.get("metadata") if isinstance(meta, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("explicit_traversal_requested") is True
    assert autonomy.get("traversal_state") == "walk"
    assert autonomy.get("used_prior_coordinates") is True
    assert autonomy.get("traversed_coord_count") == 2
    assert autonomy.get("requested_traversal_steps") == 1
    assert autonomy.get("requested_traversal_max_opened_coords") == 2
    assert autonomy.get("effective_traversal_opened_coords") == 2
    assert autonomy.get("traversal_bound_status") == "honored"
    assert autonomy.get("resolved_coord_count") >= 2
    assert autonomy.get("traversal_refusal_reason") is None


def test_failed_explicit_walk_request_rewrites_fabricated_tool_invocation(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_fabricated_walk)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "failed-explicit-walk-truthfulness",
            "message": "Let's try again - Can you walk 10 steps and theme on telos? And open payloads along the way.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta.get("metadata") if isinstance(meta, dict) else None
    assert isinstance(payload_meta, dict)
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    assert autonomy.get("explicit_traversal_requested") is True
    assert autonomy.get("traversal_state") == "no_traversal"
    assert autonomy.get("walk_execution_started") is False
    assert autonomy.get("walk_execution_completed") is False
    assert autonomy.get("walk_failure_reason") == "traversal_not_selected"

    failure_contract = payload_meta.get("walk_failure_contract")
    assert isinstance(failure_contract, dict)
    assert failure_contract.get("walk_execution_started") is False
    assert failure_contract.get("walk_failure_reason") == "traversal_not_selected"
    walk_contract = payload_meta.get("walk_selection_contract")
    assert isinstance(walk_contract, dict)
    assert walk_contract.get("walk_requested_by_user") is True
    assert walk_contract.get("walk_selected_by_autonomy") is False
    assert walk_contract.get("walk_refused") is True
    assert walk_contract.get("walk_status") == "refused"

    content = str(payload_meta.get("content") or "")
    assert "introspection_signal" not in content
    assert "walk_execution_started=false" in content
    assert "walk_failure_reason=traversal_not_selected" in content


def test_extract_keywords_prioritizes_subject_terms_for_ordinary_prompt():
    keywords = orchestrator_module._extract_keywords(
        "What has the story of Genesis got to do with AI design in a nutshell? "
        "Answer briefly, but prefer historically relevant ledger evidence if available.",
        limit=8,
    )

    assert "genesis" in keywords
    assert "ai" in keywords
    assert "design" in keywords
    assert "historically" in keywords
    assert "ledger" in keywords
    assert "has" not in keywords
    assert "got" not in keywords
    assert "answer" not in keywords
    assert "briefly" not in keywords


def test_ordinary_subject_branch_exploration_triggers_for_middling_multi_branch_candidates():
    candidate_trace = [
        {
            "coord": "chat-demo:WX-genesis-1",
            "source": "history_subject",
            "relevance_score": 0.66,
            "semantic_score": 0.51,
            "resolved_payload_present": False,
        },
        {
            "coord": "chat-demo:WX-genesis-2",
            "source": "recent",
            "relevance_score": 0.61,
            "semantic_score": 0.48,
            "resolved_payload_present": False,
        },
        {
            "coord": "chat-demo:ATT-physics-1-T012",
            "source": "search_subject",
            "relevance_score": 0.59,
            "semantic_score": 0.44,
            "resolved_payload_present": False,
        },
    ]

    assert orchestrator_module._ordinary_subject_should_explore_branches(candidate_trace) is True


def test_ordinary_subject_branch_exploration_prefers_non_attachment_queue(monkeypatch):
    async def _fake_assemble(**_kwargs):
        return {"retrieved": []}

    async def _fake_search_any(**_kwargs):
        return {}

    async def _fake_thread(**_kwargs):
        return [
            {
                "role": "assistant",
                "coordinate": "chat-demo:ATT-physics-1-T012",
                "content": "Physics origin story bridge",
                "metadata": {
                    "summary": "Physics origin bridge",
                    "topics": ["physics", "origin"],
                },
            },
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-1",
                "content": "Biblical Genesis and AI design reflections",
                "metadata": {
                    "summary": "Biblical Genesis and AI design reflections",
                    "topics": ["genesis", "ai", "design", "biblical"],
                },
            },
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-2",
                "content": "Stewardship and alignment in Genesis",
                "metadata": {
                    "summary": "Stewardship and alignment in Genesis",
                    "topics": ["stewardship", "genesis", "alignment"],
                },
            },
        ]

    async def _fake_decode_coordinate(coord, **_kwargs):
        return {
            "status": "ok",
            "meta": {},
            "payload": {},
            "skim": f"decoded {coord}",
        }

    async def _fake_commit_answer(**kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": kwargs.get("metadata") or {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", _fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "thread", _fake_thread)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-branch-non-attachment",
            "message": "What has the story of Genesis got to do with AI design in a nutshell? Answer briefly, but prefer historically relevant ledger evidence if available.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    resolved_coords = autonomy.get("resolved_coords") or []
    assert "chat-demo:ATT-physics-1-T012" not in resolved_coords
    assert any(coord.startswith("chat-demo:WX-genesis-") for coord in resolved_coords)


def test_ordinary_subject_falls_back_to_open_wx_candidate_when_planner_stops(monkeypatch):
    async def _fake_assemble(**_kwargs):
        return {"retrieved": []}

    async def _fake_search_any(**_kwargs):
        return {}

    async def _fake_thread(**_kwargs):
        return [
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-1",
                "content": "Biblical Genesis and AI design reflections",
                "metadata": {
                    "summary": "Biblical Genesis and AI design reflections",
                    "topics": ["genesis", "ai", "design", "biblical"],
                },
            },
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-2",
                "content": "Stewardship and alignment in Genesis",
                "metadata": {
                    "summary": "Stewardship and alignment in Genesis",
                    "topics": ["stewardship", "genesis", "alignment"],
                },
            },
        ]

    async def _fake_decode_coordinate(coord, **_kwargs):
        return {
            "status": "ok",
            "meta": {},
            "payload": {},
            "skim": f"decoded {coord}",
        }

    async def _fake_select_choice_coord(**_kwargs):
        return ("stop", None, "low confidence")

    async def _fake_commit_answer(**kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": kwargs.get("metadata") or {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any)
    monkeypatch.setattr(orchestrator_module.api, "thread", _fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", _fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", _fake_select_choice_coord)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "subject-planner-stop-fallback",
            "message": "What has the story of Genesis got to do with AI design in a nutshell? Answer briefly, but prefer historically relevant ledger evidence if available.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("payload")
    if not isinstance(payload_meta, dict):
        payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else None
    autonomy = payload_meta.get("autonomy_evidence") if isinstance(payload_meta, dict) else None
    assert isinstance(autonomy, dict)
    resolved_coords = autonomy.get("resolved_coords") or []
    assert "chat-demo:WX-genesis-1" in resolved_coords
    assert autonomy.get("traversal_state") != "no_traversal"
    walk_contract = payload_meta.get("walk_selection_contract")
    assert isinstance(walk_contract, dict)
    assert walk_contract.get("walk_requested_by_user") is False
    assert walk_contract.get("walk_selected_by_autonomy") is True
    assert walk_contract.get("walk_started") is True
    assert walk_contract.get("walk_completed") is True
    assert "subject_history_match" in (walk_contract.get("walk_trigger_reasons") or [])
    assert walk_contract.get("branch_exploration_requested") is False
    assert walk_contract.get("branch_exploration_attempted") is False
    assert walk_contract.get("branch_exploration_suppressed_reason") is None
    branch_summary = payload_meta.get("branch_selection_summary")
    assert isinstance(branch_summary, dict)
    assert "chat-demo:WX-genesis-1" in (branch_summary.get("selected_coords") or [])
    assert branch_summary.get("subject_history_fallback_used") is True
    assert branch_summary.get("selection_reason") == "subject_history_fallback"
    artifact_identity = payload_meta.get("decision_artifact_identity")
    assert isinstance(artifact_identity, dict)
    assert artifact_identity.get("public_object_kind") == "decision-artifact"
    assert str(artifact_identity.get("untp_hash") or "").startswith("sha256:")
    assert artifact_identity.get("object_id") == artifact_identity.get("untp_hash")
    assert artifact_identity.get("coord_bridge", {}).get("coord_exposed_as_primary") is False
    canonical_envelope = artifact_identity.get("canonical_envelope")
    assert isinstance(canonical_envelope, dict)
    assert canonical_envelope.get("normalized_input", {}).get("user_message") == (
        "What has the story of Genesis got to do with AI design in a nutshell? "
        "Answer briefly, but prefer historically relevant ledger evidence if available."
    )
    assert "chat-demo:WX-genesis-1" in (canonical_envelope.get("admitted_evidence", {}).get("resolved_coords") or [])


def test_walk_selection_contract_surfaces_branch_exploration_suppression_reason():
    contract = orchestrator_module._build_walk_selection_contract(
        explicit_traversal_requested=False,
        walk_selected_by_autonomy=True,
        walk_planner_started=False,
        traversal_state="no_traversal",
        resolved_coords=[],
        traversed_coords=[],
        walk_ids=[],
        walk_trigger_reasons=["subject_history_match", "ambiguity_detected"],
        walk_termination_reason=None,
        walk_start_coord=None,
        requested_traversal_steps=None,
        effective_traversal_opened_coords=0,
        branch_exploration_requested=True,
        branch_exploration_attempted=False,
        branch_exploration_suppressed_reason="branch_exploration_planner_no_candidates",
    )

    assert contract.get("walk_refused") is True
    assert contract.get("walk_status") == "refused"
    assert contract.get("branch_exploration_requested") is True
    assert contract.get("branch_exploration_attempted") is False
    assert contract.get("branch_exploration_suppressed_reason") == "branch_exploration_planner_no_candidates"


def test_decision_artifact_identity_uses_digest_primary_id_and_internal_coord_bridge():
    identity = orchestrator_module._build_decision_artifact_identity(
        entity="chat-demo",
        user_message="What has Genesis got to do with AI design?",
        reply_text="No ledger-grounded historical link is established here.",
        response_model="anthropic/claude-haiku-4.5",
        provider="anthropic/claude-haiku-4.5",
        resolved_coords=["chat-demo:WX-genesis-1"],
        walk_selection_contract={
            "walk_selected_by_autonomy": True,
            "walk_status": "completed",
            "walk_trigger_reasons": ["subject_history_match"],
            "walk_termination_reason": "low_marginal_utility",
        },
        branch_selection_summary={
            "selected_branch": "WX:selected",
            "selected_coords": ["chat-demo:WX-genesis-1"],
            "selection_reason": "subject_history_fallback",
            "subject_history_fallback_used": True,
        },
        intent="answer",
        runtime_actor={"actor_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex"},
    )

    assert identity.get("public_object_kind") == "decision-artifact"
    assert identity.get("publication_state") == "identity_defined_not_published"
    assert identity.get("object_id") == identity.get("untp_hash")
    assert identity.get("public_object_id", "").endswith(identity.get("untp_hash"))
    assert identity.get("coord_bridge", {}).get("coord_exposed_as_primary") is False
    assert identity.get("coord_bridge", {}).get("bridge_state") == "coord_assigned_post_commit"
    canonical_envelope = identity.get("canonical_envelope")
    assert isinstance(canonical_envelope, dict)
    assert canonical_envelope.get("schema") == "dss-decision-artifact-envelope-v1"
    assert canonical_envelope.get("normalized_input", {}).get("intent") == "answer"
    assert canonical_envelope.get("admitted_evidence", {}).get("resolved_coords") == ["chat-demo:WX-genesis-1"]
    boundary = identity.get("identity_boundary")
    assert isinstance(boundary, dict)
    assert "reply_contract" in (boundary.get("included_sections") or [])
    assert "assurance" in (boundary.get("excluded_fields") or [])


def test_decision_artifact_identity_attests_model_wx_as_continuity_context():
    identity = orchestrator_module._build_decision_artifact_identity(
        entity="chat-demo",
        user_message="What has Genesis got to do with AI design?",
        reply_text="Prior model output is continuity context, not user evidence.",
        response_model="mock",
        provider="mock",
        resolved_coords=["chat-demo:WX-9C2621E0-1778237514"],
        walk_selection_contract={},
        branch_selection_summary={},
        intent="answer",
        runtime_actor={},
    )

    admitted = identity.get("canonical_envelope", {}).get("admitted_evidence", {})
    policies = admitted.get("coord_source_policies") or []
    assert policies
    policy = policies[0]
    assert policy.get("coord") == "chat-demo:WX-9C2621E0-1778237514"
    assert policy.get("origin_attestation") == "model_response_wx"
    assert policy.get("evidence_eligible") is False
    assert policy.get("evidence_role") == "continuity_context"
    assert "chat-demo:WX-9C2621E0-1778237514" in admitted.get("continuity_context_coords", [])
    assert "chat-demo:WX-9C2621E0-1778237514" not in admitted.get("grounded_evidence_coords", [])


def test_decision_artifact_identity_marks_explicit_coord_as_grounded_evidence():
    identity = orchestrator_module._build_decision_artifact_identity(
        entity="chat-demo",
        user_message="Open chat-demo:WX-9C2621E0-1778237641",
        reply_text="Explicitly requested prior coord resolved.",
        response_model="mock",
        provider="mock",
        resolved_coords=["chat-demo:WX-9C2621E0-1778237641"],
        walk_selection_contract={},
        branch_selection_summary={},
        intent="answer",
        runtime_actor={},
        explicit_coords=["chat-demo:WX-9C2621E0-1778237641"],
    )

    admitted = identity.get("canonical_envelope", {}).get("admitted_evidence", {})
    policies = admitted.get("coord_source_policies") or []
    assert policies[0].get("origin_attestation") == "explicit_user_referenced_coord"
    assert policies[0].get("evidence_eligible") is True
    assert policies[0].get("evidence_role") == "grounded_evidence"
    assert "chat-demo:WX-9C2621E0-1778237641" in admitted.get("grounded_evidence_coords", [])


def test_branch_selection_summary_includes_selected_wx_when_candidate_trace_is_stale():
    summary = orchestrator_module._build_branch_selection_summary(
        [
            {
                "coord": "chat-demo:ATT-physics-T012",
                "source": "history_search",
                "relevance_score": 0.92,
                "semantic_score": 0.88,
                "recency_score": 0.35,
                "tier_rank": 1,
                "resolved_payload_present": False,
            },
            {
                "coord": "chat-demo:ATT-physics-T013",
                "source": "history_search",
                "relevance_score": 0.9,
                "semantic_score": 0.85,
                "recency_score": 0.34,
                "tier_rank": 1,
                "resolved_payload_present": False,
            },
        ],
        selected_coords=["chat-demo:WX-genesis-1"],
        selected_reason="subject_history_fallback",
        ambiguity_detected=True,
        subject_history_fallback_used=True,
    )

    assert summary.get("selected_branch") == "WX:selected"
    assert (summary.get("candidate_branches_considered") or []) == ["WX:selected"]
    rows = summary.get("candidate_coords_considered") or []
    assert all(row.get("coord_type") == "WX" for row in rows if isinstance(row, dict))
    assert any(
        row.get("coord") == "chat-demo:WX-genesis-1" and row.get("source") == "selected"
        for row in rows
        if isinstance(row, dict)
    )


def test_successful_evidence_walk_promotes_richer_summary_over_check_placeholder(monkeypatch):
    async def _fake_assemble(**_kwargs):
        return {
            "retrieved": [],
            "summary": {
                "text": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose."
            },
        }

    async def _fake_search_any(**_kwargs):
        return {}

    async def _fake_thread(**_kwargs):
        return [
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-1",
                "content": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                "metadata": {
                    "summary": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                    "topics": ["genesis", "ai", "design", "alignment"],
                },
            },
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-2",
                "content": "Stewardship and alignment in Genesis",
                "metadata": {
                    "summary": "Stewardship and alignment in Genesis",
                    "topics": ["stewardship", "genesis", "alignment"],
                },
            },
        ]

    async def _fake_decode_coordinate(coord, **_kwargs):
        return {
            "status": "ok",
            "meta": {},
            "payload": {},
            "skim": f"decoded {coord}",
        }

    async def _fake_select_choice_coord(**_kwargs):
        return ("stop", None, "low confidence")

    async def _fake_commit_answer(**kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": kwargs.get("metadata") or {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any)
    monkeypatch.setattr(orchestrator_module.api, "thread", _fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", _fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_check_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", _fake_select_choice_coord)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "successful-walk-promotes-summary",
            "message": "What has the story of Genesis got to do with AI design in a nutshell? Answer briefly, but prefer historically relevant ledger evidence if available.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "let me check available evidence coordinates first" not in content.lower()
    assert "genesis and ai design connect through ordered differentiation" in content.lower()
    integrity = payload_meta.get("answer_surface_integrity") if isinstance(payload_meta.get("answer_surface_integrity"), dict) else {}
    assert integrity.get("status") == "resolved"
    assert integrity.get("reason") == "evidence_walk_richer_summary_promoted"


def test_successful_evidence_walk_promotes_richer_summary_over_ledger_check_placeholder(monkeypatch):
    async def _fake_assemble(**_kwargs):
        return {
            "retrieved": [],
            "summary": {
                "text": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose."
            },
        }

    async def _fake_search_any(**_kwargs):
        return {}

    async def _fake_thread(**_kwargs):
        return [
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-1",
                "content": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                "metadata": {
                    "summary": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                    "topics": ["genesis", "ai", "design", "alignment"],
                },
            },
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-2",
                "content": "Stewardship and alignment in Genesis",
                "metadata": {
                    "summary": "Stewardship and alignment in Genesis",
                    "topics": ["stewardship", "genesis", "alignment"],
                },
            },
        ]

    async def _fake_decode_coordinate(coord, **_kwargs):
        return {
            "status": "ok",
            "meta": {},
            "payload": {},
            "skim": f"decoded {coord}",
        }

    async def _fake_select_choice_coord(**_kwargs):
        return ("stop", None, "low confidence")

    async def _fake_commit_answer(**kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": kwargs.get("metadata") or {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any)
    monkeypatch.setattr(orchestrator_module.api, "thread", _fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", _fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_ledger_check_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", _fake_select_choice_coord)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "successful-walk-promotes-summary-ledger-placeholder",
            "message": "What has the story of Genesis got to do with AI design in a nutshell? Answer briefly, but prefer historically relevant ledger evidence if available.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "i'll check the ledger for historically relevant evidence on this topic" not in content.lower()
    assert "genesis and ai design connect through ordered differentiation" in content.lower()
    integrity = payload_meta.get("answer_surface_integrity") if isinstance(payload_meta.get("answer_surface_integrity"), dict) else {}
    assert integrity.get("status") == "resolved"
    assert integrity.get("reason") == "evidence_walk_richer_summary_promoted"


def test_successful_evidence_walk_suppresses_placeholder_ledger_summary(monkeypatch):
    async def _fake_assemble(**_kwargs):
        return {
            "retrieved": [],
            "summary": {
                "text": "I'll check the ledger for grounded evidence on this topic."
            },
        }

    async def _fake_search_any(**_kwargs):
        return {}

    async def _fake_thread(**_kwargs):
        return [
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-1",
                "content": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                "metadata": {
                    "summary": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                    "topics": ["genesis", "ai", "design", "alignment"],
                },
            },
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-2",
                "content": "Stewardship and alignment in Genesis",
                "metadata": {
                    "summary": "Stewardship and alignment in Genesis",
                    "topics": ["stewardship", "genesis", "alignment"],
                },
            },
        ]

    async def _fake_decode_coordinate(coord, **_kwargs):
        return {
            "status": "ok",
            "meta": {},
            "payload": {},
            "skim": f"decoded {coord}",
        }

    async def _fake_select_choice_coord(**_kwargs):
        return ("stop", None, "low confidence")

    async def _fake_commit_answer(**kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": kwargs.get("metadata") or {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any)
    monkeypatch.setattr(orchestrator_module.api, "thread", _fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", _fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_ledger_check_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", _fake_select_choice_coord)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "successful-walk-suppresses-placeholder-ledger-summary",
            "message": "What has the story of Genesis got to do with AI design in a nutshell? Answer briefly, but prefer historically relevant ledger evidence if available.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "i'll check the ledger for grounded evidence on this topic" not in content.lower()
    assert "does not ground a reliable answer" in content.lower()
    integrity = payload_meta.get("answer_surface_integrity") if isinstance(payload_meta.get("answer_surface_integrity"), dict) else {}
    assert integrity.get("status") == "resolved"
    assert integrity.get("reason") == "evidence_walk_placeholder_summary_suppressed"


def test_successful_evidence_walk_suppresses_unaligned_richer_summary(monkeypatch):
    async def _fake_assemble(**_kwargs):
        return {
            "retrieved": [],
            "summary": {
                "text": (
                    "This section from a physics textbook presents a unified wave-domain framework for understanding "
                    "long-range forces, Coulomb interactions, and inverse-square laws."
                )
            },
        }

    async def _fake_search_any(**_kwargs):
        return {}

    async def _fake_thread(**_kwargs):
        return [
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-1",
                "content": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                "metadata": {
                    "summary": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                    "topics": ["genesis", "ai", "design", "alignment"],
                },
            },
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-2",
                "content": "Stewardship and alignment in Genesis",
                "metadata": {
                    "summary": "Stewardship and alignment in Genesis",
                    "topics": ["stewardship", "genesis", "alignment"],
                },
            },
        ]

    async def _fake_decode_coordinate(coord, **_kwargs):
        return {
            "status": "ok",
            "meta": {},
            "payload": {},
            "skim": f"decoded {coord}",
        }

    async def _fake_select_choice_coord(**_kwargs):
        return ("stop", None, "low confidence")

    async def _fake_commit_answer(**kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": kwargs.get("metadata") or {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any)
    monkeypatch.setattr(orchestrator_module.api, "thread", _fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", _fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_check_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", _fake_select_choice_coord)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "successful-walk-suppresses-unaligned-summary",
            "message": "What has the story of Genesis got to do with AI design in a nutshell? Answer briefly, but prefer historically relevant ledger evidence if available.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "let me check available evidence coordinates first" not in content.lower()
    assert "does not ground a reliable answer" in content.lower()
    assert "wave-domain framework" not in content.lower()
    integrity = payload_meta.get("answer_surface_integrity") if isinstance(payload_meta.get("answer_surface_integrity"), dict) else {}
    assert integrity.get("status") == "resolved"
    assert integrity.get("reason") == "evidence_walk_unaligned_summary_suppressed"


def test_successful_evidence_walk_promotes_richer_summary_over_governance_signal_placeholder(monkeypatch):
    async def _fake_assemble(**_kwargs):
        return {
            "retrieved": [],
            "summary": {
                "text": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose."
            },
        }

    async def _fake_search_any(**_kwargs):
        return {}

    async def _fake_thread(**_kwargs):
        return [
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-1",
                "content": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                "metadata": {
                    "summary": "Genesis and AI design connect through ordered differentiation, stewardship, alignment drift, and purpose.",
                    "topics": ["genesis", "ai", "design", "alignment"],
                },
            },
            {
                "role": "assistant",
                "coordinate": "chat-demo:WX-genesis-2",
                "content": "Stewardship and alignment in Genesis",
                "metadata": {
                    "summary": "Stewardship and alignment in Genesis",
                    "topics": ["stewardship", "genesis", "alignment"],
                },
            },
        ]

    async def _fake_decode_coordinate(coord, **_kwargs):
        return {
            "status": "ok",
            "meta": {},
            "payload": {},
            "skim": f"decoded {coord}",
        }

    async def _fake_select_choice_coord(**_kwargs):
        return ("stop", None, "low confidence")

    async def _fake_commit_answer(**kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": kwargs.get("metadata") or {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "search_any", _fake_search_any)
    monkeypatch.setattr(orchestrator_module.api, "thread", _fake_thread)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", _fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_governance_signal_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_grounded)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", _fake_select_choice_coord)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "successful-walk-promotes-summary-governance-placeholder",
            "message": "What has the story of Genesis got to do with AI design in a nutshell? Answer briefly, but prefer historically relevant ledger evidence if available.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta_event = next((event for event in reversed(events) if event.get("type") == "meta"), {})
    payload_meta = meta_event.get("metadata") if isinstance(meta_event, dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "signal the governance context" not in content.lower()
    assert "genesis and ai design connect through ordered differentiation" in content.lower()
    integrity = payload_meta.get("answer_surface_integrity") if isinstance(payload_meta.get("answer_surface_integrity"), dict) else {}
    assert integrity.get("status") == "resolved"
    assert integrity.get("reason") == "evidence_walk_richer_summary_promoted"


def test_packed_live_review_filters_blocked_preamble_candidate(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_assemble(**_kwargs):
        return {
            "retrieved": [
                {
                    "coordinate": "chat-demo:WX-blocked",
                    "relevance_score": 0.97,
                    "source": "retrieved",
                    "metadata": {
                        "summary": "I'll signal introspection to gather live governance and runtime field observations for this assessment.",
                        "policy_decision": "block",
                    },
                },
                {
                    "coordinate": "chat-demo:WX-runtime",
                    "relevance_score": 0.74,
                    "source": "retrieved",
                    "metadata": {
                        "summary": "Foundation identity ref and continuity checkpoint are visible.",
                    },
                },
            ],
            "decoded_context": [],
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:WX-runtime":
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "runtime witness"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {
                    "runtime_identity": {
                        "ledger_id": "chat-demo",
                        "library_boundary": {
                            "canonical_ledger_id": "chat-demo",
                            "foundation_identity": {
                                "name": "LOAM",
                                "foundation_identity_ref": "ledger:chat-demo:foundation_identity",
                            },
                            "history_continuity": {
                                "alias_aware_coord_history_lookup": True,
                            },
                        },
                    },
                },
            }
        if coord == "chat-demo:WX-blocked":
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "I'll signal introspection to gather live governance and runtime field observations for this assessment."},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {"policy_decision": "block"},
                "meta": {"governance_error": {"reason": "unspecified"}},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-runtime", "higher_score"

    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "packed-review-filter-blocked-preamble",
            "message": "Assess live DSS Epic 17 on chat-demo under these headings only: payload opacity, foundation identity, retention and gravity, continuity and consolidation, and claim-to-evidence grounding. Cite observable fields only.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    action_plan_events = [event for event in events if event.get("type") == "coord_action_plan"]
    payloads = [event.get("payload") or {} for event in action_plan_events]
    assert any(
        payload.get("action") == "open" and payload.get("coord") == "chat-demo:WX-runtime"
        for payload in payloads
    )
    context_items = [event for event in events if event.get("type") == "context_item"]
    assert any(event.get("coord") == "chat-demo:WX-runtime" for event in context_items)
    assert not any(event.get("coord") == "chat-demo:WX-blocked" for event in context_items)


def test_epic13_review_prefers_runtime_surface_summary_over_skim(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:WX-1772505927152":
            return {
                "coord": coord,
                "type": "WX",
                "skim": {"one_line": "placeholder assessment intent"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {
                    "policy_decision": "block",
                    "reason_code": "runtime_surface_review_allowed",
                    "failed_eq": "eq6_awareness",
                    "repair_actions": ["Provide complete provenance inputs and replay evidence."],
                    "enforced_controls": ["emit_block_envelope_only"],
                },
                "meta": {
                    "governance_error": {"reason": "runtime_surface_review_allowed"},
                    "display_label": "LOAM",
                    "retention_tier": "Clay",
                    "retention_tier_reason": "durable_ledger_write_path",
                    "gravity_tax_policy": {
                        "retention_tier": "Clay",
                        "retention_tier_reason": "durable_ledger_write_path",
                        "retention_tier_assignment": "durable_governed_memory_boundary",
                        "gravity_tax_accrual": "accruing_durable_governance_cost",
                        "retention_decision_state": "durable_keep",
                        "anti_hoarding_posture": "selective_retention_over_silent_accumulation",
                        "explicit_retention_cost_policy": True,
                        "governed_promotion_required": True,
                        "promotion_state": "already_durable",
                        "consolidation_readiness": "ready_when_governed_boundary_requests_merge",
                        "gravity_cost": 0.041,
                        "gravity_penalty": 10.0,
                    },
                    "runtime_identity": {
                        "ledger_id": "chat-demo",
                        "library_boundary": {
                            "canonical_ledger_id": "chat-demo",
                            "hot_path_mode": "summary_only",
                            "foundation_identity": {
                                "name": "LOAM",
                                "purpose": "Hold governed memory.",
                                "source": "control_plane_operator",
                                "rehydration_mode": "founding_constitution",
                                "foundation_identity_ref": "ledger:chat-demo:foundation_identity",
                            },
                            "identity_continuity_witness": {
                                "canonical_ledger_id": "chat-demo",
                                "foundation_identity_available": True,
                                "basis": [
                                    "foundation_identity.name",
                                    "ledger_alias_history",
                                    "ledger_consolidation_history",
                                ],
                            },
                            "alias_history": ["ledger:chat-demo-v1", "ledger:chat-demo-v2"],
                            "supersession_history": ["ledger:chat-demo-legacy"],
                            "ledger_rename_log": ["ledger:loam-137to139"],
                            "latest_consolidation_event": {
                                "event": "ledger_split_consolidated",
                                "reason": "rename_split_cleanup",
                            },
                            "latest_consolidation_event_id": "chat-demo:ledger_split_consolidated:2026-05-04T01:00:13.645664+00:00",
                            "continuity_checkpoint": {
                                "checkpoint_ref": "chat-demo:ledger_split_consolidated:2026-05-04T01:00:13.645664+00:00",
                                "checkpoint_updated_at": "2026-05-04T01:00:13.645664+00:00",
                                "ledger_version": 2,
                            },
                            "async_consolidation_state": "settled_on_canonical_boundary",
                            "canonical_identity_post_consolidation": {
                                "canonical_ledger_id": "chat-demo",
                                "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                                "continuity_survived": True,
                            },
                            "history_continuity": {
                                "alias_aware_coord_history_lookup": True,
                                "surviving_governed_memory_boundary": "chat-demo",
                                "foundation_identity_available_after_consolidation": True,
                            },
                            "latency_boundary": {
                                "hot_path_budgeted": True,
                                "deep_history_requires_fallback_or_deferral": True,
                                "interactive_path": "summary_only_or_skip",
                                "settlement_boundary_ns": "bounded_async_only",
                            },
                        },
                    },
                    "eq6_commit_allowed": True,
                    "eq6_lawfulness_level": 2,
                    "eq6_cw": 1,
                },
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "open_parent"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "epic13-runtime-surface-admission",
            "message": "Assess live DSS Epic 13 on chat-demo using runtime_identity.library_boundary.foundation_identity and history_continuity.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    context_items = [
        event for event in events
        if event.get("type") == "context_item" and event.get("coord") == "chat-demo:WX-1772505927152"
    ]
    assert context_items
    text = str(context_items[-1].get("text") or "")
    assert text.startswith("Epic 13 runtime surfaces:")
    assert "Foundation identity: name=LOAM" in text
    assert "ref=ledger:chat-demo:foundation_identity" in text
    assert "Identity continuity witness: canonical_ledger=chat-demo" in text
    assert "basis=foundation_identity.name,ledger_alias_history,ledger_consolidation_history" in text
    assert "Ledger alias/supersession continuity: aliases=ledger:chat-demo-v1, ledger:chat-demo-v2; superseded=ledger:chat-demo-legacy" in text
    assert "Ledger rename log: ledger:loam-137to139" in text
    assert "Latest consolidation event: id=chat-demo:ledger_split_consolidated:2026-05-04T01:00:13.645664+00:00, event=ledger_split_consolidated, reason=rename_split_cleanup" in text
    assert "Continuity checkpoint: ref=chat-demo:ledger_split_consolidated:2026-05-04T01:00:13.645664+00:00, ledger_version=2, updated_at=2026-05-04T01:00:13.645664+00:00" in text
    assert "Async consolidation state: settled_on_canonical_boundary" in text
    assert "Canonical identity after consolidation: ledger=chat-demo, subject=did:web:id.dualsubstrate.com:ledgers:chat-demo, continuity_survived=True" in text
    assert "Latency boundary: hot_path_budgeted=True, deep_history_requires_fallback_or_deferral=True, interactive_path=summary_only_or_skip, settlement_boundary_ns=bounded_async_only" in text
    assert "History continuity: alias_aware=True" in text
    assert "Retention tier: Clay, durable_ledger_write_path, assignment=durable_governed_memory_boundary" in text
    assert "Gravity tax posture: anti_hoarding=selective_retention_over_silent_accumulation, explicit_retention_cost_policy=True, governed_promotion_required=True" in text
    assert "Retention decision: accrual=accruing_durable_governance_cost, decision=durable_keep, promotion=already_durable, consolidation_readiness=ready_when_governed_boundary_requests_merge" in text
    assert "Gravity cost evidence: gravity_cost=0.041, gravity_penalty=10.0" in text
    assert "Governance block state:" in text
    assert "block_reason=runtime_surface_review_allowed" in text
    assert "failed_eq=eq6_awareness" in text
    assert "repair_actions=Provide complete provenance inputs and replay evidence." in text
    assert "enforced_controls=emit_block_envelope_only" in text
    admitted_events = [event for event in events if event.get("type") == "coord_context_admitted"]
    assert admitted_events
    admitted_payload = admitted_events[-1].get("payload") or {}
    assert admitted_payload.get("admission") == "epic13_runtime_surfaces_with_governance_block"
    assert admitted_payload.get("block_reason") == "runtime_surface_review_allowed"
    assert admitted_payload.get("failed_eq") == "eq6_awareness"
    assert admitted_payload.get("repair_actions") == ["Provide complete provenance inputs and replay evidence."]
    assert admitted_payload.get("enforced_controls") == ["emit_block_envelope_only"]


def test_packed_review_runtime_surface_summary_extracts_from_introspect_snapshot():
    runtime_text = orchestrator_module._extract_packed_review_runtime_surface_summary(
        {
            "runtime_identity": {
                "ledger_id": "chat-demo",
                "library_boundary": {
                    "canonical_ledger_id": "chat-demo",
                    "hot_path_mode": "summary_only",
                    "foundation_identity": {
                        "name": "LOAM",
                        "purpose": "Hold governed memory.",
                        "source": "control_plane_operator",
                        "rehydration_mode": "founding_constitution",
                        "foundation_identity_ref": "ledger:chat-demo:foundation_identity",
                    },
                    "identity_continuity_witness": {
                        "canonical_ledger_id": "chat-demo",
                        "foundation_identity_available": True,
                        "basis": [
                            "foundation_identity.name",
                            "ledger_alias_history",
                        ],
                    },
                    "history_continuity": {
                        "alias_aware_coord_history_lookup": True,
                        "surviving_governed_memory_boundary": "chat-demo",
                        "foundation_identity_available_after_consolidation": True,
                    },
                    "latest_consolidation_event": {
                        "event": "ledger_split_consolidated",
                        "reason": "rename_split_cleanup",
                    },
                    "latest_consolidation_event_id": "chat-demo:ledger_split_consolidated:2026-05-04T01:00:13.645664+00:00",
                    "continuity_checkpoint": {
                        "checkpoint_ref": "chat-demo:ledger_split_consolidated:2026-05-04T01:00:13.645664+00:00",
                        "checkpoint_updated_at": "2026-05-04T01:00:13.645664+00:00",
                        "ledger_version": 2,
                    },
                    "async_consolidation_state": "settled_on_canonical_boundary",
                    "canonical_identity_post_consolidation": {
                        "canonical_ledger_id": "chat-demo",
                        "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                        "continuity_survived": True,
                    },
                    "latency_boundary": {
                        "hot_path_budgeted": True,
                        "deep_history_requires_fallback_or_deferral": True,
                        "interactive_path": "summary_only_or_skip",
                        "settlement_boundary_ns": "bounded_async_only",
                    },
                },
            },
            "display_label": "LOAM",
            "retention_tier": "Clay",
            "retention_tier_reason": "durable_ledger_write_path",
            "gravity_tax_policy": {
                "retention_tier": "Clay",
                "retention_tier_reason": "durable_ledger_write_path",
                "retention_tier_assignment": "durable_governed_memory_boundary",
                "gravity_tax_accrual": "accruing_durable_governance_cost",
                "retention_decision_state": "durable_keep",
                "anti_hoarding_posture": "selective_retention_over_silent_accumulation",
                "explicit_retention_cost_policy": True,
                "governed_promotion_required": True,
                "promotion_state": "already_durable",
                "consolidation_readiness": "ready_when_governed_boundary_requests_merge",
                "gravity_cost": 0.041,
                "gravity_penalty": 10.0,
            },
        }
    )

    assert isinstance(runtime_text, str)
    assert runtime_text.startswith("Epic 13 runtime surfaces:")
    assert "Foundation identity: name=LOAM" in runtime_text
    assert "Retention tier: Clay, durable_ledger_write_path, assignment=durable_governed_memory_boundary" in runtime_text
    assert "Continuity checkpoint: ref=chat-demo:ledger_split_consolidated:2026-05-04T01:00:13.645664+00:00, ledger_version=2, updated_at=2026-05-04T01:00:13.645664+00:00" in runtime_text


def test_packed_review_runtime_witness_marks_opened_resolved_evidence():
    witness = orchestrator_module._build_packed_review_runtime_witness(
        {
            "entity": "chat-demo",
            "runtime_identity": {
                "ledger_id": "chat-demo",
                "library_boundary": {
                    "canonical_ledger_id": "chat-demo",
                    "foundation_identity": {
                        "name": "LOAM",
                        "foundation_identity_ref": "ledger:chat-demo:foundation_identity",
                    },
                    "history_continuity": {
                        "alias_aware_coord_history_lookup": True,
                        "surviving_governed_memory_boundary": "chat-demo",
                    },
                },
            },
            "retention_tier": "Clay",
            "gravity_tax_policy": {
                "retention_tier": "Clay",
                "retention_decision_state": "durable_keep",
            },
        },
        message="Assess Epic 17 on chat-demo under four headings only.",
        compact=False,
    )

    assert isinstance(witness, dict)
    assert witness["coord"] == "runtime:introspect:chat-demo"
    assert witness["evidence_access_state"] == "payload_opened"
    assert witness["payload_opened"] is True
    assert witness["resolved_for_answer"] is True
    assert witness["grounding_eligible"] is True
    text = str(witness["text"])
    assert "Current-turn runtime witness evidence object:" in text
    assert "- evidence_access_state=payload_opened" in text
    assert "- resolved_for_answer=true" in text
    assert "- grounding_eligible=true" in text
    assert "Epic 17 is the assessment rubric for this answer, not a coordinate or payload you must locate." in text
    assert "Use the opened witness fields below to assess Epic 17 directly under the requested headings." in text
    assert "Do not claim that no payload was opened when using it." in text
    assert "Epic 13 runtime surfaces:" in text


def test_ledger_identity_anchor_prompt_gets_current_runtime_witness(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_introspect_runtime(**_kwargs):
        return {
            "entity": "chat-demo",
            "runtime_namespace": "chat-demo",
            "runtime_identity": {
                "ledger_id": "chat-demo",
                "library_boundary": {
                    "canonical_ledger_id": "chat-demo",
                    "foundation_identity": {
                        "name": "LOAM",
                        "purpose": "Hold governed memory.",
                        "source": "control_plane_operator",
                        "rehydration_mode": "founding_constitution",
                        "foundation_identity_ref": "ledger:chat-demo:foundation_identity",
                    },
                    "history_continuity": {
                        "alias_aware_coord_history_lookup": True,
                        "surviving_governed_memory_boundary": "chat-demo",
                        "foundation_identity_available_after_consolidation": True,
                    },
                },
            },
        }

    async def fake_stream_response(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        captured["context"] = kwargs.get("context")
        return await _fake_stream_response(**kwargs)

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)
    _patch_permissive_runtime_actor(monkeypatch)

    _stream_events(
        {
            "session_id": "ledger-identity-anchor-witness",
            "message": (
                "Tell me what ledger you believe this conversation belongs to. Distinguish: "
                "1. canonical ledger identity 2. current ledger display or self-name "
                "3. any foundation identity details available to you."
            ),
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    system_prompt = str(captured.get("system_prompt") or "")
    assert "Ledger identity answer rule" in system_prompt
    assert "Do not substitute provider/model identity for governed ledger identity." in system_prompt

    context = captured.get("context")
    assert isinstance(context, list)
    context_text = "\n".join(str((item or {}).get("text") or "") for item in context if isinstance(item, dict))
    assert "Current-turn runtime witness evidence object:" in context_text
    assert "Ledger identity questions are about the governed ledger, not provider/model identity." in context_text
    assert "Foundation identity: name=LOAM" in context_text
    assert "source=control_plane_operator" in context_text
    assert "Foundation purpose: Hold governed memory." in context_text


def test_lightweight_prompt_skips_retrieval_candidate_resolution(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-lightweight",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "lightweight-no-resolve",
            "message": "reply with exactly lightweight-probe",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )

    candidate_event = next((event for event in events if event.get("type") == "candidate_trace"), {})
    top_k = (((candidate_event.get("payload") or {}) if isinstance(candidate_event, dict) else {}).get("top_k") or [])
    assert top_k == []
    decision_event = next((event for event in events if event.get("type") == "autonomy_decision"), {})
    decision = (decision_event.get("payload") or {}) if isinstance(decision_event, dict) else {}
    assert decision.get("action") == "answer_from_priors"
    opened_coords = [
        ((event.get("payload") or {}).get("coord"))
        for event in events
        if event.get("type") == "coord_action_plan"
    ]
    assert not opened_coords


def test_meta_is_compact_for_non_debug_runs_and_full_for_debug(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-compact-meta",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_assemble_heavy(**_kwargs):
        return {
            "retrieved": [
                {
                    "coordinate": "chat-demo:WX-1772505927152",
                    "relevance_score": 0.96,
                    "snippet": "resolved payload candidate",
                    "source": "retrieved",
                }
            ],
            "decoded_context": ["X" * 800],
            "recent": [{"key": "chat-demo:WX-heavy"}],
            "summary": {"title": "heavy summary"},
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble_heavy)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    normal_events = _stream_events(
        {
            "session_id": "compact-meta-normal",
            "message": "Please resolve recent coord evidence for compact-meta.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )
    normal_meta = [event for event in normal_events if event.get("type") == "meta"][-1]
    normal_assemble = normal_meta.get("assemble") if isinstance(normal_meta.get("assemble"), dict) else {}
    normal_context = normal_meta.get("context") if isinstance(normal_meta.get("context"), list) else []
    normal_decoded = normal_meta.get("decoded_context") if isinstance(normal_meta.get("decoded_context"), list) else []
    assert normal_assemble.get("retrieved_count") == 1
    assert "retrieved" not in normal_assemble
    assert len(normal_decoded) == 1
    assert len(str(normal_decoded[0])) <= 240
    assert len(normal_context) <= 2

    debug_events = _stream_events(
        {
            "session_id": "compact-meta-debug",
            "message": "debug_telemetry: reply with exactly compact-meta",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 1,
        }
    )
    debug_meta = [event for event in debug_events if event.get("type") == "meta"][-1]
    assert isinstance(debug_meta.get("assemble"), dict)
    assert isinstance(debug_meta.get("context"), list)
    assert isinstance(debug_meta.get("decoded_context"), list)


def test_coord_chain_trace_captures_multi_hop_planned_opened_and_admitted(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_assemble_with_attachment_parent(**_kwargs):
        return {
            "retrieved": [
                {
                    "coordinate": "chat-demo:ATT-parent-001",
                    "relevance_score": 0.96,
                    "snippet": "attachment parent",
                    "source": "retrieved",
                }
            ],
            "decoded_context": [],
        }

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-parent-001":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "parent summary"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-child-001",
                            "topics": ["child"],
                            "tags": ["part"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {"claims": [{"label": "parent"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        if coord == "chat-demo:ATT-child-001":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "child summary"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {"claims": [{"label": "child"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-parent-001", "open_parent"
        if call_state["select_calls"] == 2:
            return "open", "chat-demo:ATT-child-001", "open_child"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble_with_attachment_parent)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "coord-chain-trace-multi-hop",
            "message": "open parent then child",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "explicit_walk": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    chain = meta.get("coord_chain_trace") or []
    assert isinstance(chain, list) and len(chain) >= 2
    chain_by_coord = {
        str(item.get("coord")): item for item in chain if isinstance(item, dict) and isinstance(item.get("coord"), str)
    }
    parent = chain_by_coord.get("chat-demo:ATT-parent-001")
    child = chain_by_coord.get("chat-demo:ATT-child-001")
    assert parent is not None
    assert child is not None
    assert parent.get("planned") is True
    assert parent.get("opened") is True
    assert parent.get("admitted") is True
    assert child.get("planned") is True
    assert child.get("opened") is True
    assert child.get("admitted") is True


def test_attachment_answer_promotion_commits_richer_summary(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-parent-001":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "parent summary"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-child-001",
                            "topics": ["child"],
                            "tags": ["part"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {"claims": [{"label": "parent"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        if coord == "chat-demo:ATT-child-001":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "child summary"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {"claims": [{"label": "child"}]},
                "governance": {},
                "meta": {"eq6_commit_allowed": True, "eq6_lawfulness_level": 2, "eq6_cw": 1},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-parent-001", "open_parent"
        if call_state["select_calls"] == 2:
            return "open", "chat-demo:ATT-child-001", "open_child"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_live_attachment_open_placeholder)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-answer-promotion",
            "message": "Open the attachment and summarize it.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    answer_strategy = payload_meta.get("answer_commit_strategy") if isinstance(payload_meta.get("answer_commit_strategy"), dict) else {}
    assert answer_strategy.get("attachment_grounded") is True
    assert answer_strategy.get("promotion_applied") is True
    assert answer_strategy.get("preview_only_commit") is False
    assert answer_strategy.get("summary_source") == "assemble_summary"
    opened_attachment_coords = answer_strategy.get("opened_attachment_coords")
    assert isinstance(opened_attachment_coords, list) and opened_attachment_coords
    assert "Part 4 - There Was Only Ever One Tree" in str(payload_meta.get("content") or "")
    integrity = payload_meta.get("answer_surface_integrity") if isinstance(payload_meta.get("answer_surface_integrity"), dict) else {}
    assert integrity.get("status") == "resolved"
    assert integrity.get("reason") == "attachment_richer_summary_promoted"


def test_non_attachment_identity_prompt_does_not_promote_attachment_summary(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_stream_response(**_kwargs):
        async def _gen():
            yield "I'll retrieve the foundation identity details by examining the introspection signal."

        fut: asyncio.Future = asyncio.Future()
        fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 3, "output": 12}})
        return _gen(), fut

    async def fake_introspect_runtime(**_kwargs):
        return {
            "entity": "chat-demo",
            "runtime_identity": {
                "ledger_id": "chat-demo",
                "library_boundary": {
                    "canonical_ledger_id": "chat-demo",
                    "foundation_identity": {
                        "name": "LOAM",
                        "source": "control_plane_operator",
                        "rehydration_mode": "founding_constitution",
                        "foundation_identity_ref": "ledger:chat-demo:foundation_identity",
                    },
                },
            },
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.api, "introspect_runtime", fake_introspect_runtime)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "ENABLE_INTROSPECT", True)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "identity-prompt-no-attachment-summary-promotion",
            "message": (
                "What foundation identity details are available for this ledger right now? "
                "Separate operator-seeded identity, verified ledger traits, and speculative overlay."
            ),
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "Part 4 - There Was Only Ever One Tree" not in content
    assert "foundation identity details" in content.lower()
    assert "answer_commit_strategy" not in payload_meta
    integrity = payload_meta.get("answer_surface_integrity") if isinstance(payload_meta.get("answer_surface_integrity"), dict) else {}
    assert integrity.get("reason") != "attachment_richer_summary_promoted"


def test_attachment_answer_promotion_ignores_unselected_attachment_family():
    reply, strategy = orchestrator_module._attachment_answer_commit_strategy(
        "Thin preview reply.",
        {
            "summary": {"raw": "Richer assembled attachment summary."},
            "retrieved": [
                {"coord": "chat-demo:ATT-wrong-parent"},
                {"coord": "chat-demo:ATT-wrong-parent-T001"},
            ],
        },
        resolved_coords=[],
        answer_surface_integrity={
            "status": "diverged",
            "reason": "assembly_summary_richer_than_visible_answer",
            "summary_source": "assemble_summary",
        },
        allowed_attachment_parents={"chat-demo:ATT-target-parent"},
    )

    assert reply == "Thin preview reply."
    assert strategy is None


def test_opened_explicit_attachment_denial_rewrites_to_grounded_reply(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_denial)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-denial-grounded-rewrite",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "cannot open, retrieve, or read attachment payloads" not in content.lower()
    strategy = payload_meta.get("answer_commit_strategy") if isinstance(payload_meta.get("answer_commit_strategy"), dict) else {}
    assert strategy.get("attachment_grounded") is True
    assert "Part 4" in content or strategy.get("promotion_applied") is False


def test_opened_explicit_attachment_denial_uses_model_retry_before_fallback(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_denial)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_attachment_repair)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-denial-model-retry",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "transport thresholds" in content
    assert "is accessible and was resolved in this turn" not in content
    assert "cannot open, retrieve, or read attachment payloads" not in content.lower()
    consistency = payload_meta.get("consistency_check") if isinstance(payload_meta.get("consistency_check"), dict) else {}
    assert consistency.get("retry_status") in {"applied", "attachment_retry_applied"}
    attestation = payload_meta.get("payload_read_attestation") if isinstance(payload_meta.get("payload_read_attestation"), dict) else {}
    assert attestation.get("payload_delivered_to_model") is True
    assert attestation.get("model_read_acknowledgment_received") is True
    coord_accounting = payload_meta.get("coord_accounting") if isinstance(payload_meta.get("coord_accounting"), dict) else {}
    assert "chat-demo:ATT-target-parent-T015" in coord_accounting.get("payload_delivered_to_model_coords", [])
    assert coord_accounting.get("payload_delivered_to_model_count") == len(coord_accounting.get("payload_delivered_to_model_coords", []))


def test_opened_attachment_not_opened_phrase_triggers_retry(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_not_opened_denial)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_attachment_repair)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-not-opened-phrase-retry",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "has not been opened" not in content.lower()
    assert "cannot open, retrieve, or read attachment payloads" not in content.lower()
    attestation = payload_meta.get("payload_read_attestation") if isinstance(payload_meta.get("payload_read_attestation"), dict) else {}
    assert attestation.get("payload_delivered_to_model") is True


def test_payload_delivery_attestation_upgrades_grounded_wrapper_to_synthesis(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_denial)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_attachment_wrapper_then_synthesis)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-wrapper-upgraded-to-synthesis",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "is accessible and was resolved in this turn" not in content
    assert "coupled structural variables" in content
    consistency = payload_meta.get("consistency_check") if isinstance(payload_meta.get("consistency_check"), dict) else {}
    assert consistency.get("retry_status") in {"applied", "payload_synthesis_retry_applied"}
    attestation = payload_meta.get("payload_read_attestation") if isinstance(payload_meta.get("payload_read_attestation"), dict) else {}
    assert attestation.get("payload_delivered_to_model") is True
    assert attestation.get("model_read_acknowledgment_received") is True


def test_payload_delivery_attestation_upgrades_attempt_placeholder_to_synthesis(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_attempt_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_attachment_wrapper_then_synthesis)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-placeholder-upgraded-to-synthesis",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "let me attempt to open" not in content.lower()
    assert "coupled structural variables" in content
    attestation = payload_meta.get("payload_read_attestation") if isinstance(payload_meta.get("payload_read_attestation"), dict) else {}
    assert attestation.get("payload_delivered_to_model") is True
    assert attestation.get("model_read_acknowledgment_received") is True


def test_payload_delivery_attestation_upgrades_check_placeholder_to_synthesis(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_check_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_attachment_wrapper_then_synthesis)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-check-placeholder-upgraded-to-synthesis",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "let me check available evidence coordinates first" not in content.lower()
    assert "coupled structural variables" in content
    attestation = payload_meta.get("payload_read_attestation") if isinstance(payload_meta.get("payload_read_attestation"), dict) else {}
    assert attestation.get("payload_delivered_to_model") is True
    assert attestation.get("model_read_acknowledgment_received") is True


def test_payload_read_attestation_does_not_claim_delivery_when_model_reports_preview_only():
    attestation = orchestrator_module._build_payload_read_attestation(
        resolved_coords=["chat-demo:ATT-target-parent-T015"],
        epistemic_status={
            "opened_payload_coords": ["chat-demo:ATT-target-parent-T015"],
            "explicit_targets": ["chat-demo:ATT-target-parent-T015"],
            "explicit_observed": ["chat-demo:ATT-target-parent-T015"],
        },
        model_context_items=[
            {
                "coord": "chat-demo:ATT-target-parent-T015",
                "text": "skim preview only",
            }
        ],
        admitted_context_trace=[
            {
                "coord": "chat-demo:ATT-target-parent-T015",
            }
        ],
        model_attestation={
            "payload_delivered_to_model": True,
            "delivered_coords_seen": ["chat-demo:ATT-target-parent-T015"],
            "model_acknowledged_read": False,
            "used_coords": [],
            "insufficient_payload": True,
            "notes": "Only skim preview fragments were visible to the model.",
        },
    )

    assert attestation.get("coord_resolved") is True
    assert attestation.get("payload_opened") is True
    assert attestation.get("payload_preview_available") is True
    assert attestation.get("payload_delivered_to_model") is False
    assert attestation.get("model_read_acknowledgment_received") is False
    assert attestation.get("payload_used_in_answer") is False
    assert attestation.get("insufficient_payload") is True
    coord_accounting = attestation.get("coord_accounting") if isinstance(attestation.get("coord_accounting"), dict) else {}
    assert coord_accounting.get("payload_delivered_to_model_count") == 0
    assert coord_accounting.get("payload_attested_read_count") == 0


def test_payload_read_attestation_coord_accounting_preserves_origin_policy():
    attestation = orchestrator_module._build_payload_read_attestation(
        resolved_coords=["chat-demo:WX-9C2621E0-1778237514"],
        epistemic_status={
            "opened_payload_coords": ["chat-demo:WX-9C2621E0-1778237514"],
            "explicit_targets": [],
            "explicit_observed": [],
        },
        model_context_items=[
            {
                "text": "[chat-demo:WX-9C2621E0-1778237514] prior assistant answer",
            }
        ],
        admitted_context_trace=[
            {
                "coord": "chat-demo:WX-9C2621E0-1778237514",
            }
        ],
        model_attestation={
            "payload_delivered_to_model": True,
            "delivered_coords_seen": ["chat-demo:WX-9C2621E0-1778237514"],
            "model_acknowledged_read": True,
            "used_coords": ["chat-demo:WX-9C2621E0-1778237514"],
            "insufficient_payload": False,
        },
    )

    coord_accounting = attestation.get("coord_accounting") if isinstance(attestation.get("coord_accounting"), dict) else {}
    policies = coord_accounting.get("coord_source_policies") or []
    assert policies
    policy = policies[0]
    assert policy.get("origin_attestation") == "model_response_wx"
    assert policy.get("evidence_eligible") is False
    assert policy.get("evidence_role") == "continuity_context"
    assert coord_accounting.get("continuity_context_coords") == ["chat-demo:WX-9C2621E0-1778237514"]
    assert coord_accounting.get("evidence_eligible_coords") == []


def test_explicit_attachment_predecode_stop_forces_open_path(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {
                    "segments": [{"id": "ANS-01", "kind": "answer", "blob_ref": "BLOB:ATT-T:ANS-01"}],
                    "blobs": {
                        "BLOB:ATT-T:ANS-01": "Full payload body on consciousness and coupled structural variables."
                    },
                    "parts": [],
                },
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_check_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_attachment_wrapper_then_synthesis)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "explicit-attachment-predecode-stop-forces-open",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    resolved_coords = payload_meta.get("resolved_coords") if isinstance(payload_meta.get("resolved_coords"), list) else []
    assert "chat-demo:ATT-target-parent-T015" in resolved_coords
    content = str(payload_meta.get("content") or "")
    assert "coupled structural variables" in content
    attestation = payload_meta.get("payload_read_attestation") if isinstance(payload_meta.get("payload_read_attestation"), dict) else {}
    assert attestation.get("payload_delivered_to_model") is True
    assert attestation.get("model_read_acknowledgment_received") is True


def test_unread_payload_truth_reply_without_explicit_target_does_not_invent_requested_attachment():
    reply = orchestrator_module._build_unread_attachment_truth_reply(
        explicit_targets=[],
        payload_read_attestation={
            "insufficient_payload": True,
            "payload_delivered_to_model": False,
            "model_read_acknowledgment_received": False,
            "model_attestation_notes": "Selected payloads did not contain relevant Genesis evidence.",
        },
    )

    assert reply is not None
    assert "requested attachment" not in reply.lower()
    assert "selected payload coordinates" in reply
    assert "payload text was not actually delivered to the model" in reply


def test_unread_attachment_placeholder_commits_truth_reply(monkeypatch):
    call_state = {"select_calls": 0}

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_decode_coordinate(coord: str, *, entity: str | None = None, session_id: str | None = None):
        if coord == "chat-demo:ATT-target-parent":
            return {
                "coord": coord,
                "type": "ATT",
                "skim": {"one_line": "target attachment parent"},
                "walk": None,
                "refs": {},
                "payload": {
                    "parts": [
                        {
                            "coord": "chat-demo:ATT-target-parent-T015",
                            "topics": ["consciousness"],
                            "tags": ["attachment"],
                            "tokens_est": 80,
                        }
                    ]
                },
                "interpretation": {},
                "governance": {},
                "meta": {"part_count": 1},
            }
        if coord == "chat-demo:ATT-target-parent-T015":
            return {
                "coord": coord,
                "type": "ATT-T",
                "skim": {"one_line": "consciousness and structural coupling in the attachment"},
                "walk": None,
                "refs": {},
                "payload": {"parts": []},
                "interpretation": {},
                "governance": {},
                "meta": {},
            }
        return await _fake_decode_coordinate(coord, entity=entity, session_id=session_id)

    async def fake_select_choice_coord(**_kwargs):
        call_state["select_calls"] += 1
        if call_state["select_calls"] == 1:
            return "open", "chat-demo:ATT-target-parent", "open_target_parent"
        return "use_priors", None, "stop"

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_rich_attachment_summary)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response_attachment_attempt_placeholder)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response_attachment_unread_attestation)
    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: ["chat-demo:ATT-target-parent-T015"],
    )
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "attachment-unread-truth-reply",
            "message": "Use the specified attachment content.",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    payload_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    content = str(payload_meta.get("content") or "")
    assert "let me attempt to open" not in content.lower()
    assert "payload text was not actually delivered to the model" in content.lower()
    consistency = payload_meta.get("consistency_check") if isinstance(payload_meta.get("consistency_check"), dict) else {}
    assert consistency.get("status") in {"ok", "consistent"}
    attestation = payload_meta.get("payload_read_attestation") if isinstance(payload_meta.get("payload_read_attestation"), dict) else {}
    assert attestation.get("payload_delivered_to_model") is False
    assert attestation.get("insufficient_payload") is True


def test_candidate_trace_preserves_tier_rank_ordering():
    retrieved = [
        {
            "coordinate": "chat-demo:WX-low",
            "relevance_score": 0.55,
            "tier_rank": 1,
            "source": "retrieved",
        },
        {
            "coordinate": "chat-demo:WX-high",
            "relevance_score": 0.91,
            "tier_rank": 3,
            "p_adic_similarity": 0.67,
            "source": "retrieved",
        },
    ]

    trace = orchestrator_module._build_candidate_trace(retrieved)

    assert isinstance(trace, list) and len(trace) == 2
    assert trace[0].get("coord") == "chat-demo:WX-high"
    assert int(trace[0].get("tier_rank") or 0) == 3
    assert trace[0].get("ancestry_linked") is True
    assert trace[0].get("ancestry_score") == 0.67
    assert trace[0].get("origin_attestation") == "model_response_wx"
    assert trace[0].get("evidence_eligible") is False
    assert trace[0].get("evidence_role") == "continuity_context"


def test_candidate_trace_preserves_continuity_source():
    retrieved = [
        {
            "coordinate": "chat-demo:WX-continuity",
            "relevance_score": 0.41,
            "tier_rank": 1,
            "source": "recent",
            "continuity_source": "session_last_coordinate",
        }
    ]

    trace = orchestrator_module._build_candidate_trace(retrieved)

    assert isinstance(trace, list) and len(trace) == 1
    assert trace[0].get("coord") == "chat-demo:WX-continuity"
    assert trace[0].get("continuity_source") == "session_last_coordinate"


def test_candidate_trace_marks_session_opened_payload_as_reuse() -> None:
    retrieved = [
        {
            "coordinate": "chat-demo:ATT-session-parent",
            "relevance_score": 0.88,
            "tier_rank": 3,
            "source": "retrieved",
            "metadata": {"content": "payload text"},
        }
    ]

    trace = orchestrator_module._build_candidate_trace(
        retrieved,
        opened_payload_coords=["chat-demo:ATT-session-parent"],
    )

    assert isinstance(trace, list) and len(trace) == 1
    assert trace[0].get("payload_state") == "already_opened_in_session"
    assert trace[0].get("recommended_action") == "reuse_already_opened"


def test_candidate_trace_suppresses_attachment_parts_by_default() -> None:
    retrieved = [
        {
            "coordinate": "chat-demo:ATT-parent",
            "relevance_score": 0.82,
            "tier_rank": 3,
            "source": "retrieved",
        },
        {
            "coordinate": "chat-demo:ATT-parent-T001",
            "relevance_score": 0.9,
            "tier_rank": 3,
            "source": "retrieved",
        },
    ]

    trace = orchestrator_module._build_candidate_trace(retrieved)

    assert isinstance(trace, list)
    assert [row.get("coord") for row in trace] == ["chat-demo:ATT-parent"]
    assert trace[0].get("coord_type") == "ATT"
    assert trace[0].get("recommended_action") == "open"


def test_autonomy_decision_can_prefer_recent_reuse_path():
    candidate_trace = [
        {
            "coord": "chat-demo:WX-recent",
            "relevance_score": 0.48,
            "tier_rank": 1,
            "resolved_payload_present": False,
            "source": "recent",
        }
    ]

    decision = orchestrator_module._autonomy_decision_from_trace(candidate_trace, "balanced")

    assert isinstance(decision, dict)
    assert decision.get("action") == "reuse_path"


def test_walk_backstop_soft_mode_treats_hop_and_decode_as_pressure():
    result = orchestrator_module._evaluate_walk_backstop(
        dial_policy={"hops_cap": 2, "decode_token_budget_cap": 700, "hard_caps": False},
        next_hop_index=2,
        walk_spent_tokens=900,
        max_tokens_total=1050,
    )

    assert result.get("mode") == "soft_backstop"
    assert result.get("can_continue") is True
    assert result.get("hop_pressure") is True
    assert result.get("decode_pressure") is True
    assert result.get("stop_reason") is None


def test_walk_backstop_hard_mode_stops_on_hop_cap():
    result = orchestrator_module._evaluate_walk_backstop(
        dial_policy={"hops_cap": 1, "decode_token_budget_cap": 700, "hard_caps": True},
        next_hop_index=1,
        walk_spent_tokens=100,
        max_tokens_total=700,
    )

    assert result.get("mode") == "hard_cap"
    assert result.get("can_continue") is False
    assert result.get("hop_pressure") is True
    assert result.get("stop_reason") == "hard_hops_cap"


def test_walk_posture_balance_flags_over_walk_risk():
    result = orchestrator_module._evaluate_walk_posture_balance(
        walk_confidence=0.95,
        confidence_target=0.9,
        utility_per_token=0.000001,
        walk_spent_hops=2,
        law_delta=0.0,
        drift_delta=0.02,
    )

    assert result.get("decision") == "stop"
    assert result.get("reason") == "posture_over_walk_risk"
    assert result.get("over_walk_risk") is True
    assert result.get("under_walk_risk") is False


def test_walk_posture_balance_flags_under_walk_risk():
    result = orchestrator_module._evaluate_walk_posture_balance(
        walk_confidence=0.35,
        confidence_target=0.9,
        utility_per_token=0.01,
        walk_spent_hops=1,
        law_delta=0.15,
        drift_delta=-0.03,
    )

    assert result.get("decision") == "continue"
    assert result.get("reason") == "posture_under_walk_risk"
    assert result.get("over_walk_risk") is False
    assert result.get("under_walk_risk") is True


def test_posture_soft_backstop_preserves_context_under_queue_pressure(monkeypatch):
    captured_kwargs: dict[str, object] = {}

    async def fake_assemble_many(**_kwargs):
        return {
            "retrieved": [
                {
                    "coordinate": "chat-demo:WX-soft-001",
                    "relevance_score": 0.96,
                    "snippet": "alpha-soft-snippet",
                    "source": "retrieved",
                },
                {
                    "coordinate": "chat-demo:WX-soft-002",
                    "relevance_score": 0.88,
                    "snippet": "beta-soft-snippet",
                    "source": "retrieved",
                },
                {
                    "coordinate": "chat-demo:WX-soft-003",
                    "relevance_score": 0.79,
                    "snippet": "gamma-soft-snippet",
                    "source": "retrieved",
                },
            ],
            "decoded_context": [],
        }

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-soft-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_stream_response_capture(**kwargs):
        captured_kwargs.update(kwargs)
        return await _fake_stream_response(**kwargs)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-soft-001", "soft_backstop"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble_many)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", fake_stream_response_capture)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "soft-backstop-context",
            "message": "soft backstop should preserve context breadth",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "eq9_control_dial": 2,
            "k": 3,
        }
    )

    context_meta = [event for event in events if event.get("type") == "context_meta"][-1]
    posture_state = context_meta.get("posture_backstop_state") or {}
    assert posture_state.get("mode") == "soft_backstop"
    assert posture_state.get("context_pressure") is True
    posture_status = next(
        (
            event for event in events
            if event.get("type") == "ui_status" and ((event.get("payload") or {}).get("stage") == "posture_backstop")
        ),
        None,
    )
    assert posture_status is not None
    assert "preserving breadth" in str((posture_status.get("payload") or {}).get("message") or "")

    llm_context = captured_kwargs.get("context")
    assert isinstance(llm_context, list)
    rendered = "\n".join(str(item.get("text") or "") for item in llm_context if isinstance(item, dict))
    assert "alpha-soft-snippet" in rendered
    assert "beta-soft-snippet" in rendered
    assert "gamma-soft-snippet" in rendered

    meta = [event for event in events if event.get("type") == "meta"][-1]
    meta_posture = meta.get("posture_backstop_state") or {}
    assert meta_posture.get("mode") == "soft_backstop"
    assert meta_posture.get("context_pressure") is True


def test_hard_cap_queue_truncates_context(monkeypatch):
    captured_kwargs: dict[str, object] = {}

    async def fake_assemble_many(**_kwargs):
        return {
            "retrieved": [
                {
                    "coordinate": "chat-demo:WX-hard-001",
                    "relevance_score": 0.96,
                    "snippet": "alpha-hard-snippet",
                    "source": "retrieved",
                },
                {
                    "coordinate": "chat-demo:WX-hard-002",
                    "relevance_score": 0.88,
                    "snippet": "beta-hard-snippet",
                    "source": "retrieved",
                },
                {
                    "coordinate": "chat-demo:WX-hard-003",
                    "relevance_score": 0.79,
                    "snippet": "gamma-hard-snippet",
                    "source": "retrieved",
                },
            ],
            "decoded_context": [],
        }

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-hard-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    async def fake_stream_response_capture(**kwargs):
        captured_kwargs.update(kwargs)
        return await _fake_stream_response(**kwargs)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-hard-001", "hard_cap"

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble_many)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", fake_stream_response_capture)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "hard-cap-context",
            "message": "hard cap should truncate context breadth",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "eq9_control_dial": 3,
            "k": 3,
        }
    )

    context_meta = [event for event in events if event.get("type") == "context_meta"][-1]
    posture_state = context_meta.get("posture_backstop_state") or {}
    assert posture_state.get("mode") == "hard_cap"
    assert posture_state.get("queue_pressure") is False
    posture_status = next(
        (
            event for event in events
            if event.get("type") == "ui_status" and ((event.get("payload") or {}).get("stage") == "posture_backstop")
        ),
        None,
    )
    assert posture_status is not None
    assert "strict limit active" in str((posture_status.get("payload") or {}).get("message") or "")

    llm_context = captured_kwargs.get("context")
    assert isinstance(llm_context, list)
    rendered = "\n".join(str(item.get("text") or "") for item in llm_context if isinstance(item, dict))
    assert "alpha-hard-snippet" not in rendered
    assert "beta-hard-snippet" not in rendered
    assert "gamma-hard-snippet" not in rendered

    meta = [event for event in events if event.get("type") == "meta"][-1]
    meta_posture = meta.get("posture_backstop_state") or {}
    assert meta_posture.get("mode") == "hard_cap"
    assert meta_posture.get("context_pressure") is True


def test_coord_catalog_includes_prime_multiplicative_metadata(monkeypatch):
    captured_kwargs: dict[str, object] = {}

    async def fake_decode_coordinate(_coord: str, *, entity: str | None = None, session_id: str | None = None):
        return {
            "coord": "chat-demo:WX-1772505927152",
            "type": "WX",
            "skim": {"one_line": "candidate"},
            "walk": None,
            "refs": {},
            "payload": {"parts": []},
            "interpretation": {"claims": [{"label": "prime-aware"}]},
            "governance": {},
            "meta": {
                "prime_multiplicative_value": 7420738134810,
                "body_prime": 101,
                "token_primes": [29, 31, 37],
                "taxonomy_topology_ref": "visual",
                "taxonomy_mode": "indefeasible",
                "configurational_foresight": {
                    "quality": "favourable",
                    "advisory_score": 0.82,
                    "advisory_only": True,
                    "veto_allowed": False,
                },
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_cw": 1,
            },
        }

    async def fake_stream_response_capture(**kwargs):
        captured_kwargs.update(kwargs)
        return await _fake_stream_response(**kwargs)

    async def fake_select_choice_coord(**_kwargs):
        return "open", "chat-demo:WX-1772505927152", "prime_metadata"

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module, "_select_choice_coord", fake_select_choice_coord)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", fake_stream_response_capture)
    _patch_permissive_runtime_actor(monkeypatch)

    events = _stream_events(
        {
            "session_id": "coord-prime-meta-contract",
            "message": "resolve latest coord",
            "history": [],
            "provider": "openai",
            "agent": "mock",
            "enable_ledger": True,
            "k": 2,
        }
    )

    meta = [event for event in events if event.get("type") == "meta"][-1]
    coord_catalog = meta.get("coord_catalog") if isinstance(meta.get("coord_catalog"), list) else []
    assert coord_catalog
    coord_meta = coord_catalog[0].get("coord_meta") if isinstance(coord_catalog[0], dict) else {}
    assert isinstance(coord_meta, dict)
    assert coord_meta.get("prime_multiplicative_value") == 7420738134810
    assert coord_meta.get("body_prime") == 101
    assert coord_meta.get("token_primes") == [29, 31, 37]
    assert coord_meta.get("prime_semantics") == {
        "kind": "token_prime_product",
        "decode_requires": "token_prime_mapping",
        "warning": "not_direct_mmf_kernel_encoding",
    }
    assert coord_meta.get("configurational_foresight", {}).get("advisory_score") == 0.82
    assert coord_meta.get("foresight_semantics") == {
        "kind": "advisory_configurational_foresight",
        "advisory_only": True,
        "veto_allowed": False,
        "warning": "informational_weight_only_not_policy_stop",
    }
    llm_signals = captured_kwargs.get("signals")
    assert isinstance(llm_signals, list)
    coord_catalog_signal = next((item for item in llm_signals if isinstance(item, dict) and item.get("kind") == "coord_catalog"), {})
    entries = coord_catalog_signal.get("entries") if isinstance(coord_catalog_signal, dict) else []
    assert isinstance(entries, list) and entries
    assert (entries[0].get("coord_meta") or {}).get("prime_multiplicative_value") == 7420738134810
    assert (entries[0].get("coord_meta") or {}).get("prime_semantics") == {
        "kind": "token_prime_product",
        "decode_requires": "token_prime_mapping",
        "warning": "not_direct_mmf_kernel_encoding",
    }
    assert ((entries[0].get("coord_meta") or {}).get("configurational_foresight") or {}).get("quality") == "favourable"
    assert ((entries[0].get("coord_meta") or {}).get("foresight_semantics") or {}).get("advisory_only") is True



def test_thinking_trace_retention_prunes_to_turn_cap(monkeypatch):
    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo-session:WX-commit",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(orchestrator_module, "THINKING_TRACE_RETENTION_TURNS", 2)
    monkeypatch.setattr(orchestrator_module, "THINKING_TRACE_RETENTION_SECONDS", 3600)

    session_id = "thinking-trace-retention-cap"
    request_ids = ["req-retention-1", "req-retention-2", "req-retention-3"]
    for request_id in request_ids:
        _stream_events(
            {
                "session_id": session_id,
                "request_id": request_id,
                "message": f"trace retention {request_id}",
                "history": [],
                "provider": "openai",
                "agent": "mock",
                "enable_ledger": True,
                "k": 1,
            }
        )

    with client.stream(
        "GET",
        f"/api/thinking_trace/stream?session_id={session_id}&replay=1&once=1",
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    replay_events = [json.loads(line) for line in lines]
    replay_payloads = [item.get("payload") for item in replay_events if item.get("type") == "thinking_trace"]
    replay_request_ids = {
        str(item.get("request_id"))
        for item in replay_payloads
        if isinstance(item, dict) and item.get("request_id")
    }

    assert "req-retention-1" not in replay_request_ids
    assert "req-retention-2" in replay_request_ids
    assert "req-retention-3" in replay_request_ids



def test_thinking_trace_emit_endpoint_and_replay():
    session_id = "thinking-trace-emit-replay"
    request_id = "req-emit-replay-1"

    response = client.post(
        "/api/thinking_trace/emit",
        json={
            "session_id": session_id,
            "request_id": request_id,
            "type": "process_started",
            "status": "in_progress",
            "step_code": "REQ_ACCEPTED",
            "step_label": "Request accepted",
            "details": {"source": "test"},
        },
    )
    assert response.status_code == 200
    emitted = response.json()
    assert emitted.get("request_id") == request_id
    assert emitted.get("type") == "process_started"

    with client.stream(
        "GET",
        f"/api/thinking_trace/stream?session_id={session_id}&replay=1&once=1",
    ) as stream_response:
        assert stream_response.status_code == 200
        lines = [line for line in stream_response.iter_lines() if line]

    events = [json.loads(line) for line in lines]
    traces = [event.get("payload") for event in events if event.get("type") == "thinking_trace"]
    traces = [event for event in traces if isinstance(event, dict)]
    assert any(item.get("request_id") == request_id for item in traces)


def test_qp_pure_disabled_returns_400(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator_module.settings, "QP_PURE_ENABLED", False)

    with client.stream(
        "POST",
        "/api/orchestrator",
        json={"message": "test qp_pure disabled", "qp_pure": True, "_stream_passthrough": True},
    ) as response:
        assert response.status_code == 400
        body = b"".join(response.iter_bytes())
        assert b"qp_pure is not enabled" in body


def test_qp_pure_enabled_reaches_orchestration(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator_module.settings, "QP_PURE_ENABLED", True)
    _patch_permissive_runtime_actor(monkeypatch)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-qp-pure-test",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)

    events = _stream_events(
        {
            "message": "test qp_pure enabled",
            "qp_pure": True,
            "p_adic_scope": ["qp_retrieval", "p_adic_ball_read"],
            "history": [],
            "provider": "openai",
            "agent": "mock",
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events, "Expected at least one meta event"
    assert meta_events[-1].get("coordinate") == "chat-demo:WX-qp-pure-test"


async def _fake_assemble_qp_pure_signal_split(**_kwargs):
    return {
        "retrieved": [
            {
                "coordinate": "chat-demo:needle",
                "p_adic_score": 0.2,
                "search_score": 0.9,
                "recency_score": 0.1,
                "relevance_score": 0.7,
                "snippet": "candidate with strong semantic/search signal but weak p-adic signal",
                "source": "retrieved",
            },
        ],
        "decoded_context": [],
    }


def test_qp_pure_selects_qp_retrieval_route(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator_module.settings, "QP_PURE_ENABLED", True)
    _patch_permissive_runtime_actor(monkeypatch)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-qp-pure-route",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_with_candidates)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)

    events = _stream_events(
        {
            "message": "test qp_pure route",
            "qp_pure": True,
            "p_adic_scope": ["qp_retrieval", "p_adic_ball_read"],
            "session_id": "session-qp-pure-route",
            "entity": "chat-demo",
            "history": [],
            "provider": "openai",
            "agent": "mock",
        }
    )

    meta_events = [event for event in events if event.get("type") == "meta"]
    assert meta_events, "Expected at least one meta event"
    assert meta_events[-1].get("router_decision", {}).get("route") == "qp_retrieval"


def test_qp_pure_disables_mixed_signal_fallbacks(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator_module.settings, "QP_PURE_ENABLED", True)
    _patch_permissive_runtime_actor(monkeypatch)

    async def fake_commit_answer(**_kwargs):
        return {
            "status": "success",
            "coordinate": "chat-demo:WX-qp-pure-fallback",
            "metadata": {},
            "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
            "blocked": False,
        }

    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble_qp_pure_signal_split)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)

    events = _stream_events(
        {
            "message": "test qp_pure mixed signal disabled coordinate",
            "qp_pure": True,
            "p_adic_scope": ["qp_retrieval", "p_adic_ball_read"],
            "session_id": "session-qp-pure-mixed",
            "entity": "chat-demo",
            "history": [],
            "provider": "openai",
            "agent": "mock",
        }
    )

    trace_events = [event for event in events if event.get("type") == "candidate_trace"]
    assert trace_events, "Expected candidate_trace event"
    top_k = trace_events[0].get("payload", {}).get("top_k", [])
    assert top_k, "Expected non-empty top_k"
    row = top_k[0]
    # In pure mode the weak p-adic signal controls tiering/skip, not the strong search signal.
    assert row.get("p_adic_score") == 0.2
    assert row.get("relevance_tier") == 4
    assert row.get("skip_reason") == "insufficient_p_adic_search_recency_signal"
