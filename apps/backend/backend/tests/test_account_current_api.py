from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.account import router as account_router
from backend.api.admin import router as admin_router
from backend.services.pilot_account import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_LEDGER_ID,
    SETUP_PROMPT_DISMISSALS_V1_KEY,
)


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(account_router)
    app.include_router(admin_router)
    client = TestClient(app)
    # Seed a default signup so the default principal has an account (DSS-150 compat)
    from backend.services.pilot_onboarding import _persist_pilot_signups
    _persist_pilot_signups(
        app.state.db,
        {
            "signup:pilot-owner": {
                "signup_id": "signup:pilot-owner",
                "account_id": DEFAULT_ACCOUNT_ID,
                "principal_did": "principal:pilot-owner",
                "verification_status": "verified",
                "onboarding_status": "complete",
                "provisioning_status": "succeeded",
                "trial_state": "active",
            }
        },
    )
    return client


def test_current_account_api_returns_commercial_summary() -> None:
    client = _make_client()
    response = client.get("/account/current", headers={"x-principal-id": "principal:pilot-owner"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["account"]["account_id"] == DEFAULT_ACCOUNT_ID
    assert body["plan"]["plan_id"] == "free_trial"
    assert body["trial"]["current_state"] in {"active", "paused"}
    assert body["primary_ledger_id"] == DEFAULT_LEDGER_ID
    assert body["primary_workspace_label"] == "Pilot DSS Space"


def test_current_subscription_api_returns_trial_summary() -> None:
    client = _make_client()
    response = client.get("/account/current/subscription", headers={"x-principal-id": "principal:pilot-owner"})

    assert response.status_code == 200
    subscription = response.json()["subscription"]
    assert subscription["plan_id"] == "free_trial"
    assert subscription["trial_started_at"] == "2026-04-19T00:00:00Z"
    assert subscription["trial_expires_at"] == "2026-04-26T00:00:00Z"
    assert subscription["extension_metadata"]["admin_extended"] is False


def test_current_setup_checklist_api_returns_wizard_entry_for_unmapped_principal() -> None:
    response = _make_client().get("/account/current/setup-checklist")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    checklist = body["setup_checklist"]
    assert checklist["wizard_entry_recommended"] is True
    assert checklist["complete"] is False
    assert any(item["item_id"] == "account_created" for item in checklist["items"])


def test_current_setup_checklist_api_returns_items_for_mapped_principal() -> None:
    client = _make_client()
    # Use default principal to get the full checklist via default account fallback
    response = client.get("/account/current/setup-checklist", headers={"x-principal-id": "principal:pilot-owner"})

    assert response.status_code == 200
    checklist = response.json()["setup_checklist"]
    assert checklist["account_id"] == DEFAULT_ACCOUNT_ID
    assert checklist["item_version"] == "epic-04.v1"
    by_id = {item["item_id"]: item for item in checklist["items"]}
    assert set(by_id) == {
        "account_created",
        "workspace_provisioned",
        "did_created",
        "wallet_linked",
        "chat_surface_ready",
        "share_surface_ready",
        "authorised_rep_invited",
        "telegram_configured",
        "model_principal_selected",
        "agent_principal_bootstrapped",
        "ledger_document_archive_seeded",
        "document_surface_enabled",
    }
    assert by_id["account_created"]["state"] == "complete"
    assert by_id["workspace_provisioned"]["source"] == "GET /account/current/provisioning"
    assert by_id["wallet_linked"]["state"] == "incomplete"
    assert by_id["wallet_linked"]["required"] is False
    assert by_id["wallet_linked"]["actionability"] == "user_actionable"
    assert by_id["authorised_rep_invited"]["state"] == "incomplete"
    assert by_id["authorised_rep_invited"]["required"] is True
    assert by_id["telegram_configured"]["state"] == "manual_only"
    assert by_id["telegram_configured"]["actionability"] == "admin_actionable"
    assert by_id["ledger_document_archive_seeded"]["state"] == "incomplete"
    assert by_id["ledger_document_archive_seeded"]["actionability"] == "user_actionable"
    assert by_id["ledger_document_archive_seeded"]["action_href"] == "/account/setup/documents"
    assert by_id["document_surface_enabled"]["state"] == "disabled"
    assert checklist["complete"] is False
    assert checklist["summary"]["incomplete_required_item_ids"] == ["authorised_rep_invited", "model_principal_selected", "agent_principal_bootstrapped"]


def test_current_setup_prompt_returns_wizard_entry_for_unmapped_principal() -> None:
    response = _make_client().get(
        "/account/current/setup-prompt",
        headers={"x-principal-id": "principal:alice"},
    )

    assert response.status_code == 200
    prompt = response.json()["setup_prompt"]
    assert prompt["principal_id"] == "principal:alice"
    assert prompt["visible"] is True
    assert prompt["reason"] == "wizard_entry_recommended"
    assert prompt["target"]["route"] == "/wizard"
    assert "do_not_show_again_for_current_required_set" in prompt["dismissal_options"]


def test_setup_prompt_dismissal_persists_per_account_and_principal() -> None:
    client = _make_client()
    headers = {"x-principal-id": "principal:pilot-owner"}

    dismissed = client.post(
        "/account/current/setup-prompt/dismiss",
        headers=headers,
        json={"mode": "do_not_show_again_for_current_required_set"},
    )
    replay = client.get("/account/current/setup-prompt", headers=headers)
    other_principal = client.get(
        "/account/current/setup-prompt",
        headers={"x-principal-id": "principal:bob"},
    )

    assert dismissed.status_code == 200
    assert dismissed.json()["setup_prompt"]["visible"] is False
    assert dismissed.json()["setup_prompt"]["suppressed_by_dismissal"] is True
    assert replay.json()["setup_prompt"]["visible"] is False
    assert replay.json()["setup_prompt"]["dismissal"]["principal_id"] == "principal:pilot-owner"
    assert other_principal.json()["setup_prompt"]["visible"] is True
    assert other_principal.json()["setup_prompt"]["dismissal"] is None


def test_setup_prompt_session_dismissal_does_not_hide_required_work() -> None:
    client = _make_client()
    headers = {"x-principal-id": "principal:pilot-owner"}

    dismissed = client.post(
        "/account/current/setup-prompt/dismiss",
        headers=headers,
        json={"mode": "dismissed_for_session"},
    )
    replay = client.get("/account/current/setup-prompt", headers=headers)

    assert dismissed.status_code == 200
    assert dismissed.json()["setup_prompt"]["visible"] is True
    assert dismissed.json()["setup_prompt"]["suppressed_by_dismissal"] is False
    assert dismissed.json()["setup_prompt"]["dismissal_reason"] == "dismissed_for_session"
    assert replay.json()["setup_prompt"]["visible"] is True
    assert replay.json()["setup_prompt"]["required_item_ids"] == ["authorised_rep_invited", "model_principal_selected", "agent_principal_bootstrapped"]


def test_setup_prompt_dismissal_invalidates_when_required_set_changes() -> None:
    client = _make_client()
    headers = {"x-principal-id": "principal:pilot-owner"}
    dismissed = client.post(
        "/account/current/setup-prompt/dismiss",
        headers=headers,
        json={"mode": "do_not_show_again_for_current_required_set"},
    )
    assert dismissed.status_code == 200
    raw = client.app.state.db[SETUP_PROMPT_DISMISSALS_V1_KEY]
    payload = json.loads(raw.decode("utf-8"))
    key = f"{DEFAULT_ACCOUNT_ID}::principal:pilot-owner"
    payload["dismissals"][key]["required_item_set_version"] = "setup-required:stale"
    client.app.state.db[SETUP_PROMPT_DISMISSALS_V1_KEY] = json.dumps(payload).encode()

    response = client.get("/account/current/setup-prompt", headers=headers)

    assert response.status_code == 200
    prompt = response.json()["setup_prompt"]
    assert prompt["visible"] is True
    assert prompt["dismissal_invalidated"] is True
    assert prompt["dismissal_reason"] == "required_item_set_changed"


def test_setup_prompt_snooze_requires_future_time_and_suppresses_until_then() -> None:
    client = _make_client()
    headers = {
        "x-principal-id": "principal:pilot-owner",
        "x-pilot-now": "2026-04-21T00:00:00Z",
    }
    invalid = client.post(
        "/account/current/setup-prompt/dismiss",
        headers=headers,
        json={"mode": "snoozed_until", "snoozed_until": "2026-04-20T00:00:00Z"},
    )
    valid = client.post(
        "/account/current/setup-prompt/dismiss",
        headers=headers,
        json={"mode": "snoozed_until", "snoozed_until": "2026-04-22T00:00:00Z"},
    )
    replay = client.get("/account/current/setup-prompt", headers=headers)
    after_snooze = client.get(
        "/account/current/setup-prompt",
        headers={
            "x-principal-id": "principal:pilot-owner",
            "x-pilot-now": "2026-04-23T00:00:00Z",
        },
    )

    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert valid.json()["setup_prompt"]["visible"] is False
    assert replay.json()["setup_prompt"]["visible"] is False
    assert after_snooze.json()["setup_prompt"]["visible"] is True
    assert after_snooze.json()["setup_prompt"]["dismissal_invalidated"] is True


def test_admin_account_inspection_api_returns_full_model() -> None:
    response = _make_client().get(f"/admin/accounts/{DEFAULT_ACCOUNT_ID}")

    assert response.status_code == 200
    inspection = response.json()["account_inspection"]
    assert inspection["account"]["account_id"] == DEFAULT_ACCOUNT_ID
    assert inspection["trial"] == inspection["subscription"]
    assert inspection["workspace"]["ledger_id"] == DEFAULT_LEDGER_ID
    assert inspection["surfaces"]
    assert inspection["principals"]
    assert inspection["invites"]
    assert inspection["setup_checklist"]["items"]


def test_admin_account_inspection_api_rejects_unknown_account() -> None:
    response = _make_client().get("/admin/accounts/acct_unknown")

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown_account"
