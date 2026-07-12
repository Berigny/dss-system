"""Pre-authenticated account request wizard for wallet-verified signup (DSS-147)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import _create_wallet_verified_signup, _load_pilot_signups, _pilot_next_route
from backend.api.http import get_db

router = APIRouter(prefix="/account/request", tags=["wizard"])

_ACCOUNT_REQUESTS_V1_KEY = b"__account_requests_v1__"

# --- Pydantic models ---


class WizardProfileStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    display_name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)
    organisation_label: str | None = None


class WizardDidChoiceStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    did_choice: str = Field(..., min_length=1)
    did_value: str | None = None


class WizardWalletSetupStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    wallet_provider: str = Field(..., min_length=1)


class WizardVerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    request_id: str = Field(..., min_length=6)


class WizardConfirmEmailRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    request_id: str = Field(..., min_length=6)
    code: str = Field(..., min_length=4, max_length=10)


class WizardSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    idempotency_key: str = Field(..., min_length=6)


# --- Helpers ---


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _load_account_requests(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(_ACCOUNT_REQUESTS_V1_KEY)
    if raw is None:
        return {}
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return {}
    records = payload.get("requests") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    return {str(k): dict(v) for k, v in records.items() if isinstance(v, dict)}


def _persist_account_requests(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for key in sorted(records.keys()):
        record = records.get(key)
        if isinstance(record, dict):
            canonical[key] = dict(record)
    db[_ACCOUNT_REQUESTS_V1_KEY] = json.dumps(
        {"version": 1, "requests": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _request_id() -> str:
    return f"req_{secrets.token_hex(8)}"


def _anonymous_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _get_request_or_404(db: Any, request_id: str, anonymous_token: str | None = None) -> dict[str, Any]:
    requests = _load_account_requests(db)
    record = requests.get(request_id)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail={"error": "request_not_found", "recoverable": True})
    if anonymous_token is not None:
        stored_hash = str(record.get("anonymous_token_hash") or "").strip()
        if not stored_hash or _hash_token(anonymous_token) != stored_hash:
            raise HTTPException(status_code=403, detail={"error": "invalid_anonymous_token", "recoverable": True})
    return record


def _try_send_email_code(recipient: str, code: str) -> tuple[str, bool]:
    api_key = str(os.getenv("RESEND_API_KEY") or "").strip()
    sender = str(os.getenv("RESEND_FROM_EMAIL") or "").strip()
    if not api_key or not sender:
        return "development_response", False
    html_body = f"""<p>Your DSS account setup verification code is:</p>
<h1>{code}</h1>
<p>This code expires in 15 minutes.</p>
<p>If you did not request this, you can safely ignore this email.</p>"""
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"from": sender, "to": [recipient], "subject": "Your DSS verification code", "html": html_body},
            )
            if resp.status_code >= 400:
                try:
                    err_body = resp.json()
                    err_msg = err_body.get("message") or err_body.get("name") or str(err_body)
                except Exception:
                    err_msg = resp.text or f"HTTP {resp.status_code}"
                return f"resend_error_{resp.status_code}: {err_msg}", False
            return "email_sent", True
    except Exception as exc:
        return f"resend_error: {exc}", False


# --- Routes ---


@router.post("")
def create_account_request(db=Depends(get_db)):
    """Create a new anonymous account request. Returns request_id and anonymous_token."""
    request_id = _request_id()
    token = _anonymous_token()
    now_iso = _now_iso()
    record = {
        "request_id": request_id,
        "anonymous_token_hash": _hash_token(token),
        "display_name": "",
        "email": "",
        "organisation_label": "",
        "did_choice": "",
        "did_value": "",
        "wallet_provider": "",
        "email_verified": False,
        "email_verification_code": None,
        "email_verification_expires_at": None,
        "email_verification_attempts": 0,
        "steps_completed": [],
        "status": "draft",
        "credential_offer": None,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    requests = _load_account_requests(db)
    requests[request_id] = record
    _persist_account_requests(db, requests)
    return {
        "status": "ok",
        "request_id": request_id,
        "anonymous_token": token,
    }


@router.get("/{request_id}")
def get_account_request(request_id: str, request: Request, db=Depends(get_db)):
    """Retrieve current wizard state for a request."""
    token = request.headers.get("x-anonymous-token") or ""
    record = _get_request_or_404(db, request_id, anonymous_token=token)
    # Do not return sensitive fields
    safe = {
        k: v
        for k, v in record.items()
        if k not in {"anonymous_token_hash", "email_verification_code", "email_verification_expires_at"}
    }
    safe["email_verified"] = record.get("email_verified", False)
    return {"status": "ok", "request": safe}


@router.post("/{request_id}/step/{step_id}")
def save_wizard_step(
    request_id: str,
    step_id: str,
    request: Request,
    payload: WizardProfileStep | WizardDidChoiceStep | WizardWalletSetupStep,
    db=Depends(get_db),
):
    """Save data for a wizard step."""
    token = request.headers.get("x-anonymous-token") or ""
    record = _get_request_or_404(db, request_id, anonymous_token=token)

    if record.get("status") != "draft":
        raise HTTPException(
            status_code=409,
            detail={"error": "request_already_submitted", "recoverable": False},
        )

    updated = dict(record)
    step = step_id.strip().lower()
    steps_completed = list(updated.get("steps_completed") or [])

    if step == "profile":
        if isinstance(payload, WizardProfileStep):
            updated["display_name"] = payload.display_name.strip()
            updated["email"] = str(payload.email or "").strip().lower()
            updated["organisation_label"] = str(payload.organisation_label or "").strip()
            if "@" not in updated["email"]:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "email_invalid", "recoverable": True, "field": "email"},
                )
    elif step == "did_choice":
        if isinstance(payload, WizardDidChoiceStep):
            updated["did_choice"] = payload.did_choice.strip()
            updated["did_value"] = str(payload.did_value or "").strip()
    elif step == "wallet_setup":
        if isinstance(payload, WizardWalletSetupStep):
            updated["wallet_provider"] = payload.wallet_provider.strip()
    else:
        raise HTTPException(status_code=400, detail={"error": "unknown_step", "recoverable": True})

    if step not in steps_completed:
        steps_completed.append(step)
        updated["steps_completed"] = steps_completed

    updated["updated_at"] = _now_iso()
    requests = _load_account_requests(db)
    requests[request_id] = updated
    _persist_account_requests(db, requests)
    return {"status": "ok", "step": step, "steps_completed": steps_completed}


@router.post("/verify-email")
def send_verification_email(payload: WizardVerifyEmailRequest, db=Depends(get_db)):
    """Send (generate) an email verification code for a request."""
    record = _get_request_or_404(db, payload.request_id)
    if record.get("status") != "draft":
        raise HTTPException(
            status_code=409,
            detail={"error": "request_already_submitted", "recoverable": False},
        )

    email = str(record.get("email") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(
            status_code=422,
            detail={"error": "email_required", "recoverable": True},
        )

    code = _generate_email_code()
    expires_at = _now_epoch() + 900  # 15 minutes
    updated = dict(record)
    updated["email_verification_code"] = code
    updated["email_verification_expires_at"] = expires_at
    updated["email_verification_attempts"] = 0
    updated["updated_at"] = _now_iso()

    requests = _load_account_requests(db)
    requests[payload.request_id] = updated
    _persist_account_requests(db, requests)

    delivery, sent_ok = _try_send_email_code(email, code)
    response: dict[str, Any] = {
        "status": "ok",
        "delivery": delivery,
        "expires_at": expires_at,
    }
    if not sent_ok:
        response["verification_code"] = code
    return response


@router.post("/verify-email/confirm")
def confirm_verification_email(payload: WizardConfirmEmailRequest, db=Depends(get_db)):
    """Confirm an email verification code."""
    record = _get_request_or_404(db, payload.request_id)
    if record.get("status") != "draft":
        raise HTTPException(
            status_code=409,
            detail={"error": "request_already_submitted", "recoverable": False},
        )

    expected = str(record.get("email_verification_code") or "").strip()
    expires_at = record.get("email_verification_expires_at")
    attempts = int(record.get("email_verification_attempts") or 0)

    if not expected:
        raise HTTPException(
            status_code=400,
            detail={"error": "verification_not_started", "recoverable": True},
        )

    if isinstance(expires_at, (int, float)) and _now_epoch() > expires_at:
        raise HTTPException(
            status_code=401,
            detail={"error": "verification_code_expired", "recoverable": True},
        )

    if attempts >= 3:
        raise HTTPException(
            status_code=401,
            detail={"error": "verification_max_attempts_exceeded", "recoverable": True},
        )

    attempts += 1
    updated = dict(record)
    updated["email_verification_attempts"] = attempts

    if payload.code.strip() != expected:
        updated["updated_at"] = _now_iso()
        requests = _load_account_requests(db)
        requests[payload.request_id] = updated
        _persist_account_requests(db, requests)
        raise HTTPException(
            status_code=401,
            detail={
                "error": "verification_code_invalid",
                "recoverable": True,
                "attempts_remaining": max(0, 3 - attempts),
            },
        )

    updated["email_verified"] = True
    updated["email_verification_code"] = None
    updated["email_verification_expires_at"] = None
    updated["updated_at"] = _now_iso()
    steps_completed = list(updated.get("steps_completed") or [])
    if "email_verification" not in steps_completed:
        steps_completed.append("email_verification")
        updated["steps_completed"] = steps_completed

    requests = _load_account_requests(db)
    requests[payload.request_id] = updated
    _persist_account_requests(db, requests)
    return {"status": "ok", "email_verified": True}


@router.post("/{request_id}/submit")
def submit_account_request(
    request_id: str,
    request: Request,
    payload: WizardSubmitRequest,
    db=Depends(get_db),
):
    """Submit the account request, creating a wallet-verified signup."""
    token = request.headers.get("x-anonymous-token") or ""
    record = _get_request_or_404(db, request_id, anonymous_token=token)

    if record.get("status") != "draft":
        # Idempotency: if already submitted with same idempotency key, return existing signup
        existing_signup_id = record.get("signup_id")
        if existing_signup_id:
            signups = _load_pilot_signups(db)
            signup = signups.get(existing_signup_id)
            if isinstance(signup, dict) and str(signup.get("idempotency_key") or "").strip() == payload.idempotency_key.strip():
                next_route = _pilot_next_route(signup)
                return {
                    "status": "ok",
                    "signup": {
                        "signup_id": signup["signup_id"],
                        "account_id": signup["account_id"],
                        "principal_did": signup["principal_did"],
                        "primary_contact": signup.get("primary_contact", ""),
                        "verification_status": signup["verification_status"],
                        "onboarding_status": signup.get("onboarding_status", "not_started"),
                        "approval_status": signup.get("approval_status", "pending"),
                        "next_route": next_route,
                    },
                    "next_route": next_route,
                }
        raise HTTPException(
            status_code=409,
            detail={"error": "request_already_submitted", "recoverable": False},
        )

    # Validate required fields
    display_name = str(record.get("display_name") or "").strip()
    email = str(record.get("email") or "").strip()
    wallet_provider = str(record.get("wallet_provider") or "").strip()
    did_choice = str(record.get("did_choice") or "").strip()
    did_value = str(record.get("did_value") or "").strip()

    if not display_name:
        raise HTTPException(
            status_code=422,
            detail={"error": "display_name_required", "recoverable": True},
        )
    if not email or "@" not in email:
        raise HTTPException(
            status_code=422,
            detail={"error": "email_required", "recoverable": True},
        )
    if not record.get("email_verified"):
        raise HTTPException(
            status_code=422,
            detail={"error": "email_not_verified", "recoverable": True},
        )
    if not wallet_provider:
        raise HTTPException(
            status_code=422,
            detail={"error": "wallet_provider_required", "recoverable": True},
        )
    if not did_choice:
        raise HTTPException(
            status_code=422,
            detail={"error": "did_choice_required", "recoverable": True},
        )

    # Build DID and wallet DID for signup
    if did_choice == "issuer_assigned" or not did_value:
        principal_did = f"did:web:{os.getenv('DEFAULT_DID_HOST', '')}:wallet:{secrets.token_hex(8)}"
    else:
        principal_did = did_value

    wallet_did = principal_did  # For issuer-assigned, use principal DID as wallet DID

    # Create the wallet-verified signup using shared logic
    result = _create_wallet_verified_signup(
        db,
        principal_did=principal_did,
        wallet_did=wallet_did,
        email=email,
        display_name=display_name,
        wallet_provider=wallet_provider,
        idempotency_key=payload.idempotency_key.strip(),
    )

    # Mark request as submitted
    updated = dict(record)
    updated["status"] = "submitted"
    updated["signup_id"] = result["signup"]["signup_id"]
    updated["principal_did"] = principal_did
    updated["wallet_did"] = wallet_did
    updated["updated_at"] = _now_iso()
    requests = _load_account_requests(db)
    requests[request_id] = updated
    _persist_account_requests(db, requests)

    next_route = result["signup"].get("next_route", "awaiting_operator_approval")
    return {
        "status": "ok",
        "signup": result["signup"],
        "trust_step": result.get("trust_step"),
        "next_route": next_route,
    }
