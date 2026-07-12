"""Pilot onboarding state helpers for the Epic 2 entry corridor."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from backend.services.session_tokens import apply_session_token_claims_or_raise


PILOT_SIGNUPS_V1_KEY = b"__pilot_signups_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_pilot_signups(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(PILOT_SIGNUPS_V1_KEY)
    payload = _decode_json(raw)
    records = payload.get("signups") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        if isinstance(record, dict):
            out[str(key)] = dict(record)
    return out


def _persist_pilot_signups(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for key in sorted(records.keys()):
        record = records.get(key)
        if isinstance(record, dict):
            canonical[key] = dict(record)
    db[PILOT_SIGNUPS_V1_KEY] = json.dumps(
        {"version": 1, "signups": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _signup_for_principal(db: Any, principal_did: str) -> tuple[str, dict[str, Any]]:
    did = str(principal_did or "").strip()
    for signup_id, record in _load_pilot_signups(db).items():
        if str(record.get("principal_did") or "").strip() == did:
            return signup_id, dict(record)
    raise HTTPException(status_code=404, detail={"error": "pilot_signup_not_found"})


def _signup_state_for_principal(db: Any, principal_did: str) -> dict[str, Any] | None:
    """Return the signup record for a principal, or None if not found. Does not raise."""
    did = str(principal_did or "").strip()
    if not did or db is None:
        return None
    for record in _load_pilot_signups(db).values():
        if str(record.get("principal_did") or "").strip() == did:
            return dict(record)
    return None


def _current_principal_did_or_raise(request: Request) -> str:
    claims = apply_session_token_claims_or_raise(request)
    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail={"error": "authentication_required"})
    principal_did = str(claims.get("sub") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=401, detail={"error": "principal_did_required"})
    return principal_did


def _next_route(record: dict[str, Any], db: Any | None = None) -> str:
    verification_status = str(record.get("verification_status") or "").strip().lower()
    onboarding_status = str(record.get("onboarding_status") or "not_started").strip().lower()
    provisioning_status = str(record.get("provisioning_status") or "not_started").strip().lower()
    trial_state = str(record.get("trial_state") or "").strip().lower()
    if verification_status != "verified":
        return "verification_or_recovery"
    if onboarding_status not in {"submitted", "accepted", "complete", "completed"}:
        return "onboarding"
    if provisioning_status not in {"succeeded", "complete", "completed"}:
        return "provisioning_status"
    # Check model principal and agent principal for prompt readiness
    if db is not None:
        account_id = str(record.get("account_id") or "").strip()
        if account_id:
            from backend.services.model_library import _load_model_principals
            from backend.services.agent_principal import _load_agent_principals
            model_principals = _load_model_principals(db).get(account_id, [])
            has_model = any(mp.get("status") == "active" for mp in model_principals)
            if not has_model:
                return "model_library_selection"
            agent_principals = _load_agent_principals(db)
            agent = agent_principals.get(account_id)
            has_agent = isinstance(agent, dict) and agent.get("status") == "active"
            if not has_agent:
                return "agent_principal_bootstrap"
    if trial_state == "paused":
        return "account_landing_read_only"
    return "account_workspace_landing"


def _provisioning_request_id(account_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{account_id}:{idempotency_key}".encode("utf-8")).hexdigest()[:16]
    return f"provreq:{digest}"


def _onboarding_summary(record: dict[str, Any], db: Any | None = None) -> dict[str, Any]:
    onboarding = record.get("onboarding")
    onboarding = onboarding if isinstance(onboarding, dict) else {}
    summary = {
        "account_id": record.get("account_id"),
        "principal_did": record.get("principal_did"),
        "status": record.get("onboarding_status", "not_started"),
        "payload": onboarding.get("payload") if isinstance(onboarding.get("payload"), dict) else None,
        "accepted_at": onboarding.get("accepted_at"),
        "updated_at": onboarding.get("updated_at"),
        "next_route": _next_route(record, db),
    }
    # Enrich with model and agent principal state for re-entry resilience
    if db is not None:
        account_id = str(record.get("account_id") or "").strip()
        if account_id:
            from backend.services.model_library import _load_model_principals
            from backend.services.agent_principal import _load_agent_principals
            model_principals = _load_model_principals(db).get(account_id, [])
            active_models = [mp for mp in model_principals if mp.get("status") == "active"]
            summary["model_principal"] = {
                "selected": len(active_models) > 0,
                "count": len(active_models),
                "principal_ids": [mp.get("principal_id") for mp in active_models],
            }
            agent = _load_agent_principals(db).get(account_id)
            summary["agent_principal"] = {
                "bootstrapped": isinstance(agent, dict) and agent.get("status") == "active",
                "principal_id": agent.get("principal_id") if isinstance(agent, dict) else None,
            }
    return summary


def _provisioning_summary(record: dict[str, Any], db: Any | None = None) -> dict[str, Any]:
    provisioning = record.get("provisioning")
    provisioning = provisioning if isinstance(provisioning, dict) else {}
    return {
        "account_id": record.get("account_id"),
        "status": record.get("provisioning_status", "not_started"),
        "request_id": provisioning.get("request_id"),
        "job_id": provisioning.get("job_id"),
        "package_version": provisioning.get("package_version"),
        "triggered_at": provisioning.get("triggered_at"),
        "trigger_source": provisioning.get("trigger_source"),
        "completed_at": provisioning.get("completed_at"),
        "resource_step_count": provisioning.get("resource_step_count"),
        "next_route": _next_route(record, db),
    }


def get_current_onboarding(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    _signup_id, record = _signup_for_principal(db, principal_did)
    return {"status": "ok", "onboarding": _onboarding_summary(record, db)}


def get_current_provisioning(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    _signup_id, record = _signup_for_principal(db, principal_did)
    summary = _provisioning_summary(record, db)
    try:
        from backend.services.pilot_provisioning import get_provisioning_job_for_record

        job = get_provisioning_job_for_record(db, record)
        if isinstance(job, dict):
            summary["job"] = {
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "package_version": job.get("package_version"),
                "resource_steps": job.get("resource_steps") if isinstance(job.get("resource_steps"), list) else [],
            }
    except Exception:
        summary["job"] = None
    return {"status": "ok", "provisioning": summary}


def submit_current_onboarding(
    request: Request,
    db: Any,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    signups = _load_pilot_signups(db)
    signup_id, record = _signup_for_principal(db, principal_did)
    if str(record.get("verification_status") or "").strip().lower() != "verified":
        raise HTTPException(status_code=403, detail={"error": "pilot_signup_not_verified"})

    current_status = str(record.get("onboarding_status") or "not_started").strip().lower()
    existing_onboarding = record.get("onboarding")
    existing_onboarding = existing_onboarding if isinstance(existing_onboarding, dict) else {}
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        raise HTTPException(status_code=422, detail={"error": "idempotency_key_required", "field": "idempotency_key"})
    if current_status == "accepted":
        existing_key = str(existing_onboarding.get("idempotency_key") or "").strip()
        if existing_key == idempotency_key:
            return {
                "status": "ok",
                "onboarding": _onboarding_summary(record),
                "provisioning": _provisioning_summary(record),
                "idempotent_replay": True,
            }
        raise HTTPException(status_code=409, detail={"error": "onboarding_already_accepted"})

    owner_display_name = str(payload.get("owner_display_name") or "").strip()
    workspace_label = str(payload.get("workspace_or_dss_space_label") or "").strip()
    primary_contact = str(payload.get("primary_contact") or "").strip().lower()
    pilot_use_case = str(payload.get("pilot_use_case") or "").strip()
    if not owner_display_name:
        raise HTTPException(status_code=422, detail={"error": "owner_display_name_required", "field": "owner_display_name"})
    if len(workspace_label) < 2:
        raise HTTPException(status_code=422, detail={"error": "workspace_label_invalid", "field": "workspace_or_dss_space_label"})
    if "@" not in primary_contact:
        raise HTTPException(status_code=422, detail={"error": "primary_contact_invalid", "field": "primary_contact"})
    if primary_contact != str(record.get("primary_contact") or "").strip():
        raise HTTPException(status_code=409, detail={"error": "primary_contact_conflict"})
    if not pilot_use_case:
        raise HTTPException(status_code=422, detail={"error": "pilot_use_case_required", "field": "pilot_use_case"})
    if payload.get("free_trial_scope_acknowledgement") is not True:
        raise HTTPException(
            status_code=422,
            detail={"error": "free_trial_scope_acknowledgement_required", "field": "free_trial_scope_acknowledgement"},
        )

    now = _now_iso()
    account_id = str(record.get("account_id") or "").strip()
    provision_request_id = _provisioning_request_id(account_id, idempotency_key)
    onboarding_payload = {
        "owner_display_name": owner_display_name,
        "workspace_or_dss_space_label": workspace_label,
        "primary_contact": primary_contact,
        "pilot_use_case": pilot_use_case,
        "free_trial_scope_acknowledgement": True,
        "authorised_representative_email_placeholder": (
            str(payload.get("authorised_representative_email_placeholder") or "").strip() or None
        ),
    }
    updated = dict(record)
    updated["owner_display_name"] = owner_display_name
    updated["workspace_label"] = workspace_label
    updated["onboarding_status"] = "accepted"
    updated["provisioning_status"] = "queued"
    updated["updated_at"] = now
    updated["onboarding"] = {
        "status": "accepted",
        "payload": onboarding_payload,
        "idempotency_key": idempotency_key,
        "accepted_at": now,
        "updated_at": now,
    }
    updated["provisioning"] = {
        "status": "queued",
        "request_id": provision_request_id,
        "triggered_at": now,
        "trigger_source": "accepted_onboarding_submission",
    }
    signups[signup_id] = updated
    canonical = _persist_pilot_signups(db, signups)
    persisted = canonical.get(signup_id) or updated
    return {
        "status": "ok",
        "onboarding": _onboarding_summary(persisted),
        "provisioning": _provisioning_summary(persisted),
        "idempotent_replay": False,
    }
