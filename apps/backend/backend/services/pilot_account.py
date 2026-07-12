"""Derived pilot account model for the Epic 1 commercial spine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import HTTPException, Request

from backend.services.pilot_entitlements import (
    PILOT_PLAN_ID,
    PILOT_TRIAL_DURATION_DAYS,
    get_pilot_plan_contract,
)


TrialState = Literal["active", "paused", "expired", "admin_extended"]
SurfaceType = Literal["chat", "share_decode"]
SurfaceStatus = Literal["enabled", "disabled", "manual_only", "not_entitled"]
PrincipalType = Literal["human_owner", "authorised_representative"]
InviteStatus = Literal["reserved", "pending", "accepted", "expired", "revoked"]
ChecklistItemState = Literal[
    "complete",
    "incomplete",
    "pending",
    "blocked",
    "requires_admin",
    "manual_only",
    "disabled",
    "coming_soon",
    "not_applicable",
]
ChecklistActionability = Literal[
    "user_actionable",
    "admin_actionable",
    "system_pending",
    "informational",
]
SetupPromptDismissalMode = Literal[
    "dismissed_for_session",
    "snoozed_until",
    "do_not_show_again_for_current_required_set",
]

DEFAULT_ACCOUNT_ID = "acct_pilot_default"
DEFAULT_WORKSPACE_ID = "space_pilot_default"
DEFAULT_WORKSPACE_LABEL = "Pilot DSS Space"
DEFAULT_LEDGER_ID = "ledger:pilot-default"
DEFAULT_OWNER_PRINCIPAL_ID = "principal:pilot-owner"
DEFAULT_AUTHORISED_REP_INVITE_ID = "invite:authorised-rep-slot"
DEFAULT_TRIAL_STARTED_AT = datetime(2026, 4, 19, tzinfo=timezone.utc)
SETUP_PROMPT_DISMISSALS_V1_KEY = b"__pilot_setup_prompt_dismissals_v1__"
_TRIAL_EXTENSION_DAYS = 0
_TRIAL_EXTENSION_METADATA: dict[str, object] = {
    "admin_extended": False,
    "extension_count": 0,
    "last_extension_at": None,
    "last_extended_by": None,
    "last_reason": None,
}
_TRIAL_AUDIT_TRAIL: list[dict[str, object]] = [
    {
        "event": "trial_activated",
        "status": "active",
        "occurred_at": "2026-04-19T00:00:00Z",
        "actor": "system",
        "reason": "pilot_default_activation",
    }
]


@dataclass(frozen=True)
class Account:
    account_id: str
    status: str
    display_name: str
    owner_principal_id: str


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    label: str
    product_label: str
    status: str
    ledger_id: str


@dataclass(frozen=True)
class SubscriptionState:
    plan_id: str
    trial_started_at: str
    trial_expires_at: str
    current_state: TrialState
    pause_reason: str | None
    extension_metadata: dict[str, object]
    state_change_audit_trail: list[dict[str, object]]


@dataclass(frozen=True)
class Surface:
    surface_id: str
    surface_type: SurfaceType
    status: SurfaceStatus
    ledger_id: str


@dataclass(frozen=True)
class Principal:
    principal_id: str
    principal_type: PrincipalType
    status: str
    account_id: str
    workspace_id: str


@dataclass(frozen=True)
class Invite:
    invite_id: str
    invite_type: PrincipalType
    status: InviteStatus
    account_id: str
    workspace_id: str


@dataclass(frozen=True)
class SetupChecklistItem:
    item_id: str
    label: str
    state: ChecklistItemState
    actionable: bool
    actionability: ChecklistActionability
    required: bool
    explanation: str
    action_label: str | None = None
    action_href: str | None = None
    source: str | None = None


def _isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_now_header(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_setup_prompt_dismissals(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(SETUP_PROMPT_DISMISSALS_V1_KEY) if db is not None else None
    payload = _decode_json(raw)
    records = payload.get("dismissals") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        if isinstance(record, dict):
            out[str(key)] = dict(record)
    return out


def _persist_setup_prompt_dismissals(
    db: Any,
    records: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for key in sorted(records.keys()):
        record = records.get(key)
        if isinstance(record, dict):
            canonical[key] = dict(record)
    db[SETUP_PROMPT_DISMISSALS_V1_KEY] = json.dumps(
        {"version": 1, "dismissals": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _principal_id_from_request(request: Request) -> str:
    header_principal = str(request.headers.get("x-principal-id") or "").strip()
    if header_principal:
        return header_principal
    auth = str(request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or DEFAULT_OWNER_PRINCIPAL_ID
    # Also resolve from session token claims
    try:
        from backend.services.session_tokens import apply_session_token_claims_or_raise
        claims = apply_session_token_claims_or_raise(request)
        if isinstance(claims, dict):
            principal_did = str(claims.get("sub") or "").strip()
            if principal_did:
                return principal_did
    except Exception:
        pass
    return DEFAULT_OWNER_PRINCIPAL_ID


def _dismissal_key(account_id: str, principal_id: str) -> str:
    return f"{account_id}::{principal_id}"


def _required_setup_items(checklist: dict[str, object]) -> list[dict[str, object]]:
    raw_items = checklist.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    required: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("required") is False:
            continue
        state = str(item.get("state") or "").strip()
        if state in {"complete", "not_applicable"}:
            continue
        required.append(dict(item))
    return required


def _required_item_set_version(required_items: list[dict[str, object]]) -> str:
    parts = []
    for item in required_items:
        item_id = str(item.get("item_id") or "").strip()
        state = str(item.get("state") or "").strip()
        actionable = "1" if bool(item.get("actionable")) else "0"
        if item_id:
            parts.append(f"{item_id}:{state}:{actionable}")
    digest = hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]
    return f"setup-required:{digest}"


def _parse_iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def pilot_now_from_request(request: Request) -> datetime | None:
    return _parse_now_header(request.headers.get("x-pilot-now"))


def _trial_expires_at() -> datetime:
    return DEFAULT_TRIAL_STARTED_AT + timedelta(
        days=PILOT_TRIAL_DURATION_DAYS + _TRIAL_EXTENSION_DAYS
    )


def _trial_state(now: datetime, expires_at: datetime) -> TrialState:
    if now >= expires_at:
        return "paused"
    if _TRIAL_EXTENSION_METADATA.get("admin_extended"):
        return "admin_extended"
    return "active"


def _subscription(now: datetime | None = None) -> SubscriptionState:
    evaluated_at = now or DEFAULT_TRIAL_STARTED_AT
    expires_at = _trial_expires_at()
    state = _trial_state(evaluated_at, expires_at)
    return SubscriptionState(
        plan_id=PILOT_PLAN_ID,
        trial_started_at=_isoformat(DEFAULT_TRIAL_STARTED_AT),
        trial_expires_at=_isoformat(expires_at),
        current_state=state,
        pause_reason="trial_expired" if state == "paused" else None,
        extension_metadata=dict(_TRIAL_EXTENSION_METADATA),
        state_change_audit_trail=list(_TRIAL_AUDIT_TRAIL),
    )


def get_current_account_summary(
    now: datetime | None = None,
    *,
    account_id: str | None = None,
    owner_principal_id: str | None = None,
) -> dict[str, object]:
    model = get_pilot_account_model(
        now=now,
        account_id=account_id,
        owner_principal_id=owner_principal_id,
    )
    return {
        "status": "ok",
        "account": model["account"],
        "plan": model["plan"],
        "trial": model["trial"],
        "trial_status": model["subscription"]["current_state"],
        "primary_workspace_label": model["workspace"]["label"],
        "primary_ledger_id": model["workspace"]["ledger_id"],
    }


def get_current_subscription_summary(now: datetime | None = None) -> dict[str, object]:
    return {"status": "ok", "subscription": asdict(_subscription(now=now))}


def _dynamic_checklist_state(checklist: list[SetupChecklistItem], db: Any | None, account_id: str) -> list[SetupChecklistItem]:
    """Adjust checklist item states based on actual model/agent principal state."""
    if db is None or not account_id:
        return list(checklist)
    from backend.services.model_library import _load_model_principals
    from backend.services.agent_principal import _load_agent_principals
    model_principals = _load_model_principals(db).get(account_id, [])
    has_model = any(mp.get("status") == "active" for mp in model_principals)
    agent = _load_agent_principals(db).get(account_id)
    has_agent = isinstance(agent, dict) and agent.get("status") == "active"
    updated: list[SetupChecklistItem] = []
    for item in checklist:
        if item.item_id == "model_principal_selected" and has_model:
            updated.append(SetupChecklistItem(
                item_id=item.item_id,
                label=item.label,
                state="complete",
                actionable=False,
                actionability="informational",
                required=item.required,
                explanation="Model library selection is complete.",
                action_label=None,
                action_href=None,
                source=item.source,
            ))
        elif item.item_id == "agent_principal_bootstrapped" and has_agent:
            updated.append(SetupChecklistItem(
                item_id=item.item_id,
                label=item.label,
                state="complete",
                actionable=False,
                actionability="informational",
                required=item.required,
                explanation="Agent principal bootstrap is complete.",
                action_label=None,
                action_href=None,
                source=item.source,
            ))
        else:
            updated.append(item)
    return updated


def get_setup_checklist_summary(db: Any | None = None, account_id: str | None = None) -> dict[str, object]:
    static_checklist = [
        SetupChecklistItem(
            item_id="account_created",
            label="Account created",
            state="complete",
            actionable=False,
            actionability="informational",
            required=True,
            explanation="Your pilot account exists and can be inspected.",
            source="GET /account/current",
        ),
        SetupChecklistItem(
            item_id="workspace_provisioned",
            label="Workspace provisioned",
            state="complete",
            actionable=False,
            actionability="informational",
            required=True,
            explanation="Your DSS Space and default ledger-bound runtime are provisioned.",
            source="GET /account/current/provisioning",
        ),
        SetupChecklistItem(
            item_id="did_created",
            label="DID created",
            state="complete",
            actionable=False,
            actionability="informational",
            required=True,
            explanation="A DSS identity is available for the owner principal.",
            source="GET /account/current/identity",
        ),
        SetupChecklistItem(
            item_id="wallet_linked",
            label="Wallet linked",
            state="incomplete",
            actionable=True,
            actionability="user_actionable",
            required=False,
            explanation="Wallet linking is visible but does not block day-one pilot access.",
            action_label="Link wallet",
            action_href="/account/setup#wallet_linked",
            source="GET /account/current/identity",
        ),
        SetupChecklistItem(
            item_id="chat_surface_ready",
            label="Chat surface ready",
            state="complete",
            actionable=False,
            actionability="informational",
            required=True,
            explanation="The default chat surface is ready for the pilot workspace.",
            source="GET /account/current/surfaces",
        ),
        SetupChecklistItem(
            item_id="share_surface_ready",
            label="Share/decode surface enabled",
            state="complete",
            actionable=False,
            actionability="informational",
            required=True,
            explanation="The default share/decode surface is ready for the pilot workspace.",
            source="GET /account/current/surfaces",
        ),
        SetupChecklistItem(
            item_id="authorised_rep_invited",
            label="Authorised representative invited",
            state="incomplete",
            actionable=True,
            actionability="user_actionable",
            required=True,
            explanation="Invite an authorised representative when you are ready to add another accountable actor.",
            action_label="Invite representative",
            action_href="/account/setup#authorised_rep_invited",
            source="GET /account/current/setup-checklist",
        ),
        SetupChecklistItem(
            item_id="telegram_configured",
            label="Telegram configured",
            state="manual_only",
            actionable=False,
            actionability="admin_actionable",
            required=False,
            explanation="Telegram setup is manual-only for the pilot and will be completed through admin fulfilment.",
            source="Epic 11 Telegram follow-on",
        ),
        SetupChecklistItem(
            item_id="model_principal_selected",
            label="Model library selection",
            state="incomplete",
            actionable=True,
            actionability="user_actionable",
            required=True,
            explanation="Select a model from the library to power your DSS agent.",
            action_label="Select model",
            action_href="/account/setup#model_principal_selected",
            source="GET /account/current/model-library",
        ),
        SetupChecklistItem(
            item_id="agent_principal_bootstrapped",
            label="Agent principal bootstrapped",
            state="incomplete",
            actionable=True,
            actionability="user_actionable",
            required=True,
            explanation="Bootstrap your delegated DSS agent principal so it can act on your selected model.",
            action_label="Bootstrap agent",
            action_href="/account/setup#agent_principal_bootstrapped",
            source="POST /account/current/principals/agent/bootstrap",
        ),
        SetupChecklistItem(
            item_id="ledger_document_archive_seeded",
            label="Ledger document archive seeded",
            state="incomplete",
            actionable=True,
            actionability="user_actionable",
            required=False,
            explanation="Upload foundational documents to your ledger so DSS can surface them to principals and retrieval flows.",
            action_label="Upload documents",
            action_href="/account/setup/documents",
            source="GET /account/current/surfaces",
        ),
        SetupChecklistItem(
            item_id="document_surface_enabled",
            label="Document surface enabled",
            state="disabled",
            actionable=False,
            actionability="informational",
            required=False,
            explanation="Document workspace is modeled as a disabled placeholder until the document MVP is enabled.",
            source="GET /account/current/surfaces",
        ),
    ]
    resolved_account_id = account_id or DEFAULT_ACCOUNT_ID
    checklist = _dynamic_checklist_state(static_checklist, db, resolved_account_id)
    complete = all(
        item.state in {"complete", "not_applicable"}
        for item in checklist
        if item.required
    )
    incomplete_required = [
        item.item_id
        for item in checklist
        if item.required and item.state not in {"complete", "not_applicable"}
    ]
    return {
        "status": "ok",
        "setup_checklist": {
            "account_id": resolved_account_id,
            "workspace_id": DEFAULT_WORKSPACE_ID,
            "item_version": "epic-04.v1",
            "items": [asdict(item) for item in checklist],
            "complete": complete,
            "summary": {
                "total": len(checklist),
                "complete": sum(1 for item in checklist if item.state == "complete"),
                "required": sum(1 for item in checklist if item.required),
                "required_complete": sum(
                    1
                    for item in checklist
                    if item.required and item.state in {"complete", "not_applicable"}
                ),
                "incomplete_required_item_ids": incomplete_required,
            },
        },
    }


def _setup_prompt_payload(
    *,
    checklist: dict[str, object],
    principal_id: str,
    dismissal: dict[str, Any] | None,
    now: datetime,
) -> dict[str, object]:
    account_id = str(checklist.get("account_id") or DEFAULT_ACCOUNT_ID).strip() or DEFAULT_ACCOUNT_ID
    workspace_id = str(checklist.get("workspace_id") or DEFAULT_WORKSPACE_ID).strip() or DEFAULT_WORKSPACE_ID
    required_items = _required_setup_items(checklist)
    required_item_ids = [str(item.get("item_id") or "").strip() for item in required_items if str(item.get("item_id") or "").strip()]
    required_set_version = _required_item_set_version(required_items)
    highest_priority = required_items[0] if required_items else None
    target_item_id = str(highest_priority.get("item_id") or "").strip() if isinstance(highest_priority, dict) else None
    base_visible = bool(required_items)
    dismissal_record = dict(dismissal or {})
    dismissal_active = False
    dismissal_reason: str | None = None
    invalidated = False
    mode = str(dismissal_record.get("mode") or "").strip()
    dismissed_version = str(dismissal_record.get("required_item_set_version") or "").strip()
    if base_visible and mode:
        if dismissed_version and dismissed_version != required_set_version:
            invalidated = True
            dismissal_reason = "required_item_set_changed"
        elif mode == "do_not_show_again_for_current_required_set":
            dismissal_active = True
            dismissal_reason = mode
        elif mode == "snoozed_until":
            snoozed_until = _parse_iso_datetime(dismissal_record.get("snoozed_until"))
            if snoozed_until and snoozed_until > now:
                dismissal_active = True
                dismissal_reason = mode
            else:
                invalidated = True
                dismissal_reason = "snooze_expired"
        elif mode == "dismissed_for_session":
            # Required setup reminders must survive page/session churn. Session
            # dismissal is retained as audit state but cannot suppress required work.
            invalidated = True
            dismissal_reason = mode
    visible = base_visible and not dismissal_active
    return {
        "status": "ok",
        "setup_prompt": {
            "account_id": account_id,
            "workspace_id": workspace_id,
            "principal_id": principal_id,
            "visible": visible,
            "base_visible": base_visible,
            "reason": "required_setup_incomplete" if base_visible else "setup_complete",
            "suppressed_by_dismissal": dismissal_active,
            "dismissal_reason": dismissal_reason,
            "dismissal_invalidated": invalidated,
            "required_item_set_version": required_set_version,
            "required_item_count": len(required_items),
            "required_item_ids": required_item_ids,
            "target": {
                "route": "/account/setup",
                "item_id": target_item_id,
            },
            "copy": {
                "title": "Finish setting up your DSS Space",
                "body": "Some account setup tasks still need attention.",
                "action_label": "View setup checklist",
            },
            "dismissal_options": [
                "dismissed_for_session",
                "snoozed_until",
                "do_not_show_again_for_current_required_set",
            ],
            "dismissal": dismissal_record or None,
        },
    }


def _account_id_from_request(request: Request, db: Any) -> str | None:
    """Try to resolve the actual account_id from session token claims or x-principal-id header.

    Returns None for unmapped principals — no shared default fallback.
    """
    principal_candidates: list[str] = []
    try:
        from backend.services.session_tokens import apply_session_token_claims_or_raise
        claims = apply_session_token_claims_or_raise(request)
        if isinstance(claims, dict):
            principal_did = str(claims.get("sub") or "").strip()
            if principal_did:
                principal_candidates.append(principal_did)
    except Exception:
        pass
    # Fallback to x-principal-id header for test/operator contexts
    header_principal = str(request.headers.get("x-principal-id") or "").strip()
    if header_principal and header_principal not in principal_candidates:
        principal_candidates.append(header_principal)
    if db is not None:
        from backend.services.pilot_onboarding import _load_pilot_signups
        for principal_did in principal_candidates:
            for record in _load_pilot_signups(db).values():
                if str(record.get("principal_did") or "").strip() == principal_did:
                    account_id = str(record.get("account_id") or "").strip()
                    if account_id:
                        return account_id
    return None


def get_setup_prompt_state(
    request: Request,
    db: Any,
    *,
    checklist_override: dict[str, object] | None = None,
) -> dict[str, object]:
    principal_id = _principal_id_from_request(request)
    account_id = _account_id_from_request(request, db)

    # Check signup state for wizard-driven entry
    signup_state: dict[str, Any] | None = None
    if db is not None:
        from backend.services.pilot_onboarding import _signup_state_for_principal
        signup_state = _signup_state_for_principal(db, principal_id)

    # No account yet — handle wizard states
    if not account_id:
        if signup_state:
            verification_status = str(signup_state.get("verification_status") or "").strip().lower()
            if verification_status == "awaiting_operator_approval":
                # User completed wizard, waiting for approval — do not nag
                return {
                    "status": "ok",
                    "setup_prompt": {
                        "account_id": str(signup_state.get("account_id") or DEFAULT_ACCOUNT_ID),
                        "workspace_id": DEFAULT_WORKSPACE_ID,
                        "principal_id": principal_id,
                        "visible": False,
                        "base_visible": False,
                        "reason": "wizard_pending",
                        "suppressed_by_dismissal": False,
                        "dismissal_reason": None,
                        "dismissal_invalidated": False,
                        "required_item_set_version": "wizard_pending",
                        "required_item_count": 0,
                        "required_item_ids": [],
                        "target": {"route": "/account/setup", "item_id": None},
                        "copy": {
                            "title": "Awaiting Operator Approval",
                            "body": "Your account setup request has been submitted and is awaiting operator approval.",
                            "action_label": "View status",
                        },
                        "dismissal_options": [],
                        "dismissal": None,
                    },
                }

        # No signup at all — prompt to start wizard
        return {
            "status": "ok",
            "setup_prompt": {
                "account_id": DEFAULT_ACCOUNT_ID,
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "principal_id": principal_id,
                "visible": True,
                "base_visible": True,
                "reason": "wizard_entry_recommended",
                "suppressed_by_dismissal": False,
                "dismissal_reason": None,
                "dismissal_invalidated": False,
                "required_item_set_version": "wizard_entry",
                "required_item_count": 1,
                "required_item_ids": ["account_created"],
                "target": {"route": "/wizard", "item_id": None},
                "copy": {
                    "title": "Welcome to DSS",
                    "body": "Complete the Account Setup Wizard to request access.",
                    "action_label": "Start Account Setup Wizard",
                },
                "dismissal_options": [
                    "dismissed_for_session",
                    "snoozed_until",
                    "do_not_show_again_for_current_required_set",
                ],
                "dismissal": None,
            },
        }

    checklist = checklist_override or get_setup_checklist_summary(db, account_id)["setup_checklist"]
    if not isinstance(checklist, dict):
        checklist = {}
    records = _load_setup_prompt_dismissals(db)
    dismissal = records.get(_dismissal_key(account_id, principal_id))
    return _setup_prompt_payload(
        checklist=checklist,
        principal_id=principal_id,
        dismissal=dismissal,
        now=pilot_now_from_request(request) or datetime.now(timezone.utc),
    )


def dismiss_setup_prompt(
    request: Request,
    db: Any,
    *,
    mode: SetupPromptDismissalMode,
    snoozed_until: str | None = None,
) -> dict[str, object]:
    principal_id = _principal_id_from_request(request)
    account_id = _account_id_from_request(request, db)
    if not account_id:
        raise HTTPException(status_code=404, detail={"error": "account_not_found"})
    checklist = get_setup_checklist_summary(db, account_id)["setup_checklist"]
    if not isinstance(checklist, dict):
        checklist = {}
    required_items = _required_setup_items(checklist)
    required_set_version = _required_item_set_version(required_items)
    normalized_mode = str(mode or "").strip()
    allowed = {
        "dismissed_for_session",
        "snoozed_until",
        "do_not_show_again_for_current_required_set",
    }
    if normalized_mode not in allowed:
        raise HTTPException(status_code=422, detail={"error": "unsupported_dismissal_mode"})
    if normalized_mode == "snoozed_until":
        parsed = _parse_iso_datetime(snoozed_until)
        now = pilot_now_from_request(request) or datetime.now(timezone.utc)
        if parsed is None or parsed <= now:
            raise HTTPException(status_code=422, detail={"error": "snoozed_until_must_be_future"})
        snoozed_until_value = parsed.isoformat().replace("+00:00", "Z")
    else:
        snoozed_until_value = None
    records = _load_setup_prompt_dismissals(db)
    key = _dismissal_key(account_id, principal_id)
    records[key] = {
        "account_id": account_id,
        "principal_id": principal_id,
        "mode": normalized_mode,
        "required_item_set_version": required_set_version,
        "snoozed_until": snoozed_until_value,
        "dismissed_at": _now_iso(),
    }
    canonical = _persist_setup_prompt_dismissals(db, records)
    dismissal = canonical.get(key)
    return _setup_prompt_payload(
        checklist=checklist,
        principal_id=principal_id,
        dismissal=dismissal,
        now=pilot_now_from_request(request) or datetime.now(timezone.utc),
    )


def get_pilot_account_model(
    now: datetime | None = None,
    *,
    account_id: str | None = None,
    owner_principal_id: str | None = None,
    workspace_id: str | None = None,
    workspace_label: str | None = None,
    ledger_id: str | None = None,
) -> dict[str, object]:
    resolved_account_id = account_id or DEFAULT_ACCOUNT_ID
    resolved_owner_principal_id = owner_principal_id or DEFAULT_OWNER_PRINCIPAL_ID
    resolved_workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
    resolved_workspace_label = workspace_label or DEFAULT_WORKSPACE_LABEL
    resolved_ledger_id = ledger_id or DEFAULT_LEDGER_ID
    subscription = asdict(_subscription(now=now))
    account = Account(
        account_id=resolved_account_id,
        status="active",
        display_name="Pilot Account",
        owner_principal_id=resolved_owner_principal_id,
    )
    workspace = Workspace(
        workspace_id=resolved_workspace_id,
        label=resolved_workspace_label,
        product_label="DSS Space",
        status="active",
        ledger_id=resolved_ledger_id,
    )
    surfaces = [
        Surface(
            surface_id="surface:pilot-chat",
            surface_type="chat",
            status="enabled",
            ledger_id=resolved_ledger_id,
        ),
        Surface(
            surface_id="surface:pilot-share-decode",
            surface_type="share_decode",
            status="enabled",
            ledger_id=resolved_ledger_id,
        ),
    ]
    principals = [
        Principal(
            principal_id=resolved_owner_principal_id,
            principal_type="human_owner",
            status="active",
            account_id=resolved_account_id,
            workspace_id=resolved_workspace_id,
        )
    ]
    invites = [
        Invite(
            invite_id=DEFAULT_AUTHORISED_REP_INVITE_ID,
            invite_type="authorised_representative",
            status="reserved",
            account_id=resolved_account_id,
            workspace_id=resolved_workspace_id,
        )
    ]
    checklist = get_setup_checklist_summary(account_id=resolved_account_id)["setup_checklist"]
    return {
        "account": asdict(account),
        "workspace": asdict(workspace),
        "subscription": subscription,
        "trial": subscription,
        "plan": get_pilot_plan_contract(PILOT_PLAN_ID),
        "surfaces": [asdict(surface) for surface in surfaces],
        "principals": [asdict(principal) for principal in principals],
        "invites": [asdict(invite) for invite in invites],
        "setup_checklist": checklist,
        "provisioning": {
            "mode": "derived_default",
            "ledger_anchor": resolved_ledger_id,
            "workspace_is_product_context": True,
        },
    }


def get_admin_account_inspection(account_id: str) -> dict[str, object]:
    normalized = str(account_id or "").strip()
    if normalized != DEFAULT_ACCOUNT_ID:
        raise KeyError(f"unknown account: {account_id}")
    return {"status": "ok", "account_inspection": get_pilot_account_model()}


def is_pilot_paused(now: datetime | None = None) -> bool:
    return _subscription(now=now).current_state == "paused"


def assert_pilot_write_allowed(
    *,
    now: datetime | None = None,
    action: str,
) -> None:
    subscription = _subscription(now=now)
    if subscription.current_state != "paused":
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "trial_paused",
            "message": "Free trial is paused; read-only account access remains available.",
            "blocked_action": action,
            "current_state": subscription.current_state,
            "pause_reason": subscription.pause_reason,
            "trial_expires_at": subscription.trial_expires_at,
        },
    )


def enforce_pilot_write_allowed(request: Request, *, action: str) -> None:
    assert_pilot_write_allowed(now=pilot_now_from_request(request), action=action)


def extend_pilot_trial(
    *,
    days: int,
    actor: str = "admin",
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    if days <= 0:
        raise ValueError("days must be greater than zero")

    global _TRIAL_EXTENSION_DAYS
    occurred_at = now or datetime.now(timezone.utc)
    _TRIAL_EXTENSION_DAYS += days
    _TRIAL_EXTENSION_METADATA.update(
        {
            "admin_extended": True,
            "extension_count": int(_TRIAL_EXTENSION_METADATA["extension_count"]) + 1,
            "last_extension_at": _isoformat(occurred_at),
            "last_extended_by": actor,
            "last_reason": reason,
        }
    )
    _TRIAL_AUDIT_TRAIL.append(
        {
            "event": "trial_admin_extended",
            "status": "admin_extended",
            "occurred_at": _isoformat(occurred_at),
            "actor": actor,
            "reason": reason,
            "extension_days": days,
            "trial_expires_at": _isoformat(_trial_expires_at()),
        }
    )
    return get_current_subscription_summary(now=occurred_at)


def reset_pilot_trial_state_for_tests() -> None:
    global _TRIAL_EXTENSION_DAYS
    _TRIAL_EXTENSION_DAYS = 0
    _TRIAL_EXTENSION_METADATA.clear()
    _TRIAL_EXTENSION_METADATA.update(
        {
            "admin_extended": False,
            "extension_count": 0,
            "last_extension_at": None,
            "last_extended_by": None,
            "last_reason": None,
        }
    )
    del _TRIAL_AUDIT_TRAIL[1:]
