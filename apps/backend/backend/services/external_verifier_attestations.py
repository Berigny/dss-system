"""External verifier attestations keyed by evidence ref or coordinate."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.services.verifier_portals import evaluate_verifier_attestation

EXTERNAL_VERIFIER_ATTESTATIONS_V1_KEY = b"__external_verifier_attestations_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_external_verifier_attestations(db: Any) -> dict[str, list[dict[str, Any]]]:
    raw = db.get(EXTERNAL_VERIFIER_ATTESTATIONS_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("refs") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for evidence_ref, rows in records.items():
        if isinstance(rows, list):
            out[str(evidence_ref)] = [dict(row) for row in rows if isinstance(row, dict)]
    return out


def persist_external_verifier_attestations(
    db: Any,
    records: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    canonical: dict[str, list[dict[str, Any]]] = {}
    for evidence_ref in sorted(records.keys()):
        rows = records.get(evidence_ref)
        if isinstance(rows, list):
            canonical[evidence_ref] = [dict(row) for row in rows if isinstance(row, dict)][-64:]
    db[EXTERNAL_VERIFIER_ATTESTATIONS_V1_KEY] = json.dumps(
        {"version": 1, "refs": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def append_external_verifier_attestation(
    db: Any,
    *,
    evidence_ref: str,
    actor_id: str,
    actor_type: str,
    rating: int,
    verifier_portal: str,
    verifier_identity: str,
    reason: str | None = None,
    source: str | None = None,
    verification_signature_ref: str | None = None,
    verification_proof_ref: str | None = None,
    ts: str | None = None,
    attestation_id: str | None = None,
) -> dict[str, Any]:
    normalized_ref = str(evidence_ref or "").strip()
    if not normalized_ref:
        raise ValueError("evidence_ref is required")
    normalized_portal = str(verifier_portal or "").strip()
    if not normalized_portal:
        raise ValueError("verifier_portal is required")
    normalized_identity = str(verifier_identity or "").strip()
    if not normalized_identity:
        raise ValueError("verifier_identity is required")
    rating_v = max(0, min(3, int(rating)))
    timestamp = str(ts or "").strip() or _now_iso()
    if rating_v == 3:
        verification_status = "verified"
    elif rating_v == 2:
        verification_status = "plausible"
    elif rating_v == 1:
        verification_status = "unverifiable"
    else:
        verification_status = "rejected"
    record = {
        "attestation_id": str(attestation_id or f"veratt:{uuid.uuid4().hex}").strip(),
        "evidence_ref": normalized_ref,
        "actor_id": str(actor_id or "").strip() or "unknown",
        "actor_type": str(actor_type or "").strip() or "human",
        "rating": rating_v,
        "verification_status": verification_status,
        "verifier_portal": normalized_portal,
        "verifier_identity": normalized_identity,
        "reason": str(reason or "").strip() or None,
        "source": str(source or "").strip() or None,
        "verification_signature_ref": str(verification_signature_ref or "").strip() or None,
        "verification_proof_ref": str(verification_proof_ref or "").strip() or None,
        "ts": timestamp,
    }
    records = load_external_verifier_attestations(db)
    rows = records.get(normalized_ref)
    if not isinstance(rows, list):
        rows = []
    rows.append(record)
    records[normalized_ref] = rows[-64:]
    persist_external_verifier_attestations(db, records)
    return dict(record)


def get_external_verifier_attestations(db: Any, evidence_ref: str) -> list[dict[str, Any]]:
    return list(load_external_verifier_attestations(db).get(str(evidence_ref or "").strip(), []))


def get_external_verifier_summary(db: Any, evidence_ref: str) -> dict[str, Any] | None:
    rows = get_external_verifier_attestations(db, evidence_ref)
    if not rows:
        return None
    ordered = sorted(
        (dict(row) for row in rows if isinstance(row, dict)),
        key=lambda row: (str(row.get("ts") or ""), str(row.get("attestation_id") or "")),
    )
    latest = ordered[-1]
    latest_eval = evaluate_verifier_attestation(db, latest)
    latest_status = str(latest.get("verification_status") or "").strip().lower()
    trusted_verified = [
        row for row in ordered if str(row.get("verification_status") or "").strip().lower() == "verified"
        and bool(evaluate_verifier_attestation(db, row).get("trusted"))
    ]
    latest_verified = trusted_verified[-1] if trusted_verified else None
    return {
        "evidence_ref": str(evidence_ref or "").strip(),
        "latest_attestation": latest,
        "latest_attestation_evaluation": latest_eval,
        "latest_proof_check": dict(latest_eval.get("proof_check") or {}) if isinstance(latest_eval.get("proof_check"), dict) else None,
        "latest_signature_check": dict(latest_eval.get("signature_check") or {}) if isinstance(latest_eval.get("signature_check"), dict) else None,
        "latest_status": latest_status or None,
        "latest_verified_attestation": latest_verified,
        "verified": bool(latest_verified),
        "trusted": bool(latest_verified),
        "trust_reasons": [] if latest_verified else list(latest_eval.get("reasons") or []),
        "attestation_count": len(ordered),
    }
