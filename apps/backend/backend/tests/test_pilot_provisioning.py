from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.account import router as account_router
from backend.api.admin import router as admin_router
from backend.api.auth import router as auth_router
from backend.services.pilot_provisioning import PILOT_PROVISIONING_JOBS_V1_KEY


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(admin_router)
    return TestClient(app)


def _signup_verify_signin(client: TestClient) -> dict:
    signup = client.post(
        "/auth/pilot/signup",
        json={
            "primary_contact": "owner@example.com",
            "owner_display_name": "Pilot Owner",
            "pilot_terms_acknowledgement": True,
            "idempotency_key": "signup-provisioning-001",
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
        "idempotency_key": "onboarding-provisioning-001",
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


def _wallet_verified_signup_payload(**overrides):
    payload = {
        "principal_did": "did:web:id.dualsubstrate.com:wallet:abc123",
        "display_name": "Kaoru Tanaka",
        "email": "kaoru@example.com",
        "wallet_did": "did:key:z6Mkabc123",
        "wallet_provider": "altme",
        "idempotency_key": "wallet-signup-key-001",
    }
    payload.update(overrides)
    return payload


def _wallet_verified_onboarding(client: TestClient, monkeypatch) -> dict:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    admin_headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-type": "admin",
        "x-principal-id": "ops-admin",
        "Authorization": "Bearer test-admin-token",
    }

    signup = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    signup_id = signup["signup"]["signup_id"]
    principal_did = signup["signup"]["principal_did"]

    # Approve
    client.post(
        f"/admin/account-requests/{signup_id}/decide",
        headers=admin_headers,
        json={"decision": "approve"},
    )

    # Authenticate
    token = client.post(
        "/auth/token",
        json={"principal_did": principal_did, "auth_method": "wallet_verified_id"},
    )
    assert token.status_code == 200
    session_token = token.json()["session"]["token"]

    # Submit onboarding
    headers = {"x-session-token": session_token}
    submit = client.post(
        "/account/current/onboarding",
        headers=headers,
        json=_onboarding_payload(primary_contact="kaoru@example.com"),
    )
    assert submit.status_code == 200
    return {"session_token": session_token, "signup": signup["signup"]}


def test_run_provisioning_requires_authenticated_session() -> None:
    response = _make_client().post("/account/current/provisioning/run")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def test_run_provisioning_requires_accepted_onboarding() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    response = client.post(
        "/account/current/provisioning/run",
        headers={"x-session-token": signin["session"]["token"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "onboarding_not_accepted"


def test_run_provisioning_creates_default_package_job_once() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)
    headers = {"x-session-token": signin["session"]["token"]}

    response = client.post("/account/current/provisioning/run", headers=headers)
    replay = client.post("/account/current/provisioning/run", headers=headers)

    assert response.status_code == 200
    body = response.json()
    job = body["job"]
    assert body["idempotent_replay"] is False
    assert body["provisioning"]["status"] == "succeeded"
    assert body["provisioning"]["job"]["status"] == "succeeded"
    assert job["job_id"].startswith("provjob:")
    assert job["package_version"] == "free_trial_default_v1"
    assert job["resource_counts"] == {"total": 7, "succeeded": 7, "failed": 0}
    assert {step["step_id"] for step in job["resource_steps"]} == {
        "dss_space",
        "ledger_runtime",
        "wallet_provider_binding",
        "owner_human_principal",
        "chat_surface",
        "share_surface",
        "document_surface",
    }
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["job"]["job_id"] == job["job_id"]


def test_run_provisioning_updates_current_status_and_workspace_access() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)
    token = signin["session"]["token"]
    headers = {"x-session-token": token}

    run = client.post("/account/current/provisioning/run", headers=headers)
    current = client.get("/account/current/provisioning", headers=headers)
    after_signin = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    workspace_access = client.get(
        "/auth/session/access?target=workspace_runtime",
        headers=headers,
    )

    assert run.status_code == 200
    assert current.status_code == 200
    provisioning = current.json()["provisioning"]
    assert provisioning["status"] == "succeeded"
    assert provisioning["job"]["status"] == "succeeded"
    assert len(provisioning["job"]["resource_steps"]) == 7
    assert after_signin.json()["routing"]["next_route"] == "account_workspace_landing"
    assert workspace_access.json()["access"]["allowed"] is True
    assert workspace_access.json()["access"]["reason"] == "workspace_ready"


def test_document_surface_is_provisioned_as_disabled_placeholder() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)

    response = client.post(
        "/account/current/provisioning/run",
        headers={"x-session-token": signin["session"]["token"]},
    )

    assert response.status_code == 200
    document_step = next(
        step for step in response.json()["job"]["resource_steps"] if step["step_id"] == "document_surface"
    )
    assert document_step["status"] == "succeeded"
    assert document_step["metadata"]["surface_type"] == "document"
    assert document_step["metadata"]["surface_status"] == "disabled"
    assert document_step["metadata"]["entitlement_ref"] == "free_trial.manual_only.document_surface"
    assert document_step["metadata"]["disabled_reason"] == "document_surface_not_enabled_for_pilot"
    assert document_step["metadata"]["admin_enablement"]["supported"] is True
    assert document_step["metadata"]["launch_enabled"] is False


def test_current_surfaces_requires_authenticated_session() -> None:
    response = _make_client().get("/account/current/surfaces")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def test_current_surfaces_waits_for_provisioning_job() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)

    response = client.get(
        "/account/current/surfaces",
        headers={"x-session-token": signin["session"]["token"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "provisioning_not_ready"
    assert response.json()["surfaces"] == []


def test_current_surfaces_exposes_ready_chat_binding_after_provisioning() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)
    headers = {"x-session-token": signin["session"]["token"]}
    client.post("/account/current/provisioning/run", headers=headers)

    response = client.get("/account/current/surfaces", headers=headers)

    assert response.status_code == 200
    body = response.json()
    chat = next(surface for surface in body["surfaces"] if surface["surface_type"] == "chat")
    assert body["status"] == "ok"
    assert chat["status"] == "ready"
    assert chat["ready"] is True
    assert chat["account_id"] == signin["account"]["account_id"]
    assert chat["dss_space_id"]
    assert chat["ledger_id"].startswith("ledger:")
    assert chat["tenant_id"].startswith("tenant:")
    assert chat["owner_principal_id"] == signin["principal_did"]
    assert chat["policy_scope"] == "free_trial_default"
    assert chat["missing_binding_fields"] == []
    assert chat["launch_metadata"] == {
        "launch_enabled": True,
        "target": "chat",
        "route": "/chat",
        "ledger_id": chat["ledger_id"],
        "tenant_id": chat["tenant_id"],
        "principal_id": chat["owner_principal_id"],
        "policy_scope": "free_trial_default",
    }


def test_broken_chat_surface_binding_fails_closed_without_default_fallback() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)
    headers = {"x-session-token": signin["session"]["token"]}
    run = client.post("/account/current/provisioning/run", headers=headers)
    assert run.status_code == 200
    raw = client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    job_id = run.json()["job"]["job_id"]
    job = payload["jobs"][job_id]
    for step in job["resource_steps"]:
        if step["step_id"] == "chat_surface":
            step["metadata"].pop("ledger_id")
            break
    client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY] = json.dumps(payload).encode()

    response = client.get("/account/current/surfaces", headers=headers)

    assert response.status_code == 200
    chat = next(surface for surface in response.json()["surfaces"] if surface["surface_type"] == "chat")
    assert chat["status"] == "requires_admin"
    assert chat["ready"] is False
    assert chat["ledger_id"] is None
    assert chat["missing_binding_fields"] == ["ledger_id"]
    assert chat["failure_reason"] == "surface_binding_incomplete"
    assert chat["launch_metadata"] == {
        "launch_enabled": False,
        "reason": "surface_binding_not_ready",
    }


def test_current_surfaces_exposes_ready_share_decode_binding_after_provisioning() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)
    headers = {"x-session-token": signin["session"]["token"]}
    client.post("/account/current/provisioning/run", headers=headers)

    response = client.get("/account/current/surfaces", headers=headers)

    assert response.status_code == 200
    share = next(surface for surface in response.json()["surfaces"] if surface["surface_type"] == "share_decode")
    assert share["status"] == "ready"
    assert share["ready"] is True
    assert share["account_id"] == signin["account"]["account_id"]
    assert share["dss_space_id"]
    assert share["ledger_id"].startswith("ledger:")
    assert share["tenant_id"].startswith("tenant:")
    assert share["owner_principal_id"] == signin["principal_did"]
    assert share["policy_scope"] == "free_trial_default"
    assert share["entitlement_ref"] == "free_trial.included.share_surfaces"
    assert share["allowed_actions"] == ["decode_coordinate", "view_public_object_status"]
    assert share["missing_binding_fields"] == []
    assert share["launch_metadata"] == {
        "launch_enabled": True,
        "target": "share_decode",
        "route": "/web4/decode",
        "ledger_id": share["ledger_id"],
        "tenant_id": share["tenant_id"],
        "principal_id": share["owner_principal_id"],
        "policy_scope": "free_trial_default",
        "allowed_actions": ["decode_coordinate", "view_public_object_status"],
    }


def test_broken_share_decode_surface_binding_fails_closed_without_global_fallback() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)
    headers = {"x-session-token": signin["session"]["token"]}
    run = client.post("/account/current/provisioning/run", headers=headers)
    assert run.status_code == 200
    raw = client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    job_id = run.json()["job"]["job_id"]
    job = payload["jobs"][job_id]
    for step in job["resource_steps"]:
        if step["step_id"] == "share_surface":
            step["metadata"].pop("tenant_id")
            break
    client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY] = json.dumps(payload).encode()

    response = client.get("/account/current/surfaces", headers=headers)

    assert response.status_code == 200
    share = next(surface for surface in response.json()["surfaces"] if surface["surface_type"] == "share_decode")
    assert share["status"] == "requires_admin"
    assert share["ready"] is False
    assert share["tenant_id"] is None
    assert share["missing_binding_fields"] == ["tenant_id"]
    assert share["failure_reason"] == "surface_binding_incomplete"
    assert share["launch_metadata"] == {
        "launch_enabled": False,
        "reason": "surface_binding_not_ready",
    }


def test_current_surfaces_exposes_document_placeholder_without_launch_route() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)
    headers = {"x-session-token": signin["session"]["token"]}
    client.post("/account/current/provisioning/run", headers=headers)

    response = client.get("/account/current/surfaces", headers=headers)

    assert response.status_code == 200
    document = next(surface for surface in response.json()["surfaces"] if surface["surface_type"] == "document")
    assert document["status"] == "disabled"
    assert document["ready"] is False
    assert document["account_id"] == signin["account"]["account_id"]
    assert document["dss_space_id"]
    assert document["ledger_id"].startswith("ledger:")
    assert document["tenant_id"].startswith("tenant:")
    assert document["owner_principal_id"] == signin["principal_did"]
    assert document["policy_scope"] == "free_trial_default"
    assert document["entitlement_ref"] == "free_trial.manual_only.document_surface"
    assert document["display_label"] == "Documents"
    assert document["status_copy"] == "Document workspace is not enabled for this pilot yet."
    assert document["admin_enablement"] == {
        "supported": True,
        "future_statuses": ["manual_enabled", "enabled"],
        "required_authority": "admin",
    }
    assert document["missing_binding_fields"] == []
    assert document["launch_metadata"] == {
        "launch_enabled": False,
        "reason": "document_surface_not_enabled_for_pilot",
    }


def test_broken_document_placeholder_binding_fails_closed() -> None:
    client = _make_client()
    signin = _accepted_onboarding(client)
    headers = {"x-session-token": signin["session"]["token"]}
    run = client.post("/account/current/provisioning/run", headers=headers)
    assert run.status_code == 200
    raw = client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    job_id = run.json()["job"]["job_id"]
    job = payload["jobs"][job_id]
    for step in job["resource_steps"]:
        if step["step_id"] == "document_surface":
            step["metadata"].pop("owner_principal_id")
            break
    client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY] = json.dumps(payload).encode()

    response = client.get("/account/current/surfaces", headers=headers)

    assert response.status_code == 200
    document = next(surface for surface in response.json()["surfaces"] if surface["surface_type"] == "document")
    assert document["status"] == "requires_admin"
    assert document["ready"] is False
    assert document["owner_principal_id"] is None
    assert document["missing_binding_fields"] == ["owner_principal_id"]
    assert document["failure_reason"] == "surface_binding_incomplete"
    assert document["launch_metadata"] == {
        "launch_enabled": False,
        "reason": "surface_binding_not_ready",
    }


def test_wallet_verified_provisioning_includes_wallet_metadata_in_job(monkeypatch) -> None:
    client = _make_client()
    result = _wallet_verified_onboarding(client, monkeypatch)
    headers = {"x-session-token": result["session_token"]}

    response = client.post("/account/current/provisioning/run", headers=headers)

    assert response.status_code == 200
    job = response.json()["job"]
    wallet_step = next(step for step in job["resource_steps"] if step["step_id"] == "wallet_provider_binding")
    assert wallet_step["metadata"]["wallet_provider"] == "altme"
    assert wallet_step["metadata"]["wallet_did"] == "did:key:z6Mkabc123"
    assert wallet_step["metadata"]["did_method"] == "key"


def test_wallet_verified_job_id_is_wallet_aware(monkeypatch) -> None:
    client = _make_client()
    result = _wallet_verified_onboarding(client, monkeypatch)
    headers = {"x-session-token": result["session_token"]}

    run1 = client.post("/account/current/provisioning/run", headers=headers)
    assert run1.status_code == 200
    job_id1 = run1.json()["job"]["job_id"]

    # Same account, same request — should replay idempotently
    run2 = client.post("/account/current/provisioning/run", headers=headers)
    assert run2.status_code == 200
    assert run2.json()["idempotent_replay"] is True
    assert run2.json()["job"]["job_id"] == job_id1
