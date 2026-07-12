"""Tests for DSS-141: agent principal bootstrap and connection graph."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.account import router as account_router
from backend.api.auth import router as auth_router
from backend.services.agent_principal import (
    PILOT_AGENT_PRINCIPALS_V1_KEY,
    PILOT_PRINCIPAL_CONNECTIONS_V1_KEY,
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
            "idempotency_key": "signup-agent-001",
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
        "idempotency_key": "onboarding-agent-001",
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


def test_bootstrap_agent_requires_authenticated_session() -> None:
    response = _make_client().post("/account/current/principals/agent/bootstrap")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def test_bootstrap_agent_requires_provisioning_complete() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)
    headers = {"x-session-token": signin["session"]["token"]}

    response = client.post("/account/current/principals/agent/bootstrap", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "provisioning_not_complete"


def test_bootstrap_agent_requires_model_principal_selected() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    response = client.post("/account/current/principals/agent/bootstrap", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "model_principal_not_selected"


def test_bootstrap_agent_creates_agent_principal_and_graph() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    _select_model(client, headers)

    response = client.post("/account/current/principals/agent/bootstrap", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["idempotent_replay"] is False
    agent = body["agent_principal"]
    assert agent["principal_type"] == "agent"
    assert agent["account_id"] == signin["account"]["account_id"]
    assert agent["ledger_id"].startswith("ledger:")
    assert agent["status"] == "active"
    assert agent["owner_principal_id"]
    assert agent["model_principal_id"]


def test_bootstrap_agent_is_idempotent() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    _select_model(client, headers)

    first = client.post("/account/current/principals/agent/bootstrap", headers=headers)
    second = client.post("/account/current/principals/agent/bootstrap", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    assert first.json()["agent_principal"]["principal_id"] == second.json()["agent_principal"]["principal_id"]


def test_connections_returns_graph_after_bootstrap() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    _select_model(client, headers)
    bootstrap = client.post("/account/current/principals/agent/bootstrap", headers=headers)
    assert bootstrap.status_code == 200
    agent_id = bootstrap.json()["agent_principal"]["principal_id"]

    response = client.get("/account/current/connections", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["account_id"] == signin["account"]["account_id"]
    assert body["agent_principal"]["principal_id"] == agent_id
    connections = body["connections"]
    assert len(connections) == 5

    relation_types = {c["relation_type"] for c in connections}
    assert relation_types == {"owns", "acts_through", "bound_to", "can_use"}

    owns = next(c for c in connections if c["relation_type"] == "owns")
    assert owns["target_principal_id"] == agent_id

    acts = next(c for c in connections if c["relation_type"] == "acts_through")
    assert acts["source_principal_id"] == agent_id
    assert acts["target_principal_id"] == bootstrap.json()["agent_principal"]["model_principal_id"]

    bound = next(c for c in connections if c["relation_type"] == "bound_to")
    assert bound["source_principal_id"] == agent_id
    assert bound["target_principal_id"].startswith("ledger:")

    can_use_surfaces = [c for c in connections if c["relation_type"] == "can_use"]
    assert len(can_use_surfaces) == 2
    surface_types = {c.get("surface_type") for c in can_use_surfaces}
    assert surface_types == {"chat", "share_decode"}


def test_connections_requires_authenticated_session() -> None:
    response = _make_client().get("/account/current/connections")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def test_principals_returns_owner_model_and_agent() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    _select_model(client, headers)
    client.post("/account/current/principals/agent/bootstrap", headers=headers)

    response = client.get("/account/current/principals", headers=headers)

    assert response.status_code == 200
    principals = response.json()["principals"]
    assert len(principals) == 3

    owner = next(p for p in principals if p["principal_type"] == "human_owner")
    assert owner["source"] == "provisioning_job"

    model = next(p for p in principals if p["principal_type"] == "model")
    assert model["provider"] == "openrouter"

    agent = next(p for p in principals if p["principal_type"] == "agent")
    assert agent["owner_principal_id"] == owner["principal_id"]
    assert agent["model_principal_id"] == model["principal_id"]


def test_bootstrap_does_not_duplicate_connections_on_retry() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)
    _select_model(client, headers)

    client.post("/account/current/principals/agent/bootstrap", headers=headers)
    client.post("/account/current/principals/agent/bootstrap", headers=headers)

    response = client.get("/account/current/connections", headers=headers)
    connections = response.json()["connections"]
    assert len(connections) == 5

    # Verify no duplicate edge_ids
    edge_ids = [c["edge_id"] for c in connections]
    assert len(edge_ids) == len(set(edge_ids))


def test_connections_returns_empty_before_bootstrap() -> None:
    client = _make_client()
    signin, headers = _provisioned_account(client)

    response = client.get("/account/current/connections", headers=headers)

    assert response.status_code == 200
    assert response.json()["connections"] == []
    assert response.json()["agent_principal"] is None
