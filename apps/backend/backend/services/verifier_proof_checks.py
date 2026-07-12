"""Stored resolver-backed proof verification results for external verifier portals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

VERIFIER_PROOF_CHECKS_V1_KEY = b"__verifier_proof_checks_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_verifier_proof_checks(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(VERIFIER_PROOF_CHECKS_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("proofs") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for proof_ref, record in records.items():
        if isinstance(record, dict):
            out[str(proof_ref)] = dict(record)
    return out


def persist_verifier_proof_checks(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for proof_ref in sorted(records.keys()):
        record = records.get(proof_ref)
        if isinstance(record, dict):
            canonical[proof_ref] = dict(record)
    db[VERIFIER_PROOF_CHECKS_V1_KEY] = json.dumps(
        {"version": 1, "proofs": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def get_verifier_proof_check(db: Any, proof_ref: str) -> dict[str, Any] | None:
    key = str(proof_ref or "").strip()
    if not key:
        return None
    return load_verifier_proof_checks(db).get(key)


def upsert_verifier_proof_check(
    db: Any,
    *,
    proof_ref: str,
    resolver_ref: str,
    portal_id: str | None = None,
    verifier_identity: str | None = None,
    verification_status: str = "verified",
    checked_at: str | None = None,
    proof_hash: str | None = None,
    trust_root_ref: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    normalized_proof_ref = str(proof_ref or "").strip()
    if not normalized_proof_ref:
        raise ValueError("proof_ref is required")
    normalized_resolver_ref = str(resolver_ref or "").strip()
    if not normalized_resolver_ref:
        raise ValueError("resolver_ref is required")
    normalized_status = str(verification_status or "").strip().lower() or "verified"
    if normalized_status not in {"verified", "failed", "unverifiable", "revoked"}:
        raise ValueError("unsupported verification_status")
    timestamp = str(checked_at or "").strip() or _now_iso()
    records = load_verifier_proof_checks(db)
    existing = records.get(normalized_proof_ref)
    created_at = str(existing.get("created_at") or "").strip() if isinstance(existing, dict) else ""
    record = {
        "proof_ref": normalized_proof_ref,
        "resolver_ref": normalized_resolver_ref,
        "portal_id": str(portal_id or "").strip() or None,
        "verifier_identity": str(verifier_identity or "").strip() or None,
        "verification_status": normalized_status,
        "checked_at": timestamp,
        "proof_hash": str(proof_hash or "").strip() or None,
        "trust_root_ref": str(trust_root_ref or "").strip() or None,
        "notes": str(notes or "").strip() or None,
        "created_at": created_at or timestamp,
        "updated_at": _now_iso(),
    }
    records[normalized_proof_ref] = record
    persist_verifier_proof_checks(db, records)
    return dict(record)
