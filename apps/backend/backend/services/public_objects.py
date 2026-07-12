"""Versioned public object registry for verifier-facing lifecycle reads."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from typing import Any

PUBLIC_OBJECTS_V1_KEY = b"__public_objects_v1__"
DECISION_RECORD_REPLAY_SCHEMA = "dss-decision-record-replay-v1"
DECISION_RECORD_OVERLAY_SCHEMA = "dss-decision-record-overlay-event-v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def load_public_objects(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(PUBLIC_OBJECTS_V1_KEY)
    if raw is None:
        return {}
    payload = _decode_json(raw)
    records = payload.get("objects") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    return {str(key): dict(value) for key, value in records.items() if isinstance(value, dict)}


def persist_public_objects(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical = {key: dict(records[key]) for key in sorted(records.keys()) if isinstance(records.get(key), dict)}
    db[PUBLIC_OBJECTS_V1_KEY] = json.dumps(
        {"version": 1, "objects": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def get_public_object(db: Any, public_object_id: str) -> dict[str, Any] | None:
    key = str(public_object_id or "").strip()
    if not key:
        return None
    return load_public_objects(db).get(key)


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError):
        return value


def _base_record_view(record: dict[str, Any]) -> dict[str, Any]:
    excluded = {"immutable_base_record", "overlay_events", "replay_contract"}
    return {
        key: _json_clone(value)
        for key, value in record.items()
        if key not in excluded
    }


def _changed_materialized_fields(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    ignored = {"created_at", "updated_at"}
    fields = sorted((set(previous.keys()) | set(current.keys())) - ignored)
    changed: list[str] = []
    for field in fields:
        if _json_clone(previous.get(field)) != _json_clone(current.get(field)):
            changed.append(field)
    return changed


def _overlay_event_type(current: dict[str, Any], changed_fields: list[str]) -> str:
    lifecycle_state = str(current.get("lifecycle_state") or "").strip().lower()
    if lifecycle_state == "revoked" or "revoked_at" in changed_fields or "invalidation_reason" in changed_fields:
        return "revocation_v1"
    if lifecycle_state == "superseded" or "superseded_by" in changed_fields or "previous_version_id" in changed_fields:
        return "supersession_v1"
    if "artifact_identity" in changed_fields:
        return "governance_enrichment_v1"
    if "shareability" in changed_fields or "lifecycle_state" in changed_fields:
        return "lifecycle_update_v1"
    return "correction_v1"


def _build_overlay_event(
    *,
    public_object_id: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    changed_fields: list[str],
    seq: int,
    timestamp: str,
) -> dict[str, Any]:
    event_type = _overlay_event_type(current, changed_fields)
    event_hash = hashlib.sha256(
        json.dumps(
            {
                "public_object_id": public_object_id,
                "seq": seq,
                "event_type": event_type,
                "changed_fields": changed_fields,
                "timestamp": timestamp,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:16]
    return {
        "schema": DECISION_RECORD_OVERLAY_SCHEMA,
        "event_id": f"poov-{event_hash}",
        "seq": seq,
        "created_at": timestamp,
        "event_type": event_type,
        "public_object_id": public_object_id,
        "object_kind": str(current.get("object_kind") or "").strip() or None,
        "object_id": str(current.get("object_id") or "").strip() or None,
        "changed_fields": changed_fields,
        "patch": {field: _json_clone(current.get(field)) for field in changed_fields},
        "previous_values": {field: _json_clone(previous.get(field)) for field in changed_fields},
    }


def _replay_contract(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": DECISION_RECORD_REPLAY_SCHEMA,
        "immutable_base_record": True,
        "append_only_overlay_events": True,
        "current_materialized_view_mutable": True,
        "append_only_scope": "overlay_events_only",
        "replay_order": "immutable_base_record_then_overlay_events_by_seq",
        "export_route": f"/public/objects/{record.get('object_kind')}/{record.get('object_id')}/replay",
        "verifier_guidance": {
            "historical_issuance": "read immutable_base_record",
            "current_effective_state": "read current_materialized_view after replaying overlay_events in seq order",
            "revoked_or_superseded": "historical issuance remains replayable, but lifecycle state controls current shareability",
        },
    }


def public_object_replay_export(record: dict[str, Any]) -> dict[str, Any]:
    base_record = record.get("immutable_base_record")
    if not isinstance(base_record, dict):
        base_record = _base_record_view(record)
    overlay_events = record.get("overlay_events")
    if not isinstance(overlay_events, list):
        overlay_events = []
    ordered_events = [
        dict(event)
        for event in sorted(
            [event for event in overlay_events if isinstance(event, dict)],
            key=lambda item: int(item.get("seq") or 0),
        )
    ]
    current_view = _base_record_view(record)
    return {
        "schema": DECISION_RECORD_REPLAY_SCHEMA,
        "public_object_id": str(record.get("public_object_id") or "").strip() or None,
        "object_kind": str(record.get("object_kind") or "").strip() or None,
        "object_id": str(record.get("object_id") or "").strip() or None,
        "base_record": _json_clone(base_record),
        "overlay_events": _json_clone(ordered_events),
        "current_materialized_view": _json_clone(current_view),
        "current_effective_state": {
            "lifecycle_state": str(record.get("lifecycle_state") or "").strip() or "current",
            "shareability": str(record.get("shareability") or "").strip() or None,
            "content_digest": str(record.get("content_digest") or "").strip() or None,
            "status_ref": str(record.get("status_ref") or "").strip() or None,
            "superseded_by": str(record.get("superseded_by") or "").strip() or None,
            "revoked_at": str(record.get("revoked_at") or "").strip() or None,
            "invalidation_reason": str(record.get("invalidation_reason") or "").strip() or None,
        },
        "replay_contract": _replay_contract(record),
    }


def upsert_public_object(
    db: Any,
    *,
    public_object_id: str,
    object_kind: str,
    object_id: str,
    subject_id: str,
    issuer_id: str,
    content_digest: str,
    coord_ref: str | None = None,
    evidence_refs: list[str] | None = None,
    status_ref: str | None = None,
    previous_version_id: str | None = None,
    superseded_by: str | None = None,
    lifecycle_state: str = "current",
    invalidation_reason: str | None = None,
    revoked_at: str | None = None,
    shareability: str | None = None,
    artifact_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public_key = str(public_object_id or "").strip()
    if not public_key:
        raise ValueError("public_object_id is required")
    normalized_kind = str(object_kind or "").strip().lower()
    if not normalized_kind:
        raise ValueError("object_kind is required")
    normalized_state = str(lifecycle_state or "").strip().lower() or "current"
    if normalized_state not in {"current", "historical", "superseded", "revoked"}:
        raise ValueError("unsupported lifecycle_state")
    normalized_shareability = str(shareability or "").strip().lower() or (
        "share-ready" if normalized_state in {"current", "historical", "superseded"} else "not-shareable"
    )
    if normalized_shareability not in {"share-ready", "internal-only", "fallback-only", "not-shareable"}:
        raise ValueError("unsupported shareability")

    records = load_public_objects(db)
    existing = records.get(public_key)
    created_at = str(existing.get("created_at") or "").strip() if isinstance(existing, dict) else ""
    timestamp = _now_iso()
    record = {
        "public_object_id": public_key,
        "object_kind": normalized_kind,
        "object_id": str(object_id or "").strip() or None,
        "subject_id": str(subject_id or "").strip() or None,
        "issuer_id": str(issuer_id or "").strip() or None,
        "content_digest": str(content_digest or "").strip() or None,
        "coord_ref": str(coord_ref or "").strip() or None,
        "evidence_refs": [str(item).strip() for item in (evidence_refs or []) if str(item).strip()],
        "status_ref": str(status_ref or "").strip() or None,
        "previous_version_id": str(previous_version_id or "").strip() or None,
        "superseded_by": str(superseded_by or "").strip() or None,
        "lifecycle_state": normalized_state,
        "invalidation_reason": str(invalidation_reason or "").strip() or None,
        "revoked_at": str(revoked_at or "").strip() or None,
        "shareability": normalized_shareability,
        "artifact_identity": dict(artifact_identity) if isinstance(artifact_identity, dict) else None,
        "created_at": created_at or timestamp,
        "updated_at": timestamp,
    }
    if isinstance(existing, dict):
        base_record = existing.get("immutable_base_record")
        if not isinstance(base_record, dict):
            base_record = _base_record_view(existing)
        overlay_events = existing.get("overlay_events")
        if not isinstance(overlay_events, list):
            overlay_events = []
        previous_view = _base_record_view(existing)
        current_view = _base_record_view(record)
        changed_fields = _changed_materialized_fields(previous_view, current_view)
        if changed_fields:
            overlay_events = [
                dict(event)
                for event in overlay_events
                if isinstance(event, dict)
            ]
            overlay_events.append(
                _build_overlay_event(
                    public_object_id=public_key,
                    previous=previous_view,
                    current=current_view,
                    changed_fields=changed_fields,
                    seq=len(overlay_events) + 1,
                    timestamp=timestamp,
                )
            )
    else:
        base_record = _base_record_view(record)
        overlay_events = []
    record["immutable_base_record"] = _json_clone(base_record)
    record["overlay_events"] = _json_clone(overlay_events)
    record["replay_contract"] = _replay_contract(record)
    records[public_key] = record
    persist_public_objects(db, records)
    return dict(record)
