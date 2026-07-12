from __future__ import annotations

from datetime import datetime, timezone

from backend.services.pilot_account import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_LEDGER_ID,
    get_current_account_summary,
    get_current_subscription_summary,
    get_pilot_account_model,
    get_setup_checklist_summary,
)


def test_current_account_summary_exposes_primary_commercial_context() -> None:
    summary = get_current_account_summary(now=datetime(2026, 4, 20, tzinfo=timezone.utc))

    assert summary["status"] == "ok"
    assert summary["account"]["account_id"] == DEFAULT_ACCOUNT_ID
    assert summary["plan"]["plan_id"] == "free_trial"
    assert summary["trial"]["current_state"] == "active"
    assert summary["trial_status"] == "active"
    assert summary["primary_workspace_label"] == "Pilot DSS Space"
    assert summary["primary_ledger_id"] == DEFAULT_LEDGER_ID


def test_subscription_summary_exposes_trial_dates_and_extension_metadata() -> None:
    summary = get_current_subscription_summary(
        now=datetime(2026, 4, 20, tzinfo=timezone.utc)
    )

    subscription = summary["subscription"]
    assert subscription["plan_id"] == "free_trial"
    assert subscription["trial_started_at"] == "2026-04-19T00:00:00Z"
    assert subscription["trial_expires_at"] == "2026-04-26T00:00:00Z"
    assert subscription["current_state"] == "active"
    assert subscription["pause_reason"] is None
    assert subscription["extension_metadata"]["admin_extended"] is False


def test_subscription_summary_pauses_after_trial_expiry() -> None:
    summary = get_current_subscription_summary(
        now=datetime(2026, 4, 27, tzinfo=timezone.utc)
    )

    assert summary["subscription"]["current_state"] == "paused"
    assert summary["subscription"]["pause_reason"] == "trial_expired"


def test_pilot_account_model_keeps_domain_entities_separate() -> None:
    model = get_pilot_account_model(now=datetime(2026, 4, 20, tzinfo=timezone.utc))

    assert model["account"]["account_id"] == DEFAULT_ACCOUNT_ID
    assert model["workspace"]["ledger_id"] == DEFAULT_LEDGER_ID
    assert model["workspace"]["product_label"] == "DSS Space"
    assert model["trial"] == model["subscription"]
    assert {surface["surface_type"] for surface in model["surfaces"]} == {
        "chat",
        "share_decode",
    }
    assert model["principals"][0]["principal_type"] == "human_owner"
    assert model["invites"][0]["status"] == "reserved"
    assert model["provisioning"]["workspace_is_product_context"] is True


def test_setup_checklist_summary_is_backend_truth() -> None:
    summary = get_setup_checklist_summary(account_id=DEFAULT_ACCOUNT_ID)

    checklist = summary["setup_checklist"]
    assert checklist["account_id"] == DEFAULT_ACCOUNT_ID
    assert checklist["complete"] is False
    assert {item["item_id"] for item in checklist["items"]} == {
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


