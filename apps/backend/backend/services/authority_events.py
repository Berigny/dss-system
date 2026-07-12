"""Backend append-only standing/authority events and materialized authority state."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.services.evidence_manifests import get_evidence_manifest, upsert_evidence_manifest
from backend.services.issuer_authorities import get_issuer_authority, validate_issuer_authority_record
from backend.services.live_identity_checks import get_live_identity_check
from backend.services.credential_status_checks import get_credential_status_check
from backend.services.subject_events import get_subject_event

AUTHORITY_EVENTS_V1_KEY = b"__authority_events_v1__"
AUTHORITY_STATE_V1_KEY = b"__authority_state_v1__"


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


def load_authority_events(db: Any) -> dict[str, dict[str, Any]]:
    return _load_json_map(db, AUTHORITY_EVENTS_V1_KEY, "events")


def persist_authority_events(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _persist_json_map(db, AUTHORITY_EVENTS_V1_KEY, "events", records)


def load_authority_state(db: Any) -> dict[str, dict[str, Any]]:
    return _load_json_map(db, AUTHORITY_STATE_V1_KEY, "subjects")


def persist_authority_state(db: Any, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _persist_json_map(db, AUTHORITY_STATE_V1_KEY, "subjects", records)


def get_authority_event(db: Any, event_id: str) -> dict[str, Any] | None:
    key = str(event_id or "").strip()
    if not key:
        return None
    return load_authority_events(db).get(key)


def get_authority_state(db: Any, authority_subject_id: str) -> dict[str, Any] | None:
    key = str(authority_subject_id or "").strip()
    if not key:
        return None
    return load_authority_state(db).get(key)




def _parse_checked_at(value: str, *, field_name: str) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale_check(value: str | None, *, max_age_days: int = 30) -> bool:
    parsed = _parse_checked_at(str(value or ""), field_name="checked_at")
    if parsed is None:
        return True
    age = datetime.now(timezone.utc) - parsed
    return age.total_seconds() > max_age_days * 24 * 60 * 60

def _validate_event_type(event_type: str) -> str:
    normalized = str(event_type or "").strip().lower()
    allowed = {"sanction", "repair", "decay", "probation", "trust_adjustment"}
    if normalized not in allowed:
        raise ValueError("unsupported authority event_type")
    return normalized


def _validate_issuer(db: Any, issuer: str, event_type: str) -> tuple[str, dict[str, Any]]:
    normalized = str(issuer or "").strip()
    if not normalized:
        raise ValueError("issuer is required")
    lowered = normalized.lower()
    event = str(event_type or "").strip().lower()
    if lowered.startswith("self:"):
        raise ValueError("self-issued authority events are forbidden")
    authority = get_issuer_authority(db, normalized)
    if not isinstance(authority, dict):
        raise ValueError("issuer is not registered in issuer authority registry")
    if str(authority.get("status") or "").strip().lower() != "active":
        raise ValueError("issuer authority is not active")
    allowed_event_types = {
        str(item).strip().lower()
        for item in (authority.get("allowed_event_types") or [])
        if str(item).strip()
    }
    if event not in allowed_event_types:
        raise ValueError("issuer authority does not allow this event_type")
    issuer_class = str(authority.get("issuer_class") or "").strip().lower()
    credential_ref = str(authority.get("credential_ref") or "").strip()
    issuer_did = str(authority.get("issuer_did") or "").strip()
    identity_anchor_ref = str(authority.get("identity_anchor_ref") or "").strip()
    verification_state = str(authority.get("verification_state") or "").strip().lower()
    vc_id = str(authority.get("vc_id") or "").strip()
    vc_type = str(authority.get("vc_type") or "").strip()
    credential_status_ref = str(authority.get("credential_status_ref") or "").strip()
    credential_status_state = str(authority.get("credential_status_state") or "").strip().lower()
    if event in {"repair", "trust_adjustment"} and issuer_class == "advisory_model_evaluator":
        raise ValueError("advisory issuer cannot directly grant repair or trust adjustment")
    if event in {"repair", "trust_adjustment"}:
        if not credential_ref:
            raise ValueError("issuer authority requires credential_ref for high-impact event_type")
        if not issuer_did:
            raise ValueError("issuer authority requires issuer_did for high-impact event_type")
        if not identity_anchor_ref:
            raise ValueError("issuer authority requires identity_anchor_ref for high-impact event_type")
        if verification_state not in {"anchored", "verified"}:
            raise ValueError("issuer authority is not anchored for high-impact event_type")
        if credential_status_state != "active":
            raise ValueError("issuer credential status is not active for high-impact event_type")
        validate_issuer_authority_record(dict(authority), high_impact=True)
        live_identity = get_live_identity_check(db, issuer_did)
        if not isinstance(live_identity, dict):
            raise ValueError("issuer live identity resolution is missing for high-impact event_type")
        live_identity_status = str(live_identity.get("resolution_status") or "").strip().lower()
        if live_identity_status != "verified":
            raise ValueError("issuer live identity resolution is not verified for high-impact event_type")
        if _is_stale_check(live_identity.get("checked_at"), max_age_days=30):
            raise ValueError("issuer live identity resolution is stale for high-impact event_type")
        live_anchor_ref = str(live_identity.get("identity_anchor_ref") or "").strip()
        if identity_anchor_ref and live_anchor_ref and live_anchor_ref != identity_anchor_ref:
            raise ValueError("issuer live identity anchor does not match issuer authority anchor")
        live_binding_ref = str(live_identity.get("authority_binding_ref") or "").strip()
        if credential_ref and live_binding_ref and live_binding_ref != credential_ref:
            raise ValueError("issuer live identity binding does not match credential_ref")
        live_status = get_credential_status_check(db, credential_status_ref)
        if not isinstance(live_status, dict):
            raise ValueError("issuer live credential status is missing for high-impact event_type")
        live_status_state = str(live_status.get("status_state") or "").strip().lower()
        if live_status_state != "active":
            raise ValueError("issuer live credential status is not active for high-impact event_type")
        if _is_stale_check(live_status.get("checked_at"), max_age_days=30):
            raise ValueError("issuer live credential status is stale for high-impact event_type")
    return normalized, dict(authority)


def _validate_reason_code(reason_code: str) -> str:
    normalized = str(reason_code or "").strip().lower()
    if not normalized:
        raise ValueError("reason_code is required")
    return normalized


def _validate_evidence_refs(event_type: str, evidence_refs: list[str] | None) -> list[str]:
    refs = [str(item).strip() for item in (evidence_refs or []) if str(item).strip()]
    if event_type in {"sanction", "repair", "trust_adjustment"} and not refs:
        raise ValueError("evidence_refs are required for sanction, repair, and trust adjustment events")
    return refs


def _reject_duplicate_evidence_reuse(
    db: Any,
    *,
    authority_subject_id: str,
    issuer: str,
    event_type: str,
    manifest_hash: str | None,
    current_event_id: str | None = None,
) -> None:
    if event_type not in {"sanction", "repair", "trust_adjustment"}:
        return
    target_hash = str(manifest_hash or "").strip()
    if not target_hash:
        return
    authority_events = load_authority_events(db)
    for row in authority_events.values():
        if not isinstance(row, dict):
            continue
        if current_event_id and str(row.get("event_id") or "").strip() == current_event_id:
            continue
        if str(row.get("authority_subject_id") or "").strip() != authority_subject_id:
            continue
        if str(row.get("issuer") or "").strip() != issuer:
            continue
        if str(row.get("evidence_manifest_hash") or "").strip() != target_hash:
            continue
        prior_type = str(row.get("event_type") or "").strip().lower()
        if prior_type in {"sanction", "repair", "trust_adjustment"}:
            raise RuntimeError("authority_event evidence manifest already used for high-impact event")


def _normalize_delta(delta: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(delta or {})
    normalized: dict[str, Any] = {}
    trust_class = str(payload.get("trust_class") or "").strip()
    posture_class = str(payload.get("posture_class") or "").strip()
    probation_status = str(payload.get("probation_status") or "").strip()
    if trust_class:
        normalized["trust_class"] = trust_class
    if posture_class:
        normalized["posture_class"] = posture_class
    if probation_status:
        normalized["probation_status"] = probation_status
    return normalized


def _default_standing_view(subject_id: str) -> dict[str, Any]:
    return {
        "authority_subject_id": subject_id,
        "trust_class": "T1",
        "posture_class": "P1",
        "probation_status": "probation",
        "active_sanctions": [],
        "last_event_id": None,
        "last_event_type": None,
        "last_reason_code": None,
        "credential_ref": None,
        "standing_envelope_ref": None,
        "evidence_manifest_ref": None,
        "evidence_manifest_hash": None,
        "subject_transition_event_ref": None,
        "current_validation_status": "active",
        "current_invalidation_reasons": [],
        "updated_at": None,
    }


def _materialize_event(
    current: dict[str, Any] | None,
    event: dict[str, Any],
) -> dict[str, Any]:
    subject_id = str(event.get("authority_subject_id") or "").strip()
    standing_view = dict(current) if isinstance(current, dict) else _default_standing_view(subject_id)
    delta_dict = event.get("delta") if isinstance(event.get("delta"), dict) else {}
    normalized_event_type = str(event.get("event_type") or "").strip().lower()
    normalized_reason = str(event.get("reason_code") or "").strip().lower()
    created_at = str(event.get("created_at") or "").strip() or _now_iso()

    if isinstance(delta_dict.get("trust_class"), str) and str(delta_dict.get("trust_class")).strip():
        standing_view["trust_class"] = str(delta_dict.get("trust_class")).strip()
    if isinstance(delta_dict.get("posture_class"), str) and str(delta_dict.get("posture_class")).strip():
        standing_view["posture_class"] = str(delta_dict.get("posture_class")).strip()
    if isinstance(delta_dict.get("probation_status"), str) and str(delta_dict.get("probation_status")).strip():
        standing_view["probation_status"] = str(delta_dict.get("probation_status")).strip()

    active_sanctions = standing_view.get("active_sanctions") if isinstance(standing_view.get("active_sanctions"), list) else []
    active_sanctions = [str(item).strip() for item in active_sanctions if str(item).strip()]
    if normalized_event_type == "sanction":
        if normalized_reason not in active_sanctions:
            active_sanctions.append(normalized_reason)
        standing_view["probation_status"] = "probation"
    elif normalized_event_type == "repair":
        active_sanctions = [item for item in active_sanctions if item != normalized_reason]
        if not active_sanctions:
            standing_view["probation_status"] = standing_view.get("probation_status") or "probation"
    elif normalized_event_type == "probation":
        standing_view["probation_status"] = "probation"
    elif normalized_event_type == "decay" and not active_sanctions:
        standing_view["probation_status"] = "cleared"

    standing_view["active_sanctions"] = active_sanctions
    standing_view["last_event_id"] = event.get("event_id")
    standing_view["last_event_type"] = normalized_event_type
    standing_view["last_reason_code"] = normalized_reason
    standing_view["credential_ref"] = event.get("credential_ref")
    standing_view["standing_envelope_ref"] = event.get("standing_envelope_ref")
    standing_view["evidence_manifest_ref"] = event.get("evidence_manifest_ref")
    standing_view["evidence_manifest_hash"] = event.get("evidence_manifest_hash")
    standing_view["subject_transition_event_ref"] = event.get("subject_transition_event_ref")
    standing_view["principal_did"] = event.get("principal_did")
    standing_view["canonical_subject"] = event.get("canonical_subject")
    standing_view["updated_at"] = created_at
    return standing_view


def _apply_current_validation(
    db: Any,
    *,
    event: dict[str, Any],
    standing_view: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    issuer = str(event.get("issuer") or "").strip()
    manifest_ref = str(event.get("evidence_manifest_ref") or "").strip()
    authority = get_issuer_authority(db, issuer) if issuer else None
    normalized_event_type = str(event.get("event_type") or "").strip().lower()
    if isinstance(authority, dict):
        issuer_status = str(authority.get("status") or "").strip().lower()
        if issuer_status in {"suspended", "revoked"}:
            reasons.append(f"issuer_{issuer_status}")
        credential_status_state = str(authority.get("credential_status_state") or "").strip().lower()
        if credential_status_state in {"suspended", "revoked", "unverifiable"}:
            reasons.append(f"credential_status_{credential_status_state}")
        if normalized_event_type in {"repair", "trust_adjustment"}:
            vc_verification_status = str(authority.get("vc_verification_status") or "").strip().lower()
            if vc_verification_status != "verified":
                reasons.append("issuer_vc_unverified")
            elif _is_stale_check(authority.get("vc_verification_checked_at"), max_age_days=30):
                reasons.append("issuer_vc_verification_stale")
            if _is_stale_check(authority.get("credential_status_checked_at"), max_age_days=30):
                reasons.append("credential_status_stale")
    elif issuer:
        reasons.append("issuer_missing")
    if manifest_ref:
        manifest = get_evidence_manifest(db, manifest_ref)
        if not isinstance(manifest, dict):
            reasons.append("evidence_manifest_missing")
        else:
            manifest_status = str(manifest.get("status") or "").strip().lower()
            if manifest_status in {"revoked", "invalidated"}:
                reasons.append(f"evidence_manifest_{manifest_status}")
            package_type = str(manifest.get("package_type") or "").strip().lower()
            signature_status = str(manifest.get("signature_status") or "").strip().lower()
            verification_status = str(manifest.get("verification_status") or "").strip().lower()
            if package_type in {"signed_manifest", "vc_evidence_bundle"}:
                if signature_status == "unsigned":
                    reasons.append("evidence_manifest_unsigned")
                if verification_status != "verified":
                    reasons.append("evidence_manifest_unverified")
                elif _is_stale_check(manifest.get("verification_checked_at"), max_age_days=30):
                    reasons.append("evidence_manifest_verification_stale")
    status = "invalidated" if reasons else "active"
    enriched = dict(standing_view)
    enriched["current_validation_status"] = status
    enriched["current_invalidation_reasons"] = reasons
    return enriched


def replay_authority_state(
    db: Any,
    *,
    authority_subject_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    authority_filter = str(authority_subject_id or "").strip()
    replayed: dict[str, dict[str, Any]] = {}
    events = load_authority_events(db)
    ordered = sorted(
        (dict(row) for row in events.values() if isinstance(row, dict)),
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("event_id") or ""),
        ),
    )
    for event in ordered:
        subject_id = str(event.get("authority_subject_id") or "").strip()
        if not subject_id:
            continue
        if authority_filter and subject_id != authority_filter:
            continue
        replayed[subject_id] = _materialize_event(replayed.get(subject_id), event)
        replayed[subject_id] = _apply_current_validation(
            db,
            event=event,
            standing_view=replayed[subject_id],
        )

    persisted = load_authority_state(db)
    if authority_filter:
        if authority_filter in replayed:
            persisted[authority_filter] = replayed[authority_filter]
        else:
            persisted.pop(authority_filter, None)
    else:
        persisted = replayed
    persist_authority_state(db, persisted)
    return replayed


def _validate_subject_transition_ref(
    db: Any,
    *,
    authority_subject_id: str,
    subject_transition_event_ref: str | None = None,
) -> str | None:
    ref = str(subject_transition_event_ref or "").strip()
    if not ref:
        return None
    event = get_subject_event(db, ref)
    if not isinstance(event, dict):
        raise ValueError("subject_transition_event_ref did not resolve to a stored subject event")
    resulting = str(event.get("resulting_authority_subject_id") or "").strip()
    if resulting and resulting != authority_subject_id:
        raise ValueError("subject_transition_event_ref does not match authority_subject_id")
    return ref


def append_authority_event(
    db: Any,
    *,
    authority_subject_id: str,
    event_type: str,
    issuer: str,
    reason_code: str,
    delta: dict[str, Any] | None = None,
    evidence_refs: list[str] | None = None,
    idempotency_key: str,
    principal_did: str | None = None,
    canonical_subject: str | None = None,
    credential_ref: str | None = None,
    standing_envelope_ref: str | None = None,
    subject_transition_event_ref: str | None = None,
    event_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    subject_id = str(authority_subject_id or "").strip()
    if not subject_id:
        raise ValueError("authority_subject_id is required")
    normalized_event_type = _validate_event_type(event_type)
    normalized_issuer, issuer_authority = _validate_issuer(db, issuer, normalized_event_type)
    normalized_reason = _validate_reason_code(reason_code)
    normalized_nonce = str(idempotency_key or "").strip()
    if not normalized_nonce:
        raise ValueError("idempotency_key is required")
    normalized_evidence_refs = _validate_evidence_refs(normalized_event_type, evidence_refs)
    evidence_requirement = str(issuer_authority.get("evidence_requirement") or "").strip().lower()
    if evidence_requirement == "required" and not normalized_evidence_refs:
        raise ValueError("issuer authority requires evidence_refs")
    normalized_delta = _normalize_delta(delta)
    normalized_transition_ref = _validate_subject_transition_ref(
        db,
        authority_subject_id=subject_id,
        subject_transition_event_ref=subject_transition_event_ref,
    )

    authority_events = load_authority_events(db)
    for row in authority_events.values():
        if not isinstance(row, dict):
            continue
        if str(row.get("authority_subject_id") or "").strip() != subject_id:
            continue
        if str(row.get("issuer") or "").strip() != normalized_issuer:
            continue
        if str(row.get("idempotency_key") or "").strip() == normalized_nonce:
            raise RuntimeError(f"authority_event already recorded: {normalized_issuer}:{normalized_nonce}")

    resolved_event_id = str(event_id or f"aevt:{uuid.uuid4().hex}").strip()
    if resolved_event_id in authority_events:
        raise RuntimeError(f"authority_event already recorded: {resolved_event_id}")
    created_at = _now_iso()
    manifest = upsert_evidence_manifest(
        db,
        issuer=normalized_issuer,
        evidence_refs=normalized_evidence_refs,
        authority_subject_id=subject_id,
    ) if normalized_evidence_refs else None
    manifest_hash = str((manifest or {}).get("manifest_hash") or "").strip() or None
    _reject_duplicate_evidence_reuse(
        db,
        authority_subject_id=subject_id,
        issuer=normalized_issuer,
        event_type=normalized_event_type,
        manifest_hash=manifest_hash,
    )
    event = {
        "event_id": resolved_event_id,
        "authority_subject_id": subject_id,
        "event_type": normalized_event_type,
        "issuer": normalized_issuer,
        "issuer_class": str(issuer_authority.get("issuer_class") or "").strip() or None,
        "issuer_authority_ref": normalized_issuer,
        "issuer_credential_ref": str(issuer_authority.get("credential_ref") or "").strip() or None,
        "issuer_did": str(issuer_authority.get("issuer_did") or "").strip() or None,
        "issuer_identity_anchor_ref": str(issuer_authority.get("identity_anchor_ref") or "").strip() or None,
        "issuer_trust_basis": str(issuer_authority.get("trust_basis") or "").strip() or None,
        "issuer_verification_state": str(issuer_authority.get("verification_state") or "").strip() or None,
        "issuer_vc_type": str(issuer_authority.get("vc_type") or "").strip() or None,
        "issuer_vc_id": str(issuer_authority.get("vc_id") or "").strip() or None,
        "issuer_vc_envelope": dict(issuer_authority.get("vc_envelope") or {}) if isinstance(issuer_authority.get("vc_envelope"), dict) else None,
        "issuer_credential_status_ref": str(issuer_authority.get("credential_status_ref") or "").strip() or None,
        "issuer_credential_status_state": str(issuer_authority.get("credential_status_state") or "").strip() or None,
        "issuer_credential_status_checked_at": str(issuer_authority.get("credential_status_checked_at") or "").strip() or None,
        "reason_code": normalized_reason,
        "delta": normalized_delta,
        "evidence_refs": normalized_evidence_refs,
        "evidence_manifest_ref": str((manifest or {}).get("manifest_ref") or "").strip() or None,
        "evidence_manifest_hash": manifest_hash,
        "idempotency_key": normalized_nonce,
        "principal_did": str(principal_did or "").strip() or None,
        "canonical_subject": str(canonical_subject or "").strip() or None,
        "credential_ref": str(credential_ref or "").strip() or None,
        "standing_envelope_ref": str(standing_envelope_ref or "").strip() or None,
        "subject_transition_event_ref": normalized_transition_ref,
        "created_at": created_at,
    }
    authority_events[resolved_event_id] = event
    persist_authority_events(db, authority_events)

    authority_state = load_authority_state(db)
    current = authority_state.get(subject_id)
    standing_view = _materialize_event(current, event)
    standing_view = _apply_current_validation(
        db,
        event=event,
        standing_view=standing_view,
    )
    authority_state[subject_id] = standing_view
    persist_authority_state(db, authority_state)
    return dict(standing_view), dict(event)
