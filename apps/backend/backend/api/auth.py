"""Passkey/WebAuthn challenge and verify endpoints."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.api.http import get_db
from backend.services.session_tokens import (
    apply_refresh_token_claims_or_raise,
    apply_session_token_claims_or_raise,
    mint_session_token,
    mint_surface_session_bundle,
    refresh_surface_session_bundle,
)


router = APIRouter(prefix="/auth", tags=["auth"])

_AUTH_CHALLENGES_V1_KEY = b"__auth_challenges_v1__"
_PASSKEY_BINDINGS_V1_KEY = b"__passkey_bindings_v1__"
_PRINCIPAL_REGISTRY_V1_KEY = b"__principals_v1__"
_PILOT_SIGNUPS_V1_KEY = b"__pilot_signups_v1__"
_DEFAULT_ALLOWED_ORIGINS = os.getenv("AUTH_ALLOWED_ORIGINS", "")
AUTO_APPROVE_FIRST_SIGNUP = str(
    os.getenv("AUTO_APPROVE_FIRST_SIGNUP", "true")
).strip().lower() in {"1", "true", "yes"}


class AuthChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    principal_did: str = Field(..., min_length=5)
    origin: str | None = None
    rp_id: str | None = None
    challenge_ttl_seconds: int | None = Field(default=None, ge=30, le=900)


class AuthRegisterChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    principal_did: str = Field(..., min_length=5)
    origin: str | None = None
    rp_id: str | None = None
    rp_name: str | None = None
    user_name: str | None = None
    user_display_name: str | None = None
    challenge_ttl_seconds: int | None = Field(default=None, ge=30, le=900)


class AuthRegisterVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    challenge_id: str = Field(..., min_length=6)
    credential_id: str = Field(..., min_length=8)
    client_data_json_b64u: str = Field(..., min_length=8)
    authenticator_data_b64u: str = Field(..., min_length=8)
    public_key_spki_b64u: str = Field(..., min_length=8)
    principal_key_id: str | None = None


class AuthVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    challenge_id: str = Field(..., min_length=6)
    credential_id: str = Field(..., min_length=8)
    client_data_json_b64u: str = Field(..., min_length=8)
    authenticator_data_b64u: str = Field(..., min_length=8)
    signature_b64u: str = Field(..., min_length=8)
    principal_did: str | None = None
    principal_key_id: str | None = None
    public_key_pem: str | None = None
    issue_session_token: bool = True
    session_ttl_seconds: int | None = Field(default=None, ge=60, le=86_400)


class AuthTokenIssueRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    principal_did: str = Field(..., min_length=5)
    principal_key_id: str | None = None
    credential_id: str | None = None
    auth_method: str = "passkey"
    roles: list[str] = Field(default_factory=list)
    allowed_context_ids: list[str] = Field(default_factory=list)
    ledger_ids: list[str] = Field(default_factory=list)
    ttl_seconds: int | None = Field(default=None, ge=60, le=86_400)


class PilotSignupRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_contact: str = Field(..., min_length=3)
    owner_display_name: str = Field(..., min_length=1)
    pilot_terms_acknowledgement: bool
    idempotency_key: str = Field(..., min_length=6)


class PilotSignupVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    signup_id: str = Field(..., min_length=6)
    verification_token: str = Field(..., min_length=8)


class PilotWalletVerifiedSignupRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    principal_did: str = Field(..., min_length=5)
    display_name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)
    wallet_did: str = Field(..., min_length=5)
    wallet_provider: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=6)


class AuthSigninRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_contact: str = Field(..., min_length=3)
    ttl_seconds: int | None = Field(default=None, ge=60, le=86_400)


class CredentialRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str | None = None


class SessionRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jti: str = Field(..., min_length=4)
    reason: str | None = None


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64u_decode(value: str) -> bytes:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("missing base64url payload")
    padding = "=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_json_map(db: Any, key: bytes, field: str) -> dict[str, dict[str, Any]]:
    raw = db.get(key)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, record in records.items():
        if isinstance(record, dict):
            out[str(name)] = dict(record)
    return out


def _persist_json_map(db: Any, key: bytes, field: str, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for item_key in sorted(records.keys()):
        record = records.get(item_key)
        if isinstance(record, dict):
            canonical[item_key] = dict(record)
    db[key] = json.dumps(
        {"version": 1, field: canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_challenges(db: Any) -> dict[str, dict[str, Any]]:
    return _load_json_map(db, _AUTH_CHALLENGES_V1_KEY, "challenges")


def _persist_challenges(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _persist_json_map(db, _AUTH_CHALLENGES_V1_KEY, "challenges", records)


def _load_bindings(db: Any) -> dict[str, dict[str, Any]]:
    return _load_json_map(db, _PASSKEY_BINDINGS_V1_KEY, "bindings")


def _persist_bindings(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _persist_json_map(db, _PASSKEY_BINDINGS_V1_KEY, "bindings", records)


def _load_session_revocations(db: Any) -> set[str]:
    raw = db.get(b"__session_revocations_v1__")
    payload = _decode_json(raw)
    items = payload.get("revoked_jtis") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return set()
    return {str(item).strip() for item in items if str(item).strip()}


def _persist_session_revocations(db: Any, revoked_jtis: set[str]) -> set[str]:
    canonical = sorted({item.strip() for item in revoked_jtis if item and item.strip()})
    db[b"__session_revocations_v1__"] = json.dumps(
        {"version": 1, "revoked_jtis": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return set(canonical)


def _load_principals(db: Any) -> dict[str, dict[str, Any]]:
    return _load_json_map(db, _PRINCIPAL_REGISTRY_V1_KEY, "principals")


def _persist_principals(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _persist_json_map(db, _PRINCIPAL_REGISTRY_V1_KEY, "principals", records)


def _load_pilot_signups(db: Any) -> dict[str, dict[str, Any]]:
    return _load_json_map(db, _PILOT_SIGNUPS_V1_KEY, "signups")


def _persist_pilot_signups(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _persist_json_map(db, _PILOT_SIGNUPS_V1_KEY, "signups", records)


def _normalize_primary_contact(value: str) -> str:
    return str(value or "").strip().lower()


def _pilot_signup_id(primary_contact: str) -> str:
    digest = hashlib.sha256(primary_contact.encode("utf-8")).hexdigest()[:16]
    return f"pilot_signup:{digest}"


def _pilot_account_id(primary_contact: str) -> str:
    digest = hashlib.sha256(primary_contact.encode("utf-8")).hexdigest()[:16]
    return f"acct_pilot_{digest}"


def _pilot_principal_did(primary_contact: str) -> str:
    digest = hashlib.sha256(primary_contact.encode("utf-8")).hexdigest()[:24]
    return f"did:dss:pilot:{digest}"


def _pilot_next_route(record: dict[str, Any]) -> str:
    verification_status = str(record.get("verification_status") or "").strip().lower()
    approval_status = str(record.get("approval_status") or "").strip().lower()
    onboarding_status = str(record.get("onboarding_status") or "not_started").strip().lower()
    provisioning_status = str(record.get("provisioning_status") or "not_started").strip().lower()
    trial_state = str(record.get("trial_state") or "").strip().lower()
    if verification_status != "verified":
        return "verification_or_recovery"
    # Wallet-verified signups must be operator-approved before onboarding.
    # Records without an approval_status field are treated as legacy/email signups.
    if approval_status and approval_status != "approved":
        return "awaiting_operator_approval"
    if onboarding_status not in {"submitted", "accepted", "complete", "completed"}:
        return "onboarding"
    if provisioning_status not in {"succeeded", "complete", "completed"}:
        return "provisioning_status"
    if trial_state == "paused":
        return "account_landing_read_only"
    return "account_workspace_landing"


def _pilot_signup_for_contact(db: Any, primary_contact: str) -> dict[str, Any] | None:
    normalized = _normalize_primary_contact(primary_contact)
    for record in _load_pilot_signups(db).values():
        if not isinstance(record, dict):
            continue
        if str(record.get("primary_contact") or "").strip() == normalized:
            return dict(record)
    return None


def _pilot_signup_for_principal(db: Any, principal_did: str) -> dict[str, Any] | None:
    did = str(principal_did or "").strip()
    for record in _load_pilot_signups(db).values():
        if not isinstance(record, dict):
            continue
        if str(record.get("principal_did") or "").strip() == did:
            return dict(record)
    return None


def _pilot_signup_for_wallet_did(db: Any, wallet_did: str) -> dict[str, Any] | None:
    did = str(wallet_did or "").strip()
    for record in _load_pilot_signups(db).values():
        if not isinstance(record, dict):
            continue
        wallet = record.get("wallet")
        if isinstance(wallet, dict) and str(wallet.get("did") or "").strip() == did:
            return dict(record)
    return None


def _pilot_signup_id_from_wallet(wallet_did: str) -> str:
    digest = hashlib.sha256(wallet_did.encode("utf-8")).hexdigest()[:16]
    return f"pilot_signup:{digest}"


def _pilot_account_id_from_wallet(wallet_did: str) -> str:
    digest = hashlib.sha256(wallet_did.encode("utf-8")).hexdigest()[:16]
    return f"acct_pilot_{digest}"


def _pilot_session_state(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {
            "account_id": None,
            "primary_contact": None,
            "next_route": "verification_or_recovery",
            "read_only": False,
            "trial_state": "unknown",
            "onboarding_status": "unknown",
            "provisioning_status": "unknown",
        }
    next_route = _pilot_next_route(record)
    trial_state = str(record.get("trial_state") or "active").strip().lower() or "active"
    return {
        "account_id": record.get("account_id"),
        "primary_contact": record.get("primary_contact"),
        "next_route": next_route,
        "read_only": trial_state == "paused",
        "trial_state": trial_state,
        "onboarding_status": record.get("onboarding_status", "not_started"),
        "provisioning_status": record.get("provisioning_status", "not_started"),
    }


def _access_decision_for_target(
    *,
    authenticated: bool,
    session_state: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    normalized_target = str(target or "account").strip().lower() or "account"
    if not authenticated:
        return {
            "allowed": False,
            "reason": "authentication_required",
            "target": normalized_target,
        }
    next_route = str(session_state.get("next_route") or "").strip()
    read_only = bool(session_state.get("read_only"))
    if read_only and normalized_target in {
        "account",
        "account_viewing",
        "subscription",
        "setup_checklist",
        "signout",
    }:
        return {"allowed": True, "reason": "paused_read_access", "target": normalized_target}
    if read_only:
        return {"allowed": False, "reason": "paused_read_only", "target": normalized_target}
    if normalized_target in {"account", "account_viewing", "signout"}:
        return {"allowed": True, "reason": "authenticated", "target": normalized_target}
    if normalized_target in {"onboarding", "onboarding_viewing"}:
        return {
            "allowed": next_route == "onboarding",
            "reason": "onboarding_required" if next_route == "onboarding" else "not_onboarding_route",
            "target": normalized_target,
        }
    if normalized_target in {"provisioning", "provisioning_status"}:
        return {
            "allowed": next_route in {"provisioning_status", "account_workspace_landing"},
            "reason": "provisioning_visible" if next_route == "provisioning_status" else "not_provisioning_route",
            "target": normalized_target,
        }
    if normalized_target in {"workspace", "workspace_runtime", "user_write_surfaces"}:
        return {
            "allowed": next_route == "account_workspace_landing",
            "reason": "workspace_ready" if next_route == "account_workspace_landing" else "workspace_not_ready",
            "target": normalized_target,
        }
    return {
        "allowed": False,
        "reason": "unknown_target",
        "target": normalized_target,
    }


def _upsert_pending_pilot_principal(
    db: Any,
    *,
    principal_did: str,
    display_name: str,
    primary_contact: str,
    account_id: str,
) -> dict[str, Any]:
    principals = _load_principals(db)
    existing = principals.get(principal_did)
    now_iso = _now_iso()
    if isinstance(existing, dict):
        record = dict(existing)
        if str(record.get("status") or "").strip().lower() == "active":
            return record
    else:
        record = {
            "principal_did": principal_did,
            "created_at": now_iso,
            "key_references": [],
            "disabled_at": None,
            "disable_reason": None,
        }
    record.update(
        {
            "display_name": display_name,
            "status": "pending_verification",
            "updated_at": now_iso,
            "metadata": {
                **(record.get("metadata") if isinstance(record.get("metadata"), dict) else {}),
                "bootstrap_source": "pilot_signup",
                "primary_contact": primary_contact,
                "account_id": account_id,
            },
            "provisioning_source": "pilot_signup_v1",
        }
    )
    # Store wallet_provider on principal if already known from signup
    signups = _load_pilot_signups(db)
    for signup_record in signups.values():
        if str(signup_record.get("principal_did") or "").strip() == principal_did:
            wallet = signup_record.get("wallet")
            if isinstance(wallet, dict):
                wallet_provider = str(wallet.get("provider") or "").strip()
                if wallet_provider and wallet_provider != "provider_pending":
                    record["metadata"]["wallet_provider"] = wallet_provider
            break
    principals[principal_did] = record
    persisted = _persist_principals(db, principals)
    return persisted.get(principal_did) or record


def _upsert_pending_approval_pilot_principal(
    db: Any,
    *,
    principal_did: str,
    display_name: str,
    primary_contact: str,
    account_id: str,
    wallet_provider: str | None = None,
) -> dict[str, Any]:
    principals = _load_principals(db)
    existing = principals.get(principal_did)
    now_iso = _now_iso()
    if isinstance(existing, dict):
        record = dict(existing)
        if str(record.get("status") or "").strip().lower() == "active":
            return record
    else:
        record = {
            "principal_did": principal_did,
            "created_at": now_iso,
            "key_references": [],
            "disabled_at": None,
            "disable_reason": None,
        }
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    record.update(
        {
            "display_name": display_name,
            "status": "pending_approval",
            "updated_at": now_iso,
            "metadata": {
                **metadata,
                "bootstrap_source": "wallet_verified_signup",
                "primary_contact": primary_contact,
                "account_id": account_id,
                "wallet_provider": wallet_provider,
            },
            "provisioning_source": "wallet_verified_signup_v1",
        }
    )
    principals[principal_did] = record
    persisted = _persist_principals(db, principals)
    return persisted.get(principal_did) or record


def _activate_pilot_principal(db: Any, *, principal_did: str) -> dict[str, Any]:
    principals = _load_principals(db)
    record = principals.get(principal_did)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail={"error": "principal_not_found"})
    updated = dict(record)
    updated["status"] = "active"
    updated["updated_at"] = _now_iso()
    principals[principal_did] = updated
    persisted = _persist_principals(db, principals)
    return persisted.get(principal_did) or updated


def _active_principal_or_403(db: Any, principal_did: str) -> dict[str, Any]:
    principals = _load_principals(db)
    record = principals.get(principal_did)
    if not isinstance(record, dict):
        raise HTTPException(status_code=403, detail={"error": "principal_not_registered", "principal_did": principal_did})
    if str(record.get("status") or "unknown").strip().lower() != "active":
        raise HTTPException(status_code=403, detail={"error": "principal_not_active", "principal_did": principal_did})
    return record


def _ensure_active_principal(db: Any, principal_did: str) -> dict[str, Any]:
    principals = _load_principals(db)
    record = principals.get(principal_did)
    if isinstance(record, dict):
        status = str(record.get("status") or "").strip().lower()
        if status == "active":
            return record
        # Preserve terminal/non-active statuses (pending_approval, rejected, disabled, pending_verification)
        # so they cannot be bypassed by challenge/registration endpoints.
        if status in {"pending_approval", "rejected", "disabled", "pending_verification"}:
            return record
    now_iso = _now_iso()
    if not isinstance(record, dict):
        record = {
            "principal_did": principal_did,
            "display_name": principal_did,
            "status": "active",
            "key_references": [],
            "created_at": now_iso,
            "updated_at": now_iso,
            "disabled_at": None,
            "disable_reason": None,
            "metadata": {"bootstrap_source": "passkey_register"},
            "provisioning_source": "auth_register_v1",
        }
    else:
        record = dict(record)
        record["status"] = "active"
        record["updated_at"] = now_iso
        record["disabled_at"] = None
        record["disable_reason"] = None
    principals[principal_did] = record
    persisted = _persist_principals(db, principals)
    return persisted.get(principal_did) or record


def _is_first_unapproved_signup(db: Any) -> bool:
    signups = _load_pilot_signups(db)
    for record in signups.values():
        if isinstance(record, dict) and str(record.get("approval_status") or "").strip().lower() == "approved":
            return False
    principals = _load_principals(db)
    for record in principals.values():
        if isinstance(record, dict) and str(record.get("status") or "").strip().lower() == "active":
            return False
    return True


def _auto_approve_signup_record(
    db: Any,
    signup_record: dict[str, Any],
    wallet_provider: str,
) -> dict[str, Any]:
    now_iso = _now_iso()
    updated = dict(signup_record)
    updated["approval_status"] = "approved"
    updated["onboarding_status"] = "not_started"
    updated["updated_at"] = now_iso

    principal_did = str(updated.get("principal_did") or "").strip()
    if principal_did:
        _activate_pilot_principal(db, principal_did=principal_did)
        principals = _load_principals(db)
        principal = principals.get(principal_did)
        if isinstance(principal, dict):
            principal = dict(principal)
            metadata = principal.get("metadata") if isinstance(principal.get("metadata"), dict) else {}
            metadata["operator_approved_at"] = now_iso
            principal["metadata"] = metadata
            principal["updated_at"] = now_iso
            principals[principal_did] = principal
            _persist_principals(db, principals)

    if wallet_provider:
        try:
            from backend.api.wallet import _build_credential_offer
            credential_offer = _build_credential_offer(
                str(updated.get("signup_id") or ""), wallet_provider
            )
            updated["credential_offer"] = credential_offer
        except Exception:
            updated["credential_offer"] = None
    return updated


def _allowed_origins() -> set[str]:
    raw = os.getenv("AUTH_WEBAUTHN_ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS)
    return {origin.strip() for origin in raw.split(",") if origin.strip()}


def _rp_id_for_request(request: Request, payload: AuthChallengeRequest) -> str:
    configured = os.getenv("AUTH_WEBAUTHN_RP_ID", "").strip()
    if configured:
        return configured
    if payload.rp_id and payload.rp_id.strip():
        rp_id = payload.rp_id.strip()
    else:
        host = (request.url.hostname or "").strip()
        rp_id = host or os.getenv("AUTH_WEBAUTHN_FALLBACK_RP_ID", "")
    lowered = rp_id.lower()
    base_domain = os.getenv("BASE_DOMAIN", "").strip().lower()
    if base_domain and (lowered == base_domain or lowered.endswith("." + base_domain)):
        return base_domain
    return rp_id


def _origin_for_challenge(request: Request, payload: AuthChallengeRequest) -> str:
    if payload.origin and payload.origin.strip():
        return payload.origin.strip()
    header_origin = (request.headers.get("origin") or "").strip()
    if header_origin:
        return header_origin
    return os.getenv("AUTH_FALLBACK_ORIGIN", "")


def _verify_signature(public_key_pem: str, signed_payload: bytes, signature: bytes) -> None:
    public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise HTTPException(status_code=400, detail={"error": "unsupported_public_key_type"})
    try:
        public_key.verify(signature, signed_payload, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise HTTPException(status_code=401, detail={"error": "signature_invalid"}) from exc


def _require_revocation_token(request: Request) -> None:
    expected = os.getenv("AUTH_REVOCATION_TOKEN", "").strip() or os.getenv("ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail={"error": "revocation_token_not_configured"})
    provided = (request.headers.get("x-admin-token") or "").strip()
    authz = (request.headers.get("authorization") or "").strip()
    if authz.lower().startswith("bearer "):
        provided = authz.split(" ", 1)[1].strip()
    if provided != expected:
        raise HTTPException(status_code=403, detail={"error": "invalid_revocation_token"})


def _spki_b64u_to_pem(value: str) -> str:
    try:
        raw = _b64u_decode(value)
        key = serialization.load_der_public_key(raw)
        return key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "public_key_spki_invalid"}) from exc


def _principal_user_id_b64u(principal_did: str) -> str:
    digest = hashlib.sha256(principal_did.encode("utf-8")).digest()[:16]
    return _b64u_encode(digest)


def _credential_descriptors_for_principal(
    bindings: dict[str, dict[str, Any]],
    principal_did: str,
) -> list[dict[str, str]]:
    did = str(principal_did or "").strip()
    if not did:
        return []
    descriptors: list[dict[str, str]] = []
    for credential_id, record in bindings.items():
        if not isinstance(record, dict):
            continue
        if str(record.get("principal_did") or "").strip() != did:
            continue
        if str(record.get("status") or "active").strip().lower() != "active":
            continue
        cid = str(credential_id or "").strip()
        if not cid:
            continue
        descriptors.append({"type": "public-key", "id": cid})
    return descriptors


@router.post("/pilot/signup")
def pilot_signup(payload: PilotSignupRequest, db=Depends(get_db)):
    primary_contact = _normalize_primary_contact(payload.primary_contact)
    if "@" not in primary_contact:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "primary_contact_invalid",
                "recoverable": True,
                "field": "primary_contact",
            },
        )
    if not payload.pilot_terms_acknowledgement:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "pilot_terms_acknowledgement_required",
                "recoverable": True,
                "field": "pilot_terms_acknowledgement",
            },
        )

    signups = _load_pilot_signups(db)
    existing_for_contact = next(
        (
            record
            for record in signups.values()
            if isinstance(record, dict)
            and str(record.get("primary_contact") or "").strip() == primary_contact
        ),
        None,
    )
    if isinstance(existing_for_contact, dict):
        same_key = str(existing_for_contact.get("idempotency_key") or "").strip() == payload.idempotency_key.strip()
        response_status = "ok" if same_key else "duplicate"
        return {
            "status": response_status,
            "recoverable": not same_key,
            "duplicate": not same_key,
            "signup": {
                "signup_id": existing_for_contact["signup_id"],
                "account_id": existing_for_contact["account_id"],
                "principal_did": existing_for_contact["principal_did"],
                "primary_contact": existing_for_contact["primary_contact"],
                "verification_status": existing_for_contact["verification_status"],
                "onboarding_status": existing_for_contact.get("onboarding_status", "not_started"),
                "next_route": _pilot_next_route(existing_for_contact),
            },
            "recovery": {
                "action": "sign_in_or_verify_existing_signup",
                "message": "An existing pilot signup already owns this contact.",
            },
        }

    signup_id = _pilot_signup_id(primary_contact)
    account_id = _pilot_account_id(primary_contact)
    principal_did = _pilot_principal_did(primary_contact)
    verification_token = _b64u_encode(secrets.token_bytes(18))
    now_iso = _now_iso()
    record = {
        "signup_id": signup_id,
        "account_id": account_id,
        "principal_did": principal_did,
        "primary_contact": primary_contact,
        "owner_display_name": payload.owner_display_name.strip(),
        "idempotency_key": payload.idempotency_key.strip(),
        "pilot_terms_acknowledgement": True,
        "verification_status": "pending",
        "verification_token": verification_token,
        "onboarding_status": "not_started",
        "provisioning_status": "not_started",
        "trial_state": "active",
        "created_at": now_iso,
        "updated_at": now_iso,
        "verified_at": None,
    }
    signups[signup_id] = record
    canonical = _persist_pilot_signups(db, signups)
    persisted = canonical.get(signup_id) or record
    _upsert_pending_pilot_principal(
        db,
        principal_did=principal_did,
        display_name=payload.owner_display_name.strip(),
        primary_contact=primary_contact,
        account_id=account_id,
    )
    return {
        "status": "ok",
        "recoverable": False,
        "duplicate": False,
        "signup": {
            "signup_id": persisted["signup_id"],
            "account_id": persisted["account_id"],
            "principal_did": persisted["principal_did"],
            "primary_contact": persisted["primary_contact"],
            "verification_status": persisted["verification_status"],
            "onboarding_status": persisted["onboarding_status"],
            "next_route": _pilot_next_route(persisted),
        },
        "trust_step": {
            "type": "email_verification_or_equivalent",
            "status": "pending",
            "delivery": "development_response",
            "verification_token": verification_token,
        },
    }


@router.post("/pilot/signup/verify")
def pilot_signup_verify(payload: PilotSignupVerifyRequest, db=Depends(get_db)):
    signup_id = payload.signup_id.strip()
    signups = _load_pilot_signups(db)
    record = signups.get(signup_id)
    if not isinstance(record, dict):
        raise HTTPException(
            status_code=404,
            detail={"error": "pilot_signup_not_found", "recoverable": True},
        )
    if str(record.get("verification_status") or "").strip().lower() == "verified":
        return {
            "status": "ok",
            "signup": {
                "signup_id": record["signup_id"],
                "account_id": record["account_id"],
                "principal_did": record["principal_did"],
                "verification_status": "verified",
                "onboarding_status": record.get("onboarding_status", "not_started"),
                "approval_status": record.get("approval_status", "pending"),
                "next_route": _pilot_next_route(record),
            },
        }
    expected = str(record.get("verification_token") or "").strip()
    if not expected or payload.verification_token.strip() != expected:
        raise HTTPException(
            status_code=401,
            detail={"error": "verification_token_invalid", "recoverable": True},
        )

    should_auto_approve = AUTO_APPROVE_FIRST_SIGNUP and _is_first_unapproved_signup(db)
    updated = dict(record)
    updated["verification_status"] = "verified"
    updated["verified_at"] = _now_iso()
    updated["updated_at"] = updated["verified_at"]
    signups[signup_id] = updated
    canonical = _persist_pilot_signups(db, signups)
    persisted = canonical.get(signup_id) or updated
    principal = _activate_pilot_principal(db, principal_did=str(persisted["principal_did"]))
    if should_auto_approve:
        persisted = _auto_approve_signup_record(db, persisted, "")
        signups = _load_pilot_signups(db)
        signups[signup_id] = persisted
        _persist_pilot_signups(db, signups)
    return {
        "status": "ok",
        "signup": {
            "signup_id": persisted["signup_id"],
            "account_id": persisted["account_id"],
            "principal_did": persisted["principal_did"],
            "verification_status": persisted["verification_status"],
            "onboarding_status": persisted.get("onboarding_status", "not_started"),
            "approval_status": persisted.get("approval_status", "pending"),
            "next_route": _pilot_next_route(persisted),
        },
        "principal": {
            "principal_did": principal.get("principal_did"),
            "status": principal.get("status"),
        },
    }


def _create_wallet_verified_signup(
    db: Any,
    *,
    principal_did: str,
    wallet_did: str,
    email: str,
    display_name: str,
    wallet_provider: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Core logic for creating a wallet-verified signup record and principal.

    Returns the signup response dict. Raises HTTPException on validation failure.
    """
    principal_did = str(principal_did or "").strip()
    wallet_did = str(wallet_did or "").strip()
    email = str(email or "").strip().lower()
    display_name = display_name.strip()
    wallet_provider = wallet_provider.strip()
    idempotency_key = idempotency_key.strip()

    if "@" not in email:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "email_invalid",
                "recoverable": True,
                "field": "email",
            },
        )

    signups = _load_pilot_signups(db)

    # Idempotency / duplicate checks by wallet_did or principal_did
    existing_for_wallet = _pilot_signup_for_wallet_did(db, wallet_did)
    existing_for_principal = _pilot_signup_for_principal(db, principal_did)
    existing = existing_for_wallet or existing_for_principal

    if isinstance(existing, dict):
        same_key = str(existing.get("idempotency_key") or "").strip() == idempotency_key
        response_status = "ok" if same_key else "duplicate"
        return {
            "status": response_status,
            "recoverable": not same_key,
            "duplicate": not same_key,
            "signup": {
                "signup_id": existing["signup_id"],
                "account_id": existing["account_id"],
                "principal_did": existing["principal_did"],
                "primary_contact": existing.get("primary_contact", email),
                "verification_status": existing["verification_status"],
                "onboarding_status": existing.get("onboarding_status", "not_started"),
                "approval_status": existing.get("approval_status", "pending"),
                "next_route": _pilot_next_route(existing),
            },
            "recovery": {
                "action": "sign_in_or_wait_for_approval",
                "message": "An existing wallet-verified signup already owns this wallet or principal.",
            },
        }

    signup_id = _pilot_signup_id_from_wallet(wallet_did)
    account_id = _pilot_account_id_from_wallet(wallet_did)
    now_iso = _now_iso()
    record = {
        "signup_id": signup_id,
        "account_id": account_id,
        "principal_did": principal_did,
        "primary_contact": email,
        "owner_display_name": display_name,
        "idempotency_key": idempotency_key,
        "pilot_terms_acknowledgement": True,
        "verification_status": "verified",
        "verification_token": None,
        "approval_status": "pending",
        "onboarding_status": "not_started",
        "provisioning_status": "not_started",
        "trial_state": "active",
        "signup_method": "wallet_verified",
        "wallet": {
            "did": wallet_did,
            "provider": wallet_provider,
            "state": "linked",
        },
        "created_at": now_iso,
        "updated_at": now_iso,
        "verified_at": now_iso,
    }
    should_auto_approve = AUTO_APPROVE_FIRST_SIGNUP and _is_first_unapproved_signup(db)
    signups[signup_id] = record
    canonical = _persist_pilot_signups(db, signups)
    persisted = canonical.get(signup_id) or record
    _upsert_pending_approval_pilot_principal(
        db,
        principal_did=principal_did,
        display_name=display_name,
        primary_contact=email,
        account_id=account_id,
        wallet_provider=wallet_provider,
    )
    if should_auto_approve:
        persisted = _auto_approve_signup_record(db, persisted, wallet_provider)
        signups = _load_pilot_signups(db)
        signups[signup_id] = persisted
        _persist_pilot_signups(db, signups)
    return {
        "status": "ok",
        "recoverable": False,
        "duplicate": False,
        "signup": {
            "signup_id": persisted["signup_id"],
            "account_id": persisted["account_id"],
            "principal_did": persisted["principal_did"],
            "primary_contact": persisted["primary_contact"],
            "verification_status": persisted["verification_status"],
            "onboarding_status": persisted["onboarding_status"],
            "approval_status": persisted.get("approval_status", "pending"),
            "next_route": _pilot_next_route(persisted),
        },
        "trust_step": {
            "type": "wallet_verified",
            "status": "verified",
            "wallet_provider": wallet_provider,
        },
    }


@router.post("/pilot/signup/wallet-verified")
def pilot_signup_wallet_verified(payload: PilotWalletVerifiedSignupRequest, db=Depends(get_db)):
    return _create_wallet_verified_signup(
        db,
        principal_did=payload.principal_did,
        wallet_did=payload.wallet_did,
        email=payload.email,
        display_name=payload.display_name,
        wallet_provider=payload.wallet_provider,
        idempotency_key=payload.idempotency_key,
    )


@router.post("/signin")
def auth_signin(payload: AuthSigninRequest, db=Depends(get_db)):
    record = _pilot_signup_for_contact(db, payload.primary_contact)
    if not isinstance(record, dict):
        raise HTTPException(
            status_code=404,
            detail={"error": "pilot_signup_not_found", "recoverable": True},
        )
    verification_status = str(record.get("verification_status") or "").strip().lower()
    if verification_status != "verified":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "pilot_signup_not_verified",
                "recoverable": True,
                "next_route": "verification_or_recovery",
            },
        )
    principal_did = str(record.get("principal_did") or "").strip()
    _active_principal_or_403(db, principal_did)
    token_bundle = mint_surface_session_bundle(
        principal_did=principal_did,
        auth_method="pilot_signup",
        access_ttl_seconds=payload.ttl_seconds,
    )
    session_token = token_bundle["session"]
    refresh_token = token_bundle["refresh_session"]
    session_state = _pilot_session_state(record)
    return {
        "status": "ok",
        "authenticated": True,
        "principal_did": principal_did,
        "session": {
            "token": session_token["token"],
            "token_type": session_token["token_type"],
            "expires_at": session_token["expires_at"],
            "issued_at": session_token["issued_at"],
            "jti": session_token["jti"],
        },
        "refresh_session": {
            "token": refresh_token["token"],
            "token_type": refresh_token["token_type"],
            "expires_at": refresh_token["expires_at"],
            "issued_at": refresh_token["issued_at"],
            "jti": refresh_token["jti"],
        },
        "routing": {
            "next_route": session_state["next_route"],
            "read_only": session_state["read_only"],
            "trial_state": session_state["trial_state"],
        },
        "account": {
            "account_id": session_state["account_id"],
            "primary_contact": session_state["primary_contact"],
        },
    }


@router.post("/challenge")
def auth_challenge(payload: AuthChallengeRequest, request: Request, db=Depends(get_db)):
    principal_did = payload.principal_did.strip()
    # Allow first-time DID bootstrap for passkey-first UX. This mirrors
    # register/challenge behavior and prevents hard 403 on initial login attempts.
    _ensure_active_principal(db, principal_did)

    origin = _origin_for_challenge(request, payload)
    if origin not in _allowed_origins():
        raise HTTPException(status_code=400, detail={"error": "origin_not_allowed", "origin": origin})

    rp_id = _rp_id_for_request(request, payload)
    challenge = _b64u_encode(secrets.token_bytes(32))
    challenge_id = _b64u_encode(secrets.token_bytes(16))
    now = _now_epoch()
    ttl = payload.challenge_ttl_seconds or int(os.getenv("AUTH_WEBAUTHN_CHALLENGE_TTL_SECONDS", "300"))
    record = {
        "challenge_id": challenge_id,
        "challenge": challenge,
        "flow": "authenticate",
        "principal_did": principal_did,
        "origin": origin,
        "rp_id": rp_id,
        "created_at": now,
        "expires_at": now + int(ttl),
        "used_at": None,
    }

    challenges = _load_challenges(db)
    challenges[challenge_id] = record
    _persist_challenges(db, challenges)
    bindings = _load_bindings(db)
    allow_credentials = _credential_descriptors_for_principal(bindings, principal_did)
    return {
        "status": "ok",
        "challenge_id": challenge_id,
        "challenge": challenge,
        "principal_did": principal_did,
        "origin": origin,
        "rp_id": rp_id,
        "allow_credentials": allow_credentials,
        "expires_at": record["expires_at"],
        "request_options": {
            "challenge": challenge,
            "rpId": rp_id,
            "allowCredentials": allow_credentials,
            "userVerification": "preferred",
            "timeout": 60000,
        },
    }


@router.post("/register/challenge")
def auth_register_challenge(
    payload: AuthRegisterChallengeRequest,
    request: Request,
    db=Depends(get_db),
):
    principal_did = payload.principal_did.strip()
    _ensure_active_principal(db, principal_did)

    origin = _origin_for_challenge(request, AuthChallengeRequest(principal_did=principal_did, origin=payload.origin, rp_id=payload.rp_id))
    if origin not in _allowed_origins():
        raise HTTPException(status_code=400, detail={"error": "origin_not_allowed", "origin": origin})
    rp_id = _rp_id_for_request(request, AuthChallengeRequest(principal_did=principal_did, origin=payload.origin, rp_id=payload.rp_id))

    challenge = _b64u_encode(secrets.token_bytes(32))
    challenge_id = _b64u_encode(secrets.token_bytes(16))
    now = _now_epoch()
    ttl = payload.challenge_ttl_seconds or int(os.getenv("AUTH_WEBAUTHN_CHALLENGE_TTL_SECONDS", "300"))
    record = {
        "challenge_id": challenge_id,
        "challenge": challenge,
        "flow": "register",
        "principal_did": principal_did,
        "origin": origin,
        "rp_id": rp_id,
        "created_at": now,
        "expires_at": now + int(ttl),
        "used_at": None,
    }
    challenges = _load_challenges(db)
    challenges[challenge_id] = record
    _persist_challenges(db, challenges)

    user_name = (payload.user_name or principal_did).strip()
    user_display_name = (payload.user_display_name or user_name).strip()
    rp_name = (payload.rp_name or os.getenv("AUTH_WEBAUTHN_RP_NAME", "Dual Substrate")).strip() or "Dual Substrate"
    bindings = _load_bindings(db)
    exclude_credentials = _credential_descriptors_for_principal(bindings, principal_did)
    return {
        "status": "ok",
        "challenge_id": challenge_id,
        "challenge": challenge,
        "principal_did": principal_did,
        "origin": origin,
        "rp_id": rp_id,
        "expires_at": record["expires_at"],
        "creation_options": {
            "rp": {"id": rp_id, "name": rp_name},
            "user": {
                "id": _principal_user_id_b64u(principal_did),
                "name": user_name,
                "displayName": user_display_name,
            },
            "challenge": challenge,
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
            "excludeCredentials": exclude_credentials,
            "attestation": "none",
            "authenticatorSelection": {"userVerification": "preferred"},
            "timeout": 60000,
        },
    }


@router.post("/register/verify")
def auth_register_verify(payload: AuthRegisterVerifyRequest, db=Depends(get_db)):
    challenge_id = payload.challenge_id.strip()
    credential_id = payload.credential_id.strip()
    if not challenge_id or not credential_id:
        raise HTTPException(status_code=400, detail={"error": "challenge_id_and_credential_id_required"})

    challenges = _load_challenges(db)
    challenge_record = challenges.get(challenge_id)
    if not isinstance(challenge_record, dict):
        raise HTTPException(status_code=401, detail={"error": "challenge_not_found"})
    if str(challenge_record.get("flow") or "").strip() not in {"", "register"}:
        raise HTTPException(status_code=401, detail={"error": "challenge_flow_invalid"})

    now = _now_epoch()
    expires_at = int(challenge_record.get("expires_at") or 0)
    if expires_at <= now:
        raise HTTPException(status_code=401, detail={"error": "challenge_expired"})
    if challenge_record.get("used_at") is not None:
        raise HTTPException(status_code=401, detail={"error": "challenge_already_used"})

    expected_challenge = str(challenge_record.get("challenge") or "").strip()
    expected_origin = str(challenge_record.get("origin") or "").strip()
    rp_id = str(challenge_record.get("rp_id") or "").strip()
    principal_did = str(challenge_record.get("principal_did") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=400, detail={"error": "principal_did_required"})
    _ensure_active_principal(db, principal_did)

    try:
        client_data_json = _b64u_decode(payload.client_data_json_b64u)
        authenticator_data = _b64u_decode(payload.authenticator_data_b64u)
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_base64url_payload"}) from exc
    try:
        client_data = json.loads(client_data_json.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "client_data_json_invalid"}) from exc

    client_type = str(client_data.get("type") or "").strip()
    if client_type != "webauthn.create":
        raise HTTPException(status_code=401, detail={"error": "webauthn_type_invalid", "value": client_type})
    if str(client_data.get("challenge") or "").strip() != expected_challenge:
        raise HTTPException(status_code=401, detail={"error": "challenge_mismatch"})
    if str(client_data.get("origin") or "").strip() != expected_origin:
        raise HTTPException(status_code=401, detail={"error": "origin_mismatch"})

    if len(authenticator_data) < 37:
        raise HTTPException(status_code=400, detail={"error": "authenticator_data_too_short"})
    rp_id_hash = authenticator_data[:32]
    flags = authenticator_data[32]
    sign_count = int.from_bytes(authenticator_data[33:37], "big")
    if rp_id_hash != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise HTTPException(status_code=401, detail={"error": "rp_id_hash_mismatch"})
    if (flags & 0x04) == 0:
        raise HTTPException(status_code=401, detail={"error": "user_verification_required"})

    public_key_pem = _spki_b64u_to_pem(payload.public_key_spki_b64u)

    bindings = _load_bindings(db)
    existing = bindings.get(credential_id)
    now_iso = _now_iso()
    if isinstance(existing, dict):
        bound_principal = str(existing.get("principal_did") or "").strip()
        if bound_principal and bound_principal != principal_did:
            raise HTTPException(status_code=403, detail={"error": "credential_principal_mismatch"})
        binding = dict(existing)
    else:
        binding = {
            "credential_id": credential_id,
            "created_at": now_iso,
        }
    binding["principal_did"] = principal_did
    if payload.principal_key_id and payload.principal_key_id.strip():
        binding["principal_key_id"] = payload.principal_key_id.strip()
    binding["status"] = "active"
    binding["public_key_pem"] = public_key_pem
    binding["updated_at"] = now_iso
    binding["last_used_at"] = None
    binding["sign_count"] = sign_count
    binding["revoked_at"] = None
    bindings[credential_id] = binding
    _persist_bindings(db, bindings)

    challenge_record["used_at"] = now
    challenge_record["verified_credential_id"] = credential_id
    challenges[challenge_id] = challenge_record
    _persist_challenges(db, challenges)

    return {
        "status": "ok",
        "principal_did": principal_did,
        "credential_id": credential_id,
        "sign_count": sign_count,
    }


@router.post("/verify")
def auth_verify(payload: AuthVerifyRequest, db=Depends(get_db)):
    challenge_id = payload.challenge_id.strip()
    credential_id = payload.credential_id.strip()
    if not challenge_id or not credential_id:
        raise HTTPException(status_code=400, detail={"error": "challenge_id_and_credential_id_required"})

    challenges = _load_challenges(db)
    challenge_record = challenges.get(challenge_id)
    if not isinstance(challenge_record, dict):
        raise HTTPException(status_code=401, detail={"error": "challenge_not_found"})
    if str(challenge_record.get("flow") or "").strip() not in {"", "authenticate"}:
        raise HTTPException(status_code=401, detail={"error": "challenge_flow_invalid"})

    now = _now_epoch()
    expires_at = int(challenge_record.get("expires_at") or 0)
    if expires_at <= now:
        raise HTTPException(status_code=401, detail={"error": "challenge_expired"})
    if challenge_record.get("used_at") is not None:
        raise HTTPException(status_code=401, detail={"error": "challenge_already_used"})

    challenge = str(challenge_record.get("challenge") or "").strip()
    expected_origin = str(challenge_record.get("origin") or "").strip()
    rp_id = str(challenge_record.get("rp_id") or "").strip()
    principal_did = str(
        (payload.principal_did.strip() if isinstance(payload.principal_did, str) else "")
        or challenge_record.get("principal_did")
        or ""
    ).strip()
    if not principal_did:
        raise HTTPException(status_code=400, detail={"error": "principal_did_required"})
    _active_principal_or_403(db, principal_did)

    if expected_origin not in _allowed_origins():
        raise HTTPException(status_code=400, detail={"error": "origin_not_allowed", "origin": expected_origin})

    try:
        client_data_json = _b64u_decode(payload.client_data_json_b64u)
        authenticator_data = _b64u_decode(payload.authenticator_data_b64u)
        signature = _b64u_decode(payload.signature_b64u)
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_base64url_payload"}) from exc

    try:
        client_data = json.loads(client_data_json.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"error": "client_data_json_invalid"}) from exc

    challenge_from_client = str(client_data.get("challenge") or "").strip()
    client_origin = str(client_data.get("origin") or "").strip()
    client_type = str(client_data.get("type") or "").strip()
    if client_type != "webauthn.get":
        raise HTTPException(status_code=401, detail={"error": "webauthn_type_invalid", "value": client_type})
    if challenge_from_client != challenge:
        raise HTTPException(status_code=401, detail={"error": "challenge_mismatch"})
    if client_origin != expected_origin:
        raise HTTPException(status_code=401, detail={"error": "origin_mismatch"})

    if len(authenticator_data) < 37:
        raise HTTPException(status_code=400, detail={"error": "authenticator_data_too_short"})
    rp_id_hash = authenticator_data[:32]
    flags = authenticator_data[32]
    sign_count = int.from_bytes(authenticator_data[33:37], "big")
    expected_rp_id_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
    if rp_id_hash != expected_rp_id_hash:
        raise HTTPException(status_code=401, detail={"error": "rp_id_hash_mismatch"})
    if (flags & 0x04) == 0:
        raise HTTPException(status_code=401, detail={"error": "user_verification_required"})

    bindings = _load_bindings(db)
    binding = bindings.get(credential_id)
    created_binding = False
    if isinstance(binding, dict):
        status = str(binding.get("status") or "").strip().lower()
        if status and status != "active":
            raise HTTPException(status_code=403, detail={"error": "credential_not_active", "status": status})
        bound_principal = str(binding.get("principal_did") or "").strip()
        if bound_principal and bound_principal != principal_did:
            raise HTTPException(status_code=403, detail={"error": "credential_principal_mismatch"})
        public_key_pem = str(binding.get("public_key_pem") or "").strip()
    else:
        public_key_pem = str(payload.public_key_pem or "").strip()
        if not public_key_pem:
            raise HTTPException(status_code=400, detail={"error": "public_key_pem_required_for_new_binding"})
        created_binding = True
        binding = {
            "credential_id": credential_id,
            "principal_did": principal_did,
            "principal_key_id": (payload.principal_key_id or "").strip() or None,
            "status": "active",
            "public_key_pem": public_key_pem,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "last_used_at": None,
            "sign_count": 0,
            "revoked_at": None,
        }

    previous_sign_count = int(binding.get("sign_count") or 0)
    if not (sign_count == 0 and previous_sign_count == 0) and sign_count <= previous_sign_count:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "sign_count_replay_detected",
                "previous_sign_count": previous_sign_count,
                "received_sign_count": sign_count,
            },
        )

    signed_payload = authenticator_data + hashlib.sha256(client_data_json).digest()
    _verify_signature(public_key_pem, signed_payload, signature)

    now_iso = _now_iso()
    binding["principal_did"] = principal_did
    if payload.principal_key_id and payload.principal_key_id.strip():
        binding["principal_key_id"] = payload.principal_key_id.strip()
    binding["updated_at"] = now_iso
    binding["last_used_at"] = now_iso
    binding["sign_count"] = sign_count
    binding["status"] = "active"
    binding["public_key_pem"] = public_key_pem
    bindings[credential_id] = binding

    challenge_record["used_at"] = now
    challenge_record["verified_credential_id"] = credential_id
    challenges[challenge_id] = challenge_record

    _persist_bindings(db, bindings)
    _persist_challenges(db, challenges)

    response: dict[str, Any] = {
        "status": "ok",
        "principal_did": principal_did,
        "credential_id": credential_id,
        "sign_count": sign_count,
        "binding_created": created_binding,
    }
    if payload.issue_session_token:
        token_bundle = mint_surface_session_bundle(
            principal_did=principal_did,
            principal_key_id=(payload.principal_key_id or "").strip() or str(binding.get("principal_key_id") or "").strip() or None,
            credential_id=credential_id,
            auth_method="passkey",
            access_ttl_seconds=payload.session_ttl_seconds,
        )
        session_token = token_bundle["session"]
        refresh_token = token_bundle["refresh_session"]
        response["session"] = {
            "token": session_token["token"],
            "token_type": session_token["token_type"],
            "expires_at": session_token["expires_at"],
            "issued_at": session_token["issued_at"],
            "jti": session_token["jti"],
        }
        response["refresh_session"] = {
            "token": refresh_token["token"],
            "token_type": refresh_token["token_type"],
            "expires_at": refresh_token["expires_at"],
            "issued_at": refresh_token["issued_at"],
            "jti": refresh_token["jti"],
        }
    return response


@router.post("/token")
def auth_issue_token(payload: AuthTokenIssueRequest, db=Depends(get_db)):
    principal_did = payload.principal_did.strip()
    _active_principal_or_403(db, principal_did)

    credential_id = (payload.credential_id or "").strip()
    if credential_id:
        bindings = _load_bindings(db)
        binding = bindings.get(credential_id)
        if not isinstance(binding, dict):
            raise HTTPException(status_code=403, detail={"error": "credential_not_bound", "credential_id": credential_id})
        status = str(binding.get("status") or "").strip().lower()
        if status != "active":
            raise HTTPException(status_code=403, detail={"error": "credential_not_active", "status": status})
        bound_did = str(binding.get("principal_did") or "").strip()
        if bound_did != principal_did:
            raise HTTPException(status_code=403, detail={"error": "credential_principal_mismatch"})

    token_bundle = mint_surface_session_bundle(
        principal_did=principal_did,
        principal_key_id=(payload.principal_key_id or "").strip() or None,
        credential_id=credential_id or None,
        auth_method=(payload.auth_method or "passkey").strip() or "passkey",
        roles=payload.roles,
        allowed_context_ids=payload.allowed_context_ids,
        ledger_ids=payload.ledger_ids,
        access_ttl_seconds=payload.ttl_seconds,
    )
    session_token = token_bundle["session"]
    refresh_token = token_bundle["refresh_session"]
    return {
        "status": "ok",
        "session": {
            "token": session_token["token"],
            "token_type": session_token["token_type"],
            "expires_at": session_token["expires_at"],
            "issued_at": session_token["issued_at"],
            "jti": session_token["jti"],
            "claims": session_token["claims"],
        },
        "refresh_session": {
            "token": refresh_token["token"],
            "token_type": refresh_token["token_type"],
            "expires_at": refresh_token["expires_at"],
            "issued_at": refresh_token["issued_at"],
            "jti": refresh_token["jti"],
            "claims": refresh_token["claims"],
        },
    }


@router.get("/session")
def auth_session(request: Request, db=Depends(get_db)):
    claims = apply_session_token_claims_or_raise(request)
    if not isinstance(claims, dict):
        return {"status": "ok", "authenticated": False}

    principal_did = str(claims.get("sub") or "").strip()
    principal_status = "unknown"
    if principal_did:
        principal_record = _load_principals(db).get(principal_did)
        if isinstance(principal_record, dict):
            principal_status = str(principal_record.get("status") or "unknown").strip() or "unknown"
    session_state = _pilot_session_state(_pilot_signup_for_principal(db, principal_did))

    return {
        "status": "ok",
        "authenticated": True,
        "principal_did": principal_did or None,
        "principal_key_id": str(claims.get("principal_key_id") or "").strip() or None,
        "credential_id": str(claims.get("credential_id") or "").strip() or None,
        "session_jti": str(claims.get("jti") or "").strip() or None,
        "auth_method": str(claims.get("auth_method") or "").strip() or None,
        "principal_status": principal_status,
        "routing": {
            "next_route": session_state["next_route"],
            "read_only": session_state["read_only"],
            "trial_state": session_state["trial_state"],
        },
        "account": {
            "account_id": session_state["account_id"],
            "primary_contact": session_state["primary_contact"],
        },
    }


@router.get("/session/access")
def auth_session_access(
    request: Request,
    target: str = Query("account"),
    db=Depends(get_db),
):
    claims = apply_session_token_claims_or_raise(request)
    if not isinstance(claims, dict):
        decision = _access_decision_for_target(
            authenticated=False,
            session_state={},
            target=target,
        )
        return {"status": "ok", "authenticated": False, "access": decision}

    principal_did = str(claims.get("sub") or "").strip()
    principal_record = _load_principals(db).get(principal_did)
    if not isinstance(principal_record, dict):
        decision = _access_decision_for_target(
            authenticated=False,
            session_state={},
            target=target,
        )
        decision["reason"] = "principal_not_registered"
        return {"status": "ok", "authenticated": False, "access": decision}
    principal_status = str(principal_record.get("status") or "").strip().lower()
    if principal_status != "active":
        decision = _access_decision_for_target(
            authenticated=False,
            session_state={},
            target=target,
        )
        decision["reason"] = "principal_not_active"
        return {"status": "ok", "authenticated": False, "access": decision}

    session_state = _pilot_session_state(_pilot_signup_for_principal(db, principal_did))
    return {
        "status": "ok",
        "authenticated": True,
        "principal_did": principal_did,
        "routing": {
            "next_route": session_state["next_route"],
            "read_only": session_state["read_only"],
            "trial_state": session_state["trial_state"],
        },
        "access": _access_decision_for_target(
            authenticated=True,
            session_state=session_state,
            target=target,
        ),
    }


@router.post("/signout")
def auth_signout(request: Request, db=Depends(get_db)):
    claims = apply_session_token_claims_or_raise(request)
    if not isinstance(claims, dict):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required"},
        )
    jti = str(claims.get("jti") or "").strip()
    if not jti:
        raise HTTPException(status_code=400, detail={"error": "session_jti_required"})
    revoked = _load_session_revocations(db)
    revoked.add(jti)
    canonical = _persist_session_revocations(db, revoked)
    return {
        "status": "ok",
        "signed_out": True,
        "jti": jti,
        "revoked_count": len(canonical),
    }


@router.get("/session/verify")
def auth_session_verify(request: Request, db=Depends(get_db)):
    claims = apply_session_token_claims_or_raise(request)
    if not isinstance(claims, dict):
        return {
            "status": "ok",
            "verified": False,
            "reason": "missing_session_token",
        }

    principal_did = str(claims.get("sub") or "").strip()
    if not principal_did:
        return {
            "status": "ok",
            "verified": False,
            "reason": "missing_principal_did",
        }

    principal_record = _load_principals(db).get(principal_did)
    if not isinstance(principal_record, dict):
        return {
            "status": "ok",
            "verified": False,
            "reason": "principal_not_registered",
            "principal_did": principal_did,
        }

    principal_status = str(principal_record.get("status") or "").strip().lower()
    if principal_status != "active":
        return {
            "status": "ok",
            "verified": False,
            "reason": "principal_not_active",
            "principal_did": principal_did,
            "principal_status": principal_status or "unknown",
        }

    return {
        "status": "ok",
        "verified": True,
        "reason": "verified",
        "principal_did": principal_did,
        "principal_key_id": str(claims.get("principal_key_id") or "").strip() or None,
        "credential_id": str(claims.get("credential_id") or "").strip() or None,
        "session_jti": str(claims.get("jti") or "").strip() or None,
        "auth_method": str(claims.get("auth_method") or "").strip() or None,
        "principal_status": principal_status,
    }


@router.post("/session/refresh")
def auth_session_refresh(request: Request, db=Depends(get_db)):
    claims = apply_refresh_token_claims_or_raise(request)
    if not isinstance(claims, dict):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required"},
        )

    principal_did = str(claims.get("sub") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=401, detail={"error": "missing_principal_did"})

    principal_record = _load_principals(db).get(principal_did)
    if not isinstance(principal_record, dict):
        raise HTTPException(status_code=401, detail={"error": "principal_not_registered"})

    principal_status = str(principal_record.get("status") or "").strip().lower()
    if principal_status != "active":
        raise HTTPException(
            status_code=401,
            detail={"error": "principal_not_active", "principal_status": principal_status or "unknown"},
        )

    metadata = principal_record.get("metadata") if isinstance(principal_record.get("metadata"), dict) else {}
    refreshed_ledger_id = (
        str(metadata.get("provisioned_ledger_id") or "").strip()
        or str(metadata.get("ledger_id") or "").strip()
    )
    refreshed_ledger_ids = [refreshed_ledger_id] if refreshed_ledger_id else []
    token_bundle = refresh_surface_session_bundle(
        claims,
        ledger_ids=refreshed_ledger_ids,
    )
    session_token = token_bundle["session"]
    refresh_token = token_bundle["refresh_session"]
    return {
        "status": "ok",
        "refreshed": True,
        "reason": "interactive_activity",
        "principal_did": principal_did,
        "principal_status": principal_status,
        "session": {
            "token": session_token["token"],
            "token_type": session_token["token_type"],
            "expires_at": session_token["expires_at"],
            "issued_at": session_token["issued_at"],
            "jti": session_token["jti"],
        },
        "refresh_session": {
            "token": refresh_token["token"],
            "token_type": refresh_token["token_type"],
            "expires_at": refresh_token["expires_at"],
            "issued_at": refresh_token["issued_at"],
            "jti": refresh_token["jti"],
        },
    }


@router.post("/passkeys/{credential_id}/revoke")
def auth_revoke_credential(
    credential_id: str,
    payload: CredentialRevokeRequest,
    request: Request,
    db=Depends(get_db),
):
    _require_revocation_token(request)
    key = credential_id.strip()
    if not key:
        raise HTTPException(status_code=400, detail={"error": "credential_id_required"})
    bindings = _load_bindings(db)
    binding = bindings.get(key)
    if not isinstance(binding, dict):
        raise HTTPException(status_code=404, detail={"error": "credential_not_bound"})
    now_iso = _now_iso()
    updated = dict(binding)
    updated["status"] = "revoked"
    updated["revoked_at"] = now_iso
    updated["updated_at"] = now_iso
    reason = (payload.reason or "").strip()
    updated["revoke_reason"] = reason or "revoked_by_operator"
    bindings[key] = updated
    canonical = _persist_bindings(db, bindings)
    return {"status": "ok", "credential": canonical.get(key)}


@router.post("/sessions/revoke")
def auth_revoke_session(
    payload: SessionRevokeRequest,
    request: Request,
    db=Depends(get_db),
):
    _require_revocation_token(request)
    jti = payload.jti.strip()
    if not jti:
        raise HTTPException(status_code=400, detail={"error": "jti_required"})
    revoked = _load_session_revocations(db)
    revoked.add(jti)
    canonical = _persist_session_revocations(db, revoked)
    return {
        "status": "ok",
        "revoked": True,
        "jti": jti,
        "revoked_count": len(canonical),
    }


@router.get("/dev/passkey", response_class=HTMLResponse)
def auth_dev_passkey_page() -> str:
    return """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Passkey Dev Check</title></head>
<body>
  <h1>Passkey Dev Check</h1>
  <label>Principal DID <input id="did" value="did:key:dev-user" style="width:380px"></label><br><br>
  <button id="register">Register Passkey</button>
  <button id="login">Login (Verify)</button>
  <pre id="out"></pre>
<script>
const out = document.getElementById("out");
const b64uToBuf = (s) => Uint8Array.from(atob((s + "===".slice((s.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));
const bufToB64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/g, "");
let lastCredentialId = null;
let lastPublicKeySpki = null;
let lastPrincipalDid = null;
async function j(url, body) {
  const r = await fetch(url, {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(body)});
  const p = await r.json();
  if (!r.ok) throw new Error(JSON.stringify(p));
  return p;
}
document.getElementById("register").onclick = async () => {
  try {
    const principalDid = document.getElementById("did").value.trim();
    const c = await j("/auth/register/challenge", {principal_did: principalDid});
    const o = c.creation_options;
    const publicKey = {
      challenge: b64uToBuf(o.challenge),
      rp: o.rp,
      user: { id: b64uToBuf(o.user.id), name: o.user.name, displayName: o.user.displayName },
      pubKeyCredParams: o.pubKeyCredParams,
      timeout: o.timeout,
      attestation: o.attestation,
      authenticatorSelection: o.authenticatorSelection,
    };
    const cred = await navigator.credentials.create({ publicKey });
    const resp = cred.response;
    const payload = {
      challenge_id: c.challenge_id,
      credential_id: bufToB64u(cred.rawId),
      client_data_json_b64u: bufToB64u(resp.clientDataJSON),
      authenticator_data_b64u: bufToB64u(resp.getAuthenticatorData()),
      public_key_spki_b64u: bufToB64u(resp.getPublicKey()),
    };
    const v = await j("/auth/register/verify", payload);
    lastCredentialId = payload.credential_id;
    lastPublicKeySpki = payload.public_key_spki_b64u;
    lastPrincipalDid = principalDid;
    out.textContent = "REGISTER OK\\n" + JSON.stringify(v, null, 2);
  } catch (e) {
    out.textContent = "REGISTER ERROR\\n" + String(e);
  }
};
document.getElementById("login").onclick = async () => {
  try {
    if (!lastCredentialId) throw new Error("Register first");
    const c = await j("/auth/challenge", {principal_did: lastPrincipalDid});
    const cred = await navigator.credentials.get({
      publicKey: {
        challenge: b64uToBuf(c.challenge),
        allowCredentials: [{type: "public-key", id: b64uToBuf(lastCredentialId)}],
        userVerification: "preferred",
        timeout: 60000,
      }
    });
    const resp = cred.response;
    const payload = {
      challenge_id: c.challenge_id,
      credential_id: bufToB64u(cred.rawId),
      client_data_json_b64u: bufToB64u(resp.clientDataJSON),
      authenticator_data_b64u: bufToB64u(resp.authenticatorData),
      signature_b64u: bufToB64u(resp.signature),
    };
    const v = await j("/auth/verify", payload);
    out.textContent = "LOGIN OK\\n" + JSON.stringify(v, null, 2);
  } catch (e) {
    out.textContent = "LOGIN ERROR\\n" + String(e);
  }
};
</script>
</body>
</html>"""
