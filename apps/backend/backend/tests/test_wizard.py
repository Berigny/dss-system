from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.auth import router as auth_router
from backend.api.wizard import router as wizard_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(wizard_router)
    app.include_router(auth_router)
    return TestClient(app)


def test_create_account_request_returns_token() -> None:
    client = _make_client()
    response = client.post("/account/request")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["request_id"].startswith("req_")
    assert len(body["anonymous_token"]) > 20


def test_get_account_request_requires_token() -> None:
    client = _make_client()
    created = client.post("/account/request").json()

    # Missing token
    no_token = client.get(f"/account/request/{created['request_id']}")
    assert no_token.status_code == 403

    # Valid token
    with_token = client.get(
        f"/account/request/{created['request_id']}",
        headers={"x-anonymous-token": created["anonymous_token"]},
    )
    assert with_token.status_code == 200
    assert with_token.json()["request"]["status"] == "draft"


def test_save_profile_step() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]
    token = created["anonymous_token"]

    response = client.post(
        f"/account/request/{req_id}/step/profile",
        headers={"x-anonymous-token": token},
        json={"display_name": "Kaoru Tanaka", "email": "kaoru@example.com", "organisation_label": "DSS"},
    )
    assert response.status_code == 200
    assert response.json()["step"] == "profile"
    assert "profile" in response.json()["steps_completed"]


def test_save_did_choice_step() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]
    token = created["anonymous_token"]

    response = client.post(
        f"/account/request/{req_id}/step/did_choice",
        headers={"x-anonymous-token": token},
        json={"did_choice": "issuer_assigned"},
    )
    assert response.status_code == 200
    assert response.json()["step"] == "did_choice"


def test_save_wallet_setup_step() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]
    token = created["anonymous_token"]

    response = client.post(
        f"/account/request/{req_id}/step/wallet_setup",
        headers={"x-anonymous-token": token},
        json={"wallet_provider": "altme"},
    )
    assert response.status_code == 200
    assert response.json()["step"] == "wallet_setup"


def test_email_verification_flow() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]
    token = created["anonymous_token"]

    # Save profile first
    client.post(
        f"/account/request/{req_id}/step/profile",
        headers={"x-anonymous-token": token},
        json={"display_name": "Kaoru", "email": "kaoru@example.com"},
    )

    # Send verification
    send = client.post("/account/request/verify-email", json={"request_id": req_id})
    assert send.status_code == 200
    code = send.json()["verification_code"]
    assert len(code) == 6

    # Confirm with wrong code
    wrong = client.post(
        "/account/request/verify-email/confirm",
        json={"request_id": req_id, "code": "000000"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["attempts_remaining"] == 2

    # Confirm with correct code
    confirm = client.post(
        "/account/request/verify-email/confirm",
        json={"request_id": req_id, "code": code},
    )
    assert confirm.status_code == 200
    assert confirm.json()["email_verified"] is True


def test_submit_request_creates_signup() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]
    token = created["anonymous_token"]

    client.post(
        f"/account/request/{req_id}/step/profile",
        headers={"x-anonymous-token": token},
        json={"display_name": "Kaoru Tanaka", "email": "kaoru@example.com"},
    )
    client.post(
        f"/account/request/{req_id}/step/did_choice",
        headers={"x-anonymous-token": token},
        json={"did_choice": "issuer_assigned"},
    )
    client.post(
        f"/account/request/{req_id}/step/wallet_setup",
        headers={"x-anonymous-token": token},
        json={"wallet_provider": "altme"},
    )

    send = client.post("/account/request/verify-email", json={"request_id": req_id})
    code = send.json()["verification_code"]
    client.post(
        "/account/request/verify-email/confirm",
        json={"request_id": req_id, "code": code},
    )

    submit = client.post(
        f"/account/request/{req_id}/submit",
        headers={"x-anonymous-token": token},
        json={"idempotency_key": "wizard-submit-001"},
    )
    assert submit.status_code == 200
    body = submit.json()
    assert body["status"] == "ok"
    # First signup in a fresh database is auto-approved
    assert body["next_route"] != "awaiting_operator_approval"
    assert body["signup"]["verification_status"] == "verified"
    assert body["signup"]["approval_status"] == "approved"


def test_second_signup_awaits_operator_approval() -> None:
    client = _make_client()

    def _complete_wizard(email: str, idempotency_key: str) -> dict[str, Any]:
        created = client.post("/account/request").json()
        req_id = created["request_id"]
        token = created["anonymous_token"]
        client.post(
            f"/account/request/{req_id}/step/profile",
            headers={"x-anonymous-token": token},
            json={"display_name": "Kaoru", "email": email},
        )
        client.post(
            f"/account/request/{req_id}/step/did_choice",
            headers={"x-anonymous-token": token},
            json={"did_choice": "issuer_assigned"},
        )
        client.post(
            f"/account/request/{req_id}/step/wallet_setup",
            headers={"x-anonymous-token": token},
            json={"wallet_provider": "altme"},
        )
        send = client.post("/account/request/verify-email", json={"request_id": req_id})
        code = send.json()["verification_code"]
        client.post(
            "/account/request/verify-email/confirm",
            json={"request_id": req_id, "code": code},
        )
        submit = client.post(
            f"/account/request/{req_id}/submit",
            headers={"x-anonymous-token": token},
            json={"idempotency_key": idempotency_key},
        )
        assert submit.status_code == 200
        return submit.json()

    first = _complete_wizard("first@example.com", "wizard-submit-first")
    assert first["signup"]["approval_status"] == "approved"

    second = _complete_wizard("second@example.com", "wizard-submit-second")
    assert second["next_route"] == "awaiting_operator_approval"
    assert second["signup"].get("approval_status", "pending") != "approved"


def test_submit_rejects_unverified_email() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]
    token = created["anonymous_token"]

    client.post(
        f"/account/request/{req_id}/step/profile",
        headers={"x-anonymous-token": token},
        json={"display_name": "Kaoru", "email": "kaoru@example.com"},
    )
    client.post(
        f"/account/request/{req_id}/step/did_choice",
        headers={"x-anonymous-token": token},
        json={"did_choice": "issuer_assigned"},
    )
    client.post(
        f"/account/request/{req_id}/step/wallet_setup",
        headers={"x-anonymous-token": token},
        json={"wallet_provider": "altme"},
    )

    submit = client.post(
        f"/account/request/{req_id}/submit",
        headers={"x-anonymous-token": token},
        json={"idempotency_key": "wizard-submit-002"},
    )
    assert submit.status_code == 422
    assert submit.json()["detail"]["error"] == "email_not_verified"


def test_submit_rejects_missing_wallet_provider() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]
    token = created["anonymous_token"]

    client.post(
        f"/account/request/{req_id}/step/profile",
        headers={"x-anonymous-token": token},
        json={"display_name": "Kaoru", "email": "kaoru@example.com"},
    )
    client.post(
        f"/account/request/{req_id}/step/did_choice",
        headers={"x-anonymous-token": token},
        json={"did_choice": "issuer_assigned"},
    )

    send = client.post("/account/request/verify-email", json={"request_id": req_id})
    code = send.json()["verification_code"]
    client.post(
        "/account/request/verify-email/confirm",
        json={"request_id": req_id, "code": code},
    )

    submit = client.post(
        f"/account/request/{req_id}/submit",
        headers={"x-anonymous-token": token},
        json={"idempotency_key": "wizard-submit-003"},
    )
    assert submit.status_code == 422
    assert submit.json()["detail"]["error"] == "wallet_provider_required"


def test_email_verification_expires() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]

    client.post(
        f"/account/request/{req_id}/step/profile",
        headers={"x-anonymous-token": created["anonymous_token"]},
        json={"display_name": "Kaoru", "email": "kaoru@example.com"},
    )

    send = client.post("/account/request/verify-email", json={"request_id": req_id})
    code = send.json()["verification_code"]

    # Manually expire the code
    import json
    raw = client.app.state.db[b"__account_requests_v1__"]
    payload = json.loads(raw.decode("utf-8"))
    payload["requests"][req_id]["email_verification_expires_at"] = 0
    client.app.state.db[b"__account_requests_v1__"] = json.dumps(payload).encode()

    confirm = client.post(
        "/account/request/verify-email/confirm",
        json={"request_id": req_id, "code": code},
    )
    assert confirm.status_code == 401
    assert confirm.json()["detail"]["error"] == "verification_code_expired"


def test_duplicate_submit_returns_existing_signup() -> None:
    client = _make_client()
    created = client.post("/account/request").json()
    req_id = created["request_id"]
    token = created["anonymous_token"]

    client.post(
        f"/account/request/{req_id}/step/profile",
        headers={"x-anonymous-token": token},
        json={"display_name": "Kaoru", "email": "kaoru@example.com"},
    )
    client.post(
        f"/account/request/{req_id}/step/did_choice",
        headers={"x-anonymous-token": token},
        json={"did_choice": "issuer_assigned"},
    )
    client.post(
        f"/account/request/{req_id}/step/wallet_setup",
        headers={"x-anonymous-token": token},
        json={"wallet_provider": "altme"},
    )

    send = client.post("/account/request/verify-email", json={"request_id": req_id})
    code = send.json()["verification_code"]
    client.post(
        "/account/request/verify-email/confirm",
        json={"request_id": req_id, "code": code},
    )

    first = client.post(
        f"/account/request/{req_id}/submit",
        headers={"x-anonymous-token": token},
        json={"idempotency_key": "wizard-submit-004"},
    ).json()

    # Try again with same idempotency key
    second = client.post(
        f"/account/request/{req_id}/submit",
        headers={"x-anonymous-token": token},
        json={"idempotency_key": "wizard-submit-004"},
    )
    assert second.status_code == 200
    assert second.json()["signup"]["signup_id"] == first["signup"]["signup_id"]
