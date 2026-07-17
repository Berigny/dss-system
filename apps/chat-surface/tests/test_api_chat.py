
import json
import inspect
import pathlib
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import app as app_module
from app import app

client = TestClient(app)
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_str_list(value: object) -> list[str]:
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


@pytest.fixture(autouse=True)
def _frontdoor_auth_cookie():
    client.cookies.set(
        app_module.FRONTDOOR_AUTH_COOKIE,
        app_module._frontdoor_cookie_signature(),
    )
    yield


def test_api_chat_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    class DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return DummyResponse(
                200,
                {
                    "reply": "hello from middleware",
                    "stats": {"model": "openai", "last_latency": 12},
                    "knowledge_tree": [],
                    "coordinate": None,
                    "web4_key": None,
                    "unverified": [],
                },
            )

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    response = client.post(
        "/api/chat",
        json={
            "session_id": "test",
            "message": "hello",
            "history": [],
            "provider": "openai",
            "enable_ledger": True,
        },
    )
    assert response.status_code == 200
    assert "reply" in response.json()
    assert "stats" in response.json()
    assert str(captured.get("url") or "").endswith("/api/chat")


def test_install_starlette_init_compat_drops_missing_kwargs(monkeypatch):
    original_init = app_module.Starlette.__init__

    def legacy_init(self, debug=False, routes=None, middleware=None, exception_handlers=None):
        self._legacy_starlette_init_called = {
            "debug": debug,
            "routes": routes,
            "middleware": middleware,
            "exception_handlers": exception_handlers,
        }

    monkeypatch.setattr(app_module.Starlette, "__init__", legacy_init)
    app_module._install_starlette_init_compat()

    shimmed_init = app_module.Starlette.__init__
    assert shimmed_init is not legacy_init
    assert "on_startup" not in inspect.signature(legacy_init).parameters

    app = app_module.Starlette()
    cast(Any, shimmed_init)(
        app,
        False,
        None,
        None,
        None,
        on_startup=["boot"],
        on_shutdown=["stop"],
        lifespan="ignored",
    )

    assert getattr(app, "_legacy_starlette_init_called") == {
        "debug": False,
        "routes": None,
        "middleware": None,
        "exception_handlers": None,
    }
    monkeypatch.setattr(app_module.Starlette, "__init__", original_init)


def test_api_chat_forwards_context_coords(monkeypatch):
    captured: dict[str, object] = {}

    class DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return DummyResponse(
                200,
                {
                    "reply": "ok",
                    "stats": {"model": "openai"},
                    "knowledge_tree": [],
                    "coordinate": None,
                    "web4_key": None,
                    "unverified": [],
                },
            )

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    response = client.post(
        "/api/chat",
        json={
            "session_id": "test-context-coords",
            "message": "hello",
            "history": [],
            "provider": "openai",
            "enable_ledger": True,
            "context_coords": ["chat-demo:ATT-1", "", None],
        },
    )

    assert response.status_code == 200
    chat_payload = captured.get("json")
    assert isinstance(chat_payload, dict)
    assert chat_payload.get("context_coords") == ["chat-demo:ATT-1", "", None]
    assert str(captured.get("url") or "").endswith("/api/chat")


def test_api_auth_identity_card_proxies_to_middleware(monkeypatch):
    captured: dict[str, object] = {}

    class DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return DummyResponse(
                200,
                {
                    "status": "ok",
                    "identity_vc": {
                        "verified": True,
                        "verification_state": "verified",
                        "reason_code": "verified",
                        "principal_did": "did:key:test",
                        "auth_method": "passkey",
                        "ledger_access_ready": True,
                        "ledger_id": "ledger:test",
                    },
                    "usage_stats": {},
                    "eq9": {},
                },
            )

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    client.cookies.set(app_module.BACKEND_SESSION_TOKEN_COOKIE, "token-123")
    client.cookies.set("ds_session", "session-123")

    response = client.get("/api/auth/identity_card")

    assert response.status_code == 200
    payload = response.json()
    assert payload["identity_vc"]["principal_did"] == "did:key:test"
    assert str(captured.get("url") or "").endswith("/api/auth/identity_card")


def test_api_auth_identity_card_returns_503_when_middleware_is_unavailable(monkeypatch):
    import app as app_module

    async def fake_fetch_middleware_identity_card(_request):
        return None

    monkeypatch.setattr(app_module, "_fetch_middleware_identity_card", fake_fetch_middleware_identity_card)

    response = client.get("/api/auth/identity_card")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["error"] == "middleware_identity_card_unavailable"


def test_api_chat_smart_stream(monkeypatch):
    import app as app_module
    captured: dict[str, object] = {}

    class DummyStreamResponse:
        def __init__(self, status_code: int, chunks: list[bytes]):
            self.status_code = status_code
            self._chunks = chunks

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

        async def aread(self):
            return b""

        async def aclose(self):
            return None

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        def build_request(self, method, url, json=None, headers=None):
            return {"method": method, "url": url, "json": json, "headers": headers}

        async def send(self, request, stream=False):
            captured["request"] = request
            chunks = [
                b'{"type":"status","message":"Backend stream mode"}\n',
                b'{"type":"token","content":"Hello"}\n',
                b'{"type":"meta","model":"mock","posture_policy":{"policy_gate_version":"policy-gate-v1","policy_decision":"allow","reason_code":"baseline_satisfied","failed_eq":null,"repair_actions":[],"trust_class":"T2","eq9_posture_class":"P2"},"query_integrity":{"source_tier":"hot","staleness_ms":0,"integrity_status":"verified","witness_status":"not_attested","reconstruction_path":"live_stream"}}\n',
            ]
            return DummyStreamResponse(200, chunks)

        async def aclose(self):
            return None

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    async def fake_verified_model_auth_context(_request):
        return {
            "identity_vc": {
                "verified": True,
                "verification_state": "verified",
                "principal_did": "did:key:z6MkModelCtx",
                "session_jti": "sess-model-ctx",
                "auth_method": "passkey",
                "reason_code": "verified",
            },
            "usage_stats": {"chat_unit_cost": 0.0},
            "eq9": {"eq9_posture_class": "P2", "trust_class": "T2", "reason_code": "baseline_satisfied"},
        }

    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)
    client.cookies.set(app_module.BACKEND_SESSION_TOKEN_COOKIE, "opaque-session-token")

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "test",
            "message": "hello",
            "history": [],
            "provider": "openai",
            "enable_ledger": True,
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers.get("x-ds-upstream-url")
        assert response.headers.get("x-ds-upstream-fallback") == "false"
        lines = [line for line in response.iter_lines() if line]

    assert lines
    events = [json.loads(line) for line in lines]
    token_indices = [idx for idx, event in enumerate(events) if event.get("type") == "token"]
    status_indices = [idx for idx, event in enumerate(events) if event.get("type") == "status"]
    assert token_indices
    assert status_indices
    assert min(status_indices) < min(token_indices)
    meta_event = next((event for event in events if event.get("type") == "meta"), {})
    posture_policy = meta_event.get("posture_policy") if isinstance(meta_event, dict) else {}
    assert isinstance(posture_policy, dict)
    assert posture_policy.get("policy_gate_version") == "policy-gate-v1"
    assert posture_policy.get("policy_decision") in {"allow", "degrade", "deny"}
    assert isinstance(posture_policy.get("reason_code"), str)
    assert "failed_eq" in posture_policy
    assert isinstance(posture_policy.get("repair_actions"), list)
    assert posture_policy.get("trust_class") in {"T0", "T1", "T2"}
    assert posture_policy.get("eq9_posture_class") in {"P0", "P1", "P2", "P3"}
    query_integrity = meta_event.get("query_integrity") if isinstance(meta_event, dict) else {}
    assert isinstance(query_integrity, dict)
    assert query_integrity.get("source_tier")
    assert isinstance(query_integrity.get("staleness_ms"), int)
    assert query_integrity.get("integrity_status")
    assert query_integrity.get("witness_status")
    assert query_integrity.get("reconstruction_path")
    request_data = _as_dict(captured.get("request"))
    request_payload = _as_dict(request_data.get("json"))
    request_headers = _as_dict(request_data.get("headers"))
    metadata = _as_dict(request_payload.get("metadata"))
    model_auth_context = _as_dict(metadata.get("model_auth_context"))
    identity_vc = _as_dict(model_auth_context.get("identity_vc"))
    assert isinstance(identity_vc, dict)
    assert identity_vc.get("principal_did") == "did:key:z6MkModelCtx"
    assert request_payload.get("principal_did") == "did:key:z6MkModelCtx"
    assert request_payload.get("session_jti") == "sess-model-ctx"
    assert request_headers.get("x-principal-did") == "did:key:z6MkModelCtx"
    assert request_headers.get("x-session-jti") == "sess-model-ctx"
    assert request_headers.get("authorization") == "Bearer opaque-session-token"


def test_api_chat_smart_stream_fallback_marker(monkeypatch):
    import app as app_module

    class DummyStreamResponse:
        def __init__(self, status_code: int, chunks: list[bytes]):
            self.status_code = status_code
            self._chunks = chunks

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

        async def aread(self):
            return b""

        async def aclose(self):
            return None

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        def build_request(self, method, url, json=None, headers=None):
            return {"method": method, "url": url, "json": json, "headers": headers}

        async def send(self, request, stream=False):
            url = str(request.get("url") or "")
            if url.endswith("/api/chat/smart_stream"):
                return DummyStreamResponse(404, [])
            chunks = [
                b'{"type":"status","message":"Fallback stream"}\n',
                b'{"type":"token","content":"Hello"}\n',
                b'{"type":"meta","model":"mock"}\n',
            ]
            return DummyStreamResponse(200, chunks)

        async def aclose(self):
            return None

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    async def fake_verified_model_auth_context(_request):
        return {}
    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "test-fallback",
            "message": "hello",
            "history": [],
            "provider": "openai",
            "enable_ledger": True,
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers.get("x-ds-upstream-url")
        assert response.headers.get("x-ds-upstream-fallback") == "true"


def test_api_chat_smart_stream_codex_prompt_mode_builds_delegated_principal(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "CODEX_PRINCIPAL_DID", "did:web:id.dualsubstrate.com:principals:agent:openai:codex")
    captured: dict[str, object] = {}

    class DummyStreamResponse:
        def __init__(self, status_code: int, chunks: list[bytes]):
            self.status_code = status_code
            self._chunks = chunks

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

        async def aread(self):
            return b""

        async def aclose(self):
            return None

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        def build_request(self, method, url, json=None, headers=None):
            return {"method": method, "url": url, "json": json, "headers": headers}

        async def send(self, request, stream=False):
            captured["request"] = request
            chunks = [
                b'{"type":"token","content":"Hello"}\n',
                b'{"type":"meta","model":"mock"}\n',
            ]
            return DummyStreamResponse(200, chunks)

        async def aclose(self):
            return None

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    async def fake_verified_model_auth_context(_request):
        return {
            "identity_vc": {
                "verified": True,
                "verification_state": "verified",
                "principal_did": "did:key:z6MkOperator",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:wallet-user",
                "principal_display_name": "David Berigny",
                "session_jti": "sess-codex",
                "auth_method": "wallet_verified_id",
                "reason_code": "verified",
            }
        }

    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)
    client.cookies.set(app_module.BACKEND_SESSION_TOKEN_COOKIE, "opaque-session-token")

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "test-codex",
            "message": "hello",
            "history": [],
            "provider": "anthropic/claude-haiku-4.5",
            "agent": "anthropic/claude-haiku-4.5",
            "model": "anthropic/claude-haiku-4.5",
            "enable_ledger": True,
            "prompt_principal_mode": "codex",
        },
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert lines
    request_data = _as_dict(captured.get("request"))
    request_payload = _as_dict(request_data.get("json"))
    delegated = _as_dict(request_payload.get("delegated_principal"))
    request_headers = _as_dict(request_data.get("headers"))
    assert delegated.get("principal_did") == app_module.CODEX_PRINCIPAL_DID
    assert delegated.get("principal_key_id") == app_module.CODEX_PRINCIPAL_KEY_ID
    assert delegated.get("principal_id") == app_module.CODEX_PRINCIPAL_ID
    assert delegated.get("principal_display_name") == "openai/codex"
    assert delegated.get("prompt_principal_display_name") == "openai/codex"
    assert delegated.get("delegated_by_principal_did") == "did:key:z6MkOperator"
    assert delegated.get("delegated_by_principal_id") == "wallet-user"
    assert delegated.get("surface_id") == app_module.settings.CHAT_SURFACE_ID
    assert delegated.get("ledger_scope") == ["loam"]
    assert request_payload.get("provider") == "anthropic/claude-haiku-4.5"
    assert request_payload.get("agent") == "anthropic/claude-haiku-4.5"
    assert request_payload.get("model") == "anthropic/claude-haiku-4.5"
    assert request_payload.get("metadata", {}).get("principal_display_name") == "David Berigny"
    assert request_headers.get("x-principal-did") == "did:key:z6MkOperator"
    assert request_headers.get("x-principal-id") == "wallet-user"
    assert request_headers.get("x-principal-type") == "user"
    assert request_headers.get("authorization") == "Bearer opaque-session-token"


def test_api_chat_smart_stream_codex_prompt_mode_fails_without_authenticated_context(monkeypatch):
    import app as app_module

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def aclose(self):
            return None

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    async def fake_verified_model_auth_context(_request):
        return {}

    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)
    client.cookies.pop(app_module.BACKEND_SESSION_TOKEN_COOKIE, None)

    response = client.post(
        "/api/chat/smart_stream",
        json={
            "session_id": "test-codex-fail",
            "message": "hello",
            "history": [],
            "provider": "anthropic/claude-haiku-4.5",
            "enable_ledger": True,
            "prompt_principal_mode": "codex",
        },
    )

    assert response.status_code == 400
    assert response.json().get("detail") == "codex_prompt_requires_authenticated_delegation_context"


def test_api_chat_smart_stream_kimi_prompt_mode_builds_delegated_principal_and_preserves_model(monkeypatch):
    import app as app_module
    captured: dict[str, object] = {}

    monkeypatch.setattr(app_module, "KIMI_PRINCIPAL_DID", "did:web:chat.dualsubstrate.com:principals:agent:moonshot:kimi-code")

    class DummyStreamResponse:
        def __init__(self, status_code: int, chunks: list[bytes]):
            self.status_code = status_code
            self._chunks = chunks

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

        async def aread(self):
            return b""

        async def aclose(self):
            return None

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        def build_request(self, method, url, json=None, headers=None):
            return {"method": method, "url": url, "json": json, "headers": headers}

        async def send(self, request, stream=False):
            captured["request"] = request
            chunks = [
                b'{"type":"token","content":"Hello"}\n',
                b'{"type":"meta","model":"mock"}\n',
            ]
            return DummyStreamResponse(200, chunks)

        async def aclose(self):
            return None

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    async def fake_verified_model_auth_context(_request):
        return {
            "identity_vc": {
                "verified": True,
                "verification_state": "verified",
                "principal_did": "did:key:z6MkOperator",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:wallet-user",
                "principal_display_name": "David Berigny",
                "session_jti": "sess-kimi",
                "auth_method": "wallet_verified_id",
                "reason_code": "verified",
            }
        }

    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)
    client.cookies.set(app_module.BACKEND_SESSION_TOKEN_COOKIE, "opaque-session-token")

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "test-kimi",
            "message": "hello",
            "history": [],
            "provider": "moonshotai/kimi-k2.5",
            "agent": "moonshotai/kimi-k2.5",
            "model": "moonshotai/kimi-k2.5",
            "enable_ledger": True,
            "prompt_principal_mode": "kimi",
        },
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert lines
    request_data = _as_dict(captured.get("request"))
    request_payload = _as_dict(request_data.get("json"))
    delegated = _as_dict(request_payload.get("delegated_principal"))
    assert delegated.get("principal_did") == "did:web:chat.dualsubstrate.com:principals:agent:moonshot:kimi-code"
    assert delegated.get("principal_key_id") == "moonshot:agent:kimi-code"
    assert delegated.get("principal_id") == "moonshot:kimi-code"
    assert delegated.get("principal_display_name") == "Moonshot: Kimi-code"
    assert delegated.get("delegated_by_principal_did") == "did:key:z6MkOperator"
    assert delegated.get("delegated_by_principal_id") == "wallet-user"
    assert request_payload.get("provider") == "moonshotai/kimi-k2.5"
    assert request_payload.get("agent") == "moonshotai/kimi-k2.5"
    assert request_payload.get("model") == "moonshotai/kimi-k2.5"


def test_api_chat_smart_stream_sets_canonical_human_principal_headers(monkeypatch):
    import app as app_module
    captured: dict[str, object] = {}

    class DummyStreamResponse:
        def __init__(self, status_code: int, chunks: list[bytes]):
            self.status_code = status_code
            self._chunks = chunks

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

        async def aread(self):
            return b""

        async def aclose(self):
            return None

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        def build_request(self, method, url, json=None, headers=None):
            return {"method": method, "url": url, "json": json, "headers": headers}

        async def send(self, request, stream=False):
            captured["request"] = request
            chunks = [
                b'{"type":"token","content":"Hello"}\n',
                b'{"type":"meta","model":"mock"}\n',
            ]
            return DummyStreamResponse(200, chunks)

        async def aclose(self):
            return None

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    async def fake_verified_model_auth_context(_request):
        return {
            "identity_vc": {
                "verified": True,
                "verification_state": "verified",
                "principal_did": "did:key:z6MkVerifiedHuman",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:wallet-user",
                "principal_display_name": "David Berigny",
                "session_jti": "sess-human",
                "auth_method": "wallet_verified_id",
                "reason_code": "verified",
            }
        }

    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)
    client.cookies.set(app_module.BACKEND_SESSION_TOKEN_COOKIE, "opaque-session-token")

    with client.stream(
        "POST",
        "/api/chat/smart_stream",
        json={
            "session_id": "test-human-canonical-principal",
            "message": "hello",
            "history": [],
            "provider": "anthropic/claude-haiku-4.5",
            "enable_ledger": True,
        },
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert lines
    request_data = _as_dict(captured.get("request"))
    request_headers = _as_dict(request_data.get("headers"))
    assert request_headers.get("x-principal-did") == "did:key:z6MkVerifiedHuman"
    assert request_headers.get("x-principal-id") == "wallet-user"
    assert request_headers.get("x-principal-type") == "user"


def test_api_thinking_trace_emit_proxy(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    class DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return DummyResponse(200, {"ok": True, "request_id": str((json or {}).get("request_id") or "")})

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    response = client.post(
        "/api/thinking_trace/emit",
        json={
            "request_id": "req-proxy-emit-1",
            "type": "step",
            "status": "in_progress",
            "step_code": "HISTORY_LOAD_START",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body.get("ok") is True
    assert str(captured.get("url") or "").endswith("/api/thinking_trace/emit")
    payload = _as_dict(captured.get("json"))
    assert payload.get("request_id") == "req-proxy-emit-1"
    assert isinstance(payload.get("session_id"), str) and payload.get("session_id")


def test_api_thinking_trace_stream_proxy(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    class DummyStreamResponse:
        def __init__(self, status_code: int, chunks: list[bytes]):
            self.status_code = status_code
            self._chunks = chunks

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

        async def aread(self):
            return b""

        async def aclose(self):
            return None

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        def build_request(self, method, url, params=None, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return {"method": method, "url": url, "params": params, "headers": headers}

        async def send(self, request, stream=False):
            chunks = [
                b'{"type":"thinking_trace","payload":{"request_id":"req-1","type":"process_started"}}\n',
                b'{"type":"thinking_trace","payload":{"request_id":"req-1","type":"process_completed"}}\n',
            ]
            return DummyStreamResponse(200, chunks)

        async def aclose(self):
            return None

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    with client.stream(
        "GET",
        "/api/thinking_trace/stream?replay=1&once=1",
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert lines
    events = [json.loads(line) for line in lines]
    assert events[0].get("type") == "thinking_trace"
    assert events[-1].get("type") == "thinking_trace"
    assert str(captured.get("url") or "").endswith("/api/thinking_trace/stream")
    params = _as_dict(captured.get("params"))
    assert params.get("replay") == "1"
    assert params.get("once") == "1"
    assert isinstance(params.get("session_id"), str) and params.get("session_id")


def test_api_thinking_trace_stream_proxy_includes_upstream_url_on_http_error(monkeypatch):
    import app as app_module

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        def build_request(self, method, url, params=None, headers=None):
            return {"method": method, "url": url, "params": params, "headers": headers}

        async def send(self, request, stream=False):
            raise app_module.httpx.ConnectError("connect failed")

        async def aclose(self):
            return None

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    response = client.get("/api/thinking_trace/stream?replay=1", follow_redirects=False)

    assert response.status_code == 502
    body = response.json()
    assert "Thinking trace stream upstream unavailable" in str(body.get("detail") or "")
    assert str(body.get("upstream_url") or "").endswith("/api/thinking_trace/stream")


def test_cookie_domain_uses_root_domain_for_dualsubstrate_subdomains():
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "server": ("chat.dualsubstrate.com", 443),
        "path": "/",
        "headers": [(b"x-forwarded-host", b"chat.dualsubstrate.com")],
    }
    request = Request(scope)
    assert app_module._cookie_domain(request) == ".dualsubstrate.com"


def test_control_plane_login_url_preserves_chat_return_url(monkeypatch):
    monkeypatch.setattr(app_module.settings, "CONTROL_PLANE_BASE", "https://id.dualsubstrate.com")
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "server": ("chat.dualsubstrate.com", 443),
        "path": "/ui/history/chat-demo",
        "query_string": b"view=recent",
        "headers": [
            (b"x-forwarded-host", b"chat.dualsubstrate.com"),
            (b"x-forwarded-proto", b"https"),
        ],
    }
    request = Request(scope)

    login_url = app_module._control_plane_login_url(request)

    assert login_url.startswith("https://id.dualsubstrate.com/login?next=")
    assert "https%3A%2F%2Fchat.dualsubstrate.com%2Fui%2Fhistory%2Fchat-demo%3Fview%3Drecent" in login_url


def test_frontdoor_form_auth_redirects_unauthenticated_chat_to_control_plane(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setattr(app_module.settings, "CONTROL_PLANE_BASE", "https://id.dualsubstrate.com")

    async def _no_shared_session(_request):
        return False, None

    monkeypatch.setattr(app_module, "_shared_backend_session_identity", _no_shared_session)
    client.cookies.clear()

    response = client.get(
        "/ui/history/chat-demo?view=recent",
        headers={"x-forwarded-host": "chat.dualsubstrate.com", "x-forwarded-proto": "https"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers.get("location") or ""
    assert location.startswith("https://id.dualsubstrate.com/login?next=")
    assert "https%3A%2F%2Fchat.dualsubstrate.com%2Fui%2Fhistory%2Fchat-demo%3Fview%3Drecent" in location


def test_chat_surface_exposes_session_refresh_route_and_activity_hook() -> None:
    source = (REPO_ROOT / "app.py").read_text()
    js_source = (REPO_ROOT / "static" / "js" / "app.js").read_text()

    assert '@rt("/api/auth/session/refresh", methods=["POST"])' in source
    assert "async def api_auth_session_refresh" in source
    assert "await _refresh_shared_backend_session(request)" in source
    assert '"login_url": _control_plane_login_url(request)' in source
    assert "const refreshPath = window.dsSessionRefreshPath || '/api/auth/session/refresh';" in js_source
    assert "document.cookie.includes('ds_backend_refresh_token=')" in js_source
    assert "redirectToControlPlaneLogin(loginUrl);" in js_source
    assert "['pointerdown', 'keydown', 'submit']" in js_source


def test_chat_surface_attachment_ingest_uses_parent_and_part_coordinates() -> None:
    js_source = (REPO_ROOT / "static" / "js" / "app.js").read_text()

    assert "parsed.parent_coordinate || parsed.coordinate" in js_source
    assert "Array.isArray(parsed.part_coordinates)" in js_source
    assert "event.parent_coordinate || event.coordinate" in js_source
    assert "Array.isArray(event.part_coordinates)" in js_source
    assert "Attachment ingested without usable coordinate." in js_source


def test_chat_surface_stream_failure_semantics_are_explicit() -> None:
    js_source = (REPO_ROOT / "static" / "js" / "app.js").read_text()

    assert "if (payload.type === 'error')" in js_source
    assert "describeAttachmentContextFailure" in js_source
    assert "deriveExplicitStreamFailure" in js_source
    assert "Attachment context was requested but not queued for this turn." in js_source
    assert "Attachment context was requested but attachment parts were unavailable." in js_source
    assert "Attachment context was requested but not resolved for this turn." in js_source
    assert "No answer was returned for this prompt." in js_source
    assert "The response stream ended before an answer was returned." in js_source


def test_api_auth_session_refresh_sets_access_and_refresh_cookies(monkeypatch):
    async def fake_refresh(_request):
        return (
            200,
            {
                "status": "ok",
                "principal_did": "did:key:z6MkSurfaceRefresh",
                "session": {"token": "access-456"},
                "refresh_session": {"token": "refresh-789"},
            },
        )

    monkeypatch.setattr(app_module, "_refresh_shared_backend_session", fake_refresh)

    response = client.post("/api/auth/session/refresh")

    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{app_module.BACKEND_SESSION_TOKEN_COOKIE}=access-456" in set_cookie
    assert f"{app_module.BACKEND_REFRESH_TOKEN_COOKIE}=refresh-789" in set_cookie


def test_api_auth_session_refresh_returns_login_url_on_401(monkeypatch):
    async def fake_refresh(request):
        return 401, {"error": "authentication_required"}

    monkeypatch.setattr(app_module, "_refresh_shared_backend_session", fake_refresh)

    response = client.post(
        "/api/auth/session/refresh",
        headers={"x-forwarded-host": "chat.dualsubstrate.com", "x-forwarded-proto": "https"},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "authentication_required"
    assert payload["login_url"].startswith("https://id.dualsubstrate.com/login?next=")


def test_api_auth_session_refresh_is_exempt_from_frontdoor_auth(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    client.cookies.clear()

    async def fake_refresh(request):
        return (
            200,
            {
                "status": "ok",
                "principal_did": "did:key:z6MkRefreshExempt",
                "session": {
                    "token": "access-exempt",
                    "token_type": "bearer",
                    "expires_at": 1,
                    "issued_at": 0,
                    "jti": "jti-access",
                },
                "refresh_session": {
                    "token": "refresh-exempt",
                    "token_type": "bearer",
                    "expires_at": 1,
                    "issued_at": 0,
                    "jti": "jti-refresh",
                },
            },
        )

    monkeypatch.setattr(app_module, "_refresh_shared_backend_session", fake_refresh)

    response = client.post("/api/auth/session/refresh")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["principal_did"] == "did:key:z6MkRefreshExempt"


def test_login_page_is_github_only(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    client.cookies.clear()

    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    assert "Continue with GitHub" in body
    assert "control plane and linked identity flow" in body
    assert "/login/github?next=/" in body
    assert "Continue with Passkey" not in body
    assert "Password" not in body
    assert 'name="password"' not in body
    assert 'name="user"' not in body


def test_login_page_stays_github_only_when_linked_identity_exists(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    client.cookies.clear()
    client.cookies.set("ds_principal_did", "did:key:z6MkLinkedUser")

    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    assert "Continue with Passkey" not in body
    assert "/login/github?next=/" in body
    client.cookies.pop("ds_principal_did", None)


def test_login_page_shows_github_specific_error_copy(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    client.cookies.clear()

    response = client.get("/login?github_error=oauth_state_invalid&next=/", follow_redirects=False)
    assert response.status_code == 200
    assert "GitHub sign-in could not be verified" in response.text


def test_login_github_page_renders_authorize_text(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    client.cookies.clear()

    response = client.get("/login/github?next=/", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    assert "Authorize DSS" in body
    assert "choose which account you would like to use" in body
    assert "Contact operator." in body
    assert "GitHub unavailable" in body
    assert "Continue with Passkey" not in body

    head_response = client.head("/login/github?next=/", follow_redirects=False)
    assert head_response.status_code == 200


def test_login_github_page_stays_github_only_when_linked_identity_exists(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    client.cookies.clear()
    client.cookies.set("ds_principal_did", "did:key:z6MkLinkedUser")

    response = client.get("/login/github?next=/", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    assert "Continue with Passkey" not in body
    assert "GitHub unavailable" in body
    client.cookies.pop("ds_principal_did", None)


def test_login_github_start_redirects_to_provider_when_configured(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-client-1")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret-1")
    monkeypatch.setattr("app.secrets.token_urlsafe", lambda _n: "fixed-state")
    client.cookies.clear()

    response = client.get("/login/github/start?next=/ui/history/chat-demo", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers.get("location") or ""
    assert location.startswith("https://github.com/login/oauth/authorize?")
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert query.get("client_id") == ["gh-client-1"]
    assert query.get("state") == ["fixed-state"]


def test_login_github_callback_sets_session_cookie(monkeypatch):
    import app as app_module

    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-client-1")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret-1")
    monkeypatch.setattr("app.secrets.token_urlsafe", lambda _n: "fixed-state")
    client.cookies.clear()

    class DummyResponse:
        def __init__(self, status_code: int, payload: object):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, data=None):
            assert "github.com/login/oauth/access_token" in url
            return DummyResponse(200, {"access_token": "gh-token-1"})

        async def get(self, url, headers=None):
            if "api.github.com/user/emails" in url:
                return DummyResponse(200, [{"email": "david@berigny.org", "verified": True, "primary": True}])
            assert "api.github.com/user" in url
            return DummyResponse(200, {"id": 12345, "login": "david"})

    async def fake_principal_upsert(payload: dict[str, object]):
        assert payload.get("principal_did") == "did:github:12345"
        return 200, {"status": "ok"}

    async def fake_principal_registry_post(path: str, payload: dict[str, object]):
        assert path == "/api/principals/link/github/start"
        assert payload.get("github_user_id") == "12345"
        return 404, {"detail": {"error": "principal_link_not_found"}}

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(app_module, "_principal_upsert", fake_principal_upsert)
    monkeypatch.setattr(app_module, "_principal_registry_post", fake_principal_registry_post)

    start = client.get("/login/github/start?next=/", follow_redirects=False)
    assert start.status_code == 303
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    assert state == "fixed-state"

    callback = client.get(f"/login/github/callback?state={state}&code=abc123", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers.get("location") == "/login/link?next=/"


def test_login_github_callback_redirects_to_link_flow_when_existing_principal_requires_verification(monkeypatch):
    import app as app_module

    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-client-1")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret-1")
    monkeypatch.setattr("app.secrets.token_urlsafe", lambda _n: "fixed-state")
    client.cookies.clear()

    class DummyResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, data=None):
            return DummyResponse(200, {"access_token": "gh-token-1"})

        async def get(self, url, headers=None):
            if "api.github.com/user/emails" in url:
                return DummyResponse(200, [{"email": "david@berigny.org", "verified": True, "primary": True}])
            return DummyResponse(200, {"id": 12345, "login": "david"})

    async def fake_principal_registry_post(path: str, payload: dict[str, object]):
        assert path == "/api/principals/link/github/start"
        return 200, {
            "link_state": "verification_required",
            "challenge_id": "challenge-1",
            "masked_destination": "d***@berigny.org",
            "delivery_channel": "email",
            "principal_did": "did:key:z6MkExisting",
        }

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(app_module, "_principal_registry_post", fake_principal_registry_post)

    start = client.get("/login/github/start?next=/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    callback = client.get(f"/login/github/callback?state={state}&code=abc123", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers.get("location") == "/login/link?next=/&challenge=1"


def test_login_github_callback_uses_github_error_channel(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    client.cookies.clear()

    response = client.get("/login/github/callback?error=access_denied", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers.get("location") == "/login?github_error=oauth_provider_error&next=/"


def test_login_github_callback_uses_github_error_when_session_token_exchange_fails(monkeypatch):
    import app as app_module

    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-client-1")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret-1")
    monkeypatch.setattr("app.secrets.token_urlsafe", lambda _n: "fixed-state")
    client.cookies.clear()

    class DummyResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, data=None):
            return DummyResponse(200, {"access_token": "gh-token-1"})

        async def get(self, url, headers=None):
            if "api.github.com/user/emails" in url:
                return DummyResponse(200, [{"email": "david@berigny.org", "verified": True, "primary": True}])
            return DummyResponse(200, {"id": 12345, "login": "david"})

    async def fake_principal_registry_post(path: str, payload: dict[str, object]):
        assert path == "/api/principals/link/github/start"
        return 200, {
            "link_state": "linked",
            "principal": {"principal_did": "did:key:z6MkExisting"},
        }

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(app_module, "_principal_registry_post", fake_principal_registry_post)

    start = client.get("/login/github/start?next=/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    callback = client.get(f"/login/github/callback?state={state}&code=abc123", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers.get("location") == "/"
    cookies = callback.headers.get_list("set-cookie")
    assert any("ds_principal_did=did:key:z6MkExisting" in item for item in cookies)


def test_login_link_verify_completes_existing_principal_login(monkeypatch):
    import app as app_module

    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-client-1")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret-1")
    monkeypatch.setattr("app.secrets.token_urlsafe", lambda _n: "fixed-state")
    client.cookies.clear()

    class DummyResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, data=None):
            return DummyResponse(200, {"access_token": "gh-token-1"})

        async def get(self, url, headers=None):
            if "api.github.com/user/emails" in url:
                return DummyResponse(200, [{"email": "david@berigny.org", "verified": True, "primary": True}])
            return DummyResponse(200, {"id": 12345, "login": "david"})

    async def fake_principal_registry_post(path: str, payload: dict[str, object]):
        if path == "/api/principals/link/github/start":
            return 200, {
                "link_state": "verification_required",
                "challenge_id": "challenge-1",
                "masked_destination": "d***@berigny.org",
                "delivery_channel": "email",
                "principal_did": "did:key:z6MkExisting",
            }
        assert path == "/api/principals/link/github/verify"
        return 200, {"status": "ok", "principal": {"principal_did": "did:key:z6MkExisting"}}

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(app_module, "_principal_registry_post", fake_principal_registry_post)

    start = client.get("/login/github/start?next=/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    callback = client.get(f"/login/github/callback?state={state}&code=abc123", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers.get("location") == "/login/link?next=/&challenge=1"

    response = client.post("/login/link/verify", data={"code": "123456", "next": "/"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers.get("location") == "/"
    cookies = response.headers.get_list("set-cookie")
    assert any("ds_principal_did=did:key:z6MkExisting" in item for item in cookies)


def test_login_link_start_preserves_email_delivery_failure(monkeypatch):
    import app as app_module

    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-client-1")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret-1")
    monkeypatch.setattr("app.secrets.token_urlsafe", lambda _n: "fixed-state")
    client.cookies.clear()

    class DummyResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, data=None):
            return DummyResponse(200, {"access_token": "gh-token-1"})

        async def get(self, url, headers=None):
            if "api.github.com/user/emails" in url:
                return DummyResponse(200, [{"email": "david@berigny.org", "verified": True, "primary": True}])
            return DummyResponse(200, {"id": 12345, "login": "david"})

    call_count = 0

    async def fake_principal_registry_post(path: str, payload: dict[str, object]):
        nonlocal call_count
        assert path == "/api/principals/link/github/start"
        call_count += 1
        if call_count == 1:
            return 404, {"detail": {"error": "principal_link_not_found"}}
        return 503, {"detail": "email_delivery_not_configured"}

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(app_module, "_principal_registry_post", fake_principal_registry_post)

    start = client.get("/login/github/start?next=/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    callback = client.get(f"/login/github/callback?state={state}&code=abc123", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers.get("location") == "/login/link?next=/"

    response = client.post(
        "/login/link/start",
        data={"contact_channel": "email", "contact_value": "david@berigny.org", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers.get("location") == "/login/link?next=/&error=email_delivery_not_configured"


def test_login_link_page_shows_email_delivery_failure_copy(monkeypatch):
    import app as app_module

    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-client-1")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret-1")
    monkeypatch.setattr("app.secrets.token_urlsafe", lambda _n: "fixed-state")
    client.cookies.clear()

    class DummyResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, data=None):
            return DummyResponse(200, {"access_token": "gh-token-1"})

        async def get(self, url, headers=None):
            if "api.github.com/user/emails" in url:
                return DummyResponse(200, [{"email": "david@berigny.org", "verified": True, "primary": True}])
            return DummyResponse(200, {"id": 12345, "login": "david"})

    async def fake_principal_registry_post(path: str, payload: dict[str, object]):
        assert path == "/api/principals/link/github/start"
        return 404, {"detail": {"error": "principal_link_not_found"}}

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(app_module, "_principal_registry_post", fake_principal_registry_post)

    start = client.get("/login/github/start?next=/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    callback = client.get(f"/login/github/callback?state={state}&code=abc123", follow_redirects=False)
    assert callback.status_code == 303

    response = client.get("/login/link?next=/", follow_redirects=False)
    assert response.status_code == 200
    response = client.get("/login/link?next=/&error=email_delivery_failed", follow_redirects=False)
    assert response.status_code == 200
    assert "verification email could not be delivered" in response.text


def test_login_link_page_shows_email_sender_not_configured_copy(monkeypatch):
    import app as app_module

    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh-client-1")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh-secret-1")
    monkeypatch.setattr("app.secrets.token_urlsafe", lambda _n: "fixed-state")
    client.cookies.clear()

    class DummyResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None, data=None):
            return DummyResponse(200, {"access_token": "gh-token-1"})

        async def get(self, url, headers=None):
            if "api.github.com/user/emails" in url:
                return DummyResponse(200, [{"email": "david@berigny.org", "verified": True, "primary": True}])
            return DummyResponse(200, {"id": 12345, "login": "david"})

    async def fake_principal_registry_post(path: str, payload: dict[str, object]):
        assert path == "/api/principals/link/github/start"
        return 404, {"detail": {"error": "principal_link_not_found"}}

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(app_module, "_principal_registry_post", fake_principal_registry_post)

    start = client.get("/login/github/start?next=/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    callback = client.get(f"/login/github/callback?state={state}&code=abc123", follow_redirects=False)
    assert callback.status_code == 303

    response = client.get("/login/link?next=/&error=email_sender_not_configured", follow_redirects=False)
    assert response.status_code == 200
    assert "sender identity is not configured" in response.text


def test_login_post_redirects_to_github(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    client.cookies.clear()

    response = client.post("/login", data={"next": "/ui/history/chat-demo"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers.get("location") == "/login/github?next=/ui/history/chat-demo"


def test_login_post_redirects_to_github_when_linked_identity_exists(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    client.cookies.clear()
    client.cookies.set("ds_principal_did", "did:key:z6MkLinkedUser")

    response = client.post("/login", data={"next": "/ui/history/chat-demo"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers.get("location") == "/login/github?next=/ui/history/chat-demo"
    client.cookies.pop("ds_principal_did", None)


def test_home_page_renders_simplified_session_panel(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "off")
    client.cookies.clear()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    assert 'id="settings-panel"' in body
    assert 'id="agent-select"' in body
    assert 'id="ledger-select"' in body
    assert 'id="entity-select"' not in body
    assert 'id="panel-performance"' not in body
    assert 'id="panel-accuracy-rate"' not in body
    assert 'id="panel-total-cost"' not in body
    assert 'id="trust-panel-toggle"' not in body


def test_login_routes_support_head_requests(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_AUTH_MODE", "form")
    client.cookies.clear()

    login_response = client.head("/login?next=/", follow_redirects=False)
    assert login_response.status_code == 200

    github_response = client.head("/login/github?next=/", follow_redirects=False)
    assert github_response.status_code == 200


def test_vercel_feedback_probe_returns_no_content():
    client.cookies.clear()

    get_response = client.get("/.well-known/vercel/jwe", follow_redirects=False)
    assert get_response.status_code == 204

    head_response = client.head("/.well-known/vercel/jwe", follow_redirects=False)
    assert head_response.status_code == 204


def test_api_auth_identity_card_returns_verified_ui_state(monkeypatch):
    import app as app_module

    async def fake_fetch_middleware_identity_card(_request):
        return {
            "status": "ok",
            "identity_vc": {
                "verified": True,
                "verification_state": "verified",
                "reason_code": "verified",
                "principal_did": "did:key:z6MkVerified",
                "auth_method": "passkey",
                "session_jti": "sess-verified-1",
                "credential_ref": "cred:wallet:verified",
                "wallet_provider": "microsoft_authenticator",
                "principal_lookup_status": 200,
                "provisioning_lookup_status": 200,
                "ledger_access_ready": True,
                "ledger_id": "ledger:verified",
            },
            "usage_stats": {"chat_unit_cost": 0.0, "totals": {}},
            "eq9": {
                "eq9_posture_class": "P2",
                "trust_class": "T2",
                "reason_code": "baseline_satisfied",
                "failed_eq": None,
                "repair_actions": [],
            },
        }

    monkeypatch.setattr(app_module, "_fetch_middleware_identity_card", fake_fetch_middleware_identity_card)

    response = client.get("/api/auth/identity_card")
    assert response.status_code == 200
    body = response.json()
    identity = body["identity_vc"]
    assert identity["credential_ref"] == "cred:wallet:verified"
    assert identity["wallet_provider"] == "microsoft_authenticator"
    assert identity["principal_lookup_status"] == 200
    assert identity["provisioning_lookup_status"] == 200
    assert identity["ledger_access_ready"] is True
    assert identity["ledger_id"] == "ledger:verified"
    assert "principal_lookup_error" not in identity
    assert body["ui"]["panel_state"] == "verified"
    assert body["ui"]["headline"] == "Verified identity and posture"
    assert "Source authenticity and current posture are aligned" in body["ui"]["posture_copy"]
    assert "microsoft_authenticator" in body["ui"]["wallet_copy"]
    assert "Provisioned ledger access is ready" in body["ui"]["provisioning_copy"]


def test_api_auth_identity_card_returns_degraded_ui_state(monkeypatch):
    import app as app_module

    async def fake_fetch_middleware_identity_card(_request):
        return {
            "status": "ok",
            "identity_vc": {
                "verified": True,
                "verification_state": "verified",
                "reason_code": "verified",
                "principal_did": "did:key:z6MkVerified",
                "auth_method": "github",
                "session_jti": "sess-degraded-1",
                "principal_lookup_status": 200,
            },
            "usage_stats": {"chat_unit_cost": 0.0, "totals": {}},
            "eq9": {
                "eq9_posture_class": "P1",
                "trust_class": "T1",
                "reason_code": "baseline_satisfied",
                "failed_eq": "eq6",
                "repair_actions": ["link stronger credential", "refresh context"],
            },
        }

    monkeypatch.setattr(app_module, "_fetch_middleware_identity_card", fake_fetch_middleware_identity_card)

    response = client.get("/api/auth/identity_card")
    assert response.status_code == 200
    body = response.json()
    assert body["identity_vc"]["principal_lookup_status"] == 200
    assert body["ui"]["panel_state"] == "degraded"
    assert body["ui"]["posture_state"] == "degraded"
    assert body["ui"]["headline"] == "Verified identity, limited trust posture"
    assert "Next steps:" in body["ui"]["repair_copy"]


def test_api_auth_identity_card_reports_pending_provisioning(monkeypatch):
    import app as app_module

    async def fake_fetch_middleware_identity_card(_request):
        return {
            "status": "ok",
            "identity_vc": {
                "verified": True,
                "verification_state": "verified",
                "reason_code": "verified",
                "principal_did": "did:key:z6MkPending",
                "auth_method": "passkey",
                "session_jti": "sess-pending-1",
                "credential_ref": "cred:wallet:pending",
                "wallet_provider": "microsoft_authenticator",
                "provisioning_lookup_status": 200,
                "activation_state": "pending_provisioning",
                "ledger_access_ready": False,
            },
            "usage_stats": {"chat_unit_cost": 0.0, "totals": {}},
            "eq9": {
                "eq9_posture_class": "P2",
                "trust_class": "T2",
                "reason_code": "baseline_satisfied",
                "failed_eq": None,
                "repair_actions": [],
            },
        }

    monkeypatch.setattr(app_module, "_fetch_middleware_identity_card", fake_fetch_middleware_identity_card)

    response = client.get("/api/auth/identity_card")
    assert response.status_code == 200
    body = response.json()
    identity = body["identity_vc"]
    assert identity["provisioning_lookup_status"] == 200
    assert identity["activation_state"] == "pending_provisioning"
    assert identity["ledger_access_ready"] is False
    assert "Ledger provisioning and activation are still pending" in body["ui"]["provisioning_copy"]


def test_api_auth_identity_card_returns_unverified_ui_state(monkeypatch):
    import app as app_module

    async def fake_fetch_middleware_identity_card(_request):
        return {
            "status": "ok",
            "identity_vc": {
                "verified": False,
                "verification_state": "unverified",
                "reason_code": "verification_unavailable",
            },
            "usage_stats": {"chat_unit_cost": 0.0, "totals": {}},
            "eq9": {},
        }

    monkeypatch.setattr(app_module, "_fetch_middleware_identity_card", fake_fetch_middleware_identity_card)

    response = client.get("/api/auth/identity_card")
    assert response.status_code == 200
    body = response.json()
    assert body["ui"]["panel_state"] == "unverified"
    assert body["ui"]["headline"] == "Verification required"
    assert "No privileged access is granted" in body["ui"]["verification_copy"]


def test_api_auth_identity_card_surfaces_principal_lookup_failure(monkeypatch):
    import app as app_module

    async def fake_fetch_middleware_identity_card(_request):
        return {
            "status": "ok",
            "identity_vc": {
                "verified": True,
                "verification_state": "verified",
                "reason_code": "verified",
                "principal_did": "did:key:z6MkVerified",
                "auth_method": "passkey",
                "session_jti": "sess-verified-1",
                "principal_lookup_status": 503,
                "principal_lookup_error": "principal_registry_unreachable",
                "principal_lookup_text": "middleware unavailable",
                "credential_ref": None,
            },
            "usage_stats": {"chat_unit_cost": 0.0, "totals": {}},
            "eq9": {},
        }

    monkeypatch.setattr(app_module, "_fetch_middleware_identity_card", fake_fetch_middleware_identity_card)

    response = client.get("/api/auth/identity_card")
    assert response.status_code == 200
    body = response.json()
    identity = body["identity_vc"]
    assert identity["principal_lookup_status"] == 503
    assert identity["principal_lookup_error"] == "principal_registry_unreachable"
    assert identity["principal_lookup_text"] == "middleware unavailable"
    assert identity["credential_ref"] is None


def test_stamp_authenticated_session_populates_frontend_session():
    import app as app_module
    from utils.session import sessions

    sessions.pop("sess-github-stamp", None)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", b"ds_session=sess-github-stamp")],
        "scheme": "http",
        "server": ("testserver", 80),
    }
    request = Request(scope)
    app_module._stamp_authenticated_session(
        request=request,
        principal_did="did:key:z6MkGithubUser",
        auth_session={
            "jti": "sess-jti-123",
            "canonical_subject": "did:web:id.dualsubstrate.com:principals:wallet-user",
            "principal_display_name": "David Berigny",
        },
        auth_method="github",
    )
    session = app_module.get_session("sess-github-stamp")
    assert session.get("principal_did") == "did:key:z6MkGithubUser"
    assert session.get("principal_type") == "user"
    assert session.get("principal_id") == "wallet-user"
    assert session.get("principal_display_name") == "David Berigny"
    assert session.get("principal_canonical_subject") == "did:web:id.dualsubstrate.com:principals:wallet-user"
    assert session.get("auth_method") == "github"


def test_decode_coordinate_v2_key_order(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    class DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return DummyResponse(
                200,
                {
                    "coord": "chat-demo-session:WX-123",
                    "type": "WX",
                    "skim": {"one_line": "hi", "relevance": 0.9, "reasons": [], "recommended": [], "budgets": {}},
                    "walk": None,
                    "refs": {},
                    "payload": {},
                    "interpretation": {},
                    "governance": {},
                    "meta": {},
                },
            )

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    response = client.post("/api/decode_coordinate", json={"coordinate": "WX-123"})
    assert response.status_code == 200
    assert str(captured.get("url") or "").endswith("/api/decode_coordinate")
    assert captured.get("json") == {
        "coordinate": "WX-123",
        "ledger_id": "loam",
        "surface_id": app_module.settings.CHAT_SURFACE_ID,
    }
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


def test_commit_answer_proxies_to_middleware(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    async def fake_post_middleware_json(request, path: str, payload: dict[str, object], *, timeout: float = 20.0):
        captured["path"] = path
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {"status": "ok"}

    monkeypatch.setattr(app_module, "_post_middleware_json", fake_post_middleware_json)

    response = client.post(
        "/api/chat/commit-answer",
        json={"message": "hello", "reply": "world", "metadata": {"source": "test"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert captured["path"] == "/api/chat/commit-answer"
    payload = _as_dict(captured.get("payload"))
    assert payload["message"] == "hello"
    assert payload["reply"] == "world"


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
            "pipeline_events": [],
        }

    monkeypatch.setattr(app_module, "_run_openai_via_middleware_orchestrator", fake_orchestrator)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai",
            "messages": [{"role": "user", "content": "hello"}],
            "enable_ledger": False,
            "s_mode": "s1",
        },
    )
    assert response.status_code == 200
    base_payload = _as_dict(captured.get("base_payload"))
    metadata = _as_dict(base_payload.get("metadata"))
    policy_controls = _as_dict(metadata.get("policy_controls"))
    rejected = _as_str_list(policy_controls.get("rejected_overrides"))

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
            "pipeline_events": [],
        }

    monkeypatch.setattr(app_module, "_run_openai_via_middleware_orchestrator", fake_orchestrator)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "openai",
            "messages": [{"role": "user", "content": "hello"}],
            "enable_ledger": False,
            "s_mode": "s1",
            "principal_did": "did:key:test-user",
            "session_jti": "sess-123",
        },
        headers={"Authorization": "Bearer opaque-session-token"},
    )
    assert response.status_code == 200
    base_payload = _as_dict(captured.get("base_payload"))
    metadata = _as_dict(base_payload.get("metadata"))
    policy_controls = _as_dict(metadata.get("policy_controls"))
    rejected = _as_str_list(policy_controls.get("rejected_overrides"))

    assert base_payload.get("enable_ledger") is False
    assert base_payload.get("s_mode") == "s1"
    assert policy_controls.get("override_authorized") is True
    assert rejected == []


def test_commit_answer_includes_runtime_identity_metadata(monkeypatch):
    import app as app_module

    captured: dict[str, object] = {}

    async def fake_post_middleware_json(request, path: str, payload: dict[str, object], *, timeout: float = 20.0):
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    async def fake_verified_model_auth_context(_request):
        return {
            "identity_vc": {
                "principal_did": "did:key:z6MkVerified",
                "canonical_subject": "did:web:id.dualsubstrate.com:principals:verified",
                "canonical_subject_source": "did:web:principal",
                "credential_ref": "cred:wallet:verified",
                "standing_envelope_ref": "env:wallet:verified",
                "wallet_did": "did:ion:wallet123",
                "issuer_did": "did:web:id.dualsubstrate.com",
            },
            "usage_stats": {},
            "eq9": {},
        }

    monkeypatch.setattr(app_module, "_post_middleware_json", fake_post_middleware_json)
    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)

    response = client.post(
        "/api/chat/commit-answer",
        json={"message": "hello", "reply": "world", "metadata": {"source": "test"}},
    )

    assert response.status_code == 200
    payload = _as_dict(captured.get("payload"))
    metadata = _as_dict(payload.get("metadata"))
    runtime_identity = _as_dict(metadata.get("runtime_identity"))
    assert runtime_identity.get("ledger_canonical_subject") == "did:web:testserver:ledgers:loam"
    assert runtime_identity.get("principal_canonical_subject") == "did:web:id.dualsubstrate.com:principals:verified"
    vc_refs = _as_dict(runtime_identity.get("vc_refs"))
    assert vc_refs.get("credential_ref") == "cred:wallet:verified"
    assert vc_refs.get("standing_envelope_ref") == "env:wallet:verified"


def test_canonicalize_ledger_scope_value_strips_prefix_and_lowercases():
    import app as app_module

    assert app_module._canonicalize_ledger_scope_value("LOAM") == "loam"
    assert app_module._canonicalize_ledger_scope_value("ledger:LOAM") == "loam"
    assert app_module._canonicalize_ledger_scope_value("Loam-Root-01") == "loam-root-01"
    assert app_module._canonicalize_ledger_scope_value(None) == ""


def test_decode_coordinate_forwards_ledger_and_surface_scope(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_post_middleware_json(request, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"coord": "loam:WX-1"}

    monkeypatch.setattr(app_module, "_post_middleware_json", fake_post_middleware_json)

    response = client.post("/api/decode_coordinate", json={"coordinate": "loam:WX-1"})
    assert response.status_code == 200
    payload = _as_dict(captured.get("payload"))
    assert payload.get("coordinate") == "loam:WX-1"
    assert payload.get("ledger_id") == "loam"
    assert payload.get("surface_id") == app_module.settings.CHAT_SURFACE_ID


def test_delegated_codex_mode_fails_closed_when_principal_unset(monkeypatch):
    monkeypatch.setattr(app_module, "CODEX_PRINCIPAL_DID", "")

    async def fake_verified_model_auth_context(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkOperator",
                "session_jti": "sess-codex-missing",
                "auth_method": "wallet_verified_id",
            }
        }

    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)
    client.cookies.set(app_module.BACKEND_SESSION_TOKEN_COOKIE, "opaque-session-token")

    response = client.post(
        "/api/chat/smart_stream",
        json={
            "session_id": "test-codex-missing",
            "message": "hello",
            "history": [],
            "provider": "openai",
            "enable_ledger": True,
            "prompt_principal_mode": "codex",
        },
    )
    assert response.status_code == 400
    assert "codex_principal_not_configured" in response.text


def test_delegated_kimi_mode_fails_closed_when_principal_unset(monkeypatch):
    monkeypatch.setattr(app_module, "KIMI_PRINCIPAL_DID", "")

    async def fake_verified_model_auth_context(_request):
        return {
            "identity_vc": {
                "verified": True,
                "principal_did": "did:key:z6MkOperator",
                "session_jti": "sess-kimi-missing",
                "auth_method": "wallet_verified_id",
            }
        }

    monkeypatch.setattr(app_module, "_verified_model_auth_context", fake_verified_model_auth_context)
    client.cookies.set(app_module.BACKEND_SESSION_TOKEN_COOKIE, "opaque-session-token")

    response = client.post(
        "/api/chat/smart_stream",
        json={
            "session_id": "test-kimi-missing",
            "message": "hello",
            "history": [],
            "provider": "openai",
            "enable_ledger": True,
            "prompt_principal_mode": "kimi",
        },
    )
    assert response.status_code == 400
    assert "kimi_principal_not_configured" in response.text
