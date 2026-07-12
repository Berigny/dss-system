"""Backend evidence-manifest storage for authority-event provenance."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from backend.services.external_verifier_attestations import get_external_verifier_summary

EVIDENCE_MANIFESTS_V1_KEY = b"__evidence_manifests_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_evidence_manifests(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(EVIDENCE_MANIFESTS_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("manifests") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for ref, record in records.items():
        if isinstance(record, dict):
            out[str(ref)] = dict(record)
    return out


def persist_evidence_manifests(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for ref in sorted(records.keys()):
        record = records.get(ref)
        if isinstance(record, dict):
            canonical[ref] = dict(record)
    db[EVIDENCE_MANIFESTS_V1_KEY] = json.dumps(
        {"version": 1, "manifests": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def get_evidence_manifest(db: Any, manifest_ref: str) -> dict[str, Any] | None:
    key = str(manifest_ref or "").strip()
    if not key:
        return None
    return load_evidence_manifests(db).get(key)


def _manifest_hash(*, issuer: str, evidence_refs: list[str], authority_subject_id: str | None = None) -> str:
    payload = {
        "issuer": str(issuer or "").strip(),
        "authority_subject_id": str(authority_subject_id or "").strip() or None,
        "evidence_refs": sorted({str(item).strip() for item in evidence_refs if str(item).strip()}),
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_checked_at(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def upsert_evidence_manifest(
    db: Any,
    *,
    issuer: str,
    evidence_refs: list[str],
    authority_subject_id: str | None = None,
    manifest_ref: str | None = None,
    package_type: str = "hashed_manifest",
    signature_ref: str | None = None,
    signature_status: str = "unsigned",
    verification_method: str | None = None,
    verification_status: str | None = None,
    verification_checked_at: str | None = None,
    verification_proof_ref: str | None = None,
    status: str = "active",
) -> dict[str, Any]:
    normalized_issuer = str(issuer or "").strip()
    if not normalized_issuer:
        raise ValueError("issuer is required")
    refs = sorted({str(item).strip() for item in (evidence_refs or []) if str(item).strip()})
    if not refs:
        raise ValueError("evidence_refs are required")
    normalized_status = str(status or "").strip().lower() or "active"
    if normalized_status not in {"active", "revoked", "invalidated"}:
        raise ValueError("unsupported evidence manifest status")
    normalized_package_type = str(package_type or "").strip().lower() or "hashed_manifest"
    if normalized_package_type not in {"hashed_manifest", "signed_manifest", "vc_evidence_bundle"}:
        raise ValueError("unsupported evidence manifest package_type")
    normalized_signature_status = str(signature_status or "").strip().lower() or "unsigned"
    if normalized_signature_status not in {"unsigned", "signed", "verified"}:
        raise ValueError("unsupported evidence manifest signature_status")
    normalized_verification_method = str(verification_method or "").strip().lower() or None
    if normalized_verification_method not in {None, "manual_attestation", "signature_check", "vc_check", "external_resolver"}:
        raise ValueError("unsupported evidence manifest verification_method")
    default_verification_status = "verified" if normalized_signature_status == "verified" else "unverified"
    normalized_verification_status = str(verification_status or "").strip().lower() or default_verification_status
    if normalized_verification_status not in {"unverified", "verified", "failed", "unverifiable"}:
        raise ValueError("unsupported evidence manifest verification_status")
    if verification_checked_at:
        _parse_checked_at(str(verification_checked_at), field_name="verification_checked_at")
    if normalized_signature_status == "verified" and normalized_verification_status != "verified":
        raise ValueError("verified signatures require verified evidence manifest verification_status")

    derived_summary = None
    if refs and not verification_status:
        summaries = [get_external_verifier_summary(db, ref) for ref in refs]
        summaries = [row for row in summaries if isinstance(row, dict)]
        if summaries:
            derived_summary = {
                "verified_refs": sorted(
                    {
                        str(row.get("evidence_ref") or "").strip()
                        for row in summaries
                        if bool(row.get("verified"))
                    }
                ),
                "untrusted_refs": sorted(
                    {
                        str(row.get("evidence_ref") or "").strip()
                        for row in summaries
                        if not bool(row.get("trusted"))
                    }
                ),
                "portals": sorted(
                    {
                        str((row.get("latest_verified_attestation") or row.get("latest_attestation") or {}).get("verifier_portal") or "").strip()
                        for row in summaries
                        if isinstance((row.get("latest_verified_attestation") or row.get("latest_attestation")), dict)
                        and str((row.get("latest_verified_attestation") or row.get("latest_attestation") or {}).get("verifier_portal") or "").strip()
                    }
                ),
                "proof_refs": sorted(
                    {
                        str((row.get("latest_verified_attestation") or row.get("latest_attestation") or {}).get("verification_proof_ref") or "").strip()
                        for row in summaries
                        if isinstance((row.get("latest_verified_attestation") or row.get("latest_attestation")), dict)
                        and str((row.get("latest_verified_attestation") or row.get("latest_attestation") or {}).get("verification_proof_ref") or "").strip()
                    }
                ),
                "latest_checked_at": max(
                    (
                        str((row.get("latest_verified_attestation") or row.get("latest_attestation") or {}).get("ts") or "").strip()
                        for row in summaries
                        if isinstance((row.get("latest_verified_attestation") or row.get("latest_attestation")), dict)
                        and str((row.get("latest_verified_attestation") or row.get("latest_attestation") or {}).get("ts") or "").strip()
                    ),
                    default="",
                )
                or None,
                "trust_reasons": {
                    str(row.get("evidence_ref") or "").strip(): list(row.get("trust_reasons") or [])
                    for row in summaries
                    if not bool(row.get("trusted"))
                },
            }
            if derived_summary["verified_refs"] and len(derived_summary["verified_refs"]) == len(refs):
                normalized_verification_status = "verified"
            elif derived_summary["untrusted_refs"]:
                normalized_verification_status = "failed"
            elif any(
                str((row.get("latest_attestation") or {}).get("verification_status") or "").strip().lower() == "rejected"
                for row in summaries
            ):
                normalized_verification_status = "failed"
            else:
                normalized_verification_status = "unverified"
            normalized_verification_method = normalized_verification_method or "external_resolver"
            if not verification_checked_at and derived_summary["latest_checked_at"]:
                _parse_checked_at(str(derived_summary["latest_checked_at"]), field_name="verification_checked_at")

    manifest_hash = _manifest_hash(
        issuer=normalized_issuer,
        evidence_refs=refs,
        authority_subject_id=authority_subject_id,
    )
    records = load_evidence_manifests(db)
    resolved_ref = str(manifest_ref or f"evidm:{manifest_hash[:24]}").strip()
    existing = records.get(resolved_ref)
    if isinstance(existing, dict) and manifest_ref is None:
        existing_hash = str(existing.get("manifest_hash") or "").strip()
        if existing_hash == manifest_hash:
            return dict(existing)
    created_at = str(existing.get("created_at") or "").strip() if isinstance(existing, dict) else ""
    timestamp = _now_iso()
    record = {
        "manifest_ref": resolved_ref,
        "manifest_hash": manifest_hash,
        "issuer": normalized_issuer,
        "authority_subject_id": str(authority_subject_id or "").strip() or None,
        "evidence_refs": refs,
        "package_type": normalized_package_type,
        "signature_ref": str(signature_ref or "").strip() or None,
        "signature_status": normalized_signature_status,
        "verification_method": normalized_verification_method,
        "verification_status": normalized_verification_status,
        "verification_checked_at": str(verification_checked_at or "").strip()
        or str((derived_summary or {}).get("latest_checked_at") or "").strip()
        or (timestamp if normalized_verification_status == "verified" else None),
        "verification_proof_ref": str(verification_proof_ref or "").strip()
        or ",".join((derived_summary or {}).get("proof_refs") or [])
        or None,
        "external_verifier_summary": derived_summary,
        "status": normalized_status,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }
    records[resolved_ref] = record
    persist_evidence_manifests(db, records)
    return dict(record)
