"""Public key registry for live verifier portal signature checks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

VERIFIER_PUBLIC_KEYS_V1_KEY = b"__verifier_public_keys_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_verifier_public_keys(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(VERIFIER_PUBLIC_KEYS_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("keys") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for public_key_ref, record in records.items():
        if isinstance(record, dict):
            out[str(public_key_ref)] = dict(record)
    return out


def persist_verifier_public_keys(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for public_key_ref in sorted(records.keys()):
        record = records.get(public_key_ref)
        if isinstance(record, dict):
            canonical[public_key_ref] = dict(record)
    db[VERIFIER_PUBLIC_KEYS_V1_KEY] = json.dumps(
        {"version": 1, "keys": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def get_verifier_public_key(db: Any, public_key_ref: str) -> dict[str, Any] | None:
    key = str(public_key_ref or "").strip()
    if not key:
        return None
    return load_verifier_public_keys(db).get(key)


def upsert_verifier_public_key(
    db: Any,
    *,
    public_key_ref: str,
    algorithm: str,
    public_key_pem: str,
    trust_root_ref: str | None = None,
    status: str = "active",
    notes: str | None = None,
) -> dict[str, Any]:
    normalized_ref = str(public_key_ref or "").strip()
    if not normalized_ref:
        raise ValueError("public_key_ref is required")
    normalized_algorithm = str(algorithm or "").strip().lower()
    if normalized_algorithm not in {"ecdsa-p256", "ed25519"}:
        raise ValueError("unsupported algorithm")
    normalized_pem = str(public_key_pem or "").strip()
    if not normalized_pem:
        raise ValueError("public_key_pem is required")
    normalized_status = str(status or "").strip().lower() or "active"
    if normalized_status not in {"active", "revoked"}:
        raise ValueError("unsupported public key status")
    records = load_verifier_public_keys(db)
    existing = records.get(normalized_ref)
    created_at = str(existing.get("created_at") or "").strip() if isinstance(existing, dict) else ""
    timestamp = _now_iso()
    record = {
        "public_key_ref": normalized_ref,
        "algorithm": normalized_algorithm,
        "public_key_pem": normalized_pem,
        "trust_root_ref": str(trust_root_ref or "").strip() or None,
        "status": normalized_status,
        "notes": str(notes or "").strip() or None,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }
    records[normalized_ref] = record
    persist_verifier_public_keys(db, records)
    return dict(record)
