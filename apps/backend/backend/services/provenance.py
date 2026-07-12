"""Provenance helpers for ledger writes."""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import Request

from backend.fieldx_kernel.mmf_foundation import CANONICAL_MMF_PRIME_TOPOLOGIES, MMF_DOMAIN_SET
from backend.services.subject_events import get_subject_event


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _state_claim(request: Request, attr: str) -> str | None:
    state = getattr(request, "state", None)
    if state is None:
        return None
    return _clean(getattr(state, attr, None))


def _claim_from_request(request: Request, *, state_attr: str, headers: tuple[str, ...]) -> str | None:
    from_state = _state_claim(request, state_attr)
    if from_state:
        return from_state
    for header in headers:
        value = _clean(request.headers.get(header))
        if value:
            return value
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


_RETENTION_TIERS = {"Sand", "Silt", "Loam", "Clay"}


def _build_retention_tier(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    meta = _as_dict(metadata)
    explicit_tier = _clean(meta.get("retention_tier"))
    if explicit_tier in _RETENTION_TIERS:
        return {
            "retention_tier": explicit_tier,
            "retention_tier_reason": "metadata_override",
        }

    kind = _clean(meta.get("kind")) or ""
    input_mode = _clean(meta.get("input_mode")) or ""
    streaming = bool(meta.get("streaming"))

    if kind in {
        "stream_ingress",
        "audio_stream",
        "video_stream",
        "surface_recognition",
        "multimodal_window",
    } or (input_mode in {"audio", "video", "multimodal"} and streaming):
        return {
            "retention_tier": "Sand",
            "retention_tier_reason": "high_velocity_multimodal_or_streaming_ingress",
        }

    if kind in {
        "autonomy_pattern",
        "learned_autonomy_profile",
        "profile_delta",
        "consent_snapshot",
        "session_continuity",
        "working_profile_state",
    }:
        return {
            "retention_tier": "Silt",
            "retention_tier_reason": "active_continuity_or_working_profile_state",
        }

    if kind in {
        "draft",
        "pending_commit",
        "candidate",
        "uncommitted_turn",
        "fertile_pending",
    }:
        return {
            "retention_tier": "Loam",
            "retention_tier_reason": "fertile_pending_decay_candidate",
        }

    return {
        "retention_tier": "Clay",
        "retention_tier_reason": "durable_ledger_write_path",
    }


def _build_gravity_tax_policy(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    meta = _as_dict(metadata)
    retention = _build_retention_tier(meta)
    gravity_cost = meta.get("gravity_cost")
    gravity_penalty = meta.get("gravity_penalty")
    try:
        gravity_cost_value = float(gravity_cost) if gravity_cost is not None else None
    except (TypeError, ValueError):
        gravity_cost_value = None
    try:
        gravity_penalty_value = float(gravity_penalty) if gravity_penalty is not None else None
    except (TypeError, ValueError):
        gravity_penalty_value = None

    retention_tier = str(retention.get("retention_tier") or "Clay")
    governed_promotion_required = retention_tier in {"Silt", "Clay"}
    retention_tier_assignment = (
        _clean(meta.get("retention_tier_assignment"))
        or {
            "Sand": "high_velocity_ephemeral_ingress",
            "Silt": "active_continuity_working_set",
            "Loam": "fertile_pending_decay_candidate",
            "Clay": "durable_governed_memory_boundary",
        }.get(retention_tier, "durable_governed_memory_boundary")
    )
    gravity_tax_accrual = (
        _clean(meta.get("gravity_tax_accrual"))
        or {
            "Sand": "accruing_ephemeral_drain_pressure",
            "Silt": "accruing_bounded_carry_forward_pressure",
            "Loam": "accruing_fertile_decay_pressure",
            "Clay": "accruing_durable_governance_cost",
        }.get(retention_tier, "accruing_durable_governance_cost")
    )
    retention_decision_state = (
        _clean(meta.get("retention_decision_state"))
        or {
            "Sand": "evict_or_rolloff_unless_promoted",
            "Silt": "carry_forward_under_review",
            "Loam": "decay_or_promote_after_review",
            "Clay": "durable_keep",
        }.get(retention_tier, "durable_keep")
    )
    promotion_state = (
        _clean(meta.get("promotion_state"))
        or {
            "Sand": "promotion_optional",
            "Silt": "governed_promotion_required",
            "Loam": "governed_promotion_required",
            "Clay": "already_durable",
        }.get(retention_tier, "already_durable")
    )
    consolidation_readiness = (
        _clean(meta.get("consolidation_readiness"))
        or {
            "Sand": "not_ready",
            "Silt": "review_pending",
            "Loam": "review_pending",
            "Clay": "ready_when_governed_boundary_requests_merge",
        }.get(retention_tier, "ready_when_governed_boundary_requests_merge")
    )
    return {
        "gravity_tax_contract_version": "gravity-tax-v1",
        "explicit_retention_cost_policy": True,
        "retention_tier": retention_tier,
        "retention_tier_reason": str(retention.get("retention_tier_reason") or ""),
        "retention_tier_assignment": retention_tier_assignment,
        "gravity_cost": gravity_cost_value,
        "gravity_penalty": gravity_penalty_value,
        "gravity_tax_accrual": gravity_tax_accrual,
        "retention_decision_state": retention_decision_state,
        "governed_promotion_required": governed_promotion_required,
        "promotion_state": promotion_state,
        "consolidation_readiness": consolidation_readiness,
        "anti_hoarding_posture": "selective_retention_over_silent_accumulation",
        "noisy_or_low_coherence_drains_by_default": True,
        "cost_inputs": {
            "eq4_coupling_live_input": True,
            "eq5_persistence_cost_live_input": True,
        },
    }


def _canonical_subject_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    meta = _as_dict(metadata)
    standing_envelope = _as_dict(meta.get("standing_envelope"))
    model_auth = _as_dict(meta.get("model_auth_context"))
    auth_identity = _as_dict(model_auth.get("identity_vc"))
    auth_standing = _as_dict(model_auth.get("standing_envelope"))

    canonical_subject = (
        _clean(meta.get("canonical_subject"))
        or _clean(standing_envelope.get("canonical_subject"))
        or _clean(auth_standing.get("canonical_subject"))
        or _clean(auth_identity.get("canonical_subject"))
    )
    canonical_subject_source = (
        _clean(meta.get("canonical_subject_source"))
        or _clean(standing_envelope.get("canonical_subject_source"))
        or _clean(auth_standing.get("canonical_subject_source"))
        or _clean(auth_identity.get("canonical_subject_source"))
    )
    return canonical_subject, canonical_subject_source


def resolve_authority_subject(
    request: Request,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_subject, canonical_subject_source = _canonical_subject_from_metadata(metadata)
    principal_did = _claim_from_request(
        request,
        state_attr="auth_claim_principal_did",
        headers=("x-principal-did", "x-did"),
    )
    if canonical_subject:
        return {
            "authority_subject_id": f"subject:{canonical_subject}",
            "authority_subject_source": "canonical_subject",
            "canonical_subject": canonical_subject,
            "canonical_subject_source": canonical_subject_source,
            "principal_did": principal_did,
        }
    if principal_did:
        return {
            "authority_subject_id": f"subject:{principal_did}",
            "authority_subject_source": "principal_did",
            "canonical_subject": None,
            "canonical_subject_source": None,
            "principal_did": principal_did,
        }
    return {
        "authority_subject_id": None,
        "authority_subject_source": None,
        "canonical_subject": canonical_subject,
        "canonical_subject_source": canonical_subject_source,
        "principal_did": principal_did,
    }


def normalize_subject_transition(
    request: Request,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = _as_dict(metadata)
    authority = resolve_authority_subject(request, metadata=meta)
    current_authority_subject_id = _clean(authority.get("authority_subject_id"))
    prior_authority_subject_id = (
        _clean(meta.get("prior_authority_subject_id"))
        or _clean(meta.get("authority_subject_id_prior"))
    )
    transition_event_ref = _clean(meta.get("subject_transition_event_ref"))
    transition_type = _clean(meta.get("subject_transition_type")) or "subject_change"
    standing_carryover = _clean(meta.get("standing_carryover"))
    credential_carryover = _clean(meta.get("credential_carryover"))
    db = getattr(getattr(request, "app", None), "state", None)
    db = getattr(db, "db", None)

    if prior_authority_subject_id and current_authority_subject_id and prior_authority_subject_id != current_authority_subject_id:
        if not transition_event_ref:
            raise ValueError("subject_transition_event_ref is required when authority subject changes")
        event = get_subject_event(db, transition_event_ref) if db is not None else None
        if not isinstance(event, dict):
            raise ValueError("subject_transition_event_ref did not resolve to a stored subject event")
        event_prior = _clean(event.get("prior_authority_subject_id"))
        event_result = _clean(event.get("resulting_authority_subject_id"))
        event_type = _clean(event.get("event_type"))
        if event_prior and event_prior != prior_authority_subject_id:
            raise ValueError("subject transition prior authority does not match stored event")
        if event_result and event_result != current_authority_subject_id:
            raise ValueError("subject transition resulting authority does not match stored event")
        if event_type and event_type != transition_type:
            raise ValueError("subject transition type does not match stored event")
        return {
            "authority_subject_id": current_authority_subject_id,
            "authority_subject_source": authority.get("authority_subject_source"),
            "prior_authority_subject_id": prior_authority_subject_id,
            "subject_transition_type": transition_type,
            "subject_transition_event_ref": transition_event_ref,
            "subject_transition_event_validated": True,
            "subject_transition_review_required": True,
            "standing_carryover": standing_carryover or _clean(event.get("standing_carryover")) or "probation",
            "credential_carryover": credential_carryover or _clean(event.get("credential_carryover")) or "review_required",
        }

    if transition_event_ref:
        event = get_subject_event(db, transition_event_ref) if db is not None else None
        if not isinstance(event, dict):
            raise ValueError("subject_transition_event_ref did not resolve to a stored subject event")
        event_result = _clean(event.get("resulting_authority_subject_id"))
        event_type = _clean(event.get("event_type"))
        if event_result and current_authority_subject_id and event_result != current_authority_subject_id:
            raise ValueError("subject transition resulting authority does not match stored event")
        if event_type and event_type != transition_type:
            raise ValueError("subject transition type does not match stored event")
        event_prior = _clean(event.get("prior_authority_subject_id"))
        return {
            "authority_subject_id": current_authority_subject_id,
            "authority_subject_source": authority.get("authority_subject_source"),
            "prior_authority_subject_id": prior_authority_subject_id or event_prior,
            "subject_transition_type": transition_type,
            "subject_transition_event_ref": transition_event_ref,
            "subject_transition_event_validated": True,
            "subject_transition_review_required": bool(
                (prior_authority_subject_id or event_prior) and (prior_authority_subject_id or event_prior) != current_authority_subject_id
            ),
            "standing_carryover": standing_carryover or _clean(event.get("standing_carryover")) or "inherit",
            "credential_carryover": credential_carryover or _clean(event.get("credential_carryover")) or "inherit",
        }

    return {
        "authority_subject_id": current_authority_subject_id,
        "authority_subject_source": authority.get("authority_subject_source"),
    }


def _principal_from_request(request: Request) -> tuple[str, str]:
    principal_did = _claim_from_request(
        request,
        state_attr="auth_claim_principal_did",
        headers=("x-principal-did", "x-did"),
    )
    principal_id = (
        _clean(request.headers.get("x-principal-id"))
        or _clean(request.headers.get("x-user-id"))
        or principal_did
        or "anonymous"
    )
    principal_type = _clean(request.headers.get("x-principal-type")) or "service"
    return principal_id, principal_type


def build_write_provenance(
    request: Request,
    *,
    ledger_id: str,
    metadata: Mapping[str, Any] | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    context_id: str | None = None,
) -> dict[str, Any]:
    """Build canonical provenance fields for persisted metadata."""
    principal_id, principal_type = _principal_from_request(request)
    context = _clean(context_id) or _state_claim(request, "context_id") or _clean(request.headers.get("x-context-id"))
    contributor_id = f"{principal_type}:{principal_id}"

    payload: dict[str, Any] = {
        "ledger_id": str(ledger_id),
        "contributor_id": contributor_id,
        "contributor": {
            "principal_id": principal_id,
            "principal_type": principal_type,
        },
    }
    principal_did = _claim_from_request(
        request,
        state_attr="auth_claim_principal_did",
        headers=("x-principal-did", "x-did"),
    )
    principal_key_id = _claim_from_request(
        request,
        state_attr="auth_claim_principal_key_id",
        headers=("x-principal-key-id", "x-key-id"),
    )
    session_jti = _claim_from_request(
        request,
        state_attr="auth_claim_session_jti",
        headers=("x-session-jti", "x-auth-jti"),
    )
    auth_method = _state_claim(request, "auth_claim_auth_method") or _clean(request.headers.get("x-auth-method"))
    subject_identity = resolve_authority_subject(request, metadata=metadata)
    canonical_subject = _clean(subject_identity.get("canonical_subject"))
    canonical_subject_source = _clean(subject_identity.get("canonical_subject_source"))
    if principal_did or principal_key_id or session_jti or auth_method:
        contributor = payload.get("contributor")
        if not isinstance(contributor, dict):
            contributor = {}
        if principal_did:
            contributor["principal_did"] = principal_did
        if principal_key_id:
            contributor["principal_key_id"] = principal_key_id
        if session_jti:
            contributor["session_jti"] = session_jti
        payload["contributor"] = contributor
        if auth_method:
            payload["auth_method"] = auth_method
    if canonical_subject:
        payload["canonical_subject"] = canonical_subject
        authority_subject_id = str(subject_identity.get("authority_subject_id") or "").strip()
        payload["authority_subject_id"] = authority_subject_id
        contributor = payload.get("contributor")
        if not isinstance(contributor, dict):
            contributor = {}
        contributor["canonical_subject"] = canonical_subject
        contributor["authority_subject_id"] = authority_subject_id
        if canonical_subject_source:
            contributor["canonical_subject_source"] = canonical_subject_source
            payload["canonical_subject_source"] = canonical_subject_source
        payload["contributor"] = contributor
    elif subject_identity.get("authority_subject_id"):
        authority_subject_id = str(subject_identity.get("authority_subject_id") or "").strip()
        payload["authority_subject_id"] = authority_subject_id
        payload["authority_subject_source"] = str(subject_identity.get("authority_subject_source") or "").strip() or None
        contributor = payload.get("contributor")
        if not isinstance(contributor, dict):
            contributor = {}
        contributor["authority_subject_id"] = authority_subject_id
        payload["contributor"] = contributor
    payload["provenance_dual_write"] = _provenance_dual_write_status(
        contributor_id=contributor_id,
        contributor=payload.get("contributor"),
    )
    if context:
        payload["context_id"] = context
    if _clean(session_id):
        payload["session_id"] = str(session_id).strip()
    if _clean(turn_id):
        payload["turn_id"] = str(turn_id).strip()
    if _clean(provider_id):
        payload["provider_id"] = str(provider_id).strip()
    if _clean(model_id):
        payload["model_id"] = str(model_id).strip()
    payload.update(_build_retention_tier(metadata))
    payload["gravity_tax_policy"] = _build_gravity_tax_policy(metadata)
    return payload


def build_taxonomy_provenance(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = metadata if isinstance(metadata, dict) else {}
    domain = _extract_taxonomy_domain(payload)
    topology_ref = domain or "kernel"
    topology = CANONICAL_MMF_PRIME_TOPOLOGIES[topology_ref]
    return {
        "taxonomy_mode": "indefeasible",
        "taxonomy_version": "mmf-projection-v1",
        "topology_ref": topology_ref,
        "domain": domain,
        "anchor_nodes": list(topology.anchor_nodes),
        "anchor_primes": list(topology.anchor_primes),
        "extension_primes": list(topology.extension_primes),
        "cube_primes": list(topology.cube_primes),
    }


def _extract_taxonomy_domain(metadata: dict[str, Any]) -> str | None:
    candidates = [
        metadata.get("mmf_domain"),
        metadata.get("domain"),
    ]
    projection = metadata.get("projection") if isinstance(metadata.get("projection"), dict) else {}
    if projection:
        candidates.extend([projection.get("domain"), projection.get("topology_ref")])
    for candidate in candidates:
        cleaned = _clean(candidate)
        if cleaned and cleaned in MMF_DOMAIN_SET:
            return cleaned
    return None


def _provenance_dual_write_status(
    *,
    contributor_id: str,
    contributor: Any,
) -> dict[str, Any]:
    contributor_map = contributor if isinstance(contributor, dict) else {}
    principal_id = _clean(contributor_map.get("principal_id"))
    principal_type = _clean(contributor_map.get("principal_type"))
    expected_contributor_id = (
        f"{principal_type}:{principal_id}" if principal_id and principal_type else None
    )
    legacy_tuple_present = bool(principal_id and principal_type and expected_contributor_id == contributor_id)
    principal_did = _clean(contributor_map.get("principal_did"))
    principal_key_id = _clean(contributor_map.get("principal_key_id"))
    session_jti = _clean(contributor_map.get("session_jti"))
    did_fields_present = bool(principal_did)
    if legacy_tuple_present and did_fields_present:
        status = "dual_write_ok"
    elif legacy_tuple_present and not did_fields_present:
        status = "legacy_only"
    elif did_fields_present and not legacy_tuple_present:
        status = "did_only"
    else:
        status = "missing_identity"
    return {
        "status": status,
        "legacy_tuple_present": legacy_tuple_present,
        "did_fields_present": did_fields_present,
        "principal_did_present": bool(principal_did),
        "principal_key_id_present": bool(principal_key_id),
        "session_jti_present": bool(session_jti),
    }
