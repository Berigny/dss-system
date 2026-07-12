"""Idempotent default provisioning jobs for pilot accounts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from backend.services.pilot_onboarding import (
    _current_principal_did_or_raise,
    _load_pilot_signups,
    _persist_pilot_signups,
    _signup_for_principal,
)


PILOT_PROVISIONING_JOBS_V1_KEY = b"__pilot_provisioning_jobs_v1__"
DEFAULT_PACKAGE_VERSION = "free_trial_default_v1"
SURFACE_REQUIRED_METADATA = (
    "account_id",
    "dss_space_id",
    "ledger_id",
    "tenant_id",
    "owner_principal_id",
    "policy_scope",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_jobs(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(PILOT_PROVISIONING_JOBS_V1_KEY)
    payload = _decode_json(raw)
    records = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        if isinstance(record, dict):
            out[str(key)] = dict(record)
    return out


def _persist_jobs(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for key in sorted(records.keys()):
        record = records.get(key)
        if isinstance(record, dict):
            canonical[key] = dict(record)
    db[PILOT_PROVISIONING_JOBS_V1_KEY] = json.dumps(
        {"version": 1, "jobs": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _job_id(account_id: str, provisioning_request_id: str, package_version: str = DEFAULT_PACKAGE_VERSION, wallet_did: str | None = None) -> str:
    if wallet_did:
        return _stable_id("provjob", account_id, provisioning_request_id, package_version, wallet_did)
    return _stable_id("provjob", account_id, provisioning_request_id, package_version)


def _resource_id(prefix: str, account_id: str, provisioning_request_id: str) -> str:
    return _stable_id(prefix, account_id, provisioning_request_id)


def _step(
    *,
    step_id: str,
    resource_type: str,
    resource_id: str,
    status: str,
    now: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "idempotency_key": f"{step_id}:{resource_id}",
        "retry_eligible": status in {"failed", "requires_admin"},
        "failure_reason": None,
        "created_at": now,
        "updated_at": now,
        "metadata": metadata or {},
    }


def _resource_steps(
    *,
    account_id: str,
    provisioning_request_id: str,
    record: dict[str, Any],
    now: str,
) -> list[dict[str, Any]]:
    workspace_label = str(record.get("workspace_label") or "Pilot DSS Space").strip() or "Pilot DSS Space"
    primary_contact = str(record.get("primary_contact") or "").strip()
    owner_display_name = str(record.get("owner_display_name") or "Pilot Owner").strip() or "Pilot Owner"
    dss_space_id = _resource_id("space", account_id, provisioning_request_id)
    ledger_id = _resource_id("ledger", account_id, provisioning_request_id)
    owner_principal_id = str(record.get("principal_did") or "").strip() or _resource_id(
        "principal",
        account_id,
        provisioning_request_id,
    )
    tenant_id = _resource_id("tenant", account_id, provisioning_request_id)
    policy_scope = "free_trial_default"
    common = {
        "account_id": account_id,
        "dss_space_id": dss_space_id,
        "ledger_id": ledger_id,
        "tenant_id": tenant_id,
        "owner_principal_id": owner_principal_id,
        "policy_scope": policy_scope,
    }

    # Extract wallet provider and DID from signup record
    wallet = record.get("wallet")
    wallet_provider = "provider_pending"
    wallet_did = ""
    if isinstance(wallet, dict):
        wallet_provider = str(wallet.get("provider") or "provider_pending").strip() or "provider_pending"
        wallet_did = str(wallet.get("did") or "").strip()

    # Derive did_method from wallet_did or principal_did
    did_method = ""
    if wallet_did.startswith("did:"):
        did_method = wallet_did.split(":", 2)[1]
    else:
        principal_did = str(record.get("principal_did") or "").strip()
        if principal_did.startswith("did:"):
            did_method = principal_did.split(":", 2)[1]
    if not did_method:
        did_method = "unknown"

    return [
        _step(
            step_id="dss_space",
            resource_type="dss_space",
            resource_id=dss_space_id,
            status="succeeded",
            now=now,
            metadata={**common, "display_name": workspace_label, "product_label": "DSS Space"},
        ),
        _step(
            step_id="ledger_runtime",
            resource_type="ledger_runtime",
            resource_id=ledger_id,
            status="succeeded",
            now=now,
            metadata={**common, "runtime_boundary": "ledger", "status": "active"},
        ),
        _step(
            step_id="wallet_provider_binding",
            resource_type="wallet_provider_binding",
            resource_id=_resource_id("wallet", account_id, provisioning_request_id),
            status="succeeded",
            now=now,
            metadata={
                **common,
                "wallet_provider": wallet_provider,
                "wallet_did": wallet_did,
                "did_method": did_method,
                "credential_offer_uri": f"/wallet/credential-offer?session_id={account_id}&wallet_provider={wallet_provider}",
                "did_resolution_url": f"/wallet/{wallet_provider}/did.json",
            },
        ),
        _step(
            step_id="owner_human_principal",
            resource_type="human_principal",
            resource_id=owner_principal_id,
            status="succeeded",
            now=now,
            metadata={**common, "principal_type": "human_owner", "display_name": owner_display_name, "primary_contact": primary_contact},
        ),
        _step(
            step_id="chat_surface",
            resource_type="surface_binding",
            resource_id=_resource_id("surface:chat", account_id, provisioning_request_id),
            status="succeeded",
            now=now,
            metadata={**common, "surface_type": "chat", "surface_status": "ready"},
        ),
        _step(
            step_id="share_surface",
            resource_type="surface_binding",
            resource_id=_resource_id("surface:share", account_id, provisioning_request_id),
            status="succeeded",
            now=now,
            metadata={
                **common,
                "surface_type": "share_decode",
                "surface_status": "ready",
                "entitlement_ref": "free_trial.included.share_surfaces",
                "allowed_actions": ["decode_coordinate", "view_public_object_status"],
            },
        ),
        _step(
            step_id="document_surface",
            resource_type="surface_binding",
            resource_id=_resource_id("surface:document", account_id, provisioning_request_id),
            status="succeeded",
            now=now,
            metadata={
                **common,
                "surface_type": "document",
                "surface_status": "disabled",
                "entitlement_ref": "free_trial.manual_only.document_surface",
                "disabled_reason": "document_surface_not_enabled_for_pilot",
                "display_label": "Documents",
                "status_copy": "Document workspace is not enabled for this pilot yet.",
                "admin_enablement": {
                    "supported": True,
                    "future_statuses": ["manual_enabled", "enabled"],
                    "required_authority": "admin",
                },
                "launch_enabled": False,
            },
        ),
    ]


def _job_summary(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(job, dict):
        return None
    steps = job.get("resource_steps") if isinstance(job.get("resource_steps"), list) else []
    return {
        "job_id": job.get("job_id"),
        "account_id": job.get("account_id"),
        "status": job.get("status"),
        "package_version": job.get("package_version"),
        "provisioning_request_id": job.get("provisioning_request_id"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "completed_at": job.get("completed_at"),
        "resource_steps": steps,
        "resource_counts": {
            "total": len(steps),
            "succeeded": len([step for step in steps if isinstance(step, dict) and step.get("status") in {"succeeded", "skipped_existing"}]),
            "failed": len([step for step in steps if isinstance(step, dict) and step.get("status") in {"failed", "requires_admin"}]),
        },
    }


def _surface_launch_metadata(
    surface_type: str,
    metadata: dict[str, Any],
    *,
    ready: bool,
    binding_complete: bool = True,
) -> dict[str, Any]:
    if not ready and not binding_complete:
        return {"launch_enabled": False, "reason": "surface_binding_not_ready"}
    if surface_type == "chat":
        return {
            "launch_enabled": True,
            "target": "chat",
            "route": "/chat",
            "ledger_id": metadata.get("ledger_id"),
            "tenant_id": metadata.get("tenant_id"),
            "principal_id": metadata.get("owner_principal_id"),
            "policy_scope": metadata.get("policy_scope"),
        }
    if surface_type == "share_decode":
        return {
            "launch_enabled": True,
            "target": "share_decode",
            "route": "/web4/decode",
            "ledger_id": metadata.get("ledger_id"),
            "tenant_id": metadata.get("tenant_id"),
            "principal_id": metadata.get("owner_principal_id"),
            "policy_scope": metadata.get("policy_scope"),
            "allowed_actions": metadata.get("allowed_actions") if isinstance(metadata.get("allowed_actions"), list) else [],
        }
    if surface_type == "document":
        return {
            "launch_enabled": False,
            "reason": str(metadata.get("disabled_reason") or "surface_disabled"),
        }
    return {"launch_enabled": False, "reason": "surface_disabled"}


def _surface_binding_from_step(step: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(step, dict) or step.get("resource_type") != "surface_binding":
        return None
    metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
    surface_type = str(metadata.get("surface_type") or "").strip()
    if not surface_type:
        return None
    missing = [
        field
        for field in SURFACE_REQUIRED_METADATA
        if not str(metadata.get(field) or "").strip()
    ]
    step_status = str(step.get("status") or "").strip().lower()
    declared_status = str(metadata.get("surface_status") or "").strip().lower()
    ready = not missing and step_status in {"succeeded", "skipped_existing"} and declared_status == "ready"
    disabled = declared_status in {"disabled", "coming_soon"}
    status = "ready" if ready else "disabled" if disabled and not missing else "requires_admin" if missing else declared_status or "pending"
    return {
        "surface_id": step.get("resource_id"),
        "surface_type": surface_type,
        "account_id": metadata.get("account_id"),
        "dss_space_id": metadata.get("dss_space_id"),
        "ledger_id": metadata.get("ledger_id"),
        "tenant_id": metadata.get("tenant_id"),
        "owner_principal_id": metadata.get("owner_principal_id"),
        "policy_scope": metadata.get("policy_scope"),
        "entitlement_ref": metadata.get("entitlement_ref"),
        "allowed_actions": metadata.get("allowed_actions") if isinstance(metadata.get("allowed_actions"), list) else [],
        "display_label": metadata.get("display_label"),
        "status_copy": metadata.get("status_copy"),
        "admin_enablement": metadata.get("admin_enablement") if isinstance(metadata.get("admin_enablement"), dict) else None,
        "status": status,
        "ready": ready,
        "missing_binding_fields": missing,
        "failure_reason": "surface_binding_incomplete" if missing else step.get("failure_reason"),
        "launch_metadata": _surface_launch_metadata(surface_type, metadata, ready=ready, binding_complete=not missing),
    }


def _surface_bindings_from_job(job: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(job, dict):
        return []
    steps = job.get("resource_steps") if isinstance(job.get("resource_steps"), list) else []
    surfaces: list[dict[str, Any]] = []
    for step in steps:
        surface = _surface_binding_from_step(step)
        if surface is not None:
            surfaces.append(surface)
    return surfaces


def _signup_provisioning_summary(record: dict[str, Any], job: dict[str, Any] | None = None) -> dict[str, Any]:
    provisioning = record.get("provisioning") if isinstance(record.get("provisioning"), dict) else {}
    summary = {
        "account_id": record.get("account_id"),
        "status": record.get("provisioning_status", "not_started"),
        "request_id": provisioning.get("request_id"),
        "triggered_at": provisioning.get("triggered_at"),
        "trigger_source": provisioning.get("trigger_source"),
        "job": _job_summary(job),
    }
    return summary


def get_provisioning_job_for_record(db: Any, record: dict[str, Any]) -> dict[str, Any] | None:
    provisioning = record.get("provisioning") if isinstance(record.get("provisioning"), dict) else {}
    request_id = str(provisioning.get("request_id") or "").strip()
    account_id = str(record.get("account_id") or "").strip()
    if not request_id or not account_id:
        return None
    wallet = record.get("wallet")
    wallet_did = str(wallet.get("did") or "").strip() if isinstance(wallet, dict) else None
    return _load_jobs(db).get(_job_id(account_id, request_id, wallet_did=wallet_did or None))


def get_current_surfaces(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    _signup_id, record = _signup_for_principal(db, principal_did)
    job = get_provisioning_job_for_record(db, record)
    surfaces = _surface_bindings_from_job(job)
    status = "ok" if surfaces else "provisioning_not_ready"
    return {
        "status": status,
        "account_id": record.get("account_id"),
        "provisioning_status": record.get("provisioning_status", "not_started"),
        "job_id": job.get("job_id") if isinstance(job, dict) else None,
        "surfaces": surfaces,
    }


def run_current_provisioning(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    signup_id, record = _signup_for_principal(db, principal_did)
    if str(record.get("verification_status") or "").strip().lower() != "verified":
        raise HTTPException(status_code=403, detail={"error": "pilot_signup_not_verified"})
    if str(record.get("onboarding_status") or "").strip().lower() != "accepted":
        raise HTTPException(status_code=409, detail={"error": "onboarding_not_accepted"})

    provisioning = record.get("provisioning") if isinstance(record.get("provisioning"), dict) else {}
    provisioning_request_id = str(provisioning.get("request_id") or "").strip()
    account_id = str(record.get("account_id") or "").strip()
    if not provisioning_request_id:
        raise HTTPException(status_code=409, detail={"error": "provisioning_request_missing"})
    if not account_id:
        raise HTTPException(status_code=409, detail={"error": "account_id_missing"})

    wallet = record.get("wallet")
    wallet_did = str(wallet.get("did") or "").strip() if isinstance(wallet, dict) else None
    jobs = _load_jobs(db)
    job_id = _job_id(account_id, provisioning_request_id, wallet_did=wallet_did or None)
    existing = jobs.get(job_id)
    if isinstance(existing, dict):
        if str(record.get("provisioning_status") or "").strip().lower() != str(existing.get("status") or "").strip().lower():
            signups = _load_pilot_signups(db)
            updated = dict(record)
            updated["provisioning_status"] = str(existing.get("status") or "succeeded").strip() or "succeeded"
            updated["updated_at"] = _now_iso()
            updated_provisioning = dict(provisioning)
            updated_provisioning.update(
                {
                    "status": updated["provisioning_status"],
                    "job_id": job_id,
                    "package_version": existing.get("package_version") or DEFAULT_PACKAGE_VERSION,
                    "completed_at": existing.get("completed_at"),
                    "resource_step_count": len(existing.get("resource_steps") or []),
                }
            )
            updated["provisioning"] = updated_provisioning
            signups[signup_id] = updated
            persisted_signups = _persist_pilot_signups(db, signups)
            record = persisted_signups.get(signup_id, updated)
        return {
            "status": "ok",
            "provisioning": _signup_provisioning_summary(record, existing),
            "job": _job_summary(existing),
            "idempotent_replay": True,
        }

    now = _now_iso()
    steps = _resource_steps(
        account_id=account_id,
        provisioning_request_id=provisioning_request_id,
        record=record,
        now=now,
    )
    job = {
        "job_id": job_id,
        "account_id": account_id,
        "principal_did": record.get("principal_did"),
        "onboarding_request_id": record.get("onboarding", {}).get("idempotency_key") if isinstance(record.get("onboarding"), dict) else None,
        "provisioning_request_id": provisioning_request_id,
        "package_version": DEFAULT_PACKAGE_VERSION,
        "status": "succeeded",
        "created_at": now,
        "updated_at": now,
        "completed_at": now,
        "resource_steps": steps,
        "error": None,
    }
    jobs[job_id] = job
    persisted_jobs = _persist_jobs(db, jobs)
    persisted_job = persisted_jobs.get(job_id, job)

    signups = _load_pilot_signups(db)
    updated = dict(record)
    updated["provisioning_status"] = "succeeded"
    updated["updated_at"] = now
    updated_provisioning = dict(provisioning)
    updated_provisioning.update(
        {
            "status": "succeeded",
            "job_id": job_id,
            "package_version": DEFAULT_PACKAGE_VERSION,
            "completed_at": now,
            "resource_step_count": len(steps),
        }
    )
    updated["provisioning"] = updated_provisioning
    signups[signup_id] = updated
    persisted_signups = _persist_pilot_signups(db, signups)
    persisted_record = persisted_signups.get(signup_id, updated)
    return {
        "status": "ok",
        "provisioning": _signup_provisioning_summary(persisted_record, persisted_job),
        "job": _job_summary(persisted_job),
        "idempotent_replay": False,
    }
