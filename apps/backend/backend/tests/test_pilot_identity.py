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
            "idempotency_key": "signup-identity-001",
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


def test_current_identity_requires_authenticated_session() -> None:
    response = _make_client().get("/account/current/identity")

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "authentication_required"


def test_current_identity_shows_did_created_and_wallet_available() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    response = client.get(
        "/account/current/identity",
        headers={"x-session-token": signin["session"]["token"]},
    )

    assert response.status_code == 200
    body = response.json()["identity_status"]
    assert body["identity"]["did_state"] == "created"
    assert body["identity"]["did"] == signin["principal_did"]
    assert body["identity"]["high_trust_ready"] is False
    assert body["wallet"]["wallet_state"] == "available"
    assert body["wallet"]["required_for_day_one_access"] is False
    assert body["checklist_item"]["item_id"] == "wallet_linking"
    assert body["checklist_item"]["actionable"] is True
    assert body["checklist_item"]["blocking_day_one_access"] is False


def test_wallet_link_can_be_started_from_account_identity_surface() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    response = client.post(
        "/account/current/identity/wallet-link/start",
        headers={"x-session-token": signin["session"]["token"]},
        json={"provider": "microsoft_authenticator"},
    )

    assert response.status_code == 200
    body = response.json()["identity_status"]
    assert body["wallet"]["wallet_state"] == "in_progress"
    assert body["wallet"]["provider"] == "microsoft_authenticator"
    assert body["wallet"]["next_action"] == "complete_wallet_link"
    assert body["identity"]["high_trust_ready"] is False


def test_wallet_link_can_be_deferred_without_blocking_day_one_access() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    response = client.post(
        "/account/current/identity/wallet-link/defer",
        headers={"x-session-token": signin["session"]["token"]},
    )

    assert response.status_code == 200
    body = response.json()["identity_status"]
    assert body["wallet"]["wallet_state"] == "deferred"
    assert body["wallet"]["required_for_day_one_access"] is False
    assert body["checklist_item"]["actionable"] is True


def test_wallet_completion_marks_high_trust_ready_but_remains_separate_from_did_existence() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    start = client.post(
        "/account/current/identity/wallet-link/start",
        headers={"x-session-token": signin["session"]["token"]},
        json={"provider": "microsoft_authenticator"},
    )
    assert start.status_code == 200
    response = client.post(
        "/account/current/identity/wallet-link/complete",
        headers={"x-session-token": signin["session"]["token"]},
        json={
            "provider": "microsoft_authenticator",
            "wallet_did": "did:example:wallet-owner",
        },
    )

    assert response.status_code == 200
    body = response.json()["identity_status"]
    assert body["identity"]["did_state"] == "created"
    assert body["identity"]["high_trust_ready"] is True
    assert body["wallet"]["wallet_state"] == "linked"
    assert body["wallet"]["wallet_did"] == "did:example:wallet-owner"
    assert body["checklist_item"]["state"] == "complete"
    assert body["checklist_item"]["actionable"] is False


def test_incomplete_wallet_does_not_block_workspace_ready_access() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)
    token = signin["session"]["token"]
    # Simulate later provisioning completion without completing wallet linking.
    signups_raw = client.app.state.db[b"__pilot_signups_v1__"]
    import json

    signups_payload = json.loads(signups_raw.decode("utf-8"))
    signup_id = next(iter(signups_payload["signups"].keys()))
    record = signups_payload["signups"][signup_id]
    record["onboarding_status"] = "accepted"
    record["provisioning_status"] = "succeeded"
    client.app.state.db[b"__pilot_signups_v1__"] = json.dumps(signups_payload).encode()

    access = client.get(
        "/auth/session/access?target=workspace_runtime",
        headers={"x-session-token": token},
    )
    identity = client.get(
        "/account/current/identity",
        headers={"x-session-token": token},
    )

    assert access.status_code == 200
    assert access.json()["access"]["allowed"] is True
    assert access.json()["access"]["reason"] == "workspace_ready"
    assert identity.json()["identity_status"]["wallet"]["wallet_state"] == "available"
    assert identity.json()["identity_status"]["identity"]["high_trust_ready"] is False
