"""Registry-backed trust records for external verifier portals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from backend.services.verifier_proof_checks import get_verifier_proof_check
from backend.services.verifier_signature_checks import get_verifier_signature_check

VERIFIER_PORTALS_V1_KEY = b"__verifier_portals_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_verifier_portals(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(VERIFIER_PORTALS_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("portals") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for portal_id, record in records.items():
        if isinstance(record, dict):
            out[str(portal_id)] = dict(record)
    return out


def persist_verifier_portals(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for portal_id in sorted(records.keys()):
        record = records.get(portal_id)
        if isinstance(record, dict):
            canonical[portal_id] = dict(record)
    db[VERIFIER_PORTALS_V1_KEY] = json.dumps(
        {"version": 1, "portals": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def get_verifier_portal(db: Any, portal_id: str) -> dict[str, Any] | None:
    key = str(portal_id or "").strip()
    if not key:
        return None
    return load_verifier_portals(db).get(key)


def upsert_verifier_portal(
    db: Any,
    *,
    portal_id: str,
    portal_type: str,
    trust_basis: str,
    verification_mode: str,
    trusted_identities: list[str] | None = None,
    allowed_sources: list[str] | None = None,
    resolver_ref: str | None = None,
    public_key_ref: str | None = None,
    status: str = "active",
    notes: str | None = None,
) -> dict[str, Any]:
    normalized_portal_id = str(portal_id or "").strip()
    if not normalized_portal_id:
        raise ValueError("portal_id is required")
    normalized_portal_type = str(portal_type or "").strip().lower()
    if normalized_portal_type not in {"decoder_app", "mcp_tool", "service_portal", "human_reviewer"}:
        raise ValueError("unsupported portal_type")
    normalized_trust_basis = str(trust_basis or "").strip().lower()
    if normalized_trust_basis not in {"local_registry", "resolver_registry", "untp_dia", "signature_anchor"}:
        raise ValueError("unsupported trust_basis")
    normalized_verification_mode = str(verification_mode or "").strip().lower()
    if normalized_verification_mode not in {"registry_backed", "signature_required", "resolver_backed"}:
        raise ValueError("unsupported verification_mode")
    normalized_status = str(status or "").strip().lower() or "active"
    if normalized_status not in {"active", "suspended", "revoked"}:
        raise ValueError("unsupported verifier portal status")
    identities = sorted({str(item).strip() for item in (trusted_identities or []) if str(item).strip()})
    sources = sorted({str(item).strip() for item in (allowed_sources or []) if str(item).strip()})
    if normalized_verification_mode == "signature_required" and not str(public_key_ref or "").strip():
        raise ValueError("signature_required portals need public_key_ref")
    if normalized_verification_mode == "resolver_backed" and not str(resolver_ref or "").strip():
        raise ValueError("resolver_backed portals need resolver_ref")

    records = load_verifier_portals(db)
    existing = records.get(normalized_portal_id)
    created_at = str(existing.get("created_at") or "").strip() if isinstance(existing, dict) else ""
    timestamp = _now_iso()
    record = {
        "portal_id": normalized_portal_id,
        "portal_type": normalized_portal_type,
        "trust_basis": normalized_trust_basis,
        "verification_mode": normalized_verification_mode,
        "trusted_identities": identities,
        "allowed_sources": sources,
        "resolver_ref": str(resolver_ref or "").strip() or None,
        "public_key_ref": str(public_key_ref or "").strip() or None,
        "status": normalized_status,
        "notes": str(notes or "").strip() or None,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }
    records[normalized_portal_id] = record
    persist_verifier_portals(db, records)
    return dict(record)


def evaluate_verifier_attestation(db: Any, attestation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(attestation, dict):
        return {"trusted": False, "reasons": ["attestation_missing"], "portal": None}
    portal_id = str(attestation.get("verifier_portal") or "").strip()
    portal = get_verifier_portal(db, portal_id) if portal_id else None
    reasons: list[str] = []
    proof_check = None
    signature_check = None
    if not isinstance(portal, dict):
        reasons.append("portal_unregistered")
    else:
        status = str(portal.get("status") or "").strip().lower()
        if status != "active":
            reasons.append(f"portal_{status or 'inactive'}")
        trusted_identities = {
            str(item).strip()
            for item in (portal.get("trusted_identities") or [])
            if str(item).strip()
        }
        verifier_identity = str(attestation.get("verifier_identity") or "").strip()
        if trusted_identities and verifier_identity not in trusted_identities:
            reasons.append("identity_untrusted")
        allowed_sources = {
            str(item).strip()
            for item in (portal.get("allowed_sources") or [])
            if str(item).strip()
        }
        source = str(attestation.get("source") or "").strip()
        if allowed_sources and source not in allowed_sources:
            reasons.append("source_untrusted")
        verification_mode = str(portal.get("verification_mode") or "").strip().lower()
        signature_ref = str(attestation.get("verification_signature_ref") or "").strip()
        proof_ref = str(attestation.get("verification_proof_ref") or "").strip()
        if verification_mode == "signature_required" and not signature_ref:
            reasons.append("signature_missing")
        if verification_mode == "signature_required" and signature_ref:
            signature_check = get_verifier_signature_check(db, signature_ref)
            if not isinstance(signature_check, dict):
                reasons.append("signature_unverified")
            else:
                expected_public_key_ref = str(portal.get("public_key_ref") or "").strip()
                actual_public_key_ref = str(signature_check.get("public_key_ref") or "").strip()
                if expected_public_key_ref and actual_public_key_ref != expected_public_key_ref:
                    reasons.append("signature_key_mismatch")
                signature_status = str(signature_check.get("verification_status") or "").strip().lower()
                if signature_status != "verified":
                    reasons.append(f"signature_{signature_status or 'invalid'}")
                expected_portal_id = str(portal.get("portal_id") or "").strip()
                signature_portal_id = str(signature_check.get("portal_id") or "").strip()
                if signature_portal_id and expected_portal_id and signature_portal_id != expected_portal_id:
                    reasons.append("signature_portal_mismatch")
                signature_identity = str(signature_check.get("verifier_identity") or "").strip()
                verifier_identity = str(attestation.get("verifier_identity") or "").strip()
                if signature_identity and verifier_identity and signature_identity != verifier_identity:
                    reasons.append("signature_identity_mismatch")
        if verification_mode == "resolver_backed":
            if not proof_ref:
                reasons.append("resolver_proof_missing")
            else:
                proof_check = get_verifier_proof_check(db, proof_ref)
                if not isinstance(proof_check, dict):
                    reasons.append("resolver_proof_unverified")
                else:
                    expected_resolver_ref = str(portal.get("resolver_ref") or "").strip()
                    actual_resolver_ref = str(proof_check.get("resolver_ref") or "").strip()
                    if expected_resolver_ref and actual_resolver_ref != expected_resolver_ref:
                        reasons.append("resolver_mismatch")
                    proof_status = str(proof_check.get("verification_status") or "").strip().lower()
                    if proof_status != "verified":
                        reasons.append(f"resolver_proof_{proof_status or 'invalid'}")
                    expected_portal_id = str(portal.get("portal_id") or "").strip()
                    proof_portal_id = str(proof_check.get("portal_id") or "").strip()
                    if proof_portal_id and expected_portal_id and proof_portal_id != expected_portal_id:
                        reasons.append("resolver_portal_mismatch")
                    proof_identity = str(proof_check.get("verifier_identity") or "").strip()
                    verifier_identity = str(attestation.get("verifier_identity") or "").strip()
                    if proof_identity and verifier_identity and proof_identity != verifier_identity:
                        reasons.append("resolver_identity_mismatch")
    return {
        "trusted": not reasons,
        "reasons": reasons,
        "portal": dict(portal) if isinstance(portal, dict) else None,
        "proof_check": dict(proof_check) if isinstance(proof_check, dict) else None,
        "signature_check": dict(signature_check) if isinstance(signature_check, dict) else None,
    }
