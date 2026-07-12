"""Stored live identity-resolution checks for issuer and verifier trust."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

LIVE_IDENTITY_CHECKS_V1_KEY = b"__live_identity_checks_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_live_identity_checks(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(LIVE_IDENTITY_CHECKS_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("checks") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for subject_ref, record in records.items():
        if isinstance(record, dict):
            out[str(subject_ref)] = dict(record)
    return out


def persist_live_identity_checks(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for subject_ref in sorted(records.keys()):
        record = records.get(subject_ref)
        if isinstance(record, dict):
            canonical[subject_ref] = dict(record)
    db[LIVE_IDENTITY_CHECKS_V1_KEY] = json.dumps(
        {"version": 1, "checks": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def get_live_identity_check(db: Any, subject_ref: str) -> dict[str, Any] | None:
    key = str(subject_ref or "").strip()
    if not key:
        return None
    return load_live_identity_checks(db).get(key)


def upsert_live_identity_check(
    db: Any,
    *,
    subject_ref: str,
    subject_type: str,
    resolver_ref: str,
    resolution_status: str = "verified",
    resolved_identity: str | None = None,
    authority_binding_ref: str | None = None,
    identity_anchor_ref: str | None = None,
    checked_at: str | None = None,
    trust_root_ref: str | None = None,
    evidence_ref: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    normalized_subject_ref = str(subject_ref or "").strip()
    if not normalized_subject_ref:
        raise ValueError("subject_ref is required")
    normalized_subject_type = str(subject_type or "").strip().lower()
    if normalized_subject_type not in {"issuer", "verifier_portal"}:
        raise ValueError("unsupported subject_type")
    normalized_resolver_ref = str(resolver_ref or "").strip()
    if not normalized_resolver_ref:
        raise ValueError("resolver_ref is required")
    normalized_status = str(resolution_status or "").strip().lower() or "verified"
    if normalized_status not in {"verified", "failed", "revoked", "unverifiable"}:
        raise ValueError("unsupported resolution_status")

    records = load_live_identity_checks(db)
    existing = records.get(normalized_subject_ref)
    created_at = str(existing.get("created_at") or "").strip() if isinstance(existing, dict) else ""
    timestamp = _now_iso()
    record = {
        "subject_ref": normalized_subject_ref,
        "subject_type": normalized_subject_type,
        "resolver_ref": normalized_resolver_ref,
        "resolution_status": normalized_status,
        "resolved_identity": str(resolved_identity or "").strip() or None,
        "authority_binding_ref": str(authority_binding_ref or "").strip() or None,
        "identity_anchor_ref": str(identity_anchor_ref or "").strip() or None,
        "checked_at": str(checked_at or "").strip() or timestamp,
        "trust_root_ref": str(trust_root_ref or "").strip() or None,
        "evidence_ref": str(evidence_ref or "").strip() or None,
        "notes": str(notes or "").strip() or None,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }
    records[normalized_subject_ref] = record
    persist_live_identity_checks(db, records)
    return dict(record)
