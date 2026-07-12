"""Tests for DSS-142: Verified ID sign-out/sign-back-in re-entry."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.account import router as account_router
from backend.api.auth import router as auth_router


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
            "idempotency_key": "signup-reentry-001",
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
        "idempotency_key": "onboarding-reentry-001",
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


def _select_model(client: TestClient, headers: dict) -> None:
    select = client.post(
        "/account/current/model-library/select",
        headers=headers,
        json={
            "provider": "openrouter",
            "model_id": "anthropic/claude-3.5-sonnet",
            "api_key": "sk-or-test-key-123",
        },
    )
    assert select.status_code == 200


def _bootstrap_agent(client: TestClient, headers: dict) -> None:
    bootstrap = client.post("/account/current/principals/agent/bootstrap", headers=headers)
    assert bootstrap.status_code == 200


def test_reentry_preserves_onboarding_state_after_sign_out_sign_in() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    account_id = signin["account"]["account_id"]

    # Fresh sign-in (same account, new session token)
    fresh_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    assert fresh_signin.status_code == 200
    fresh_headers = {"x-session-token": fresh_signin.json()["session"]["token"]}

    onboarding = client.get("/account/current/onboarding", headers=fresh_headers)
    assert onboarding.status_code == 200
    body = onboarding.json()["onboarding"]
    assert body["status"] == "accepted"
    assert body["account_id"] == account_id
    assert body["next_route"] == "model_library_selection"


def test_reentry_does_not_duplicate_provisioning_job() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    # First provisioning run
    first = client.post("/account/current/provisioning/run", headers=headers)
    assert first.json()["idempotent_replay"] is True
    job_id = first.json()["job"]["job_id"]

    # Fresh sign-in and re-run
    fresh_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    fresh_headers = {"x-session-token": fresh_signin.json()["session"]["token"]}
    second = client.post("/account/current/provisioning/run", headers=fresh_headers)

    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert second.json()["job"]["job_id"] == job_id


def test_reentry_restores_model_principal_state() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    _select_model(client, headers)

    # Fresh sign-in
    fresh_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    fresh_headers = {"x-session-token": fresh_signin.json()["session"]["token"]}

    principals = client.get("/account/current/principals", headers=fresh_headers)
    assert principals.status_code == 200
    model_principals = [p for p in principals.json()["principals"] if p["principal_type"] == "model"]
    assert len(model_principals) == 1
    assert model_principals[0]["model_id"] == "anthropic/claude-3.5-sonnet"

    onboarding = client.get("/account/current/onboarding", headers=fresh_headers)
    assert onboarding.json()["onboarding"]["model_principal"]["selected"] is True
    assert onboarding.json()["onboarding"]["next_route"] == "agent_principal_bootstrap"


def test_reentry_restores_agent_principal_and_graph() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    _select_model(client, headers)
    _bootstrap_agent(client, headers)

    # Fresh sign-in
    fresh_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    fresh_headers = {"x-session-token": fresh_signin.json()["session"]["token"]}

    connections = client.get("/account/current/connections", headers=fresh_headers)
    assert connections.status_code == 200
    assert len(connections.json()["connections"]) == 5

    principals = client.get("/account/current/principals", headers=fresh_headers)
    agent_principals = [p for p in principals.json()["principals"] if p["principal_type"] == "agent"]
    assert len(agent_principals) == 1
    assert agent_principals[0]["status"] == "active"

    onboarding = client.get("/account/current/onboarding", headers=fresh_headers)
    assert onboarding.json()["onboarding"]["agent_principal"]["bootstrapped"] is True
    assert onboarding.json()["onboarding"]["next_route"] == "account_workspace_landing"


def test_reentry_prompt_readiness_aligns_with_principal_graph() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    # Before model selection: not prompt-ready
    onboarding1 = client.get("/account/current/onboarding", headers=headers)
    assert onboarding1.json()["onboarding"]["next_route"] == "model_library_selection"

    _select_model(client, headers)

    # After model selection but before agent bootstrap
    onboarding2 = client.get("/account/current/onboarding", headers=headers)
    assert onboarding2.json()["onboarding"]["next_route"] == "agent_principal_bootstrap"

    _bootstrap_agent(client, headers)

    # After agent bootstrap: prompt-ready
    onboarding3 = client.get("/account/current/onboarding", headers=headers)
    assert onboarding3.json()["onboarding"]["next_route"] == "account_workspace_landing"

    # Fresh sign-in should preserve prompt readiness
    fresh_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    fresh_headers = {"x-session-token": fresh_signin.json()["session"]["token"]}
    onboarding4 = client.get("/account/current/onboarding", headers=fresh_headers)
    assert onboarding4.json()["onboarding"]["next_route"] == "account_workspace_landing"


def test_reentry_setup_prompt_reflects_correct_incomplete_items() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    # Before model selection: setup prompt should show model and agent as incomplete
    prompt1 = client.get("/account/current/setup-prompt", headers=headers)
    assert prompt1.status_code == 200
    required1 = prompt1.json()["setup_prompt"]["required_item_ids"]
    assert "model_principal_selected" in required1
    assert "agent_principal_bootstrapped" in required1

    _select_model(client, headers)
    _bootstrap_agent(client, headers)

    # Fresh sign-in after completion
    fresh_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    fresh_headers = {"x-session-token": fresh_signin.json()["session"]["token"]}

    # After completion, only authorised_rep_invited remains incomplete
    prompt2 = client.get("/account/current/setup-prompt", headers=fresh_headers)
    required2 = prompt2.json()["setup_prompt"]["required_item_ids"]
    assert "model_principal_selected" not in required2
    assert "agent_principal_bootstrapped" not in required2


def test_provisioning_summary_includes_next_route_after_reentry() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    provisioning = client.get("/account/current/provisioning", headers=headers)
    assert provisioning.status_code == 200
    assert provisioning.json()["provisioning"]["next_route"] == "model_library_selection"

    _select_model(client, headers)
    _bootstrap_agent(client, headers)

    fresh_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    fresh_headers = {"x-session-token": fresh_signin.json()["session"]["token"]}

    provisioning2 = client.get("/account/current/provisioning", headers=fresh_headers)
    assert provisioning2.json()["provisioning"]["next_route"] == "account_workspace_landing"
