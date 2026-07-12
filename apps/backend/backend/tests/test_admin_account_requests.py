from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import PILOT_SIGNUPS_V1_KEY, PRINCIPAL_REGISTRY_V1_KEY, public_router, router as admin_router
from backend.api.auth import router as auth_router
from backend.api.wallet import router as wallet_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(admin_router)
    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(wallet_router)
    return TestClient(app)


@pytest.fixture
def admin_headers(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    return {
        "x-admin-token": "test-admin-token",
        "x-principal-type": "admin",
        "x-principal-id": "ops-admin",
        "Authorization": "Bearer test-admin-token",
    }


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


def test_list_account_requests_returns_wallet_verified_signups(admin_headers) -> None:
    client = _make_client()
    signup = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()

    response = client.get("/admin/account-requests", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["requests"][0]["signup_id"] == signup["signup"]["signup_id"]
    assert body["requests"][0]["signup_method"] == "wallet_verified"


def test_list_account_requests_filters_by_status(admin_headers) -> None:
    client = _make_client()
    client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()

    pending = client.get("/admin/account-requests?status=pending", headers=admin_headers)
    approved = client.get("/admin/account-requests?status=approved", headers=admin_headers)

    assert pending.status_code == 200
    assert approved.status_code == 200
    # First signup is auto-approved
    assert pending.json()["count"] == 0
    assert approved.json()["count"] == 1


def test_second_signup_stays_pending(admin_headers) -> None:
    client = _make_client()
    client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    second = client.post(
        "/auth/pilot/signup/wallet-verified",
        json=_wallet_verified_signup_payload(
            principal_did="did:web:id.dualsubstrate.com:wallet:second",
            wallet_did="did:key:z6Mksecond",
            email="second@example.com",
            idempotency_key="wallet-signup-key-002",
        ),
    ).json()

    pending = client.get("/admin/account-requests?status=pending", headers=admin_headers)
    approved = client.get("/admin/account-requests?status=approved", headers=admin_headers)

    assert pending.status_code == 200
    assert approved.status_code == 200
    assert pending.json()["count"] == 1
    assert approved.json()["count"] == 1
    assert second["signup"].get("approval_status", "pending") != "approved"


def test_approve_account_request_activates_principal(admin_headers) -> None:
    client = _make_client()
    signup = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    signup_id = signup["signup"]["signup_id"]
    principal_did = signup["signup"]["principal_did"]

    response = client.post(
        f"/admin/account-requests/{signup_id}/decide",
        headers=admin_headers,
        json={"decision": "approve", "reason": "Verified pilot user"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["decision"] == "approve"
    assert body["principal_status"] == "active"

    raw = client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY]
    principals = json.loads(raw.decode("utf-8"))["principals"]
    principal = principals[principal_did]
    assert principal["status"] == "active"
    assert principal["metadata"]["operator_approved_at"]


def test_reject_account_request_sets_principal_rejected(admin_headers) -> None:
    client = _make_client()
    signup = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    signup_id = signup["signup"]["signup_id"]
    principal_did = signup["signup"]["principal_did"]

    response = client.post(
        f"/admin/account-requests/{signup_id}/decide",
        headers=admin_headers,
        json={"decision": "reject", "reason": "Organisation not recognised"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["decision"] == "reject"
    assert body["principal_status"] == "rejected"

    raw = client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY]
    principals = json.loads(raw.decode("utf-8"))["principals"]
    principal = principals[principal_did]
    assert principal["status"] == "rejected"
    assert principal["metadata"]["rejection_reason"] == "Organisation not recognised"


def test_approved_principal_can_authenticate(admin_headers) -> None:
    client = _make_client()
    # Consume the auto-approve slot with a first signup
    client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    # Use a second signup to exercise the pending-then-approved flow
    signup = client.post(
        "/auth/pilot/signup/wallet-verified",
        json=_wallet_verified_signup_payload(
            principal_did="did:web:id.dualsubstrate.com:wallet:auth-test",
            wallet_did="did:key:z6Mkauth",
            email="auth-test@example.com",
            idempotency_key="wallet-signup-key-auth",
        ),
    ).json()
    signup_id = signup["signup"]["signup_id"]
    principal_did = signup["signup"]["principal_did"]

    # Before approval: auth/token should fail
    token_before = client.post(
        "/auth/token",
        json={"principal_did": principal_did, "auth_method": "wallet_verified_id"},
    )
    assert token_before.status_code == 403
    assert token_before.json()["detail"]["error"] == "principal_not_active"

    # Approve
    client.post(
        f"/admin/account-requests/{signup_id}/decide",
        headers=admin_headers,
        json={"decision": "approve"},
    )

    # After approval: auth/token should succeed
    token_after = client.post(
        "/auth/token",
        json={"principal_did": principal_did, "auth_method": "wallet_verified_id"},
    )
    assert token_after.status_code == 200
    assert token_after.json()["session"]["token"]


def test_rejected_principal_cannot_authenticate(admin_headers) -> None:
    client = _make_client()
    signup = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    signup_id = signup["signup"]["signup_id"]
    principal_did = signup["signup"]["principal_did"]

    client.post(
        f"/admin/account-requests/{signup_id}/decide",
        headers=admin_headers,
        json={"decision": "reject", "reason": "Spam"},
    )

    token = client.post(
        "/auth/token",
        json={"principal_did": principal_did, "auth_method": "wallet_verified_id"},
    )
    assert token.status_code == 403
    assert token.json()["detail"]["error"] == "principal_not_active"


def test_challenge_endpoint_does_not_bypass_pending_approval(admin_headers) -> None:
    client = _make_client()
    # Consume the auto-approve slot with a first signup
    client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    signup = client.post(
        "/auth/pilot/signup/wallet-verified",
        json=_wallet_verified_signup_payload(
            principal_did="did:web:id.dualsubstrate.com:wallet:challenge-test",
            wallet_did="did:key:z6Mkchallenge",
            email="challenge-test@example.com",
            idempotency_key="wallet-signup-key-challenge",
        ),
    ).json()
    principal_did = signup["signup"]["principal_did"]

    # Challenge endpoint should NOT overwrite pending_approval to active
    challenge = client.post(
        "/auth/challenge",
        json={"principal_did": principal_did, "origin": "http://localhost:3000"},
    )
    assert challenge.status_code == 200

    raw = client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY]
    principals = json.loads(raw.decode("utf-8"))["principals"]
    assert principals[principal_did]["status"] == "pending_approval"


def test_decide_rejects_invalid_decision(admin_headers) -> None:
    client = _make_client()
    signup = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    signup_id = signup["signup"]["signup_id"]

    response = client.post(
        f"/admin/account-requests/{signup_id}/decide",
        headers=admin_headers,
        json={"decision": "invalid"},
    )
    assert response.status_code == 422


def test_decide_returns_404_for_missing_signup(admin_headers) -> None:
    client = _make_client()

    response = client.post(
        "/admin/account-requests/nonexistent/decide",
        headers=admin_headers,
        json={"decision": "approve"},
    )
    assert response.status_code == 404


def test_approval_generates_credential_offer(admin_headers) -> None:
    client = _make_client()
    signup = client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    signup_id = signup["signup"]["signup_id"]

    response = client.post(
        f"/admin/account-requests/{signup_id}/decide",
        headers=admin_headers,
        json={"decision": "approve"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "credential_offer" in body
    assert body["credential_offer"]["credential_issuer"]
    assert body["credential_offer"]["grants"]

    # Retrieve via public endpoint
    offer_get = client.get(f"/wallet/credential-offer/{signup_id}")
    assert offer_get.status_code == 200
    assert offer_get.json()["credential_offer"]["credential_issuer"] == body["credential_offer"]["credential_issuer"]


def test_unapproved_signup_has_no_credential_offer(admin_headers) -> None:
    client = _make_client()
    # Consume the auto-approve slot with a first signup
    client.post("/auth/pilot/signup/wallet-verified", json=_wallet_verified_signup_payload()).json()
    signup = client.post(
        "/auth/pilot/signup/wallet-verified",
        json=_wallet_verified_signup_payload(
            principal_did="did:web:id.dualsubstrate.com:wallet:offer-test",
            wallet_did="did:key:z6Mkoffer",
            email="offer-test@example.com",
            idempotency_key="wallet-signup-key-offer",
        ),
    ).json()
    signup_id = signup["signup"]["signup_id"]

    # Before approval
    offer_get = client.get(f"/wallet/credential-offer/{signup_id}")
    assert offer_get.status_code == 404
    assert offer_get.json()["detail"]["error"] == "credential_offer_not_ready"
