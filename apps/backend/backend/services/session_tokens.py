"""Session token minting and validation helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from backend.services.authz import apply_auth_claim_overrides


@dataclass(frozen=True)
class SessionTokenValidationError(Exception):
    reason: str


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64u_decode(value: str) -> bytes:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("missing base64url")
    padding = "=" * ((4 - len(cleaned) % 4) % 4)
    return base64.urlsafe_b64decode(cleaned + padding)


def _token_secret() -> str:
    return os.getenv("AUTH_SESSION_TOKEN_SECRET", "")


def _token_issuer() -> str:
    return os.getenv("AUTH_SESSION_TOKEN_ISSUER", "ds-middleware")


def _token_audience() -> str:
    return os.getenv("AUTH_SESSION_TOKEN_AUDIENCE", "ds-backend")


def _now_epoch() -> int:
    return int(time.time())


def _normalize_token_use(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "refresh":
        return "refresh"
    return "access"


def _default_ttl_seconds(*, token_use: str) -> int:
    normalized = _normalize_token_use(token_use)
    if normalized == "refresh":
        return int(os.getenv("AUTH_SESSION_REFRESH_TOKEN_TTL_SECONDS", "86400"))
    return int(os.getenv("AUTH_SESSION_TOKEN_TTL_SECONDS", "3600"))


def _jwt_signing_input(header: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str, bytes]:
    encoded_header = _b64u_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    encoded_payload = _b64u_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    return encoded_header, encoded_payload, signing_input


def _mint_token(
    *,
    principal_did: str,
    principal_key_id: str | None = None,
    credential_id: str | None = None,
    auth_method: str = "passkey",
    roles: list[str] | None = None,
    allowed_context_ids: list[str] | None = None,
    ledger_ids: list[str] | None = None,
    token_use: str = "access",
    session_family_id: str | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    subject = (principal_did or "").strip()
    if not subject:
        raise ValueError("principal_did is required")
    now = _now_epoch()
    normalized_token_use = _normalize_token_use(token_use)
    ttl = int(ttl_seconds or _default_ttl_seconds(token_use=normalized_token_use))
    jti = f"st_{secrets.token_urlsafe(16)}"
    family_id = str(session_family_id or "").strip() or f"ssf_{secrets.token_urlsafe(12)}"

    payload: dict[str, Any] = {
        "iss": _token_issuer(),
        "aud": _token_audience(),
        "sub": subject,
        "jti": jti,
        "token_use": normalized_token_use,
        "session_family_id": family_id,
        "iat": now,
        "exp": now + ttl,
        "auth_method": (auth_method or "passkey").strip() or "passkey",
        "roles": [str(item).strip() for item in (roles or []) if str(item).strip()],
        "allowed_context_ids": [
            str(item).strip() for item in (allowed_context_ids or []) if str(item).strip()
        ],
        "ledger_ids": [str(item).strip() for item in (ledger_ids or []) if str(item).strip()],
    }
    if isinstance(principal_key_id, str) and principal_key_id.strip():
        payload["principal_key_id"] = principal_key_id.strip()
    if isinstance(credential_id, str) and credential_id.strip():
        payload["credential_id"] = credential_id.strip()

    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header, encoded_payload, signing_input = _jwt_signing_input(header, payload)
    signature = hmac.new(_token_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    token = f"{encoded_header}.{encoded_payload}.{_b64u_encode(signature)}"

    return {
        "token": token,
        "token_type": "Bearer",
        "token_use": normalized_token_use,
        "expires_at": payload["exp"],
        "issued_at": payload["iat"],
        "jti": jti,
        "session_family_id": family_id,
        "claims": payload,
    }


def mint_session_token(
    *,
    principal_did: str,
    principal_key_id: str | None = None,
    credential_id: str | None = None,
    auth_method: str = "passkey",
    roles: list[str] | None = None,
    allowed_context_ids: list[str] | None = None,
    ledger_ids: list[str] | None = None,
    session_family_id: str | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    return _mint_token(
        principal_did=principal_did,
        principal_key_id=principal_key_id,
        credential_id=credential_id,
        auth_method=auth_method,
        roles=roles,
        allowed_context_ids=allowed_context_ids,
        ledger_ids=ledger_ids,
        token_use="access",
        session_family_id=session_family_id,
        ttl_seconds=ttl_seconds,
    )


def mint_refresh_token(
    *,
    principal_did: str,
    principal_key_id: str | None = None,
    credential_id: str | None = None,
    auth_method: str = "passkey",
    roles: list[str] | None = None,
    allowed_context_ids: list[str] | None = None,
    ledger_ids: list[str] | None = None,
    session_family_id: str | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    return _mint_token(
        principal_did=principal_did,
        principal_key_id=principal_key_id,
        credential_id=credential_id,
        auth_method=auth_method,
        roles=roles,
        allowed_context_ids=allowed_context_ids,
        ledger_ids=ledger_ids,
        token_use="refresh",
        session_family_id=session_family_id,
        ttl_seconds=ttl_seconds,
    )


def mint_surface_session_bundle(
    *,
    principal_did: str,
    principal_key_id: str | None = None,
    credential_id: str | None = None,
    auth_method: str = "passkey",
    roles: list[str] | None = None,
    allowed_context_ids: list[str] | None = None,
    ledger_ids: list[str] | None = None,
    access_ttl_seconds: int | None = None,
    refresh_ttl_seconds: int | None = None,
) -> dict[str, Any]:
    session_family_id = f"ssf_{secrets.token_urlsafe(12)}"
    session = mint_session_token(
        principal_did=principal_did,
        principal_key_id=principal_key_id,
        credential_id=credential_id,
        auth_method=auth_method,
        roles=roles,
        allowed_context_ids=allowed_context_ids,
        ledger_ids=ledger_ids,
        session_family_id=session_family_id,
        ttl_seconds=access_ttl_seconds,
    )
    refresh_session = mint_refresh_token(
        principal_did=principal_did,
        principal_key_id=principal_key_id,
        credential_id=credential_id,
        auth_method=auth_method,
        roles=roles,
        allowed_context_ids=allowed_context_ids,
        ledger_ids=ledger_ids,
        session_family_id=session_family_id,
        ttl_seconds=refresh_ttl_seconds,
    )
    return {"session": session, "refresh_session": refresh_session}


def refresh_session_token(
    claims: dict[str, Any],
    *,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    return mint_session_token(
        principal_did=str(claims.get("sub") or "").strip(),
        principal_key_id=str(claims.get("principal_key_id") or "").strip() or None,
        credential_id=str(claims.get("credential_id") or "").strip() or None,
        auth_method=str(claims.get("auth_method") or "passkey").strip() or "passkey",
        roles=[
            str(item).strip()
            for item in (claims.get("roles") or [])
            if str(item).strip()
        ],
        allowed_context_ids=[
            str(item).strip()
            for item in (claims.get("allowed_context_ids") or [])
            if str(item).strip()
        ],
        ledger_ids=[
            str(item).strip()
            for item in (claims.get("ledger_ids") or [])
            if str(item).strip()
        ],
        session_family_id=str(claims.get("session_family_id") or "").strip() or None,
        ttl_seconds=ttl_seconds,
    )


def refresh_surface_session_bundle(
    claims: dict[str, Any],
    *,
    ledger_ids: list[str] | None = None,
    access_ttl_seconds: int | None = None,
    refresh_ttl_seconds: int | None = None,
) -> dict[str, Any]:
    session_family_id = str(claims.get("session_family_id") or "").strip() or f"ssf_{secrets.token_urlsafe(12)}"
    effective_ledger_ids = (
        [str(item).strip() for item in ledger_ids if str(item).strip()]
        if ledger_ids is not None
        else [
            str(item).strip()
            for item in (claims.get("ledger_ids") or [])
            if str(item).strip()
        ]
    )
    session = mint_session_token(
        principal_did=str(claims.get("sub") or "").strip(),
        principal_key_id=str(claims.get("principal_key_id") or "").strip() or None,
        credential_id=str(claims.get("credential_id") or "").strip() or None,
        auth_method=str(claims.get("auth_method") or "passkey").strip() or "passkey",
        roles=[
            str(item).strip()
            for item in (claims.get("roles") or [])
            if str(item).strip()
        ],
        allowed_context_ids=[
            str(item).strip()
            for item in (claims.get("allowed_context_ids") or [])
            if str(item).strip()
        ],
        ledger_ids=effective_ledger_ids,
        session_family_id=session_family_id,
        ttl_seconds=access_ttl_seconds,
    )
    refresh_session = mint_refresh_token(
        principal_did=str(claims.get("sub") or "").strip(),
        principal_key_id=str(claims.get("principal_key_id") or "").strip() or None,
        credential_id=str(claims.get("credential_id") or "").strip() or None,
        auth_method=str(claims.get("auth_method") or "passkey").strip() or "passkey",
        roles=[
            str(item).strip()
            for item in (claims.get("roles") or [])
            if str(item).strip()
        ],
        allowed_context_ids=[
            str(item).strip()
            for item in (claims.get("allowed_context_ids") or [])
            if str(item).strip()
        ],
        ledger_ids=effective_ledger_ids,
        session_family_id=session_family_id,
        ttl_seconds=refresh_ttl_seconds,
    )
    return {"session": session, "refresh_session": refresh_session}


def validate_session_token(token: str, *, required_token_use: str = "access") -> dict[str, Any]:
    value = (token or "").strip()
    if not value:
        raise SessionTokenValidationError("token_missing")
    parts = value.split(".")
    if len(parts) != 3:
        raise SessionTokenValidationError("token_malformed")
    encoded_header, encoded_payload, encoded_signature = parts
    try:
        header_raw = _b64u_decode(encoded_header)
        payload_raw = _b64u_decode(encoded_payload)
        signature_raw = _b64u_decode(encoded_signature)
    except Exception as exc:
        raise SessionTokenValidationError("token_decode_failed") from exc
    try:
        header = json.loads(header_raw.decode("utf-8"))
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise SessionTokenValidationError("token_json_invalid") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise SessionTokenValidationError("token_payload_invalid")
    if str(header.get("alg") or "") != "HS256":
        raise SessionTokenValidationError("token_alg_invalid")

    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    expected_signature = hmac.new(_token_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(signature_raw, expected_signature):
        raise SessionTokenValidationError("token_signature_invalid")

    now = _now_epoch()
    exp = int(payload.get("exp") or 0)
    iat = int(payload.get("iat") or 0)
    if exp <= 0 or iat <= 0:
        raise SessionTokenValidationError("token_time_claims_invalid")
    if exp <= now:
        raise SessionTokenValidationError("token_expired")
    if iat > now + 60:
        raise SessionTokenValidationError("token_iat_in_future")
    if str(payload.get("iss") or "") != _token_issuer():
        raise SessionTokenValidationError("token_issuer_invalid")
    if str(payload.get("aud") or "") != _token_audience():
        raise SessionTokenValidationError("token_audience_invalid")
    token_use = _normalize_token_use(str(payload.get("token_use") or "access"))
    if token_use != _normalize_token_use(required_token_use):
        raise SessionTokenValidationError("token_use_invalid")
    if not str(payload.get("sub") or "").strip():
        raise SessionTokenValidationError("token_subject_missing")
    if not str(payload.get("jti") or "").strip():
        raise SessionTokenValidationError("token_jti_missing")
    return payload


def _extract_candidate_token(request: Request) -> str:
    header_auth = (request.headers.get("authorization") or "").strip()
    if header_auth.lower().startswith("bearer "):
        candidate = header_auth.split(" ", 1)[1].strip()
        # Preserve admin bearer token flow (non-JWT opaque token).
        if candidate.count(".") == 2:
            return candidate
    header_token = (request.headers.get("x-session-token") or "").strip()
    if header_token:
        return header_token
    return ""


def _extract_candidate_refresh_token(request: Request) -> str:
    header_auth = (request.headers.get("authorization") or "").strip()
    if header_auth.lower().startswith("bearer "):
        candidate = header_auth.split(" ", 1)[1].strip()
        if candidate.count(".") == 2:
            return candidate
    header_token = (request.headers.get("x-refresh-token") or "").strip()
    if header_token:
        return header_token
    return ""


def apply_session_token_claims_or_raise(request: Request) -> dict[str, Any] | None:
    token = _extract_candidate_token(request)
    if not token:
        return None
    try:
        claims = validate_session_token(token, required_token_use="access")
        _enforce_runtime_revocations_or_raise(request, claims)
    except SessionTokenValidationError as exc:
        if hasattr(request, "state"):
            try:
                request.state.auth_token_validation_failed = True  # type: ignore[attr-defined]
                request.state.auth_error_class = exc.reason  # type: ignore[attr-defined]
            except Exception:
                pass
        raise HTTPException(
            status_code=401,
            detail={
                "error": "token_validation_failed",
                "reason": exc.reason,
            },
        ) from exc

    principal_did = str(claims.get("sub") or "").strip()
    principal_key_id = str(claims.get("principal_key_id") or "").strip()
    session_jti = str(claims.get("jti") or "").strip()
    apply_auth_claim_overrides(
        request,
        principal_did=principal_did or None,
        principal_key_id=principal_key_id or None,
        session_jti=session_jti or None,
    )
    if hasattr(request, "state"):
        try:
            request.state.auth_claim_auth_method = str(claims.get("auth_method") or "").strip() or "passkey"  # type: ignore[attr-defined]
            request.state.auth_claim_roles = list(claims.get("roles") or [])  # type: ignore[attr-defined]
            request.state.auth_claim_allowed_context_ids = list(claims.get("allowed_context_ids") or [])  # type: ignore[attr-defined]
            request.state.auth_claim_ledger_ids = list(claims.get("ledger_ids") or [])  # type: ignore[attr-defined]
        except Exception:
            pass
    return claims


def apply_refresh_token_claims_or_raise(request: Request) -> dict[str, Any] | None:
    token = _extract_candidate_refresh_token(request)
    if not token:
        return None
    try:
        claims = validate_session_token(token, required_token_use="refresh")
        _enforce_runtime_revocations_or_raise(request, claims)
    except SessionTokenValidationError as exc:
        if hasattr(request, "state"):
            try:
                request.state.auth_token_validation_failed = True  # type: ignore[attr-defined]
                request.state.auth_error_class = exc.reason  # type: ignore[attr-defined]
            except Exception:
                pass
        raise HTTPException(
            status_code=401,
            detail={
                "error": "token_validation_failed",
                "reason": exc.reason,
            },
        ) from exc
    return claims


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_map(db: Any, key: bytes, field: str) -> dict[str, dict[str, Any]]:
    raw = db.get(key)
    payload = _decode_json(raw)
    records = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, value in records.items():
        if isinstance(value, dict):
            out[str(name)] = dict(value)
    return out


def _load_set(db: Any, key: bytes, field: str) -> set[str]:
    raw = db.get(key)
    payload = _decode_json(raw)
    values = payload.get(field) if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return set()
    return {str(item).strip() for item in values if str(item).strip()}


def _enforce_runtime_revocations_or_raise(request: Request, claims: dict[str, Any]) -> None:
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    db = getattr(state, "db", None)
    if db is None:
        return

    jti = str(claims.get("jti") or "").strip()
    sub = str(claims.get("sub") or "").strip()
    credential_id = str(claims.get("credential_id") or "").strip()

    revoked_jtis = _load_set(db, b"__session_revocations_v1__", "revoked_jtis")
    if jti and jti in revoked_jtis:
        raise SessionTokenValidationError("token_revoked")

    principals = _load_map(db, b"__principals_v1__", "principals")
    principal_record = principals.get(sub)
    if isinstance(principal_record, dict):
        principal_status = str(principal_record.get("status") or "").strip().lower()
        if principal_status and principal_status != "active":
            raise SessionTokenValidationError("token_principal_disabled")

    if credential_id:
        bindings = _load_map(db, b"__passkey_bindings_v1__", "bindings")
        binding = bindings.get(credential_id)
        if not isinstance(binding, dict):
            raise SessionTokenValidationError("token_credential_missing")
        credential_status = str(binding.get("status") or "").strip().lower()
        if credential_status and credential_status != "active":
            raise SessionTokenValidationError("token_credential_revoked")
