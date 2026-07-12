"""Stored authoritative credential-status checks for issuer and verifier trust."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

CREDENTIAL_STATUS_CHECKS_V1_KEY = b"__credential_status_checks_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_credential_status_checks(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(CREDENTIAL_STATUS_CHECKS_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("checks") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for status_ref, record in records.items():
        if isinstance(record, dict):
            out[str(status_ref)] = dict(record)
    return out


def persist_credential_status_checks(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for status_ref in sorted(records.keys()):
        record = records.get(status_ref)
        if isinstance(record, dict):
            canonical[status_ref] = dict(record)
    db[CREDENTIAL_STATUS_CHECKS_V1_KEY] = json.dumps(
        {"version": 1, "checks": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def get_credential_status_check(db: Any, credential_status_ref: str) -> dict[str, Any] | None:
    key = str(credential_status_ref or "").strip()
    if not key:
        return None
    return load_credential_status_checks(db).get(key)


def upsert_credential_status_check(
    db: Any,
    *,
    credential_status_ref: str,
    credential_id: str | None = None,
    resolver_ref: str,
    status_state: str = "active",
    checked_at: str | None = None,
    proof_ref: str | None = None,
    trust_root_ref: str | None = None,
    issuer: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    normalized_status_ref = str(credential_status_ref or "").strip()
    if not normalized_status_ref:
        raise ValueError("credential_status_ref is required")
    normalized_resolver_ref = str(resolver_ref or "").strip()
    if not normalized_resolver_ref:
        raise ValueError("resolver_ref is required")
    normalized_state = str(status_state or "").strip().lower() or "active"
    if normalized_state not in {"active", "suspended", "revoked", "unverifiable"}:
        raise ValueError("unsupported status_state")

    records = load_credential_status_checks(db)
    existing = records.get(normalized_status_ref)
    created_at = str(existing.get("created_at") or "").strip() if isinstance(existing, dict) else ""
    timestamp = _now_iso()
    record = {
        "credential_status_ref": normalized_status_ref,
        "credential_id": str(credential_id or "").strip() or None,
        "resolver_ref": normalized_resolver_ref,
        "status_state": normalized_state,
        "checked_at": str(checked_at or "").strip() or timestamp,
        "proof_ref": str(proof_ref or "").strip() or None,
        "trust_root_ref": str(trust_root_ref or "").strip() or None,
        "issuer": str(issuer or "").strip() or None,
        "notes": str(notes or "").strip() or None,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }
    records[normalized_status_ref] = record
    persist_credential_status_checks(db, records)
    return dict(record)
