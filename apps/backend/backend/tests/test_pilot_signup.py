from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.auth import router as auth_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(auth_router)
    return TestClient(app)


def _pilot_signup_payload(**overrides):
    payload = {
        "primary_contact": "Owner@Example.com",
        "owner_display_name": "Pilot Owner",
        "pilot_terms_acknowledgement": True,
        "idempotency_key": "signup-key-001",
    }
    payload.update(overrides)
    return payload


def _load_principals(client: TestClient) -> dict:
    raw = client.app.state.db[b"__principals_v1__"]
    return json.loads(raw.decode("utf-8"))["principals"]


def test_pilot_signup_creates_pending_verification_record_and_trust_step() -> None:
    client = _make_client()

    response = client.post("/auth/pilot/signup", json=_pilot_signup_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["duplicate"] is False
    assert body["signup"]["primary_contact"] == "owner@example.com"
    assert body["signup"]["verification_status"] == "pending"
    assert body["signup"]["next_route"] == "verification_or_recovery"
    assert body["trust_step"]["status"] == "pending"
    assert body["trust_step"]["verification_token"]

    principals = _load_principals(client)
    principal = principals[body["signup"]["principal_did"]]
    assert principal["status"] == "pending_verification"
    assert principal["metadata"]["primary_contact"] == "owner@example.com"


def test_pilot_signup_duplicate_contact_returns_recoverable_existing_signup() -> None:
    client = _make_client()
    first = client.post("/auth/pilot/signup", json=_pilot_signup_payload()).json()

    response = client.post(
        "/auth/pilot/signup",
        json=_pilot_signup_payload(idempotency_key="signup-key-002"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "duplicate"
    assert body["recoverable"] is True
    assert body["duplicate"] is True
    assert body["signup"]["signup_id"] == first["signup"]["signup_id"]
    assert body["recovery"]["action"] == "sign_in_or_verify_existing_signup"


def test_pilot_signup_idempotency_key_replays_existing_signup_without_duplicate() -> None:
    client = _make_client()
    first = client.post("/auth/pilot/signup", json=_pilot_signup_payload()).json()

    response = client.post("/auth/pilot/signup", json=_pilot_signup_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["duplicate"] is False
    assert body["signup"]["signup_id"] == first["signup"]["signup_id"]


def test_pilot_signup_verify_activates_principal_and_routes_to_onboarding() -> None:
    client = _make_client()
    signup = client.post("/auth/pilot/signup", json=_pilot_signup_payload()).json()

    response = client.post(
        "/auth/pilot/signup/verify",
        json={
            "signup_id": signup["signup"]["signup_id"],
            "verification_token": signup["trust_step"]["verification_token"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["signup"]["verification_status"] == "verified"
    assert body["signup"]["next_route"] == "onboarding"
    assert body["principal"]["status"] == "active"

    principals = _load_principals(client)
    assert principals[signup["signup"]["principal_did"]]["status"] == "active"


def test_pilot_signup_verify_rejects_invalid_token_recoverably() -> None:
    client = _make_client()
    signup = client.post("/auth/pilot/signup", json=_pilot_signup_payload()).json()

    response = client.post(
        "/auth/pilot/signup/verify",
        json={
            "signup_id": signup["signup"]["signup_id"],
            "verification_token": "wrong-token",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "verification_token_invalid"
    assert response.json()["detail"]["recoverable"] is True


def test_pilot_signup_requires_valid_contact_and_terms_acknowledgement() -> None:
    client = _make_client()

    bad_contact = client.post(
        "/auth/pilot/signup",
        json=_pilot_signup_payload(primary_contact="not-an-email"),
    )
    missing_terms = client.post(
        "/auth/pilot/signup",
        json=_pilot_signup_payload(
            primary_contact="other@example.com",
            pilot_terms_acknowledgement=False,
        ),
    )

    assert bad_contact.status_code == 422
    assert bad_contact.json()["detail"]["error"] == "primary_contact_invalid"
    assert bad_contact.json()["detail"]["recoverable"] is True
    assert missing_terms.status_code == 422
    assert missing_terms.json()["detail"]["error"] == "pilot_terms_acknowledgement_required"
    assert missing_terms.json()["detail"]["recoverable"] is True


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


def test_wallet_verified_signup_creates_verified_record_and_auto_approves_first_principal() -> None:
    client = _make_client()

    response = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["duplicate"] is False
    assert body["signup"]["verification_status"] == "verified"
    assert body["signup"]["onboarding_status"] == "not_started"
    assert body["signup"]["approval_status"] == "approved"
    assert body["signup"]["next_route"] == "onboarding"
    assert body["trust_step"]["type"] == "wallet_verified"
    assert body["trust_step"]["status"] == "verified"

    principals = _load_principals(client)
    principal = principals[body["signup"]["principal_did"]]
    assert principal["status"] == "active"
    assert principal["metadata"]["primary_contact"] == "kaoru@example.com"
    assert principal["metadata"]["wallet_provider"] == "altme"
    assert principal["metadata"]["bootstrap_source"] == "wallet_verified_signup"
    assert principal["metadata"]["operator_approved_at"]

    # Verify signup record carries wallet metadata
    raw = client.app.state.db[b"__pilot_signups_v1__"]
    signups = json.loads(raw.decode("utf-8"))["signups"]
    signup = signups[body["signup"]["signup_id"]]
    assert signup["signup_method"] == "wallet_verified"
    assert signup["wallet"]["did"] == "did:key:z6Mkabc123"
    assert signup["wallet"]["provider"] == "altme"
    assert signup["wallet"]["state"] == "linked"
    assert signup["approval_status"] == "approved"
    assert "credential_offer" in signup


def test_second_wallet_verified_signup_stays_pending_approval() -> None:
    client = _make_client()
    client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload())

    response = client.post(
        "/auth/pilot/signup/wallet-verified",
        json=_wallet_verified_signup_payload(
            principal_did="did:web:id.dualsubstrate.com:wallet:second",
            wallet_did="did:key:z6Mksecond",
            email="second@example.com",
            idempotency_key="wallet-signup-key-002",
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["signup"]["approval_status"] != "approved"
    assert body["signup"]["next_route"] == "awaiting_operator_approval"

    principals = _load_principals(client)
    principal = principals[body["signup"]["principal_did"]]
    assert principal["status"] == "pending_approval"


def test_wallet_verified_signup_duplicate_wallet_did_returns_recoverable() -> None:
    client = _make_client()
    first = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()

    response = client.post(
        "/auth/pilot/signup/wallet-verified",
        json=_wallet_verified_signup_payload(idempotency_key="wallet-signup-key-002"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "duplicate"
    assert body["recoverable"] is True
    assert body["duplicate"] is True
    assert body["signup"]["signup_id"] == first["signup"]["signup_id"]
    assert body["recovery"]["action"] == "sign_in_or_wait_for_approval"


def test_wallet_verified_signup_duplicate_principal_did_returns_recoverable() -> None:
    client = _make_client()
    first = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()

    response = client.post(
        "/auth/pilot/signup/wallet-verified",
        json=_wallet_verified_signup_payload(
            wallet_did="did:key:z6Mkdifferent",
            idempotency_key="wallet-signup-key-002",
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "duplicate"
    assert body["recoverable"] is True
    assert body["duplicate"] is True
    assert body["signup"]["signup_id"] == first["signup"]["signup_id"]


def test_wallet_verified_signup_idempotency_key_replays_without_duplicate() -> None:
    client = _make_client()
    first = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()

    response = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["duplicate"] is False
    assert body["signup"]["signup_id"] == first["signup"]["signup_id"]


def test_wallet_verified_signup_rejects_invalid_email() -> None:
    client = _make_client()

    response = client.post(
        "/auth/pilot/signup/wallet-verified",
        json=_wallet_verified_signup_payload(email="not-an-email"),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "email_invalid"
    assert response.json()["detail"]["recoverable"] is True
