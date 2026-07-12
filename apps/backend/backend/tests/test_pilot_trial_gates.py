from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.api.account import router as account_router
from backend.api.admin import router as admin_router
from backend.api.auth import router as auth_router
from backend.api.chat import router as chat_router
from backend.api.http import get_db, get_ledger_store, router as ledger_router
from backend.services.pilot_account import (
    DEFAULT_ACCOUNT_ID,
    assert_pilot_write_allowed,
    get_current_subscription_summary,
    reset_pilot_trial_state_for_tests,
)


class _FakeLedgerStore:
    def write(self, entry):
        return entry


@pytest.fixture(autouse=True)
def _reset_trial_state():
    reset_pilot_trial_state_for_tests()
    yield
    reset_pilot_trial_state_for_tests()


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(account_router)
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(ledger_router)
    app.include_router(chat_router)
    app.dependency_overrides[get_db] = lambda: app.state.db
    app.dependency_overrides[get_ledger_store] = lambda: _FakeLedgerStore()
    return TestClient(app)


def _signup_verify_signin(client: TestClient) -> dict:
    signup = client.post(
        "/auth/pilot/signup",
        json={
            "primary_contact": "owner@example.com",
            "owner_display_name": "Pilot Owner",
            "pilot_terms_acknowledgement": True,
            "idempotency_key": "signup-trial-gates-001",
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


def test_trial_activation_persists_start_expiry_and_audit_trail() -> None:
    summary = get_current_subscription_summary(
        now=datetime(2026, 4, 19, tzinfo=timezone.utc)
    )

    subscription = summary["subscription"]
    assert subscription["current_state"] == "active"
    assert subscription["trial_started_at"] == "2026-04-19T00:00:00Z"
    assert subscription["trial_expires_at"] == "2026-04-26T00:00:00Z"
    assert subscription["state_change_audit_trail"][0]["event"] == "trial_activated"


def test_expiry_deterministically_enters_paused_state() -> None:
    summary = get_current_subscription_summary(
        now=datetime(2026, 4, 27, tzinfo=timezone.utc)
    )

    subscription = summary["subscription"]
    assert subscription["current_state"] == "paused"
    assert subscription["pause_reason"] == "trial_expired"


def test_paused_state_still_allows_account_read_api() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)
    headers = {
        "x-session-token": signin["session"]["token"],
        "x-pilot-now": "2026-04-27T00:00:00Z",
    }
    response = client.get("/account/current/subscription", headers=headers)

    assert response.status_code == 200
    assert response.json()["subscription"]["current_state"] == "paused"


def test_paused_state_blocks_write_gate() -> None:
    with pytest.raises(HTTPException) as exc:
        assert_pilot_write_allowed(
            now=datetime(2026, 4, 27, tzinfo=timezone.utc),
            action="ledger.write",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "trial_paused"
    assert exc.value.detail["blocked_action"] == "ledger.write"


def test_paused_state_blocks_ledger_write_api_before_write_executes() -> None:
    response = _make_client().post(
        "/ledger/write",
        headers={"x-pilot-now": "2026-04-27T00:00:00Z"},
        json={
            "key": {"namespace": "pilot", "identifier": "blocked"},
            "state": {"coordinates": {"x": 1.0}},
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "trial_paused"
    assert response.json()["detail"]["blocked_action"] == "ledger.write"


@pytest.mark.parametrize(
    ("path", "payload", "blocked_action"),
    [
        ("/ledger/pin/pilot:item", None, "ledger.pin"),
        ("/ledger/unpin/pilot:item", None, "ledger.unpin"),
        (
            "/ledger/feedback/pilot:item",
            {"actor_id": "tester", "rating": 1, "context_id": "ctx:test"},
            "ledger.feedback",
        ),
        (
            "/ledger/feedback/auto/pilot:item",
            {"rating": 1, "context_id": "ctx:test"},
            "ledger.feedback.auto",
        ),
        (
            "/ledger/debug/ledger/write",
            {
                "key": {"namespace": "pilot", "identifier": "blocked"},
                "state": {"coordinates": {"x": 1.0}},
            },
            "ledger.debug.write",
        ),
        ("/chat/stream/confirm", {"coordinate": "pilot:item"}, "chat.stream.confirm"),
        (
            "/chat/walk/write",
            {"kind": "coord_walk", "start_coord": "pilot:item", "path": ["pilot:item"]},
            "chat.walk.write",
        ),
    ],
)
def test_paused_state_blocks_additional_mutation_routes(
    path: str,
    payload: dict[str, object] | None,
    blocked_action: str,
) -> None:
    response = _make_client().post(
        path,
        headers={"x-pilot-now": "2026-04-27T00:00:00Z"},
        json=payload,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "trial_paused"
    assert response.json()["detail"]["blocked_action"] == blocked_action


def test_admin_extension_recovers_paused_trial_and_records_audit() -> None:
    client = _make_client()
    signin = _signup_verify_signin(client)

    extension = client.post(
        f"/admin/accounts/{DEFAULT_ACCOUNT_ID}/trial/extend",
        json={"days": 14, "actor": "admin:test", "reason": "pilot rescue"},
        headers={"x-pilot-now": "2026-04-27T00:00:00Z"},
    )

    assert extension.status_code == 200
    subscription = extension.json()["subscription"]
    assert subscription["current_state"] == "admin_extended"
    assert subscription["trial_expires_at"] == "2026-05-10T00:00:00Z"
    assert subscription["extension_metadata"]["admin_extended"] is True
    assert subscription["extension_metadata"]["extension_count"] == 1
    assert subscription["state_change_audit_trail"][-1]["event"] == "trial_admin_extended"

    recovered = client.get(
        "/account/current/subscription",
        headers={
            "x-session-token": signin["session"]["token"],
            "x-pilot-now": "2026-04-27T00:00:00Z",
        },
    )

    assert recovered.status_code == 200
    assert recovered.json()["subscription"]["current_state"] == "admin_extended"
