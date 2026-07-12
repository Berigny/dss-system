"""Account and plan contract endpoints for commercial pilot consumers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.http import get_db
from backend.services.pilot_entitlements import (
    PILOT_PLAN_ID,
    get_pilot_plan_contract,
)
from backend.services.pilot_account import (
    _account_id_from_request,
    _principal_id_from_request,
    dismiss_setup_prompt,
    get_current_account_summary,
    get_current_subscription_summary,
    get_setup_checklist_summary,
    get_setup_prompt_state,
    pilot_now_from_request,
)
from backend.services.pilot_onboarding import (
    get_current_onboarding,
    get_current_provisioning,
    submit_current_onboarding,
)
from backend.services.pilot_provisioning import get_current_surfaces, run_current_provisioning
from backend.services.pilot_identity import (
    defer_wallet_link,
    get_current_identity,
    mark_wallet_linked_for_tests,
    start_wallet_link,
)
from backend.services.model_library import (
    get_current_principals as get_model_library_principals,
    get_model_library,
    select_model,
)
from backend.services.agent_principal import (
    bootstrap_agent_principal,
    get_principal_connections,
    get_current_principals_with_agent,
)


router = APIRouter(prefix="/account", tags=["account"])


class OnboardingSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    owner_display_name: str
    workspace_or_dss_space_label: str
    primary_contact: str
    pilot_use_case: str
    free_trial_scope_acknowledgement: bool
    idempotency_key: str
    authorised_representative_email_placeholder: str | None = None


class WalletLinkStartRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str | None = None


class WalletLinkCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str | None = None
    wallet_did: str | None = None


class SetupPromptDismissRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str = Field(..., min_length=1)
    snoozed_until: str | None = None


class ModelLibrarySelectRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1)
    api_key: str | None = None
    base_url: str | None = None


@router.get("/current")
async def current_account(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    account_id = _account_id_from_request(request, db)
    if not account_id:
        raise HTTPException(status_code=404, detail={"error": "account_not_found"})
    return get_current_account_summary(now=pilot_now_from_request(request), account_id=account_id)


@router.get("/current/subscription")
async def current_account_subscription(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    account_id = _account_id_from_request(request, db)
    if not account_id:
        raise HTTPException(status_code=404, detail={"error": "account_not_found"})
    return get_current_subscription_summary(now=pilot_now_from_request(request))


@router.get("/current/setup-checklist")
async def current_account_setup_checklist(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    account_id = _account_id_from_request(request, db)
    if not account_id:
        # Check signup state for wizard-driven entry (DSS-013 / DSS-018)
        principal_id = _principal_id_from_request(request)
        from backend.services.pilot_onboarding import _signup_state_for_principal
        signup_state = _signup_state_for_principal(db, principal_id)
        if signup_state:
            verification_status = str(signup_state.get("verification_status") or "").strip().lower()
            approval_status = str(signup_state.get("approval_status") or "").strip().lower()
            onboarding_status = str(signup_state.get("onboarding_status") or "not_started").strip().lower()

            if approval_status in {"pending", "rejected"} or verification_status == "awaiting_operator_approval":
                return {
                    "status": "ok",
                    "setup_checklist": {
                        "account_id": str(signup_state.get("account_id") or ""),
                        "workspace_id": "space_pilot_default",
                        "item_version": "epic-04.v1",
                        "signup_state": "awaiting_operator_approval",
                        "items": [
                            {
                                "item_id": "account_created",
                                "label": "Account created",
                                "state": "pending",
                                "actionable": False,
                                "actionability": "system_pending",
                                "required": True,
                                "explanation": "Your account setup request has been submitted and is awaiting operator approval. You will receive an email once approved.",
                                "source": "GET /account/current/setup-checklist",
                            }
                        ],
                        "complete": False,
                        "summary": {
                            "total": 1,
                            "complete": 0,
                            "required": 1,
                            "required_complete": 0,
                            "incomplete_required_item_ids": ["account_created"],
                        },
                    },
                }

            if onboarding_status not in {"accepted", "complete", "completed"}:
                return {
                    "status": "ok",
                    "setup_checklist": {
                        "account_id": str(signup_state.get("account_id") or ""),
                        "workspace_id": "space_pilot_default",
                        "item_version": "epic-04.v1",
                        "signup_state": "approved_pending_onboarding",
                        "items": [
                            {
                                "item_id": "complete_account_setup",
                                "label": "Complete account setup",
                                "state": "incomplete",
                                "actionable": True,
                                "actionability": "user_actionable",
                                "required": True,
                                "explanation": "Your request has been approved. Finish account setup to provision your workspace.",
                                "action_label": "Complete account setup",
                                "action_href": "/settings?section=account#account-setup-checklist",
                                "source": "GET /account/current/setup-checklist",
                            }
                        ],
                        "complete": False,
                        "summary": {
                            "total": 1,
                            "complete": 0,
                            "required": 1,
                            "required_complete": 0,
                            "incomplete_required_item_ids": ["complete_account_setup"],
                        },
                    },
                }
        # No account, no signup — recommend wizard entry
        return {
            "status": "ok",
            "setup_checklist": {
                "account_id": None,
                "workspace_id": None,
                "item_version": "epic-04.v1",
                "wizard_entry_recommended": True,
                "items": [
                    {
                        "item_id": "account_created",
                        "label": "Account created",
                        "state": "incomplete",
                        "actionable": True,
                        "actionability": "user_actionable",
                        "required": True,
                        "explanation": "Start the Account Setup Wizard to request access to DSS.",
                        "action_label": "Start Account Setup Wizard",
                        "action_href": "/wizard",
                        "source": "GET /account/current/setup-checklist",
                    }
                ],
                "complete": False,
                "summary": {
                    "total": 1,
                    "complete": 0,
                    "required": 1,
                    "required_complete": 0,
                    "incomplete_required_item_ids": ["account_created"],
                },
            },
        }
    return get_setup_checklist_summary(db, account_id)


@router.get("/current/setup-prompt")
async def current_account_setup_prompt(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return get_setup_prompt_state(request, db)


@router.post("/current/setup-prompt/dismiss")
async def dismiss_current_account_setup_prompt(
    request: Request,
    payload: SetupPromptDismissRequest,
    db=Depends(get_db),
) -> dict[str, Any]:
    return dismiss_setup_prompt(
        request,
        db,
        mode=payload.mode,  # type: ignore[arg-type]
        snoozed_until=payload.snoozed_until,
    )


@router.get("/current/onboarding")
async def current_account_onboarding(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return get_current_onboarding(request, db)


@router.post("/current/onboarding")
async def submit_account_onboarding(
    request: Request,
    payload: OnboardingSubmitRequest,
    db=Depends(get_db),
) -> dict[str, Any]:
    return submit_current_onboarding(request, db, payload=payload.model_dump())


@router.get("/current/provisioning")
async def current_account_provisioning(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return get_current_provisioning(request, db)


@router.post("/current/provisioning/run")
async def run_account_provisioning(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return run_current_provisioning(request, db)


@router.get("/current/surfaces")
async def current_account_surfaces(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return get_current_surfaces(request, db)


@router.get("/current/identity")
async def current_account_identity(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return get_current_identity(request, db)


@router.post("/current/identity/wallet-link/start")
async def start_current_wallet_link(
    request: Request,
    payload: WalletLinkStartRequest,
    db=Depends(get_db),
) -> dict[str, Any]:
    return start_wallet_link(request, db, provider=payload.provider)


@router.post("/current/identity/wallet-link/defer")
async def defer_current_wallet_link(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return defer_wallet_link(request, db)


@router.post("/current/identity/wallet-link/complete")
async def complete_current_wallet_link(
    request: Request,
    payload: WalletLinkCompleteRequest,
    db=Depends(get_db),
) -> dict[str, Any]:
    return mark_wallet_linked_for_tests(
        request,
        db,
        provider=payload.provider,
        wallet_did=payload.wallet_did,
    )


@router.get("/plans/default/entitlements")
async def default_plan_entitlements() -> dict[str, Any]:
    return {"status": "ok", "plan": get_pilot_plan_contract(PILOT_PLAN_ID)}


@router.get("/plans/{plan_id}/entitlements")
async def plan_entitlements(plan_id: str) -> dict[str, Any]:
    try:
        contract = get_pilot_plan_contract(plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown_plan") from exc
    return {"status": "ok", "plan": contract}


@router.get("/current/model-library")
async def current_account_model_library(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return get_model_library(request, db)


@router.post("/current/model-library/select")
async def select_account_model(
    request: Request,
    payload: ModelLibrarySelectRequest,
    db=Depends(get_db),
) -> dict[str, Any]:
    return select_model(
        request,
        db,
        provider=payload.provider,
        model_id=payload.model_id,
        api_key=payload.api_key,
        base_url=payload.base_url,
    )


@router.get("/current/principals")
async def current_account_principals(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return get_current_principals_with_agent(request, db)


@router.post("/current/principals/agent/bootstrap")
async def bootstrap_account_agent_principal(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return bootstrap_agent_principal(request, db)


@router.get("/current/connections")
async def current_account_connections(request: Request, db=Depends(get_db)) -> dict[str, Any]:
    return get_principal_connections(request, db)
