"""Backend issuer-authority registry for standing and authority events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

ISSUER_AUTHORITIES_V1_KEY = b"__issuer_authorities_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_issuer_authorities(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(ISSUER_AUTHORITIES_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("issuers") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for issuer, record in records.items():
        if isinstance(record, dict):
            out[str(issuer)] = dict(record)
    return out


def persist_issuer_authorities(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for issuer in sorted(records.keys()):
        record = records.get(issuer)
        if isinstance(record, dict):
            canonical[issuer] = dict(record)
    db[ISSUER_AUTHORITIES_V1_KEY] = json.dumps(
        {"version": 1, "issuers": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def get_issuer_authority(db: Any, issuer: str) -> dict[str, Any] | None:
    key = str(issuer or "").strip()
    if not key:
        return None
    return load_issuer_authorities(db).get(key)


def _parse_checked_at(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_fresh_check(value: str, *, field_name: str, max_age_days: int, message: str) -> None:
    checked_dt = _parse_checked_at(value, field_name=field_name)
    age = datetime.now(timezone.utc) - checked_dt
    if age.total_seconds() > max_age_days * 24 * 60 * 60:
        raise ValueError(message)


def validate_issuer_authority_record(authority: dict[str, Any], *, high_impact: bool = False) -> None:
    issuer_did = str(authority.get("issuer_did") or "").strip()
    vc_id = str(authority.get("vc_id") or "").strip()
    vc_type = str(authority.get("vc_type") or "").strip()
    vc_envelope = authority.get("vc_envelope") if isinstance(authority.get("vc_envelope"), dict) else {}
    credential_status_ref = str(authority.get("credential_status_ref") or "").strip()
    checked_at = str(authority.get("credential_status_checked_at") or "").strip()
    vc_verification_status = str(authority.get("vc_verification_status") or "").strip().lower()
    vc_verification_checked_at = str(authority.get("vc_verification_checked_at") or "").strip()
    policy_ref = str(authority.get("policy_ref") or "").strip()
    policy_verdict = str(authority.get("policy_verdict") or "").strip().lower()
    verifier_policy_ref = str(authority.get("verifier_policy_ref") or "").strip()
    policy_scope = authority.get("policy_scope") if isinstance(authority.get("policy_scope"), list) else []

    if vc_verification_status and vc_verification_status not in {"unverified", "verified", "failed", "unverifiable"}:
        raise ValueError("unsupported vc_verification_status")
    if policy_verdict and policy_verdict not in {"allow", "deny"}:
        raise ValueError("unsupported policy_verdict")
    if policy_verdict and not policy_ref:
        raise ValueError("policy_ref is required when policy_verdict is set")
    if policy_verdict == "allow" and not verifier_policy_ref:
        raise ValueError("verifier_policy_ref is required for explicit allow policy")
    for item in policy_scope:
        if not str(item).strip():
            raise ValueError("policy_scope entries must be non-empty strings")

    if high_impact:
        if not vc_id or not vc_type:
            raise ValueError("issuer authority requires VC metadata for high-impact event_type")
        if not credential_status_ref:
            raise ValueError("issuer authority requires credential_status_ref for high-impact event_type")
        if vc_verification_status != "verified":
            raise ValueError("issuer VC verification is not verified for high-impact event_type")
        if not vc_verification_checked_at:
            raise ValueError("issuer authority requires vc_verification_checked_at for high-impact event_type")

    if vc_envelope:
        vc_types = vc_envelope.get("type")
        if isinstance(vc_types, str):
            vc_type_values = {vc_types}
        elif isinstance(vc_types, list):
            vc_type_values = {str(item).strip() for item in vc_types if str(item).strip()}
        else:
            vc_type_values = set()
        if "VerifiableCredential" not in vc_type_values or "IssuerAuthorityCredential" not in vc_type_values:
            raise ValueError("issuer VC envelope is missing required credential types")
        envelope_issuer = str(vc_envelope.get("issuer") or "").strip()
        if issuer_did and envelope_issuer and envelope_issuer != issuer_did:
            raise ValueError("issuer VC envelope issuer does not match issuer_did")
        envelope_status = vc_envelope.get("credentialStatus") if isinstance(vc_envelope.get("credentialStatus"), dict) else {}
        envelope_status_id = str(envelope_status.get("id") or "").strip()
        if credential_status_ref and envelope_status_id and envelope_status_id != credential_status_ref:
            raise ValueError("issuer VC envelope credentialStatus does not match credential_status_ref")

    if checked_at:
        _validate_fresh_check(
            checked_at,
            field_name="credential_status_checked_at",
            max_age_days=30,
            message="issuer credential status check is stale for high-impact event_type",
        ) if high_impact else _parse_checked_at(checked_at, field_name="credential_status_checked_at")

    if vc_verification_checked_at:
        _validate_fresh_check(
            vc_verification_checked_at,
            field_name="vc_verification_checked_at",
            max_age_days=30,
            message="issuer VC verification check is stale for high-impact event_type",
        ) if high_impact else _parse_checked_at(vc_verification_checked_at, field_name="vc_verification_checked_at")


def upsert_issuer_authority(
    db: Any,
    *,
    issuer: str,
    issuer_class: str,
    allowed_event_types: list[str],
    evidence_requirement: str = "required",
    credential_ref: str | None = None,
    issuer_did: str | None = None,
    identity_anchor_ref: str | None = None,
    trust_basis: str | None = None,
    verification_state: str = "registry_only",
    policy_ref: str | None = None,
    policy_verdict: str | None = None,
    policy_scope: list[str] | None = None,
    verifier_policy_ref: str | None = None,
    vc_type: str | None = None,
    vc_id: str | None = None,
    vc_envelope: dict[str, Any] | None = None,
    credential_status_ref: str | None = None,
    credential_status_state: str = "active",
    credential_status_checked_at: str | None = None,
    vc_verification_method: str | None = None,
    vc_verification_status: str = "unverified",
    vc_verification_checked_at: str | None = None,
    vc_verification_proof_ref: str | None = None,
    status: str = "active",
    notes: str | None = None,
) -> dict[str, Any]:
    issuer_key = str(issuer or "").strip()
    if not issuer_key:
        raise ValueError("issuer is required")
    issuer_class_key = str(issuer_class or "").strip().lower()
    if not issuer_class_key:
        raise ValueError("issuer_class is required")
    allowed = sorted(
        {
            str(item).strip().lower()
            for item in (allowed_event_types or [])
            if str(item).strip()
        }
    )
    if not allowed:
        raise ValueError("allowed_event_types is required")
    evidence = str(evidence_requirement or "").strip().lower() or "required"
    if evidence not in {"required", "optional"}:
        raise ValueError("unsupported evidence_requirement")
    normalized_verification_state = str(verification_state or "").strip().lower() or "registry_only"
    if normalized_verification_state not in {"registry_only", "anchored", "verified"}:
        raise ValueError("unsupported verification_state")
    normalized_policy_verdict = str(policy_verdict or "").strip().lower() or None
    normalized_policy_scope = sorted(
        {
            str(item).strip().lower()
            for item in (policy_scope or [])
            if str(item).strip()
        }
    ) or None
    normalized_credential_status_state = str(credential_status_state or "").strip().lower() or "active"
    if normalized_credential_status_state not in {"active", "suspended", "revoked", "unverifiable"}:
        raise ValueError("unsupported credential_status_state")
    normalized_vc_verification_method = str(vc_verification_method or "").strip().lower() or None
    if normalized_vc_verification_method not in {None, "manual_attestation", "did_document_check", "vc_signature_check", "external_resolver"}:
        raise ValueError("unsupported vc_verification_method")
    normalized_vc_verification_status = str(vc_verification_status or "").strip().lower() or "unverified"
    if normalized_vc_verification_status not in {"unverified", "verified", "failed", "unverifiable"}:
        raise ValueError("unsupported vc_verification_status")
    normalized_status = str(status or "").strip().lower() or "active"
    if normalized_status not in {"active", "suspended", "revoked"}:
        raise ValueError("unsupported issuer authority status")

    records = load_issuer_authorities(db)
    existing = records.get(issuer_key)
    created_at = str(existing.get("created_at") or "").strip() if isinstance(existing, dict) else ""
    timestamp = _now_iso()
    record = {
        "issuer": issuer_key,
        "issuer_class": issuer_class_key,
        "allowed_event_types": allowed,
        "evidence_requirement": evidence,
        "credential_ref": str(credential_ref or "").strip() or None,
        "issuer_did": str(issuer_did or "").strip() or None,
        "identity_anchor_ref": str(identity_anchor_ref or "").strip() or None,
        "trust_basis": str(trust_basis or "").strip() or None,
        "verification_state": normalized_verification_state,
        "policy_ref": str(policy_ref or "").strip() or None,
        "policy_verdict": normalized_policy_verdict,
        "policy_scope": normalized_policy_scope,
        "verifier_policy_ref": str(verifier_policy_ref or "").strip() or None,
        "vc_type": str(vc_type or "").strip() or None,
        "vc_id": str(vc_id or "").strip() or None,
        "vc_envelope": dict(vc_envelope or {}) if isinstance(vc_envelope, dict) else None,
        "credential_status_ref": str(credential_status_ref or "").strip() or None,
        "credential_status_state": normalized_credential_status_state,
        "credential_status_checked_at": str(credential_status_checked_at or "").strip() or timestamp,
        "vc_verification_method": normalized_vc_verification_method,
        "vc_verification_status": normalized_vc_verification_status,
        "vc_verification_checked_at": str(vc_verification_checked_at or "").strip() or (timestamp if normalized_vc_verification_status == "verified" else None),
        "vc_verification_proof_ref": str(vc_verification_proof_ref or "").strip() or None,
        "status": normalized_status,
        "notes": str(notes or "").strip() or None,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }
    validate_issuer_authority_record(record, high_impact=False)
    records[issuer_key] = record
    persist_issuer_authorities(db, records)
    return dict(record)
