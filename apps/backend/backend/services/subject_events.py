"""Backend subject-event storage for canonical authority transitions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

SUBJECT_EVENTS_V1_KEY = b"__subject_events_v1__"
AUTHORITY_SUBJECTS_V1_KEY = b"__authority_subjects_v1__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


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


def load_subject_events(db: Any) -> dict[str, dict[str, Any]]:
    return _load_json_map(db, SUBJECT_EVENTS_V1_KEY, "events")


def persist_subject_events(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _persist_json_map(db, SUBJECT_EVENTS_V1_KEY, "events", records)


def load_authority_subjects(db: Any) -> dict[str, dict[str, Any]]:
    return _load_json_map(db, AUTHORITY_SUBJECTS_V1_KEY, "subjects")


def persist_authority_subjects(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _persist_json_map(db, AUTHORITY_SUBJECTS_V1_KEY, "subjects", records)


def get_subject_event(db: Any, event_id: str) -> dict[str, Any] | None:
    key = str(event_id or "").strip()
    if not key:
        return None
    return load_subject_events(db).get(key)


def append_subject_event(
    db: Any,
    *,
    event_type: str,
    issuer: str,
    resulting_authority_subject_id: str,
    principal_did: str | None = None,
    canonical_subject: str | None = None,
    prior_authority_subject_id: str | None = None,
    evidence_refs: list[str] | None = None,
    standing_carryover: str | None = None,
    credential_carryover: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    subject_events = load_subject_events(db)
    subject_id = str(resulting_authority_subject_id or "").strip()
    if not subject_id:
        raise ValueError("resulting_authority_subject_id is required")
    normalized_event_type = str(event_type or "").strip()
    if not normalized_event_type:
        raise ValueError("event_type is required")
    normalized_issuer = str(issuer or "").strip() or "system"
    created_at = _now_iso()
    resolved_event_id = str(event_id or f"subevt:{uuid.uuid4().hex}").strip()
    if resolved_event_id in subject_events:
        raise RuntimeError(f"subject_event already recorded: {resolved_event_id}")
    event = {
        "event_id": resolved_event_id,
        "event_type": normalized_event_type,
        "issuer": normalized_issuer,
        "principal_did": str(principal_did or "").strip() or None,
        "canonical_subject": str(canonical_subject or "").strip() or None,
        "prior_authority_subject_id": str(prior_authority_subject_id or "").strip() or None,
        "resulting_authority_subject_id": subject_id,
        "evidence_refs": [str(item).strip() for item in (evidence_refs or []) if str(item).strip()],
        "standing_carryover": str(standing_carryover or "").strip() or None,
        "credential_carryover": str(credential_carryover or "").strip() or None,
        "created_at": created_at,
    }
    subject_events[resolved_event_id] = event
    persist_subject_events(db, subject_events)

    authority_subjects = load_authority_subjects(db)
    authority_subjects[subject_id] = {
        "authority_subject_id": subject_id,
        "principal_did": event.get("principal_did"),
        "canonical_subject": event.get("canonical_subject"),
        "last_event_id": resolved_event_id,
        "last_event_type": event.get("event_type"),
        "prior_authority_subject_id": event.get("prior_authority_subject_id"),
        "standing_carryover": event.get("standing_carryover"),
        "credential_carryover": event.get("credential_carryover"),
        "updated_at": created_at,
    }
    persist_authority_subjects(db, authority_subjects)
    return dict(event)
