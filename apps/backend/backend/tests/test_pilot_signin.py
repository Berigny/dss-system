from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.auth import router as auth_router
from backend.services.session_tokens import mint_session_token


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(auth_router)
    return TestClient(app)


def _signup_and_verify(client: TestClient, *, contact: str = "owner@example.com") -> dict:
    signup = client.post(
        "/auth/pilot/signup",
        json={
            "primary_contact": contact,
            "owner_display_name": "Pilot Owner",
            "pilot_terms_acknowledgement": True,
            "idempotency_key": f"signup-{contact}",
        },
    ).json()
    client.post(
        "/auth/pilot/signup/verify",
        json={
            "signup_id": signup["signup"]["signup_id"],
            "verification_token": signup["trust_step"]["verification_token"],
        },
    )
    return signup


def _load_signups(client: TestClient) -> dict:
    raw = client.app.state.db[b"__pilot_signups_v1__"]
    return json.loads(raw.decode("utf-8"))["signups"]


def _persist_signups(client: TestClient, signups: dict) -> None:
    client.app.state.db[b"__pilot_signups_v1__"] = json.dumps(
        {"version": 1, "signups": signups},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _signin(client: TestClient, *, contact: str = "owner@example.com") -> dict:
    response = client.post("/auth/signin", json={"primary_contact": contact})
    assert response.status_code == 200
    return response.json()


def test_returning_verified_user_can_sign_in_and_routes_to_onboarding() -> None:
    client = _make_client()
    _signup_and_verify(client)

    body = _signin(client)

    assert body["authenticated"] is True
    assert body["principal_did"]
    assert body["session"]["token"]
    assert body["routing"]["next_route"] == "onboarding"
    assert body["routing"]["read_only"] is False


def test_returning_signin_rejects_unverified_signup_recoverably() -> None:
    client = _make_client()
    client.post(
        "/auth/pilot/signup",
        json={
            "primary_contact": "owner@example.com",
            "owner_display_name": "Pilot Owner",
            "pilot_terms_acknowledgement": True,
            "idempotency_key": "signup-unverified",
        },
    )

    response = client.post("/auth/signin", json={"primary_contact": "owner@example.com"})

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "pilot_signup_not_verified"
    assert response.json()["detail"]["recoverable"] is True
    assert response.json()["detail"]["next_route"] == "verification_or_recovery"


def test_session_reports_account_routing_for_signed_in_user() -> None:
    client = _make_client()
    _signup_and_verify(client)
    signin = _signin(client)

    response = client.get(
        "/auth/session",
        headers={"x-session-token": signin["session"]["token"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["routing"]["next_route"] == "onboarding"
    assert body["account"]["account_id"] == signin["account"]["account_id"]


def test_provisioned_user_routes_to_account_workspace_landing() -> None:
    client = _make_client()
    signup = _signup_and_verify(client)
    signups = _load_signups(client)
    record = signups[signup["signup"]["signup_id"]]
    record["onboarding_status"] = "accepted"
    record["provisioning_status"] = "succeeded"
    signups[signup["signup"]["signup_id"]] = record
    _persist_signups(client, signups)

    body = _signin(client)

    assert body["routing"]["next_route"] == "account_workspace_landing"


def test_paused_account_can_sign_in_but_workspace_runtime_is_blocked() -> None:
    client = _make_client()
    signup = _signup_and_verify(client)
    signups = _load_signups(client)
    record = signups[signup["signup"]["signup_id"]]
    record["onboarding_status"] = "accepted"
    record["provisioning_status"] = "succeeded"
    record["trial_state"] = "paused"
    signups[signup["signup"]["signup_id"]] = record
    _persist_signups(client, signups)
    signin = _signin(client)

    account_access = client.get(
        "/auth/session/access?target=account",
        headers={"x-session-token": signin["session"]["token"]},
    )
    workspace_access = client.get(
        "/auth/session/access?target=workspace_runtime",
        headers={"x-session-token": signin["session"]["token"]},
    )

    assert signin["routing"]["next_route"] == "account_landing_read_only"
    assert signin["routing"]["read_only"] is True
    assert account_access.json()["access"]["allowed"] is True
    assert account_access.json()["access"]["reason"] == "paused_read_access"
    assert workspace_access.json()["access"]["allowed"] is False
    assert workspace_access.json()["access"]["reason"] == "paused_read_only"


def test_signed_out_user_cannot_access_protected_route_guard() -> None:
    response = _make_client().get("/auth/session/access?target=workspace_runtime")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["access"]["allowed"] is False
    assert response.json()["access"]["reason"] == "authentication_required"


def test_signout_revokes_current_session() -> None:
    client = _make_client()
    _signup_and_verify(client)
    signin = _signin(client)
    token = signin["session"]["token"]

    signout = client.post("/auth/signout", headers={"x-session-token": token})
    after = client.get("/auth/session", headers={"x-session-token": token})

    assert signout.status_code == 200
    assert signout.json()["signed_out"] is True
    assert after.status_code == 401
    assert after.json()["detail"]["reason"] == "token_revoked"


def test_expired_session_token_requires_reauth() -> None:
    client = _make_client()
    signup = _signup_and_verify(client)
    principal_did = signup["signup"]["principal_did"]
    expired = mint_session_token(
        principal_did=principal_did,
        auth_method="pilot_signup",
        ttl_seconds=-1,
    )

    response = client.get(
        "/auth/session",
        headers={"x-session-token": expired["token"]},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "token_expired"
