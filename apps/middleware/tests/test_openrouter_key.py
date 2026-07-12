from __future__ import annotations

import asyncio
import json
from pathlib import Path

import openai
import pytest
from fastapi.testclient import TestClient

import app as app_module
from api.llm import LLMClient, get_openrouter_api_key, set_openrouter_api_key
from config.settings import settings
from utils import openrouter_config as openrouter_config_module

client = TestClient(app_module.app)


@pytest.fixture
def isolated_openrouter_config(monkeypatch, tmp_path: Path):
    """Point the override file at a temp path and restore the effective key after the test."""
    config_path = tmp_path / "openrouter_config.json"
    monkeypatch.setattr(openrouter_config_module, "_CONFIG_PATH", config_path)
    original_key = settings.OPENROUTER_API_KEY
    original_env = app_module.os.environ.get("OPENROUTER_API_KEY")
    yield config_path
    settings.OPENROUTER_API_KEY = original_key
    if original_env is None:
        app_module.os.environ.pop("OPENROUTER_API_KEY", None)
    else:
        app_module.os.environ["OPENROUTER_API_KEY"] = original_env


class TestOpenRouterKeyLegacy:
    def test_get_status_unmasked_when_no_admin_token(self, monkeypatch, isolated_openrouter_config):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc123def456")
        # Clear any admin token so the local auth gate is open.
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        monkeypatch.delenv("TRUST_ANCHOR_ADMIN_TOKEN", raising=False)
        settings.OPENROUTER_API_KEY = "sk-or-v1-abc123def456"

        response = client.get("/api/control-plane/providers/openrouter/key")
        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is True
        assert body["source"] == "env"
        assert body["masked"] == "sk-o*************f456"

    def test_get_status_denied_when_admin_token_required(self, monkeypatch, isolated_openrouter_config):
        monkeypatch.setenv("ADMIN_TOKEN", "secret-admin-token")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc123def456")
        settings.OPENROUTER_API_KEY = "sk-or-v1-abc123def456"

        response = client.get("/api/control-plane/providers/openrouter/key")
        assert response.status_code == 401

        response = client.get(
            "/api/control-plane/providers/openrouter/key",
            headers={"x-admin-token": "secret-admin-token"},
        )
        assert response.status_code == 200
        assert response.json()["configured"] is True

    def test_set_key_persists_override(self, monkeypatch, isolated_openrouter_config):
        monkeypatch.setenv("ADMIN_TOKEN", "secret-admin-token")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        settings.OPENROUTER_API_KEY = ""

        response = client.post(
            "/api/control-plane/providers/openrouter/key",
            headers={"x-admin-token": "secret-admin-token"},
            json={"api_key": "sk-or-v1-newkey789"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is True
        assert body["source"] == "override"
        assert "newkey789" not in body["masked"]
        assert get_openrouter_api_key() == "sk-or-v1-newkey789"
        assert isolated_openrouter_config.exists()
        assert json.loads(isolated_openrouter_config.read_text())["api_key"] == "sk-or-v1-newkey789"

    def test_set_key_rejects_empty_key(self, monkeypatch, isolated_openrouter_config):
        monkeypatch.setenv("ADMIN_TOKEN", "secret-admin-token")
        response = client.post(
            "/api/control-plane/providers/openrouter/key",
            headers={"x-admin-token": "secret-admin-token"},
            json={"api_key": "   "},
        )
        assert response.status_code == 400

    def test_get_status_endpoint_no_masked_key(self, monkeypatch, isolated_openrouter_config):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc123def456")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        monkeypatch.delenv("TRUST_ANCHOR_ADMIN_TOKEN", raising=False)
        settings.OPENROUTER_API_KEY = "sk-or-v1-abc123def456"

        response = client.get("/api/control-plane/providers/openrouter/status")
        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is True
        assert body["source"] == "env"
        assert "masked" not in body


class TestOpenRouterKeyFastAPI:
    def test_fastapi_wrapper_proxies_openrouter_key_routes(self, monkeypatch):
        import fastapi_app as fastapi_app_module

        client_f = TestClient(fastapi_app_module.app)

        class _Resp:
            def __init__(self, status_code: int, content: bytes, content_type: str = "application/json") -> None:
                self.status_code = status_code
                self.content = content
                self.headers = {"content-type": content_type}

        calls: list[tuple[str, str]] = []

        async def fake_send_to_legacy(request, method: str, path: str, *, json_payload=None):
            calls.append((method, path))
            return _Resp(200, b'{"configured":true,"masked":"sk-****","source":"override"}')

        monkeypatch.setattr(fastapi_app_module, "_send_to_legacy", fake_send_to_legacy)

        get_resp = client_f.get("/api/control-plane/providers/openrouter/key")
        status_resp = client_f.get("/api/control-plane/providers/openrouter/status")
        post_resp = client_f.post(
            "/api/control-plane/providers/openrouter/key",
            json={"api_key": "sk-or-v1-test"},
        )

        assert get_resp.status_code == 200
        assert status_resp.status_code == 200
        assert post_resp.status_code == 200
        assert ("GET", "/api/control-plane/providers/openrouter/key") in calls
        assert ("GET", "/api/control-plane/providers/openrouter/status") in calls
        assert ("POST", "/api/control-plane/providers/openrouter/key") in calls


class TestOpenRouterBillingErrors:
    def test_is_billing_error_detects_http_402(self):
        class Exc402(Exception):
            status_code = 402

        assert LLMClient._is_billing_error(Exc402("payment required")) is True

    def test_is_billing_error_detects_http_429(self):
        class Exc429(Exception):
            status_code = 429

        assert LLMClient._is_billing_error(Exc429("rate limit")) is True

    def test_is_billing_error_detects_message_markers(self):
        assert LLMClient._is_billing_error(Exception("No available credit")) is True
        assert LLMClient._is_billing_error(Exception("insufficient_quota")) is True
        assert LLMClient._is_billing_error(Exception("rate limit exceeded")) is True

    def test_is_billing_error_ignores_unrelated_errors(self):
        assert LLMClient._is_billing_error(Exception("model not found")) is False
        assert LLMClient._is_billing_error(Exception("timeout")) is False

    def test_generate_response_returns_provider_billing_payload(self, monkeypatch):
        llm = LLMClient()
        llm.local_base = ""
        llm.local_client = None
        llm.openrouter_client = openai.AsyncOpenAI(
            api_key="sk-or-test",
            base_url="https://openrouter.ai/api/v1",
        )

        async def fake_create(**_kwargs):
            raise openai.APIError("No available credit", request=None, body=None)

        monkeypatch.setattr(llm.openrouter_client.chat.completions, "create", fake_create)

        response = asyncio.run(
            llm.generate_response("hello", agent="openai/gpt-4o", system_prompt="")
        )
        assert response["error"] == "provider_billing"
        assert "no available credit" in response["text"].lower()
        assert response["detail"] == "https://openrouter.ai/settings/billing"
        assert response["cost"] == 0
        assert response["model"] == "openai/gpt-4o"

    def test_stream_response_result_future_returns_provider_billing_payload(self, monkeypatch):
        llm = LLMClient()
        llm.local_base = ""
        llm.local_client = None
        llm.openrouter_client = openai.AsyncOpenAI(
            api_key="sk-or-test",
            base_url="https://openrouter.ai/api/v1",
        )

        async def fake_create(**_kwargs):
            raise openai.APIError("insufficient credit", request=None, body=None)

        monkeypatch.setattr(llm.openrouter_client.chat.completions, "create", fake_create)

        stream, result_future = asyncio.run(
            llm.stream_response("hello", agent="openai/gpt-4o", system_prompt="")
        )
        # Drain the (empty) generator so the exception path resolves the future.
        asyncio.run(self._consume(stream))
        response = result_future.result()
        assert response["error"] == "provider_billing"
        assert response["detail"] == "https://openrouter.ai/settings/billing"

    @staticmethod
    async def _consume(stream):
        async for _ in stream:
            pass

    def test_openai_chat_completions_returns_structured_billing_error(self, monkeypatch):
        monkeypatch.setattr(app_module, "OPENAI_COMPAT_USE_PIPELINE", False)

        async def fake_generate_response(**_kwargs):
            return {
                "error": "provider_billing",
                "text": "OpenRouter account has no available credit.",
                "detail": "https://openrouter.ai/settings/billing",
                "cost": 0,
                "tokens": {},
                "model": "openai/gpt-4o",
            }

        monkeypatch.setattr(app_module.llm, "generate_response", fake_generate_response)

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "openai/gpt-4o",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert response.status_code == 402
        body = response.json()
        assert body["error"] == "provider_billing"
        assert "billing" in body["detail"]
