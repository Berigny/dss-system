"""Tests for DSS-143: admin provisioning inspection and rescue visibility."""

from __future__ import annotations

import json
import os

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


# Ensure ADMIN_TOKEN is set for all tests in this module
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")


def _admin_headers() -> dict[str, str]:
    token = os.getenv("ADMIN_TOKEN", "test-admin-token")
    return {"x-admin-token": token}


def _signup_verify_signin(client: TestClient) -> dict:
    signup = client.post(
        "/auth/pilot/signup",
        json={
            "primary_contact": "owner@example.com",
            "owner_display_name": "Pilot Owner",
            "pilot_terms_acknowledgement": True,
            "idempotency_key": "signup-admin-001",
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
        "idempotency_key": "onboarding-admin-001",
    }
    payload.update(overrides)
    return payload


def _provisioned_account(client: TestClient) -> tuple[dict, dict, str]:
    signin = _signup_verify_signin(client)
    headers = {"x-session-token": signin["session"]["token"]}
    submit = client.post(
        "/account/current/onboarding",
        headers=headers,
        json=_onboarding_payload(),
    )
    assert submit.status_code == 200
    run = client.post("/account/current/provisioning/run", headers=headers)
    assert run.status_code == 200
    job_id = run.json()["job"]["job_id"]
    return signin, headers, job_id


def test_admin_job_inspection_requires_admin_token() -> None:
    client = _make_client()
    response = client.get("/admin/provisioning/jobs/provjob:fake")
    assert response.status_code == 403


def test_admin_job_inspection_returns_404_for_unknown_job() -> None:
    client = _make_client()
    response = client.get(
        "/admin/provisioning/jobs/provjob:unknown",
        headers=_admin_headers(),
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "provisioning_job_not_found"


def test_admin_job_inspection_returns_job_summary() -> None:
    client = _make_client()
    signin, _headers, job_id = _provisioned_account(client)

    response = client.get(
        f"/admin/provisioning/jobs/{job_id}",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["inspection"]["read_only"] is True
    job = body["inspection"]["job"]
    assert job["job_id"] == job_id
    assert job["status"] == "succeeded"
    assert job["resource_counts"]["total"] == 7
    assert job["resource_counts"]["succeeded"] == 7
    assert job["resource_counts"]["failed"] == 0
    assert body["inspection"]["rescue_recommendation"]["action"] == "none"


def test_admin_job_steps_inspection_returns_step_details() -> None:
    client = _make_client()
    signin, _headers, job_id = _provisioned_account(client)

    response = client.get(
        f"/admin/provisioning/jobs/{job_id}/steps",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["inspection"]["read_only"] is True
    assert body["inspection"]["job_id"] == job_id
    assert body["inspection"]["account_id"] == signin["account"]["account_id"]
    assert body["inspection"]["job_status"] == "succeeded"
    steps = body["inspection"]["steps"]
    assert len(steps) == 7
    step_ids = {s["step_id"] for s in steps}
    assert step_ids == {
        "dss_space",
        "ledger_runtime",
        "wallet_provider_binding",
        "owner_human_principal",
        "chat_surface",
        "share_surface",
        "document_surface",
    }
    for step in steps:
        assert step["resource_id"]
        assert step["status"] == "succeeded"
        assert "metadata" in step
    assert body["inspection"]["step_counts"]["total"] == 7
    assert body["inspection"]["step_counts"]["succeeded"] == 7
    assert body["inspection"]["step_counts"]["failed"] == 0


def test_admin_job_inspection_recommends_retry_for_failed_job() -> None:
    client = _make_client()
    _signin, _headers, job_id = _provisioned_account(client)

    # Corrupt the job to simulate failure
    raw = client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    job = payload["jobs"][job_id]
    for step in job["resource_steps"]:
        step["status"] = "failed"
        step["failure_reason"] = "simulated_failure"
    job["status"] = "failed"
    client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY] = json.dumps(payload).encode()

    response = client.get(
        f"/admin/provisioning/jobs/{job_id}",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    inspection = response.json()["inspection"]
    assert inspection["job"]["status"] == "failed"
    assert inspection["rescue_recommendation"]["action"] == "retry"


def test_admin_job_inspection_recommends_inspect_for_partial_failure() -> None:
    client = _make_client()
    _signin, _headers, job_id = _provisioned_account(client)

    # Corrupt one step to simulate partial failure
    raw = client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    job = payload["jobs"][job_id]
    job["resource_steps"][0]["status"] = "failed"
    job["resource_steps"][0]["failure_reason"] = "partial_step_failure"
    job["status"] = "requires_admin"
    client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY] = json.dumps(payload).encode()

    response = client.get(
        f"/admin/provisioning/jobs/{job_id}",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    inspection = response.json()["inspection"]
    assert inspection["rescue_recommendation"]["action"] == "inspect_steps"
    assert inspection["rescue_recommendation"]["reason"] == "partial_failure"


def test_admin_job_steps_shows_failure_details() -> None:
    client = _make_client()
    _signin, _headers, job_id = _provisioned_account(client)

    # Corrupt one step
    raw = client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    job = payload["jobs"][job_id]
    job["resource_steps"][0]["status"] = "failed"
    job["resource_steps"][0]["failure_reason"] = "ledger_creation_timeout"
    job["resource_steps"][0]["retry_eligible"] = True
    client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY] = json.dumps(payload).encode()

    response = client.get(
        f"/admin/provisioning/jobs/{job_id}/steps",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    steps = response.json()["inspection"]["steps"]
    failed_step = next(s for s in steps if s["step_id"] == "dss_space")
    assert failed_step["status"] == "failed"
    assert failed_step["failure_reason"] == "ledger_creation_timeout"
    assert failed_step["retry_eligible"] is True


def test_admin_job_inspection_does_not_mutate_job() -> None:
    client = _make_client()
    _signin, _headers, job_id = _provisioned_account(client)

    # Record state before inspection
    raw_before = client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY]

    client.get(
        f"/admin/provisioning/jobs/{job_id}",
        headers=_admin_headers(),
    )
    client.get(
        f"/admin/provisioning/jobs/{job_id}/steps",
        headers=_admin_headers(),
    )

    raw_after = client.app.state.db[PILOT_PROVISIONING_JOBS_V1_KEY]
    assert raw_before == raw_after
