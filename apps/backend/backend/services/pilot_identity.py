"""Pilot identity and wallet visibility helpers for Epic 2."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from backend.services.pilot_onboarding import _load_pilot_signups, _persist_pilot_signups
from backend.services.session_tokens import apply_session_token_claims_or_raise


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_principal_did_or_raise(request: Request) -> str:
    claims = apply_session_token_claims_or_raise(request)
    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail={"error": "authentication_required"})
    principal_did = str(claims.get("sub") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=401, detail={"error": "principal_did_required"})
    return principal_did


def _signup_for_principal(db: Any, principal_did: str) -> tuple[str, dict[str, Any]]:
    for signup_id, record in _load_pilot_signups(db).items():
        if str(record.get("principal_did") or "").strip() == principal_did:
            return signup_id, dict(record)
    raise HTTPException(status_code=404, detail={"error": "pilot_signup_not_found"})


def _default_identity(record: dict[str, Any]) -> dict[str, Any]:
    principal_did = str(record.get("principal_did") or "").strip()
    if principal_did:
        return {
            "did_state": "created",
            "did": principal_did,
            "did_method": principal_did.split(":", 2)[1] if principal_did.startswith("did:") and len(principal_did.split(":")) > 1 else "unknown",
            "created_at": record.get("verified_at") or record.get("created_at"),
            "failure_reason": None,
        }
    return {
        "did_state": "not_started",
        "did": None,
        "did_method": None,
        "created_at": None,
        "failure_reason": None,
    }


def _default_wallet() -> dict[str, Any]:
    return {
        "wallet_state": "available",
        "provider": "provider_pending",
        "required_for_day_one_access": False,
        "started_at": None,
        "linked_at": None,
        "failure_reason": None,
        "next_action": "start_wallet_link",
    }


def _identity_summary(record: dict[str, Any]) -> dict[str, Any]:
    identity = record.get("identity")
    identity = dict(identity) if isinstance(identity, dict) else _default_identity(record)
    wallet = record.get("wallet")
    wallet = dict(wallet) if isinstance(wallet, dict) else _default_wallet()
    wallet_state = str(wallet.get("wallet_state") or "available").strip() or "available"
    identity["high_trust_ready"] = wallet_state == "linked"
    wallet["required_for_day_one_access"] = False
    return {
        "account_id": record.get("account_id"),
        "principal_did": record.get("principal_did"),
        "identity": identity,
        "wallet": wallet,
        "checklist_item": {
            "item_id": "wallet_linking",
            "label": "Link wallet identity",
            "state": "complete" if wallet_state == "linked" else "available",
            "actionable": wallet_state != "linked",
            "blocking_day_one_access": False,
        },
        "access_policy": {
            "workspace_access_requires_wallet": False,
            "ambiguous_identity_upgrades_trust": False,
        },
    }


def get_current_identity(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    _signup_id, record = _signup_for_principal(db, principal_did)
    return {"status": "ok", "identity_status": _identity_summary(record)}


def start_wallet_link(
    request: Request,
    db: Any,
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    signups = _load_pilot_signups(db)
    signup_id, record = _signup_for_principal(db, principal_did)
    now = _now_iso()
    wallet = record.get("wallet")
    wallet = dict(wallet) if isinstance(wallet, dict) else _default_wallet()
    if str(wallet.get("wallet_state") or "").strip() == "linked":
        return {"status": "ok", "identity_status": _identity_summary(record), "idempotent_replay": True}
    wallet.update(
        {
            "wallet_state": "in_progress",
            "provider": str(provider or wallet.get("provider") or "provider_pending").strip() or "provider_pending",
            "required_for_day_one_access": False,
            "started_at": wallet.get("started_at") or now,
            "updated_at": now,
            "failure_reason": None,
            "next_action": "complete_wallet_link",
        }
    )
    updated = dict(record)
    updated["identity"] = dict(record.get("identity")) if isinstance(record.get("identity"), dict) else _default_identity(record)
    updated["wallet"] = wallet
    updated["updated_at"] = now
    signups[signup_id] = updated
    canonical = _persist_pilot_signups(db, signups)
    return {
        "status": "ok",
        "identity_status": _identity_summary(canonical.get(signup_id) or updated),
        "idempotent_replay": False,
    }


def mark_wallet_linked_for_tests(
    request: Request,
    db: Any,
    *,
    provider: str | None = None,
    wallet_did: str | None = None,
) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    signups = _load_pilot_signups(db)
    signup_id, record = _signup_for_principal(db, principal_did)
    now = _now_iso()
    wallet = record.get("wallet")
    wallet = dict(wallet) if isinstance(wallet, dict) else _default_wallet()
    wallet.update(
        {
            "wallet_state": "linked",
            "provider": str(provider or wallet.get("provider") or "provider_pending").strip() or "provider_pending",
            "wallet_did": str(wallet_did or "").strip() or None,
            "required_for_day_one_access": False,
            "linked_at": now,
            "updated_at": now,
            "failure_reason": None,
            "next_action": None,
        }
    )
    updated = dict(record)
    updated["identity"] = dict(record.get("identity")) if isinstance(record.get("identity"), dict) else _default_identity(record)
    updated["wallet"] = wallet
    updated["updated_at"] = now
    signups[signup_id] = updated
    canonical = _persist_pilot_signups(db, signups)
    return {"status": "ok", "identity_status": _identity_summary(canonical.get(signup_id) or updated)}


def defer_wallet_link(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    signups = _load_pilot_signups(db)
    signup_id, record = _signup_for_principal(db, principal_did)
    now = _now_iso()
    wallet = record.get("wallet")
    wallet = dict(wallet) if isinstance(wallet, dict) else _default_wallet()
    wallet.update(
        {
            "wallet_state": "deferred",
            "required_for_day_one_access": False,
            "updated_at": now,
            "next_action": "start_wallet_link",
        }
    )
    updated = dict(record)
    updated["identity"] = dict(record.get("identity")) if isinstance(record.get("identity"), dict) else _default_identity(record)
    updated["wallet"] = wallet
    updated["updated_at"] = now
    signups[signup_id] = updated
    canonical = _persist_pilot_signups(db, signups)
    return {"status": "ok", "identity_status": _identity_summary(canonical.get(signup_id) or updated)}
