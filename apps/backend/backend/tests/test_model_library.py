"""Tests for DSS-140: model-library picker and model principal seeding."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.account import router as account_router
from backend.api.auth import router as auth_router
from backend.services.model_library import (
    PILOT_MODEL_PRINCIPALS_V1_KEY,
    PILOT_PROVIDER_CONFIGS_V1_KEY,
)


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(auth_router)
    app.include_router(account_router)
    return TestClient(app)


def _signup_verify_signin(client: TestClient) -> dict:
    signup = client.post(
        "/auth/pilot/signup",
        json={
            "primary_contact": "owner@example.com",
            "owner_display_name": "Pilot Owner",
            "pilot_terms_acknowledgement": True,
            "idempotency_key": "signup-model-lib-001",
        },
    ).json()
    verify = client.post(
        "/auth/pilot/signup/verify",
        json={
            "signup_id": signup["signup"]["signup_id"],
            "verification_token": signup["trust_step"]["verification_token"],
        },
    )
    assert verify.status_code == 200
    signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    assert signin.status_code == 200
    return signin.json()


def _onboarding_payload(**overrides):
    payload = {
        "owner_display_name": "Pilot Owner",
        "workspace_or_dss_space_label": "Pilot DSS Space",
        "primary_contact": "owner@example.com",
        "pilot_use_case": "Evaluate DSS for governed memory workflows.",
        "free_trial_scope_acknowledgement": True,
        "idempotency_key": "onboarding-model-lib-001",
    }
    payload.update(overrides)
    return payload


def _accepted_onboarding(client: TestClient) -> dict:
    signin = _signup_verify_signin(client)
    headers = {"x-session-token": signin["session"]["token"]}
    submit = client.post(
        "/account/current/onboarding",
        headers=headers,
        json=_onboarding_payload(),
    )
    assert submit.status_code == 200
    return signin


def _provisioned_account(client: TestClient) -> tuple[dict, dict]:
    signin = _accepted_onboarding(client)
    headers = {"x-session-token": signin["session"]["token"]}
    run = client.post("/account/current/provisioning/run", headers=headers)
    assert run.status_code == 200
    return signin, headers


def test_model_library_requires_authenticated_session() -> None:
    response = _make_client().get("/account/current/model-library")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def test_model_library_returns_providers_after_provisioning() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    response = client.get("/account/current/model-library", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["account_id"] == signin["account"]["account_id"]
    providers = body["providers"]
    assert len(providers) == 6
    provider_ids = {p["provider_id"] for p in providers}
    assert provider_ids == {
        "openrouter",
        "azure_ai_foundry",
        "google_cloud_vertex_ai",
        "hugging_face",
        "ollama",
        "custom",
    }
    openrouter = next(p for p in providers if p["provider_id"] == "openrouter")
    assert openrouter["display_name"] == "OpenRouter"
    assert openrouter["auth_type"] == "api_key"
    assert len(openrouter["models"]) >= 4
    assert "system_configured" in openrouter
    assert "account_configured" in openrouter
    assert openrouter["account_configured"] is False


def test_select_model_requires_authenticated_session() -> None:
    response = _make_client().post("/account/current/model-library/select", json={
        "provider": "openrouter",
        "model_id": "anthropic/claude-3.5-sonnet",
    })

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def test_select_model_creates_model_principal() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    response = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
            "api_key": "sk-or-test-key-123",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["idempotent_replay"] is False
    mp = body["model_principal"]
    assert mp["principal_type"] == "model"
    assert mp["provider"] == "openrouter"
    assert mp["model_id"] == "anthropic/claude-3.5-sonnet"
    assert mp["display_name"] == "Claude 3.5 Sonnet"
    assert mp["account_id"] == signin["account"]["account_id"]
    assert mp["ledger_id"].startswith("ledger:")
    assert mp["status"] == "active"
    assert mp["credential_ref"].startswith("credref:openrouter:")


def test_select_model_is_idempotent() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    first = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
            "api_key": "sk-or-test-key-123",
        },
    )
    second = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
            "api_key": "sk-or-test-key-456",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    assert first.json()["model_principal"]["principal_id"] == second.json()["model_principal"]["principal_id"]


def test_select_model_rejects_unknown_provider() -> None:
    client = _make_client()
    _signin, headers = _provisioned_account(client)

    response = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "unknown_provider",
            "model_id": "some-model",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unknown_provider"


def test_select_model_rejects_unknown_model() -> None:
    client = _make_client()
    _signin, headers = _provisioned_account(client)

    response = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "unknown/model-v999",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unknown_model"


def test_select_model_requires_api_key_for_api_key_auth() -> None:
    client = _make_client()
    _signin, headers = _provisioned_account(client)

    response = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "api_key_required"


def test_select_model_allows_ollama_without_api_key() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    response = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "ollama",
            "model_id": "llama3.1",
        },
    )

    assert response.status_code == 200
    assert response.json()["model_principal"]["provider"] == "ollama"
    assert response.json()["model_principal"]["model_id"] == "llama3.1"


def test_principals_returns_owner_and_model_principals() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
            "api_key": "sk-or-test-key-123",
        },
    )

    response = client.get("/account/current/principals", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["account_id"] == signin["account"]["account_id"]
    principals = body["principals"]
    assert len(principals) == 2
    owner = next(p for p in principals if p["principal_type"] == "human_owner")
    assert owner["source"] == "provisioning_job"
    model = next(p for p in principals if p["principal_type"] == "model")
    assert model["provider"] == "openrouter"
    assert model["model_id"] == "anthropic/claude-3.5-sonnet"


def test_model_library_survives_sign_out_sign_back_in() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    account_id = signin["account"]["account_id"]

    client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
            "api_key": "sk-or-test-key-123",
        },
    )

    # Simulate fresh sign-in (same account, new session)
    fresh_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    assert fresh_signin.status_code == 200
    fresh_headers = {"x-session-token": fresh_signin.json()["session"]["token"]}

    principals = client.get("/account/current/principals", headers=fresh_headers)
    assert principals.status_code == 200
    model_principals = [p for p in principals.json()["principals"] if p["principal_type"] == "model"]
    assert len(model_principals) == 1
    assert model_principals[0]["model_id"] == "anthropic/claude-3.5-sonnet"

    # Re-selecting should be idempotent
    reselect = client.post(
        "/account/current/model-library/select",
        headers=fresh_headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
        },
    )
    assert reselect.status_code == 200
    assert reselect.json()["idempotent_replay"] is True

    # Should still only have one model principal
    principals2 = client.get("/account/current/principals", headers=fresh_headers)
    model_principals2 = [p for p in principals2.json()["principals"] if p["principal_type"] == "model"]
    assert len(model_principals2) == 1


def test_model_library_persists_provider_config() -> None:
    client = _make_client()
    _signin, headers = _provisioned_account(client)

    client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
            "api_key": "sk-or-test-key-789",
            "base_url": "https://custom.openrouter.ai/api/v1",
        },
    )

    raw = client.app.state.db[PILOT_PROVIDER_CONFIGS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    configs = payload["configs"]
    assert any("openrouter" in k for k in configs.keys())
    config = next(v for k, v in configs.items() if "openrouter" in k)
    assert config["api_key"] == "sk-or-test-key-789"
    assert config["base_url"] == "https://custom.openrouter.ai/api/v1"


def test_principals_returns_only_owner_before_model_selection() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    response = client.get("/account/current/principals", headers=headers)

    assert response.status_code == 200
    principals = response.json()["principals"]
    assert len(principals) == 1
    assert principals[0]["principal_type"] == "human_owner"


def test_custom_provider_requires_base_url() -> None:
    client = _make_client()
    _signin, headers = _provisioned_account(client)

    response = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "custom",
            "model_id": "my-custom-model",
            "api_key": "custom-key-123",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "base_url_required_for_custom_provider"


def test_custom_provider_creates_model_principal() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    response = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "custom",
            "model_id": "my-custom-model",
            "api_key": "custom-key-123",
            "base_url": "https://my-api.example.com/v1",
        },
    )

    assert response.status_code == 200
    mp = response.json()["model_principal"]
    assert mp["principal_type"] == "model"
    assert mp["provider"] == "custom"
    assert mp["model_id"] == "my-custom-model"
    assert mp["display_name"] == "my-custom-model"
    assert mp["status"] == "active"

    # Verify config persisted base_url
    raw = client.app.state.db[PILOT_PROVIDER_CONFIGS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    config = next(v for k, v in payload["configs"].items() if "custom" in k)
    assert config["base_url"] == "https://my-api.example.com/v1"


def test_custom_provider_allows_any_model_id() -> None:
    client = _make_client()
    _signin, headers = _provisioned_account(client)

    response = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "custom",
            "model_id": "org/very-special-model:v2",
            "api_key": "key",
            "base_url": "https://api.example.com",
        },
    )

    assert response.status_code == 200
    assert response.json()["model_principal"]["model_id"] == "org/very-special-model:v2"
