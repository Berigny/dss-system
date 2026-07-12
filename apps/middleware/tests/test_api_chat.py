
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from app import app
from utils.principal_registry import PrincipalRegistry

client = TestClient(app)

def test_api_chat_endpoint():
    response = client.post(
        "/api/chat",
        json={
            "session_id": "test",
            "message": "hello",
            "history": [],
            "provider": "llama3.2:latest",
            "agent": "llama3.2:latest",
            "backend_stream": True,
            "enable_ledger": True,
        },
    )
    assert response.status_code == 200
    assert "reply" in response.json()
    assert "stats" in response.json()


def test_api_chat_smart_stream(monkeypatch, tmp_path: Path):
    import app as app_module

    captured: dict[str, object] = {}

    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    principal = app_module.PRINCIPAL_REGISTRY.upsert(
        principal_did="did:key:z6MkExample",
        tenant_id="tenant:demo",
        metadata={"actor_type": "model", "vc_status": "bound"},
    )
    assert principal.get("principal_did") == "did:key:z6MkExample"
    app_module.PRINCIPAL_REGISTRY.bind_key_ref(
        principal_did="did:key:z6MkExample",
        principal_key_ref="ollama:model:llama3.2:latest",
        tenant_id="tenant:demo",
    )

    async def fake_assemble(**_kwargs):
        return {"retrieved": []}

    async def fake_generate_response(**_kwargs):
        return {"text": "fallback response", "model": "mock", "tokens": {"input": 1}}

    def _make_lines():
        return [
            '{"type":"token","content":"Hello"}',
            '{"type":"meta","model":"mock","posture_policy":{"policy_gate_version":"policy-gate-v1","policy_decision":"allow","reason_code":"baseline_satisfied"},"query_integrity":{"source_tier":"hot","staleness_ms":0,"integrity_status":"verified","witness_status":"not_attested","reconstruction_path":"live_stream"}}',
        ]

    class DummyStreamResponse:
        def __init__(self, status_code: int, lines: list[str]):
            self.status_code = status_code
            self._lines = lines
            self.text = ""

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aread(self):
            return b""

        async def aclose(self):
            pass

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def build_request(self, method, url, *, params=None, json=None, headers=None):
            return httpx.Request(method, url, params=params, json=json, headers=headers)

        async def send(self, request, *, stream=False):
            return DummyStreamResponse(200, _make_lines())

        async def post(self, url, json=None, headers=None, timeout=None):
            captured["method"] = "POST"
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return DummyStreamResponse(200, _make_lines())

    monkeypatch.setattr(app_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(app_module.llm, "generate_response", fake_generate_response)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "test",
            "message": "hello",
            "history": [],
            "provider": "llama3.2:latest",
            "agent": "llama3.2:latest",
            "backend_stream": True,
            "enable_ledger": True,
            "principal_did": "did:key:z6MkExample",
            "principal_key_id": "key-1",
            "session_jti": "sess-abc",
            "context_id": "ctx:test",
            "tenant_id": "tenant:demo",
        },
        headers={
            "Authorization": "Bearer opaque-session-token",
            "x-principal-id": "legacy-user",
            "x-principal-type": "user",
        },
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert lines
    events = [json.loads(line) for line in lines]
    token_indices = [idx for idx, event in enumerate(events) if event.get("type") == "token"]
    status_indices = [idx for idx, event in enumerate(events) if event.get("type") == "status"]
    assert token_indices


def test_passkey_register_start_adds_origin_and_rp_id(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    async def fake_auth_backend_post(path: str, payload: dict[str, object]):
        captured["path"] = path
        captured["payload"] = payload
        return 200, {"status": "ok"}

    monkeypatch.setattr(app_module, "_auth_backend_post", fake_auth_backend_post)

    response = client.post(
        "/api/auth/passkey/register/start",
        json={"principal_did": "did:key:z6MkPasskeyUser"},
        headers={"x-forwarded-host": "id.dualsubstrate.com", "x-forwarded-proto": "https"},
    )

    assert response.status_code == 200
    assert captured.get("path") == "/auth/register/challenge"
    payload = captured.get("payload")
    assert isinstance(payload, dict)
    assert payload.get("principal_did") == "did:key:z6MkPasskeyUser"
    assert payload.get("origin") == "http://testserver"
    assert payload.get("rp_id") == "dualsubstrate.com"


def test_passkey_login_finish_sets_shared_cookies(monkeypatch):
    import app as app_module

    async def fake_auth_backend_post(path: str, payload: dict[str, object]):
        assert path == "/auth/verify"
        return 200, {
            "status": "ok",
            "principal_did": "did:key:z6MkPasskeyUser",
            "session": {
                "token": "opaque-token",
                "principal_did": "did:key:z6MkPasskeyUser",
                "jti": "sess-123",
            },
        }

    monkeypatch.setattr(app_module, "_auth_backend_post", fake_auth_backend_post)

    response = client.post(
        "/api/auth/passkey/login/finish",
        json={"challenge_id": "cid"},
        headers={"x-forwarded-host": "chat.dualsubstrate.com", "x-forwarded-proto": "https"},
    )

    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert any("Domain=.dualsubstrate.com" in item for item in cookies)
    assert any("ds_backend_session_token=opaque-token" in item for item in cookies)
    assert any("ds_principal_did=did:key:z6MkPasskeyUser" in item for item in cookies)


def test_identity_card_accepts_forwarded_session_token_header(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    async def fake_auth_backend_get(path: str, headers: dict[str, str] | None = None):
        captured["path"] = path
        captured["headers"] = dict(headers or {})
        return 200, {
            "verified": True,
            "principal_did": "did:key:z6MkWalletUser",
            "auth_method": "wallet_verified_id",
            "session_jti": "sess-wallet-1",
        }

    monkeypatch.setattr(app_module, "_auth_backend_get", fake_auth_backend_get)

    response = client.get(
        "/api/auth/identity_card",
        headers={"x-session-token": "forwarded-wallet-token"},
    )

    assert response.status_code == 200
    assert captured.get("path") == "/auth/session/verify"
    headers = captured.get("headers")
    assert isinstance(headers, dict)
    assert headers.get("x-session-token") == "forwarded-wallet-token"
    identity_vc = response.json().get("identity_vc") or {}
    assert identity_vc.get("verified") is True
    assert identity_vc.get("principal_did") == "did:key:z6MkWalletUser"


def test_resolve_runtime_actor_uses_registry_binding_and_standing(tmp_path: Path) -> None:
    import app as app_module
    from routes import orchestrator as orchestrator_module

    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    app_module.PRINCIPAL_REGISTRY.upsert(
        principal_did="did:key:z6MkBoundActor",
        tenant_id="tenant:demo",
        display_name="David Berigny",
        metadata={"actor_type": "model", "vc_status": "verified"},
    )
    app_module.PRINCIPAL_REGISTRY.bind_key_ref(
        principal_did="did:key:z6MkBoundActor",
        principal_key_ref="openrouter:model:anthropic/claude-3.7-sonnet",
        tenant_id="tenant:demo",
    )
    app_module.PRINCIPAL_REGISTRY.append_standing_event(
        principal_did="did:key:z6MkBoundActor",
        event_type="sanction",
        issuer="deterministic:eq9",
        reason_code="eq_blocked:eq9_telos",
        delta={"trust_class": "T0", "posture_class": "P0"},
        evidence_refs=["coord:WX-1"],
        idempotency_key="evt-001",
        standing_envelope_ref="env:test-bound",
    )

    actor_resolution, standing_envelope = orchestrator_module._resolve_runtime_actor(
        payload={"tenant_id": "tenant:demo"},
        auth_claims=None,
        provider="anthropic/claude-3.7-sonnet",
        agent="anthropic/claude-3.7-sonnet",
    )

    assert actor_resolution.get("actor_did") == "did:key:z6MkBoundActor"
    assert actor_resolution.get("principal_display_name") == "David Berigny"
    assert actor_resolution.get("canonical_subject") == "openrouter:model:anthropic/claude-3.7-sonnet"
    assert actor_resolution.get("verification_state") == "verified"
    assert standing_envelope.get("principal_display_name") == "David Berigny"
    assert standing_envelope.get("standing_envelope_ref") == "env:test-bound"
    assert standing_envelope.get("canonical_subject_source") == "binding:openrouter:model"
    assert standing_envelope.get("trust_class") == "T0"
    assert standing_envelope.get("posture_class") == "P0"
    assert standing_envelope.get("tool_scope") == "none"
    assert standing_envelope.get("retrieval_scope") == "none"
    assert standing_envelope.get("write_commit_allowed") is False


def test_answer_surface_integrity_marks_richer_assembly_summary() -> None:
    from routes import orchestrator as orchestrator_module

    integrity = orchestrator_module._answer_surface_integrity(
        "I'll send an introspection signal to assess the live DSS Epic 12 state on chat-demo.",
        {
            "summary": {
                "raw": "Clearly working now: delegated provenance is explicit. Partially working now: ancestry continuity is still weak. Not yet proved: later-turn explicit continuity."
            }
        },
    )

    assert isinstance(integrity, dict)
    assert integrity.get("status") == "diverged"
    assert integrity.get("reason") == "assembly_summary_richer_than_visible_answer"
    assert "introspection signal" in str(integrity.get("visible_answer_preview") or "")
    assert "Clearly working now" in str(integrity.get("committed_summary_preview") or "")


def test_answer_surface_integrity_marks_preamble_collapse_under_blocked_context() -> None:
    from routes import orchestrator as orchestrator_module

    integrity = orchestrator_module._answer_surface_integrity(
        "I'll ground this in observable fields from the current runtime state.",
        {"summary": {"raw": "short skim summary only"}},
        admitted_context_trace=[
            {
                "coord": "chat-demo:WX-1",
                "admission": "governance_block_state",
                "preview_state": "skim_only_preview",
                "block_reason": "unspecified",
            }
        ],
        resolved_coords=["chat-demo:WX-1"],
    )

    assert isinstance(integrity, dict)
    assert integrity.get("status") == "collapsed"
    assert integrity.get("reason") == "visible_answer_preamble_collapse_under_blocked_context"
    assert integrity.get("resolved_coord_count") == 1
    assert "skim_only_preview" in list(integrity.get("preview_states") or [])


def test_build_autonomy_evidence_marks_single_prior_decode() -> None:
    from routes import orchestrator as orchestrator_module

    evidence = orchestrator_module._build_autonomy_evidence(
        resolved_coords=["chat-demo:WX-1"],
        context_stream_items=[{"coord": "chat-demo:WX-1", "text": "opened payload"}],
        opened_coords={"chat-demo:WX-1"},
        walk_ids=[],
        walk_trace_coords=[],
        child_coord_count=0,
    )

    assert evidence.get("coord_access_state") == "payload_opened"
    assert evidence.get("traversal_state") == "single_coord_decode"
    assert evidence.get("used_prior_coordinates") is True


def test_build_autonomy_evidence_marks_walk_for_multi_coord_open() -> None:
    from routes import orchestrator as orchestrator_module

    evidence = orchestrator_module._build_autonomy_evidence(
        resolved_coords=["chat-demo:WX-1", "chat-demo:WX-2"],
        context_stream_items=[
            {"coord": "chat-demo:WX-1", "text": "opened one"},
            {"coord": "chat-demo:WX-2", "text": "opened two"},
        ],
        opened_coords={"chat-demo:WX-1", "chat-demo:WX-2"},
        walk_ids=["chat-demo:EV-WALK-1"],
        walk_trace_coords=["chat-demo:WX-1", "chat-demo:WX-2"],
        child_coord_count=0,
        explicit_traversal_requested=True,
        requested_traversal_steps=1,
        requested_traversal_max_opened_coords=2,
        effective_traversal_opened_coords=2,
    )

    assert evidence.get("traversal_state") == "walk"
    assert evidence.get("traversed_coord_count") == 2
    assert evidence.get("traversal_bound_status") == "honored"
    assert evidence.get("requested_traversal_steps") == 1
    assert evidence.get("requested_traversal_max_opened_coords") == 2
    assert evidence.get("effective_traversal_opened_coords") == 2
    assert evidence.get("traversal_refusal_reason") is None


def test_build_autonomy_evidence_marks_traversal_refusal_when_explicit_request_stays_single_decode() -> None:
    from routes import orchestrator as orchestrator_module

    evidence = orchestrator_module._build_autonomy_evidence(
        resolved_coords=["chat-demo:WX-1"],
        context_stream_items=[{"coord": "chat-demo:WX-1", "text": "opened payload"}],
        opened_coords={"chat-demo:WX-1"},
        walk_ids=[],
        walk_trace_coords=[],
        child_coord_count=0,
        explicit_traversal_requested=True,
        traversal_refusal_reason="traversal_not_selected",
        requested_traversal_steps=1,
        requested_traversal_max_opened_coords=2,
        effective_traversal_opened_coords=1,
    )

    assert evidence.get("traversal_state") == "single_coord_decode"
    assert evidence.get("traversal_bound_status") == "tightened"
    assert evidence.get("traversal_refusal_reason") == "traversal_not_selected"


def test_answer_surface_integrity_marks_autonomy_self_report_contradiction() -> None:
    from routes import orchestrator as orchestrator_module

    integrity = orchestrator_module._answer_surface_integrity(
        "No. I did not open any coordinates from previous turns. Yes. I used current-turn runtime witness only.",
        {"summary": {"raw": "short summary"}},
        autonomy_evidence={
            "coord_access_state": "payload_opened",
            "traversal_state": "single_coord_decode",
            "used_prior_coordinates": True,
        },
    )

    assert isinstance(integrity, dict)
    assert integrity.get("status") == "contradicted"
    assert integrity.get("reason") == "visible_answer_contradicts_persisted_autonomy_evidence"
    reasons = list(integrity.get("contradiction_reasons") or [])
    assert "claims_no_prior_coordinates_opened" in reasons
    assert "claims_current_turn_only_despite_prior_coordinate_use" in reasons


def test_resolve_runtime_actor_allows_architect_profile_despite_probationary_defaults(tmp_path: Path) -> None:
    import app as app_module
    from routes import orchestrator as orchestrator_module

    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    app_module.PRINCIPAL_REGISTRY.upsert(
        principal_did="did:key:z6MkArchitect",
        tenant_id="tenant:demo",
        metadata={"actor_type": "service", "vc_status": "bound"},
    )
    app_module.PRINCIPAL_REGISTRY.append_standing_event(
        principal_did="did:key:z6MkArchitect",
        event_type="trust_adjustment",
        issuer="operator:architect",
        reason_code="architect_testing_mode",
        delta={
            "trust_class": "T3",
            "posture_class": "P3",
            "operator_profile": "architect",
            "probation_status": "cleared",
        },
        evidence_refs=["ticket:architect-1"],
        idempotency_key="evt-architect-001",
        standing_envelope_ref="env:test-architect",
    )

    actor_resolution, standing_envelope = orchestrator_module._resolve_runtime_actor(
        payload={
            "tenant_id": "tenant:demo",
            "principal_did": "did:key:z6MkArchitect",
            "session_jti": "sess-architect",
        },
        auth_claims={"principal_did": "did:key:z6MkArchitect", "session_jti": "sess-architect"},
        provider="google/gemini-2.5-flash",
        agent="google/gemini-2.5-flash",
    )

    assert actor_resolution.get("actor_did") == "did:key:z6MkArchitect"
    assert actor_resolution.get("verification_state") == "bound_unverified"
    assert standing_envelope.get("operator_profile") == "architect"
    assert standing_envelope.get("probation_status") is None
    assert standing_envelope.get("tool_scope") == "full"
    assert standing_envelope.get("retrieval_scope") == "tenant"
    assert standing_envelope.get("write_commit_allowed") is True


def test_resolve_runtime_actor_allows_architect_profile_for_model_binding(tmp_path: Path) -> None:
    import app as app_module
    from routes import orchestrator as orchestrator_module

    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    app_module.PRINCIPAL_REGISTRY.upsert(
        principal_did="did:key:z6MkGuardianModel",
        tenant_id="tenant:demo",
        metadata={"actor_type": "model", "vc_status": "bound"},
    )
    app_module.PRINCIPAL_REGISTRY.bind_key_ref(
        principal_did="did:key:z6MkGuardianModel",
        principal_key_ref="openrouter:model:google/gemini-2.5-flash",
        tenant_id="tenant:demo",
    )
    app_module.PRINCIPAL_REGISTRY.append_standing_event(
        principal_did="did:key:z6MkGuardianModel",
        event_type="trust_adjustment",
        issuer="operator:architect",
        reason_code="guardian_testing_mode",
        delta={
            "trust_class": "T3",
            "posture_class": "P3",
            "operator_profile": "architect",
            "probation_status": "cleared",
        },
        evidence_refs=["ticket:guardian-1"],
        idempotency_key="evt-guardian-architect-001",
        standing_envelope_ref="env:test-guardian-architect",
    )

    actor_resolution, standing_envelope = orchestrator_module._resolve_runtime_actor(
        payload={"tenant_id": "tenant:demo"},
        auth_claims=None,
        provider="google/gemini-2.5-flash",
        agent="google/gemini-2.5-flash",
    )

    assert actor_resolution.get("actor_did") == "did:key:z6MkGuardianModel"
    assert actor_resolution.get("verification_state") == "bound_unverified"
    assert standing_envelope.get("operator_profile") == "architect"
    assert standing_envelope.get("probation_status") is None
    assert standing_envelope.get("tool_scope") == "full"
    assert standing_envelope.get("retrieval_scope") == "tenant"
    assert standing_envelope.get("write_commit_allowed") is True


def test_api_chat_smart_stream_disables_ledger_and_retrieval_when_standing_denies(monkeypatch, tmp_path: Path):
    import app as app_module

    captured: dict[str, object] = {"assemble_called": False}

    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    app_module.PRINCIPAL_REGISTRY.upsert(
        principal_did="did:key:z6MkDenied",
        tenant_id="tenant:demo",
        metadata={"actor_type": "model", "vc_status": "verified"},
    )
    app_module.PRINCIPAL_REGISTRY.bind_key_ref(
        principal_did="did:key:z6MkDenied",
        principal_key_ref="ollama:model:llama3.2:latest",
        tenant_id="tenant:demo",
    )
    app_module.PRINCIPAL_REGISTRY.append_standing_event(
        principal_did="did:key:z6MkDenied",
        event_type="sanction",
        issuer="deterministic:eq9",
        reason_code="eq_blocked:eq9_telos",
        delta={"trust_class": "T0", "posture_class": "P0"},
        evidence_refs=["coord:WX-2"],
        idempotency_key="evt-denied-001",
        standing_envelope_ref="env:test-denied",
    )

    async def fake_assemble(**_kwargs):
        captured["assemble_called"] = True
        return {"retrieved": []}

    class DummyStreamResponse:
        def __init__(self, status_code: int, lines: list[str]):
            self.status_code = status_code
            self._lines = lines
            self.text = ""

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aread(self):
            return b""

        async def aclose(self):
            pass

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def build_request(self, method, url, *, params=None, json=None, headers=None):
            return httpx.Request(method, url, params=params, json=json, headers=headers)

        async def send(self, request, *, stream=False):
            return DummyStreamResponse(200, ['{"type":"meta","model":"mock"}'])

        async def post(self, url, json=None, headers=None, timeout=None):
            captured["json"] = json
            lines = ['{"type":"meta","model":"mock"}']
            return DummyStreamResponse(200, lines)

    monkeypatch.setattr(app_module.api, "assemble", fake_assemble)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "test-denied",
            "message": "hello",
            "history": [],
            "provider": "llama3.2:latest",
            "agent": "llama3.2:latest",
            "backend_stream": True,
            "_stream_passthrough": True,
            "enable_ledger": True,
            "principal_did": "did:key:z6MkDenied",
            "context_id": "ctx:test",
            "tenant_id": "tenant:demo",
        },
        headers={"Authorization": "Bearer opaque-session-token"},
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert lines
    assert captured.get("assemble_called") is False
    outbound_payload = captured.get("json") if isinstance(captured.get("json"), dict) else {}
    assert outbound_payload.get("enable_ledger") is False
    metadata = outbound_payload.get("metadata") if isinstance(outbound_payload.get("metadata"), dict) else {}
    policy_controls = metadata.get("policy_controls") if isinstance(metadata.get("policy_controls"), dict) else {}
    assert policy_controls.get("standing_retrieval_allowed") is False
    rejected = policy_controls.get("rejected_overrides") if isinstance(policy_controls.get("rejected_overrides"), list) else []
    assert "standing_write_commit_denied" in rejected
    assert "standing_retrieval_scope_denied" in rejected


def test_decode_coordinate_v2_key_order(monkeypatch):
    import app as app_module

    async def fake_decode_coordinate(_coord: str):
        return {
            "coord": "chat-demo-session:WX-123",
            "type": "WX",
            "skim": {"one_line": "hi", "relevance": 0.9, "reasons": [], "recommended": [], "budgets": {}},
            "walk": None,
            "refs": {},
            "payload": {},
            "interpretation": {},
            "governance": {},
            "meta": {},
        }

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    response = client.post("/api/decode_coordinate", json={"coordinate": "WX-123"})
    assert response.status_code == 200
    body = response.text
    keys = [
        '"coord"',
        '"type"',
        '"skim"',
        '"walk"',
        '"refs"',
        '"payload"',
        '"interpretation"',
        '"governance"',
        '"meta"',
    ]
    positions = [body.find(key) for key in keys]
    assert all(pos >= 0 for pos in positions)
    assert positions == sorted(positions)


def test_api_chat_commit_answer_forwards_payload(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    async def fake_commit_answer(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(app_module.api, "commit_answer", fake_commit_answer)

    response = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-demo:principal:test",
            "ledger_id": "ledger:test",
            "message": "hello",
            "reply": "world",
            "metadata": {"source": "frontend"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert captured["entity"] == "chat-demo:principal:test"
    assert captured["message"] == "hello"
    assert captured["reply"] == "world"


def test_openai_compat_rejects_unprivileged_policy_overrides(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    async def fake_orchestrator(*, base_payload, model, message, history, session_id):
        captured["base_payload"] = base_payload
        return {
            "assistant_text": "ok",
            "response_model": model,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }

    monkeypatch.setattr(app_module, "_run_openai_via_middleware_orchestrator", fake_orchestrator)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "llama3.2:latest",
            "messages": [{"role": "user", "content": "hello"}],
            "enable_ledger": False,
            "s_mode": "s1",
        },
    )
    assert response.status_code == 200
    base_payload = captured.get("base_payload") if isinstance(captured.get("base_payload"), dict) else {}
    metadata = base_payload.get("metadata") if isinstance(base_payload.get("metadata"), dict) else {}
    policy_controls = metadata.get("policy_controls") if isinstance(metadata.get("policy_controls"), dict) else {}
    rejected = policy_controls.get("rejected_overrides") if isinstance(policy_controls.get("rejected_overrides"), list) else []

    assert base_payload.get("enable_ledger") is True
    assert base_payload.get("s_mode") == "s2"
    assert policy_controls.get("override_authorized") is False
    assert "enable_ledger_disabled_by_client" in rejected
    assert "s1_mode_requested_by_client" in rejected


def test_openai_compat_allows_authorized_policy_overrides(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    async def fake_orchestrator(*, base_payload, model, message, history, session_id):
        captured["base_payload"] = base_payload
        return {
            "assistant_text": "ok",
            "response_model": model,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }

    monkeypatch.setattr(app_module, "_run_openai_via_middleware_orchestrator", fake_orchestrator)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "llama3.2:latest",
            "messages": [{"role": "user", "content": "hello"}],
            "enable_ledger": False,
            "s_mode": "s1",
            "principal_did": "did:key:test-user",
            "session_jti": "sess-123",
        },
        headers={"Authorization": "Bearer opaque-session-token"},
    )
    assert response.status_code == 200
    base_payload = captured.get("base_payload") if isinstance(captured.get("base_payload"), dict) else {}
    metadata = base_payload.get("metadata") if isinstance(base_payload.get("metadata"), dict) else {}
    policy_controls = metadata.get("policy_controls") if isinstance(metadata.get("policy_controls"), dict) else {}
    rejected = policy_controls.get("rejected_overrides") if isinstance(policy_controls.get("rejected_overrides"), list) else []

    assert base_payload.get("enable_ledger") is False
    assert base_payload.get("s_mode") == "s1"
    assert policy_controls.get("override_authorized") is True
    assert rejected == []


def test_build_model_auth_context_item_prefers_verified_metadata():
    from routes import orchestrator as orchestrator_module

    payload = {
        "principal_did": "did:key:unverified",
        "session_jti": "sess-unverified",
        "metadata": {
            "model_auth_context": {
                "identity_vc": {
                    "principal_did": "did:key:verified-user",
                    "session_jti": "sess-verified",
                    "verification_state": "verified",
                    "auth_method": "passkey",
                    "reason_code": "verified",
                },
                "eq9": {
                    "trust_class": "T2",
                    "eq9_posture_class": "P2",
                    "reason_code": "baseline_satisfied",
                },
            }
        },
    }
    item = orchestrator_module._build_model_auth_context_item(
        payload=payload,
        auth_claims={"principal_did": "did:key:claims-user", "session_jti": "sess-claims"},
    )
    assert isinstance(item, dict)
    text = str(item.get("text") or "")
    assert "actor_did=did:key:verified-user" in text
    assert "principal_did=did:key:verified-user" in text
    assert "verification_state=verified" in text
    assert "session_jti=sess-verified" in text
    assert "auth_method=passkey" in text
    assert "trust_class=T2" in text
    assert "eq9_posture_class=P2" in text


def test_build_model_auth_context_item_includes_runtime_metadata_fields():
    from routes import orchestrator as orchestrator_module

    payload = {
        "metadata": {
            "model_auth_context": {
                "identity_vc": {
                    "principal_did": "did:key:verified-user",
                    "principal_display_name": "David Berigny",
                    "principal_type": "user",
                    "principal_status": "active",
                    "session_jti": "sess-verified",
                    "verification_state": "verified",
                    "auth_method": "github",
                    "operator_profile": "architect",
                },
                "eq9": {
                    "trust_class": "T3",
                    "eq9_posture_class": "P3",
                },
                "standing_envelope": {
                    "standing_envelope_ref": "env:architect:user",
                    "operator_profile": "architect",
                    "tool_scope": "full",
                    "retrieval_scope": "tenant",
                    "max_output_tokens": 4096,
                    "write_commit_allowed": True,
                },
            }
        },
    }

    item = orchestrator_module._build_model_auth_context_item(
        payload=payload,
        auth_claims=None,
        history_len=2,
        turn_count=13,
        query_integrity_source_tier="live",
    )

    assert isinstance(item, dict)
    text = str(item.get("text") or "")
    assert "principal_display_name=David Berigny" in text
    assert "principal_type=user" in text
    assert "principal_status=active" in text
    assert "operator_profile=architect" in text
    assert "standing_envelope_ref=env:architect:user" in text
    assert "query_integrity.source_tier=live" in text
    assert "context_window.history_len=2" in text
    assert "context_window.turn_count=13" in text



def test_api_chat_smart_stream_blocked_turn_no_token_leak(monkeypatch):
    import app as app_module

    class DummyStreamResponse:
        def __init__(self, status_code: int, lines: list[str]):
            self.status_code = status_code
            self._lines = lines
            self.text = ""

        async def aiter_lines(self):
            for line in self._lines:
                yield line

        async def aread(self):
            return b""

        async def aclose(self):
            pass

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def build_request(self, method, url, *, params=None, json=None, headers=None):
            return httpx.Request(method, url, params=params, json=json, headers=headers)

        async def send(self, request, *, stream=False):
            lines = [
                '{"type":"status","message":"Inhale (Assemble)…"}',
                '{"type":"pre_emission_deny","reason":"eq_blocked:eq9_telos"}',
                '{"type":"policy_envelope","payload":{"policy_decision":"deny","reason_code":"eq_blocked:eq9_telos","failed_eq":"eq9_telos","repair_actions":["improve grounding"],"trust_class":"T0","eq9_posture_class":"P0"}}',
                '{"type":"meta","posture_policy":{"policy_decision":"deny","reason_code":"eq_blocked:eq9_telos"}}',
            ]
            return DummyStreamResponse(200, lines)

        async def post(self, url, json=None, headers=None, timeout=None):
            lines = [
                '{"type":"status","message":"Inhale (Assemble)…"}',
                '{"type":"pre_emission_deny","reason":"eq_blocked:eq9_telos"}',
                '{"type":"policy_envelope","payload":{"policy_decision":"deny","reason_code":"eq_blocked:eq9_telos","failed_eq":"eq9_telos","repair_actions":["improve grounding"],"trust_class":"T0","eq9_posture_class":"P0"}}',
                '{"type":"meta","posture_policy":{"policy_decision":"deny","reason_code":"eq_blocked:eq9_telos"}}',
            ]
            return DummyStreamResponse(200, lines)

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "blocked-turn-no-leak",
            "message": "hello",
            "history": [],
            "provider": "llama3.2:latest",
            "agent": "llama3.2:latest",
            "backend_stream": True,
            "_stream_passthrough": True,
            "enable_ledger": True,
        },
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    events = [json.loads(line) for line in lines]
    assert any(event.get("type") == "pre_emission_deny" for event in events)
    assert any(event.get("type") == "policy_envelope" for event in events)
    assert not any(event.get("type") == "token" for event in events)



def test_api_chat_smart_stream_timeout_emits_normalized_policy_envelope(monkeypatch):
    import app as app_module

    class _TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _TimeoutClient())

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "smart-timeout",
            "message": "hello",
            "history": [],
            "provider": "llama3.2:latest",
            "agent": "llama3.2:latest",
            "backend_stream": True,
            "_stream_passthrough": True,
            "enable_ledger": True,
        },
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]

    envelope = next((e.get("payload") for e in events if e.get("type") == "policy_envelope"), None)
    assert isinstance(envelope, dict)
    assert envelope.get("policy_gate_version") == "policy-gate-v1"
    assert envelope.get("pp_version") == "pp-v1"
    assert envelope.get("cb_version") == "cb-v1"
    assert envelope.get("obs_posture_version") == "obs-posture-v1"
    assert envelope.get("policy_decision") == "deny"
    assert envelope.get("reason_code") == "upstream_timeout"
    meta_event = next((e for e in events if e.get("type") == "meta"), {})
    query_integrity = meta_event.get("query_integrity") if isinstance(meta_event, dict) else {}
    assert isinstance(query_integrity, dict)
    assert query_integrity.get("reconstruction_path") == "fallback_proxy"
    assert query_integrity.get("integrity_status") == "unknown"



def test_principal_provisioning_summary_reports_pending_wallet_proof(tmp_path: Path):
    import app as app_module

    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    app_module.PRINCIPAL_REGISTRY.upsert(
        principal_did="did:key:z6MkProvisioning",
        tenant_id="tenant:unknown",
        metadata={
            "actor_type": "human",
            "wallet_capable": True,
            "wallet_issuance_state": "issued_in_wallet",
            "profile_approval_state": "approved_pending_wallet_proof",
            "provisioning_state": "pending_wallet_proof",
        },
    )

    response = client.get("/api/principals/did:key:z6MkProvisioning/provisioning")

    assert response.status_code == 200
    provisioning = response.json()["provisioning"]
    assert provisioning["activation_state"] == "pending_wallet_proof"
    assert provisioning["ledger_access_ready"] is False
    assert provisioning["next_action"] == "complete_wallet_proof"


def test_verified_id_presentation_finalization_sets_pending_provisioning(tmp_path: Path):
    import app as app_module

    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    principal = app_module.PRINCIPAL_REGISTRY.upsert(
        principal_did="did:key:z6MkVerifiedWallet",
        tenant_id="tenant:unknown",
        metadata={
            "actor_type": "human",
            "wallet_capable": True,
            "profile_approval_state": "approved_pending_wallet_proof",
            "wallet_issuance_state": "issued_in_wallet",
            "wallet_proof_state": "pending_presentation_verification",
            "pending_wallet_did": "did:web:id.dualsubstrate.com:wallet:user-1",
            "pending_wallet_binding_ref": "wallet-binding:1",
            "pending_credential_ref": "cred:wallet:user-1",
        },
    )
    app_module.PRINCIPAL_REGISTRY.append_standing_event(
        principal_did="did:key:z6MkVerifiedWallet",
        event_type="trust_adjustment",
        issuer="operator:dss",
        reason_code="manual_approval_ready",
        delta={"trust_class": "T2", "posture_class": "P2", "operator_profile": "member"},
        idempotency_key="approval-ready-1",
        credential_ref="cred:wallet:user-1",
        standing_envelope_ref="env:member:user",
    )
    app_module.VERIFIED_ID_REQUESTS.create(
        state="vid_test_state_1",
        request_id="req-123",
        principal_did=principal["principal_did"],
        mode="presentation",
        request_payload={"requestedCredentials": [{"type": "VerifiedCredential"}]},
        response_payload={"requestId": "req-123"},
    )

    response = client.post(
        "/api/webhooks/entra/verified-id",
        json={
            "requestStatus": "presentation_verified",
            "state": "vid_test_state_1",
            "subject": "did:web:id.dualsubstrate.com:wallet:user-1",
            "verifiedCredentialsData": [{"claims": {"firstName": "User", "lastName": "One"}}],
        },
    )

    assert response.status_code == 200
    updated = app_module.PRINCIPAL_REGISTRY.get("did:key:z6MkVerifiedWallet")
    assert isinstance(updated, dict)
    metadata = updated.get("metadata") if isinstance(updated.get("metadata"), dict) else {}
    assert metadata.get("wallet_proof_state") == "verified"
    assert metadata.get("provisioning_state") == "pending_provisioning"

    provisioning = client.get("/api/principals/did:key:z6MkVerifiedWallet/provisioning").json()["provisioning"]
    assert provisioning["activation_state"] == "pending_provisioning"
    assert provisioning["ledger_access_ready"] is False
    assert provisioning["wallet_ready"] is True


def test_principal_provisioning_update_activates_ledger_assignment(tmp_path: Path):
    import app as app_module

    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    app_module.PRINCIPAL_REGISTRY.upsert(
        principal_did="did:key:z6MkActivation",
        tenant_id="tenant:unknown",
        metadata={
            "actor_type": "human",
            "wallet_capable": True,
            "wallet_proof_state": "verified",
            "profile_approval_state": "approved_wallet_verified",
            "wallet_binding_ref": "wallet-binding:2",
            "credential_ref": "cred:wallet:user-2",
            "provisioning_state": "pending_provisioning",
        },
    )

    response = client.post(
        "/api/principals/did:key:z6MkActivation/provisioning",
        json={
            "tenant_id": "tenant:acme",
            "ledger_id": "ledger:acme:user-2",
            "provisioning_state": "active",
            "notification_state": "sent",
            "authority_evidence_ref": "dia:acme:user-2",
        },
    )

    assert response.status_code == 200
    provisioning = response.json()["provisioning"]
    assert provisioning["activation_state"] == "active"
    assert provisioning["ledger_access_ready"] is True
    assert provisioning["ledger_id"] == "ledger:acme:user-2"
    assert provisioning["tenant_id"] == "tenant:acme"
    assert provisioning["authority_evidence_ref"] == "dia:acme:user-2"


def test_api_chat_smart_stream_request_error_emits_normalized_policy_envelope(monkeypatch):
    import app as app_module

    class _ErrorClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("boom", request=httpx.Request("POST", "http://example"))

    monkeypatch.setattr(app_module.httpx, "AsyncClient", lambda *args, **kwargs: _ErrorClient())

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "smart-request-error",
            "message": "hello",
            "history": [],
            "provider": "llama3.2:latest",
            "agent": "llama3.2:latest",
            "backend_stream": True,
            "_stream_passthrough": True,
            "enable_ledger": True,
        },
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]

    envelope = next((e.get("payload") for e in events if e.get("type") == "policy_envelope"), None)
    assert isinstance(envelope, dict)
    assert envelope.get("policy_gate_version") == "policy-gate-v1"
    assert envelope.get("pp_version") == "pp-v1"
    assert envelope.get("cb_version") == "cb-v1"
    assert envelope.get("obs_posture_version") == "obs-posture-v1"
    assert envelope.get("policy_decision") == "deny"
    assert envelope.get("reason_code") == "upstream_request_error"
    meta_event = next((e for e in events if e.get("type") == "meta"), {})
    query_integrity = meta_event.get("query_integrity") if isinstance(meta_event, dict) else {}
    assert isinstance(query_integrity, dict)
    assert query_integrity.get("reconstruction_path") == "fallback_proxy"


def test_identity_card_surfaces_canonical_subject_and_standing_refs(monkeypatch):
    import app as app_module

    async def fake_auth_backend_get(path: str, headers: dict[str, str] | None = None):
        return 200, {
            "verified": True,
            "principal_did": "did:key:z6MkWalletUser",
            "auth_method": "wallet_verified_id",
            "session_jti": "sess-wallet-1",
        }

    monkeypatch.setattr(app_module, "_auth_backend_get", fake_auth_backend_get)
    monkeypatch.setattr(
        app_module,
        "PRINCIPAL_REGISTRY",
        {
            "did:key:z6MkWalletUser": {
                "display_name": "Wallet User",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:wallet-user",
                "canonical_subject_source": "did:web:principal",
                "tenant_id": "tenant:test",
                "metadata": {"issuer_did": "did:web:id.dualsubstrate.com"},
                "standing_view": {
                    "credential_ref": "cred:wallet:user",
                    "standing_envelope_ref": "env:wallet:user",
                },
            }
        },
    )

    response = client.get("/api/auth/identity_card", headers={"x-session-token": "forwarded-wallet-token"})
    assert response.status_code == 200
    identity_vc = response.json().get("identity_vc") or {}
    assert identity_vc.get("canonical_subject") == "did:web:id.dualsubstrate.com:principals:wallet-user"
    assert identity_vc.get("canonical_subject_source") == "did:web:principal"
    assert identity_vc.get("standing_envelope_ref") == "env:wallet:user"


def test_decode_coordinate_preserves_coord_meta(monkeypatch):
    import app as app_module

    async def fake_decode_coordinate(_coord: str):
        return {
            "coord": "chat-demo:WX-123",
            "type": "WX",
            "skim": {"one_line": "hi", "relevance": 0.9, "reasons": [], "recommended": [], "budgets": {}},
            "walk": None,
            "refs": {},
            "payload": {},
            "interpretation": {},
            "governance": {},
            "meta": {
                "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                "runtime_namespace": "chat-demo",
                "coord_type": "WX",
            },
        }

    monkeypatch.setattr(app_module.api, "decode_coordinate", fake_decode_coordinate)

    response = client.post("/api/decode_coordinate", json={"coordinate": "WX-123"})
    assert response.status_code == 200
    body = response.json()
    meta = body.get("meta") or {}
    assert meta.get("canonical_subject") == "did:web:id.dualsubstrate.com:ledgers:chat-demo"
    assert meta.get("runtime_namespace") == "chat-demo"
