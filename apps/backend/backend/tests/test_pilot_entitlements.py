from __future__ import annotations

import pytest

from backend.services.pilot_entitlements import (
    PILOT_PLAN_ID,
    PILOT_TRIAL_DURATION_DAYS,
    get_pilot_plan_contract,
    get_pilot_plan_entitlements,
)


def test_free_trial_entitlement_contract_matches_epic_01() -> None:
    contract = get_pilot_plan_contract()

    assert contract["plan_id"] == "free_trial"
    assert contract["trial_duration_days"] == 7
    assert contract["included"] == {
        "ledgers": 1,
        "chat_surfaces": 1,
        "share_surfaces": 1,
        "owner_human_principals": 1,
        "authorised_rep_slots": 1,
    }
    assert contract["manual_only"] == [
        "document_surface",
        "telegram_setup",
        "advanced_agent_setups",
        "extra_surfaces",
        "extra_principals",
    ]
    assert contract["out_of_scope"] == [
        "self_serve_paid_checkout",
        "self_serve_custom_plans",
        "automatic_telegram_fulfilment",
        "automatic_document_surface_enablement",
        "self_serve_agent_principal_creation",
    ]
    assert contract["capability_states"] == [
        "enabled",
        "disabled",
        "manual_only",
        "not_entitled",
    ]


def test_free_trial_entitlements_are_machine_consumable_and_named() -> None:
    entitlements = get_pilot_plan_entitlements(PILOT_PLAN_ID)

    assert entitlements.plan_id == PILOT_PLAN_ID
    assert entitlements.trial_duration_days == PILOT_TRIAL_DURATION_DAYS
    assert entitlements.included.ledgers == 1
    assert "manual_only" in entitlements.capability_states


def test_unknown_pilot_plan_is_rejected() -> None:
    with pytest.raises(KeyError, match="unknown pilot plan"):
        get_pilot_plan_entitlements("standard")
