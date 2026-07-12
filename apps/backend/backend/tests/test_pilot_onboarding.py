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
            "idempotency_key": "signup-onboarding-001",
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
        "idempotency_key": "onboarding-key-001",
    }
    payload.update(overrides)
    return payload


def test_current_onboarding_requires_authenticated_session() -> None:
    response = _make_client().get("/account/current/onboarding")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def test_current_onboarding_reports_not_started_for_verified_user() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    response = client.get(
        "/account/current/onboarding",
        headers={"x-session-token": signin["session"]["token"]},
    )

    assert response.status_code == 200
    onboarding = response.json()["onboarding"]
    assert onboarding["status"] == "not_started"
    assert onboarding["next_route"] == "onboarding"


def test_submit_onboarding_accepts_payload_and_triggers_provisioning_once() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)
    headers = {"x-session-token": signin["session"]["token"]}

    response = client.post(
        "/account/current/onboarding",
        headers=headers,
        json=_onboarding_payload(),
    )
    replay = client.post(
        "/account/current/onboarding",
        headers=headers,
        json=_onboarding_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["onboarding"]["status"] == "accepted"
    assert body["onboarding"]["payload"]["workspace_or_dss_space_label"] == "Pilot DSS Space"
    assert body["provisioning"]["status"] == "queued"
    assert body["provisioning"]["request_id"].startswith("provreq:")
    assert body["provisioning"]["next_route"] == "provisioning_status"
    assert body["idempotent_replay"] is False
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["provisioning"]["request_id"] == body["provisioning"]["request_id"]


def test_onboarding_submission_updates_signin_and_access_routing_to_provisioning() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)
    token = signin["session"]["token"]

    submit = client.post(
        "/account/current/onboarding",
        headers={"x-session-token": token},
        json=_onboarding_payload(),
    )
    assert submit.status_code == 200
    after = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})
    workspace_access = client.get(
        "/auth/session/access?target=workspace_runtime",
        headers={"x-session-token": token},
    )
    provisioning_access = client.get(
        "/auth/session/access?target=provisioning_status",
        headers={"x-session-token": token},
    )

    assert after.status_code == 200
    assert after.json()["routing"]["next_route"] == "provisioning_status"
    assert workspace_access.json()["access"]["allowed"] is False
    assert workspace_access.json()["access"]["reason"] == "workspace_not_ready"
    assert provisioning_access.json()["access"]["allowed"] is True
    assert provisioning_access.json()["access"]["reason"] == "provisioning_visible"


def test_current_provisioning_reports_status_after_onboarding_submission() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)
    headers = {"x-session-token": signin["session"]["token"]}
    client.post("/account/current/onboarding", headers=headers, json=_onboarding_payload())

    response = client.get("/account/current/provisioning", headers=headers)

    assert response.status_code == 200
    provisioning = response.json()["provisioning"]
    assert provisioning["status"] == "queued"
    assert provisioning["trigger_source"] == "accepted_onboarding_submission"
    assert provisioning["next_route"] == "provisioning_status"


def test_onboarding_validation_blocks_missing_required_fields() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    response = client.post(
        "/account/current/onboarding",
        headers={"x-session-token": signin["session"]["token"]},
        json=_onboarding_payload(pilot_use_case=""),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "pilot_use_case_required"


def test_onboarding_rejects_contact_conflict() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    response = client.post(
        "/account/current/onboarding",
        headers={"x-session-token": signin["session"]["token"]},
        json=_onboarding_payload(primary_contact="other@example.com"),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "primary_contact_conflict"
