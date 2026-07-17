from __future__ import annotations

import json

from fastapi.testclient import TestClient

import fastapi_app as fastapi_app_module
import app as app_module


def test_fastapi_wrapper_proxies_github_principal_link_routes(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    async def fake_start(request):
        return {"status": "ok", "route": "start"}

    async def fake_verify(request):
        return {"status": "ok", "route": "verify"}

    monkeypatch.setattr(fastapi_app_module, "start_github_principal_link", fake_start)
    monkeypatch.setattr(fastapi_app_module, "verify_github_principal_link", fake_verify)

    start = client.post("/api/principals/link/github/start", json={"github_user_id": "123"})
    verify = client.post("/api/principals/link/github/verify", json={"challenge_id": "abc", "code": "123456"})

    assert start.status_code == 200
    assert start.json() == {"status": "ok", "route": "start"}
    assert verify.status_code == 200
    assert verify.json() == {"status": "ok", "route": "verify"}


def test_fastapi_wrapper_proxies_principal_resolve_and_bindings(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    class _Resp:
        def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

    calls: list[tuple[str, str]] = []

    async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
        calls.append((method, path))
        return _Resp(200, b'{"status":"ok"}')

    monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

    resolve = client.get("/api/principals/resolve", params={"key_ref": "openrouter:model:anthropic/claude"})
    bind = client.post("/api/principals/did:key:z6MkExample/bindings", json={"binding_ref": "mcp:server:planner"})

    assert resolve.status_code == 200
    assert bind.status_code == 200
    assert ("GET", "/api/principals/resolve") in calls
    assert ("POST", "/api/principals/did:key:z6MkExample/bindings") in calls


def test_fastapi_wrapper_proxies_control_plane_routes(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    class _Resp:
        def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

    calls: list[tuple[str, str]] = []

    async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
        calls.append((method, path))
        return _Resp(200, b'{"status":"ok"}')

    monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

    assert client.get("/api/control-plane/ledgers").status_code == 200
    assert client.post("/api/control-plane/ledgers", json={"ledger_id": "ledger:test"}).status_code == 200
    assert client.get("/api/control-plane/submissions").status_code == 200
    assert client.post("/api/control-plane/submissions/cps%3Atest/review", json={"action": "approve"}).status_code == 200
    assert client.get("/api/control-plane/providers").status_code == 200
    assert client.post("/api/control-plane/providers", json={"provider_id": "provider:openrouter:shared", "provider_type": "OpenRouter"}).status_code == 200
    assert client.get("/api/control-plane/model-bindings").status_code == 200
    assert client.post("/api/control-plane/model-bindings", json={"binding_id": "binding:chat:default", "provider_type": "OpenRouter", "model_id": "openai/gpt-4o"}).status_code == 200
    assert client.get("/api/control-plane/principals").status_code == 200
    assert client.post("/api/control-plane/principals", json={"principal_did": "did:key:z6MkTest"}).status_code == 200
    assert client.post("/api/control-plane/principals/codex/provision", json={"ledger_id": "chat-demo"}).status_code == 200
    assert client.post("/api/control-plane/principals/did:key:z6MkTest/status", json={"status": "disabled"}).status_code == 200
    assert client.get("/api/control-plane/surfaces").status_code == 200
    assert client.post("/api/control-plane/surfaces", json={"surface_id": "surface:test"}).status_code == 200
    assert client.get("/api/control-plane/relationships").status_code == 200
    assert (
        client.post(
            "/api/control-plane/relationships",
            json={
                "subject_entity_type": "principal",
                "subject_entity_id": "did:key:z6MkTest",
                "object_entity_type": "ledger",
                "object_entity_id": "ledger:test",
            },
        ).status_code
        == 200
    )
    assert client.post("/api/control-plane/entities/activate", json={"entity_type": "ledger", "entity_id": "ledger:test"}).status_code == 200

    assert ("GET", "/api/control-plane/ledgers") in calls
    assert ("POST", "/api/control-plane/ledgers") in calls
    assert ("GET", "/api/control-plane/submissions") in calls
    assert ("POST", "/api/control-plane/submissions/cps:test/review") in calls
    assert ("GET", "/api/control-plane/providers") in calls
    assert ("POST", "/api/control-plane/providers") in calls
    assert ("GET", "/api/control-plane/model-bindings") in calls
    assert ("POST", "/api/control-plane/model-bindings") in calls
    assert ("GET", "/api/control-plane/principals") in calls
    assert ("POST", "/api/control-plane/principals") in calls
    assert ("POST", "/api/control-plane/principals/codex/provision") in calls
    assert ("POST", "/api/control-plane/principals/did:key:z6MkTest/status") in calls
    assert ("GET", "/api/control-plane/surfaces") in calls
    assert ("POST", "/api/control-plane/surfaces") in calls
    assert ("GET", "/api/control-plane/relationships") in calls
    assert ("POST", "/api/control-plane/relationships") in calls
    assert ("POST", "/api/control-plane/entities/activate") in calls


def test_fastapi_wrapper_proxies_account_current_routes(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    class _Resp:
        def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

    calls: list[tuple[str, str]] = []

    async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
        calls.append((method, path))
        return _Resp(200, b'{"status":"ok"}')

    monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

    assert client.get("/account/current/model-library").status_code == 200
    assert client.post("/account/current/model-library/select", json={"provider": "openrouter"}).status_code == 200
    assert client.get("/account/current/principals").status_code == 200
    assert client.post("/account/current/principals/agent/bootstrap", json={"idempotency_key": "bootstrap-001"}).status_code == 200
    assert client.get("/account/current/connections").status_code == 200

    assert ("GET", "/account/current/model-library") in calls
    assert ("POST", "/account/current/model-library/select") in calls
    assert ("GET", "/account/current/principals") in calls
    assert ("POST", "/account/current/principals/agent/bootstrap") in calls
    assert ("GET", "/account/current/connections") in calls


def test_fastapi_wrapper_preserves_delegated_principal_for_smart_stream(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)
    captured: dict[str, object] = {}

    async def fake_stream_from_legacy(request, method: str, path: str, *, json_payload):
        captured["method"] = method
        captured["path"] = path
        captured["json_payload"] = json_payload
        return fastapi_app_module.Response(
            content=json.dumps({"status": "ok"}).encode("utf-8"),
            media_type="application/json",
        )

    monkeypatch.setattr(fastapi_app_module, "_stream_from_legacy", fake_stream_from_legacy)

    delegated = {
        "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
        "principal_key_id": "openai:agent:codex",
        "principal_type": "agent",
        "explicit_cli_request": True,
        "delegated_by_principal_did": "did:key:z6MkDelegator",
        "ledger_scope": ["chat-demo"],
        "surface_scope": ["surface:chat:primary"],
    }
    payload = {
        "message": "Hello from Codex",
        "provider": "anthropic/claude-haiku-4.5",
        "agent": "anthropic/claude-haiku-4.5",
        "model": "anthropic/claude-haiku-4.5",
        "entity": "chat-demo",
        "session_id": "codex-cli-test",
        "include_pipeline_events": True,
        "include_post_introspect_snapshot": True,
        "delegated_principal": delegated,
    }

    response = client.post("/api/chat/smart_stream", json=payload)

    assert response.status_code == 200
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/chat/smart_stream"
    forwarded = captured["json_payload"]
    assert isinstance(forwarded, dict)
    assert forwarded["delegated_principal"] == delegated
    assert forwarded["entity"] == "chat-demo"
    assert forwarded["include_pipeline_events"] is True


def test_fastapi_wrapper_preserves_prompt_principal_mode_for_smart_stream(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)
    captured: dict[str, object] = {}

    async def fake_stream_from_legacy(request, method: str, path: str, *, json_payload):
        captured["method"] = method
        captured["path"] = path
        captured["json_payload"] = json_payload
        return fastapi_app_module.Response(
            content=json.dumps({"status": "ok"}).encode("utf-8"),
            media_type="application/json",
        )

    monkeypatch.setattr(fastapi_app_module, "_stream_from_legacy", fake_stream_from_legacy)

    payload = {
        "message": "resolve loam:WX-A71BA232-1784174248",
        "provider": "anthropic",
        "agent": "anthropic/claude-haiku-4.5",
        "model": "anthropic/claude-haiku-4.5",
        "entity": "loam",
        "ledger_id": "loam",
        "session_id": "prompt-mode-smoke",
        "include_pipeline_events": True,
        "prompt_principal_mode": "codex",
    }

    response = client.post("/api/chat/smart_stream", json=payload)

    assert response.status_code == 200
    forwarded = captured["json_payload"]
    assert isinstance(forwarded, dict)
    assert forwarded.get("prompt_principal_mode") == "codex"
    assert forwarded.get("entity") == "loam"


def test_fastapi_wrapper_proxies_principal_subject_events(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    class _Resp:
        def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

    calls: list[tuple[str, str]] = []

    async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
        calls.append((method, path))
        return _Resp(200, b'{"status":"ok"}')

    monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

    get_resp = client.get("/api/principals/did:key:z6MkExample/subject/events")
    post_resp = client.post(
        "/api/principals/did:key:z6MkExample/subject/events",
        json={"event_type": "subject_reset_requested"},
    )

    assert get_resp.status_code == 200
    assert post_resp.status_code == 200
    assert ("GET", "/api/principals/did:key:z6MkExample/subject/events") in calls
    assert ("POST", "/api/principals/did:key:z6MkExample/subject/events") in calls


def test_fastapi_wrapper_proxies_principal_standing_events(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    class _Resp:
        def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

    calls: list[tuple[str, str]] = []

    async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
        calls.append((method, path))
        return _Resp(200, b'{"status":"ok"}')

    monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

    get_resp = client.get("/api/principals/did:key:z6MkExample/standing/events")
    post_resp = client.post(
        "/api/principals/did:key:z6MkExample/standing/events",
        json={"event_type": "sanction", "issuer": "deterministic:eq9", "reason_code": "x", "idempotency_key": "1"},
    )

    assert get_resp.status_code == 200
    assert post_resp.status_code == 200
    assert ("GET", "/api/principals/did:key:z6MkExample/standing/events") in calls
    assert ("POST", "/api/principals/did:key:z6MkExample/standing/events") in calls


def test_fastapi_wrapper_proxies_principal_standing_view(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    class _Resp:
        def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

    calls: list[tuple[str, str]] = []

    async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
        calls.append((method, path))
        return _Resp(200, b'{"status":"ok"}')

    monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

    get_resp = client.get("/api/principals/did:key:z6MkExample/standing")

    assert get_resp.status_code == 200
    assert ("GET", "/api/principals/did:key:z6MkExample/standing") in calls


def test_fastapi_wrapper_proxies_trust_anchor_status(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    class _Resp:
        def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

    calls: list[tuple[str, str]] = []

    async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
        calls.append((method, path))
        return _Resp(200, b'{"status":"ok"}')

    monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

    resp = client.get("/api/trust-anchor/status")

    assert resp.status_code == 200
    assert ("GET", "/api/trust-anchor/status") in calls


def test_fastapi_wrapper_proxies_trust_anchor_bundle(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    class _Resp:
        def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

    calls: list[tuple[str, str]] = []

    async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
        calls.append((method, path))
        return _Resp(200, b'{"issuer_did":"did:web:id.dualsubstrate.com"}')

    monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

    resp = client.get("/api/trust-anchor/bundle")

    assert resp.status_code == 200
    assert ("GET", "/api/trust-anchor/bundle") in calls


def test_fastapi_wrapper_forwards_qp_pure_field(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)
    captured: dict[str, object] = {}

    async def fake_stream_from_legacy(request, method: str, path: str, *, json_payload):
        captured["method"] = method
        captured["path"] = path
        captured["json_payload"] = json_payload
        return fastapi_app_module.Response(
            content=json.dumps({"status": "ok"}).encode("utf-8"),
            media_type="application/json",
        )

    monkeypatch.setattr(fastapi_app_module, "_stream_from_legacy", fake_stream_from_legacy)

    payload = {
        "message": "test qp_pure forwarding",
        "qp_pure": True,
    }
    response = client.post("/api/chat/smart_stream", json=payload)

    assert response.status_code == 200
    forwarded = captured["json_payload"]
    assert isinstance(forwarded, dict)
    assert forwarded.get("qp_pure") is True


def test_fastapi_wrapper_omits_qp_pure_when_not_set(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)
    captured: dict[str, object] = {}

    async def fake_stream_from_legacy(request, method: str, path: str, *, json_payload):
        captured["json_payload"] = json_payload
        return fastapi_app_module.Response(
            content=json.dumps({"status": "ok"}).encode("utf-8"),
            media_type="application/json",
        )

    monkeypatch.setattr(fastapi_app_module, "_stream_from_legacy", fake_stream_from_legacy)

    payload = {"message": "test no qp_pure"}
    response = client.post("/api/chat/smart_stream", json=payload)

    assert response.status_code == 200
    forwarded = captured["json_payload"]
    assert isinstance(forwarded, dict)
    assert "qp_pure" not in forwarded


def test_fastapi_wrapper_forwards_qp_context_fields(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)
    captured: dict[str, object] = {}

    async def fake_stream_from_legacy(request, method: str, path: str, *, json_payload):
        captured["json_payload"] = json_payload
        return fastapi_app_module.Response(
            content=json.dumps({"status": "ok"}).encode("utf-8"),
            media_type="application/json",
        )

    monkeypatch.setattr(fastapi_app_module, "_stream_from_legacy", fake_stream_from_legacy)

    payload = {
        "message": "test qp context forwarding coordinate",
        "qp_pure": True,
        "query_primes": [2, 3, 5],
        "query_factors": [{"prime": 5, "exponent": 1}],
        "padic_config": {"metric_prime": 5, "working_precision": 16},
        "mmf_domain": "indefeasible",
    }
    response = client.post("/api/chat/smart_stream", json=payload)

    assert response.status_code == 200
    forwarded = captured["json_payload"]
    assert forwarded.get("qp_pure") is True
    assert forwarded.get("query_primes") == [2, 3, 5]
    assert forwarded.get("query_factors") == [{"prime": 5, "exponent": 1}]
    assert forwarded.get("padic_config") == {"metric_prime": 5, "working_precision": 16}
    assert forwarded.get("mmf_domain") == "indefeasible"


def test_fastapi_wrapper_rejects_invalid_query_primes() -> None:
    client = TestClient(fastapi_app_module.app)

    invalid_cases = [
        {"message": "test invalid prime", "query_primes": [4]},
        {"message": "test duplicate primes", "query_primes": [2, 2]},
        {"message": "test too many primes", "query_primes": list(range(2, 36))},
    ]
    for payload in invalid_cases:
        response = client.post("/api/chat/smart_stream", json=payload)
        assert response.status_code == 422, f"Expected 422 for payload {payload}"


def test_chat_response_surfaces_padic_diagnostics() -> None:
    from api.client import ChatResponse

    payload = {
        "reply": "answer",
        "padic_diagnostics": {"ball_hit_count": 4, "top_p_adic_score": 1.0},
        "p_adic_write_cost": 0.05,
        "query_primes_used": [5],
    }
    response = ChatResponse.from_json(payload)
    assert response.padic_diagnostics == {"ball_hit_count": 4, "top_p_adic_score": 1.0}
    assert response.p_adic_write_cost == 0.05
    assert response.query_primes_used == [5]


class _MockRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.cookies = {}


def test_auth_envelope_advertises_qp_scopes() -> None:
    from utils.auth_envelope import build_backend_auth_envelope

    request = _MockRequest({"x-p-adic-scope": "qp_retrieval,p_adic_ball_read"})
    envelope = build_backend_auth_envelope(
        request=request,
        payload={"qp_pure": True, "hardening_level": 2},
    )

    assert envelope["headers"].get("x-p-adic-scope") == "p_adic_ball_read,qp_retrieval"
    assert envelope["claims"].get("p_adic_scope") == "p_adic_ball_read,qp_retrieval"
    assert envelope["claims"].get("p_adic_hardening_level") == "2"
    assert envelope["qp_scope_check"]["missing"] == []


def test_auth_envelope_detects_delegated_scope_exceeds_operator() -> None:
    from utils.auth_envelope import build_backend_auth_envelope

    request = _MockRequest()
    envelope = build_backend_auth_envelope(
        request=request,
        payload={
            "qp_pure": True,
            "p_adic_scope": ["qp_retrieval"],
            "delegated_principal": {
                "principal_did": "did:key:z6MkAgent",
                "principal_type": "agent",
                "explicit_cli_request": True,
                "p_adic_scope": ["qp_retrieval", "p_adic_ball_write"],
            },
        },
    )

    assert envelope["qp_scope_check"]["delegation_exceeds_operator"] is True
    assert "x-delegated-p-adic-scope" in envelope["headers"]


def test_orchestrator_rejects_qp_pure_without_scope(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)
    import routes.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module.settings, "QP_PURE_ENABLED", True)

    response = client.post(
        "/api/orchestrator",
        json={"message": "test qp_pure without scope", "qp_pure": True, "_stream_passthrough": True},
    )

    assert response.status_code == 403
    detail = response.json() if response.text else {}
    assert detail.get("error") == "missing_qp_scopes"


def test_get_ledger_founding_purpose(monkeypatch) -> None:
    client = TestClient(fastapi_app_module.app)

    async def fake_get_ledger_purpose(ledger_id: str) -> dict:
        return {"ledger_id": ledger_id, "purpose": "Hold governed memory.", "name": "LOAM"}

    monkeypatch.setattr(app_module.api, "get_ledger_purpose", fake_get_ledger_purpose)

    response = client.get("/api/ledger/LOAM/purpose")
    assert response.status_code == 200
    body = response.json()
    assert body["ledger_id"] == "LOAM"
    assert body["purpose"] == "Hold governed memory."
    assert body["name"] == "LOAM"
