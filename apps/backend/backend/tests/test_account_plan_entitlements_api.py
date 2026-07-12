from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.account import router as account_router


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(account_router)
    return TestClient(app)


def test_default_plan_entitlements_are_available_to_consumers() -> None:
    client = _make_client()

    response = client.get("/account/plans/default/entitlements")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["plan"]["plan_id"] == "free_trial"
    assert body["plan"]["trial_duration_days"] == 7
    assert body["plan"]["included"]["ledgers"] == 1
    assert body["plan"]["included"]["chat_surfaces"] == 1
    assert body["plan"]["included"]["share_surfaces"] == 1
    assert "document_surface" in body["plan"]["manual_only"]
    assert "not_entitled" in body["plan"]["capability_states"]


def test_named_free_trial_entitlements_are_available_to_consumers() -> None:
    client = _make_client()

    response = client.get("/account/plans/free_trial/entitlements")

    assert response.status_code == 200
    assert response.json()["plan"]["plan_id"] == "free_trial"


def test_unknown_plan_entitlements_fail_explicitly() -> None:
    client = _make_client()

    response = client.get("/account/plans/standard/entitlements")

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown_plan"
