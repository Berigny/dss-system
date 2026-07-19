"""REST endpoint for chat interactions and library retrieval."""

from __future__ import annotations

import contextvars
import hashlib
import logging
import re
import time
import json
import os
import uuid
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.api.http import get_memory_ledger, get_memory_substrate, parse_key
from backend.api.agent_writes import record_full_payload_blob, record_turn
from backend.config.settings import QP_PURE_OVERRIDE
from backend.api.schemas import (
    ChatAssessmentRequest,
    ChatAssessmentResponse,
    ChatCommitRequest,
    ChatCommitResponse,
    ChatGroundingGuardRequest,
    ChatGroundingGuardResponse,
    ChatRequest,
    ChatResponse,
    LedgerKeySchema,
)
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.orchestrator import (
    DEFAULT_CHAT_MODEL,
    assemble_context,
    build_chat_messages,
    complete_chat,
    enrich_turn,
    yield_chat_stream,
    COORD_PATTERN,
)
from backend.fieldx_kernel.governance_engine import CoherenceException
from backend.fieldx_kernel.guardian import guardian_enrich_turn
from backend.fieldx_kernel.ledger import allow_mediator_writes
from backend.fieldx_kernel.state import GRACE_PRIME, LAW_PRIME
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.fieldx_kernel.coord_walk import coord_walk
from openai.types.chat import ChatCompletionMessageParam
from shared_types.coord_schema import sanitize_coordinate_metadata
from backend.fieldx_kernel.kernel_origin_equations import equation_6_operational
from backend.fieldx_kernel.eval_ladder import evaluate_eq_ladder
from backend.fieldx_kernel.temporal import get_entity_engine
from backend.search.token_index import TokenPrimeIndex
from backend.utils.coord import normalise_coord, namespace_candidates
from backend.utils.resolve_format import (
    build_governance,
    build_interpretation,
    build_payload_for_parts,
    build_payload_for_text,
    build_refs,
    coord_type,
    resolve_response,
)
from backend.fieldx_kernel.models import LedgerKey
from backend.utils.knowledge_tree import merge_knowledge_trees, normalize_knowledge_tree_item
from backend.utils.normalise import normalise_text
from backend.metrics.pricing import estimate_cost_usd
from backend.metrics.store import TelemetryStore
from backend.metrics.prod_benchmark_contract import SurfaceName
from backend.metrics.telemetry import (
    RetrievalPath,
    TelemetryIds,
    TelemetryReferences,
    TelemetrySearchFlags,
    TurnTelemetry,
)
from backend.utils.assurance import hash_history_from_metadata, verify_assurance_envelope
from backend.services.authz import (
    apply_auth_claim_overrides,
    authorize_or_raise,
    authz_diagnostics_from_request,
)
from backend.services.context_scope import resolve_context_id_or_raise
from backend.services.ledger_scope import resolve_ledger_scope_or_raise
from backend.services.namespace_policy import resolve_write_namespace
from backend.services.surface_scope import assert_surface_ledger_access
from backend.services.provenance import (
    _build_gravity_tax_policy,
    build_write_provenance,
    normalize_subject_transition,
    resolve_authority_subject,
)
from backend.services.ledger_service import LedgerService
from backend.services.pilot_account import enforce_pilot_write_allowed
from backend.services.public_objects import upsert_public_object
from backend.services.standing_policy import clamp_max_tokens, resolve_standing_policy
from backend.services.authority_events import get_authority_state

router = APIRouter(prefix="/chat", tags=["chat"])
assess_router = APIRouter(prefix="/api/chat", tags=["chat"])
LOGGER = logging.getLogger(__name__)


def _set_qp_pure_override(req: ChatRequest) -> contextvars.Token | None:
    """Set the per-request Qp-pure override, if the client provided one."""
    if req.qp_pure is None:
        return None
    return QP_PURE_OVERRIDE.set(req.qp_pure)

SESSION_METRICS: dict[str, dict[str, float]] = {}
SESSION_PROMPT_STATE: dict[str, dict[str, object]] = {}
KNOWLEDGE_TREE_LIMIT = int(os.getenv("KNOWLEDGE_TREE_LIMIT", "50"))
COORDS_ONLY_MODE = os.getenv("COORDS_ONLY_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
EQ6_TWO_PASS = os.getenv("EQ6_TWO_PASS", "true").strip().lower() in {"1", "true", "yes", "on"}
EQ6_DRAFT_MAX_TOKENS = int(os.getenv("EQ6_DRAFT_MAX_TOKENS", "120"))
LOOP_SENSITIVITY = float(os.getenv("LOOP_SENSITIVITY", "0.6"))
AUTONOMY_POLICY_RAW = os.getenv("AUTONOMY_POLICY", "balanced").strip().lower()
CHAT_MAX_TOKENS_DEFAULT = int(os.getenv("CHAT_MAX_TOKENS_DEFAULT", "512"))
CHAT_MAX_TOKENS_FAST = int(os.getenv("CHAT_MAX_TOKENS_FAST", "320"))
CHAT_MAX_TOKENS_MED = int(os.getenv("CHAT_MAX_TOKENS_MED", "448"))
ASSURANCE_ENFORCE = os.getenv("ASSURANCE_ENFORCE", "0").strip().lower() in {"1", "true", "yes", "on"}
ASSURANCE_CHALLENGE_REQUIRED = os.getenv("ASSURANCE_CHALLENGE_REQUIRED", "0").strip().lower() in {"1", "true", "yes", "on"}
PRE_EMISSION_DENY_STRICT = os.getenv("PRE_EMISSION_DENY_STRICT", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_CLOSURE_PHRASES = (
    "this proves",
    "this is it",
    "completion",
    "awakening",
    "mandate",
    "destined",
    "inevitable",
    "frame collapse",
    "self-validating",
    "self sealing",
    "self-sealing",
)

_AUTONOMY_ACTIONS = ("resolve", "reuse_path", "answer_from_priors", "request_new_candidate_set")
_DIAGNOSTIC_TOP_K = 4


def _json_dumps_coordinate_safe(obj: Any, **kwargs: Any) -> str:
    """json.dumps wrapper that stringifies BigInt coordinate fields."""
    return json.dumps(sanitize_coordinate_metadata(obj), **kwargs)


_RESOLUTION_CONTRADICTION_PATTERNS = (
    re.compile(r"\b(?:i|we)\s+(?:cannot|can't|can not|do not|don't)\s+(?:access|resolve|retrieve|open|load|see)\b", re.IGNORECASE),
    re.compile(r"\b(?:no|not)\s+(?:access|visibility|ability)\s+to\s+(?:the|that)\s+(?:coord|content|thread|context)\b", re.IGNORECASE),
    re.compile(r"\b(?:do not|don't|cannot|can't)\s+have\s+(?:the|this|that)\s+(?:content|messages|context)\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+(?:the|this|that)\s+(?:content|messages|context)\s*,?\s*i\s+cannot\b", re.IGNORECASE),
)
_CANONICAL_WEB4_RE = re.compile(r"^WX-[A-Za-z0-9]+-\d+(?:-[A-Za-z0-9]+)*$")
_LITE_WEB4_RE = re.compile(r"^WX-(\d+)$")

_EQ6_DRAFT_SYSTEM = (
    "You are drafting an internal response for coherence gating. "
    "Be concise (2-4 sentences). Do not include COORDs unless essential."
)


def _candidate_catalog_limit() -> int:
    raw = os.getenv("COORD_CATALOG_LIMIT", "4")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 4
    return max(1, value)


def _candidate_metadata(item: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = item.get("state", {}).get("metadata", {}) if isinstance(item.get("state"), Mapping) else {}
    if not isinstance(metadata, Mapping) and isinstance(item.get("metadata"), Mapping):
        metadata = cast(Mapping[str, Any], item.get("metadata"))
    return metadata if isinstance(metadata, Mapping) else {}


def _candidate_origin_attestation(item: Mapping[str, Any], *, coord: str | None = None) -> str:
    source = str(item.get("source") or "").strip().lower()
    if bool(item.get("explicit") or item.get("explicit_mention")) or source == "explicit":
        return "explicit_user_referenced_coord"

    metadata = _candidate_metadata(item)
    if metadata.get("attachment_part"):
        return "user_attachment_part"
    if metadata.get("attachment") or metadata.get("attachment_summary") or metadata.get("attachment_group"):
        return "user_attachment_parent"

    coord_type_value = coord_type(coord or _canonical_coord_from_item(item) or "") if coord or _canonical_coord_from_item(item) else "UNK"
    kind = str(metadata.get("kind") or item.get("kind") or "").strip().lower()
    role = str(metadata.get("role") or item.get("role") or "").strip().lower()
    if coord_type_value == "WX" and source in {"recent", "retrieved"} and role != "user":
        return "model_response_wx"
    if role == "user" or (source == "recent" and kind in {"chat", "turn"}):
        return "user_message"
    if coord_type_value == "PL" or kind == "overlay":
        return "telemetry_overlay"
    return "system_runtime_witness"


def _candidate_payload_state(item: Mapping[str, Any]) -> str:
    if bool(item.get("resolved_payload_present") or item.get("payload_loaded")):
        return "opened"
    metadata = _candidate_metadata(item)
    if any(isinstance(metadata.get(key), str) and str(metadata.get(key)).strip() for key in ("summary", "attachment_summary", "content", "assistant_reply")):
        return "skimmed"
    if metadata.get("attachment_part") or metadata.get("attachment"):
        return "sealed"
    return "sealed"


def _candidate_p_adic_score(item: Mapping[str, Any]) -> float:
    raw = item.get("p_adic_score")
    if not isinstance(raw, (int, float)):
        raw = item.get("ancestry_score")
    if not isinstance(raw, (int, float)):
        raw = item.get("p_adic_similarity")
    return round(float(raw), 3) if isinstance(raw, (int, float)) else 0.0


def _candidate_search_score(item: Mapping[str, Any]) -> float:
    raw = item.get("search_score")
    if not isinstance(raw, (int, float)):
        raw = item.get("score")
    if not isinstance(raw, (int, float)):
        raw = item.get("relevance_score")
    return round(float(raw), 3) if isinstance(raw, (int, float)) else 0.0


def _candidate_relevance_tier(
    item: Mapping[str, Any],
    *,
    origin_attestation: str,
    p_adic_score: float,
    search_score: float,
    recency_score: float,
) -> int:
    if bool(item.get("explicit") or item.get("explicit_mention")) or origin_attestation == "explicit_user_referenced_coord":
        return 1
    if origin_attestation in {"user_attachment_parent", "user_attachment_part"}:
        return 2
    if origin_attestation == "model_response_wx":
        return 4
    if max(p_adic_score, search_score, recency_score) >= 0.65 or bool(item.get("associated_attachment")):
        return 3
    return 4


def _candidate_origin_eligibility(origin_attestation: str, relevance_tier: int) -> float:
    if relevance_tier <= 2:
        return 1.0
    if origin_attestation == "model_response_wx":
        return 0.25
    return 0.5 if origin_attestation == "user_message" else 0.15


def _candidate_skip_reason(
    item: Mapping[str, Any],
    *,
    origin_attestation: str,
    relevance_tier: int,
    p_adic_score: float,
    search_score: float,
    recency_score: float,
) -> str | None:
    if origin_attestation == "model_response_wx" and not bool(item.get("explicit") or item.get("explicit_mention")):
        return "assistant_output_demoted_to_continuity_lane"
    signal = max(p_adic_score, search_score, recency_score)
    if relevance_tier >= 4 and signal < 0.35:
        return "insufficient_p_adic_search_recency_signal"
    return None


def _candidate_recommended_action(
    item: Mapping[str, Any],
    *,
    origin_attestation: str,
    relevance_tier: int,
    payload_state: str,
    skip_reason: str | None,
) -> str:
    coord_value = _canonical_coord_from_item(item)
    coord_type_value = coord_type(coord_value) if coord_value else "UNK"
    if skip_reason == "assistant_output_demoted_to_continuity_lane":
        return "walk_referenced_coord"
    if skip_reason == "insufficient_p_adic_search_recency_signal" and relevance_tier >= 4:
        return "skip"
    if relevance_tier == 1:
        return "reuse_already_opened" if payload_state == "opened" else "open"
    if relevance_tier == 2:
        if coord_type_value == "ATT-T":
            return "walk_child"
        return "reuse_already_opened" if payload_state == "opened" else "open"
    if relevance_tier == 3:
        return "reuse_already_opened" if payload_state == "opened" else "open"
    if origin_attestation == "model_response_wx":
        return "walk_referenced_coord"
    return "skip"


def _candidate_surface_row(item: Mapping[str, Any]) -> dict[str, Any]:
    coord = _canonical_coord_from_item(item)
    if not isinstance(coord, str) or not coord.strip():
        return {}
    score = _coerce_float(item.get("relevance_score"), 0.0)
    tier = item.get("tier_rank")
    tier_rank = max(0, min(3, int(tier))) if isinstance(tier, (int, float)) else max(0, min(3, int(round(score * 3))))
    p_adic_score = _candidate_p_adic_score(item)
    search_score = _candidate_search_score(item)
    recency_score = _coerce_float(item.get("recency_score"), 0.0)
    origin_attestation = _candidate_origin_attestation(item, coord=coord)
    relevance_tier = _candidate_relevance_tier(
        item,
        origin_attestation=origin_attestation,
        p_adic_score=p_adic_score,
        search_score=search_score,
        recency_score=recency_score,
    )
    payload_state = _candidate_payload_state(item)
    skip_reason = _candidate_skip_reason(
        item,
        origin_attestation=origin_attestation,
        relevance_tier=relevance_tier,
        p_adic_score=p_adic_score,
        search_score=search_score,
        recency_score=recency_score,
    )
    row: dict[str, Any] = {
        "coord": coord.strip(),
        "coord_type": coord_type(coord),
        "origin_attestation": origin_attestation,
        "origin_eligibility": round(_candidate_origin_eligibility(origin_attestation, relevance_tier), 3),
        "relevance_tier": max(1, min(4, relevance_tier)),
        "relevance_score": round(score, 3),
        "tier_rank": tier_rank,
        "p_adic_score": round(p_adic_score, 3),
        "search_score": round(search_score, 3),
        "recency_score": round(recency_score, 3),
        "payload_state": payload_state,
        "recommended_action": _candidate_recommended_action(
            item,
            origin_attestation=origin_attestation,
            relevance_tier=relevance_tier,
            payload_state=payload_state,
            skip_reason=skip_reason,
        ),
        "skip_reason": skip_reason,
        "resolved_payload_present": bool(item.get("resolved_payload_present") or item.get("payload_loaded")),
        "payload_loaded": bool(item.get("payload_loaded") or item.get("resolved_payload_present")),
        "source": str(item.get("source") or "retrieved"),
    }
    if isinstance(item.get("semantic_score"), (int, float)):
        row["semantic_score"] = round(float(item["semantic_score"]), 3)
    elif p_adic_score or search_score:
        row["semantic_score"] = round(float(max(p_adic_score, search_score)), 3)
    ancestry_score = item.get("ancestry_score")
    if not isinstance(ancestry_score, (int, float)):
        ancestry_score = item.get("p_adic_similarity")
    if isinstance(ancestry_score, (int, float)):
        row["ancestry_score"] = round(float(ancestry_score), 3)
        row["ancestry_linked"] = True
    elif bool(item.get("ancestry_linked")):
        row["ancestry_linked"] = True
    continuity_source = item.get("continuity_source")
    if isinstance(continuity_source, str) and continuity_source.strip():
        row["continuity_source"] = continuity_source.strip()
    return row


def _candidate_surface_sort_key(item: Mapping[str, Any]) -> tuple[float, float, float, float, float, float]:
    relevance_tier = int(item.get("relevance_tier") or 4)
    origin_priority = _candidate_origin_eligibility(str(item.get("origin_attestation") or ""), relevance_tier)
    return (
        float(relevance_tier),
        -origin_priority,
        -_coerce_float(item.get("p_adic_score"), 0.0),
        -_coerce_float(item.get("search_score"), 0.0),
        -_coerce_float(item.get("recency_score"), 0.0),
        -_coerce_float(item.get("relevance_score"), 0.0),
    )


def _should_include_system_prompts(
    session_id: str,
    provider: str,
    turn_count: int,
    interval: int = 7,
) -> bool:
    state = SESSION_PROMPT_STATE.setdefault(session_id, {"provider": None, "since_prompt": 0})
    provider_changed = state.get("provider") != provider
    new_session = turn_count <= 1
    if provider_changed or new_session:
        state["provider"] = provider
        state["since_prompt"] = 0
        return True
    since_prompt_raw = state.get("since_prompt")
    since_prompt = (since_prompt_raw if isinstance(since_prompt_raw, int) else 0) + 1
    if since_prompt >= interval:
        state["since_prompt"] = 0
        return True
    state["since_prompt"] = since_prompt
    return False


def _grace_note_from_flow(flow_enrich: dict[str, Any] | None) -> str | None:
    if not flow_enrich or not isinstance(flow_enrich, dict):
        return None
    lawfulness = flow_enrich.get("lawfulness_level")
    flow_ok = flow_enrich.get("flow_ok", True)
    if lawfulness is None:
        return None
    if lawfulness <= 1 or not flow_ok:
        return (
            "I can keep going, but I'm applying extra care and may avoid persisting certain content. "
            "Want to reframe?"
        )
    return None


async def _two_pass_eq6_gate(
    *,
    provider: str,
    messages: list[ChatCompletionMessageParam],
    retrieval_payload: Mapping[str, Any] | list[Any] | None,
    entity: str,
) -> dict[str, Any] | None:
    if not EQ6_TWO_PASS:
        return None
    if not retrieval_payload:
        return None
    draft_messages = [{"role": "system", "content": _EQ6_DRAFT_SYSTEM}, *messages]
    try:
        draft_text, _cost, _latency, _usage, _finish = await complete_chat(
            provider=provider,
            messages=draft_messages,
            max_tokens=EQ6_DRAFT_MAX_TOKENS,
            log_label="eq6_draft",
        )
    except Exception:
        return None
    try:
        engine = get_entity_engine(entity)
        hysteresis = engine.calculate_memory_coherence()
    except Exception:
        hysteresis = None
    try:
        return equation_6_operational(
            query_text=draft_text,
            retrieval_payload=retrieval_payload,
            hysteresis_coherence=hysteresis,
            lawfulness_level=None,
            mediator_prime=None,
        )
    except Exception:
        return None


def _gate_system_message(eq6_gate: dict[str, Any] | None) -> str | None:
    if not eq6_gate:
        return None
    if eq6_gate.get("commit_allowed") is False:
        return (
            "Eq6 gate indicates commit is not allowed for this turn. "
            "Respond conservatively, avoid new claims, and ask for clarification."
        )
    return None


def _strip_trailing_markdown_noise(text: str) -> str:
    """Remove trailing whitespace and stray fenced-block markers."""

    if not text:
        return ""

    cleaned = text.rstrip()
    fence_pattern = re.compile(r"(?:\s*```[a-zA-Z0-9]*\s*)$")
    while True:
        match = fence_pattern.search(cleaned)
        if not match:
            return cleaned
        cleaned = cleaned[: match.start()].rstrip()


def _entry_to_dict(entry: Any) -> Dict[str, Any]:
    if hasattr(entry, "as_dict"):
        try:
            return entry.as_dict()
        except Exception:
            return {}
    try:
        key = getattr(entry, "key", None)
        state = getattr(entry, "state", None)
        created_at = getattr(entry, "created_at", None)
        return {
            "key": key.as_path() if key else None,
            "state": getattr(state, "__dict__", {}) if state else {},
            "created_at": created_at.isoformat() if created_at else None,
            "notes": getattr(entry, "notes", None),
            "pinned": getattr(entry, "pinned", False),
        }
    except Exception:
        return {}


def _extract_attachment_coords(message: str, default_namespace: str | None = None) -> list[str]:
    return _extract_attachment_coords_with_fallbacks(
        message=message,
        default_namespace=default_namespace,
        fallback_namespaces=None,
    )


def _canonical_runtime_subject(entity_type: str, entity_id: str, *, host: str | None = None) -> str:
    effective_host = str(host if host is not None else os.getenv("DEFAULT_DID_HOST", "")).strip().lower()
    entity_key = str(entity_type or "").strip().lower() or "resource"
    identifier = re.sub(r"[^a-z0-9]+", "-", str(entity_id or entity_key).strip().lower()).strip("-") or entity_key
    if entity_key in {"ledger", "surface", "provider", "binding", "relationship", "principal"}:
        suffix = "principals" if entity_key == "principal" else (f"{entity_key}s" if entity_key != "binding" else "bindings")
        return f"did:web:{effective_host}:{suffix}:{identifier}"
    return f"did:web:{effective_host}:resources:{identifier}"


def _normalize_runtime_identity_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    ledger_id: str,
    write_namespace: str,
    ledger_service: LedgerService | None = None,
) -> dict[str, Any]:
    raw = metadata if isinstance(metadata, Mapping) else {}
    runtime_identity = raw.get("runtime_identity") if isinstance(raw.get("runtime_identity"), Mapping) else {}
    model_auth_context = raw.get("model_auth_context") if isinstance(raw.get("model_auth_context"), Mapping) else {}
    identity_vc = model_auth_context.get("identity_vc") if isinstance(model_auth_context.get("identity_vc"), Mapping) else {}

    normalized: dict[str, Any] = {
        "ledger_id": str(runtime_identity.get("ledger_id") or ledger_id).strip() or ledger_id,
        "runtime_namespace": str(runtime_identity.get("runtime_namespace") or write_namespace).strip() or write_namespace,
        "ledger_canonical_subject": str(runtime_identity.get("ledger_canonical_subject") or _canonical_runtime_subject("ledger", ledger_id)).strip(),
    }
    principal_subject = str(
        runtime_identity.get("principal_canonical_subject")
        or identity_vc.get("canonical_subject")
        or identity_vc.get("principal_did")
        or raw.get("principal_did")
        or ""
    ).strip()
    if principal_subject:
        normalized["principal_canonical_subject"] = principal_subject
        normalized["principal_canonical_subject_source"] = str(
            runtime_identity.get("principal_canonical_subject_source")
            or identity_vc.get("canonical_subject_source")
            or "principal_did"
        ).strip() or "principal_did"
    principal_did = str(runtime_identity.get("principal_did") or identity_vc.get("principal_did") or raw.get("principal_did") or "").strip()
    if principal_did:
        normalized["principal_did"] = principal_did

    vc_refs_raw = runtime_identity.get("vc_refs") if isinstance(runtime_identity.get("vc_refs"), Mapping) else {}
    vc_refs = {
        "credential_ref": str(vc_refs_raw.get("credential_ref") or identity_vc.get("credential_ref") or "").strip(),
        "standing_envelope_ref": str(vc_refs_raw.get("standing_envelope_ref") or identity_vc.get("standing_envelope_ref") or "").strip(),
        "wallet_did": str(vc_refs_raw.get("wallet_did") or identity_vc.get("wallet_did") or "").strip(),
        "wallet_binding_ref": str(vc_refs_raw.get("wallet_binding_ref") or identity_vc.get("wallet_binding_ref") or "").strip(),
        "issuer_did": str(vc_refs_raw.get("issuer_did") or identity_vc.get("issuer_did") or "").strip(),
    }
    normalized["vc_refs"] = {key: value for key, value in vc_refs.items() if value}
    if ledger_service is not None:
        normalized["library_boundary"] = ledger_service.get_ledger_library_boundary(ledger_id)
    return normalized


def _delegated_prompt_path_metadata(
    request: Request | None,
    authz_diag: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    diag = authz_diag if isinstance(authz_diag, Mapping) else {}
    if not bool(diag.get("delegated_prompt_path_active")):
        return None

    raw = metadata if isinstance(metadata, Mapping) else {}
    contributor = raw.get("contributor") if isinstance(raw.get("contributor"), Mapping) else {}
    runtime_identity = raw.get("runtime_identity") if isinstance(raw.get("runtime_identity"), Mapping) else {}
    request_state = getattr(request, "state", None)
    request_principal_did = ""
    request_principal_id = ""
    if request_state is not None:
        request_principal_did = str(getattr(request_state, "auth_claim_principal_did", "") or "").strip()
    if request is not None:
        request_principal_id = str(request.headers.get("x-principal-id") or request.headers.get("x-user-id") or "").strip()
    if not request_principal_did and request is not None:
        request_principal_did = str(request.headers.get("x-principal-did") or request.headers.get("x-did") or "").strip()
    prompt_principal_did = str(
        contributor.get("principal_did")
        or runtime_identity.get("principal_did")
        or request_principal_did
        or raw.get("principal_did")
        or ""
    ).strip()
    prompt_principal_id = str(contributor.get("principal_id") or request_principal_id or raw.get("contributor_id") or "").strip()
    prompt_principal_type = str(contributor.get("principal_type") or "").strip()
    prompt_principal_display_name = str(
        contributor.get("principal_display_name")
        or raw.get("principal_display_name")
        or ""
    ).strip()
    requested_by_principal_did = str(diag.get("delegated_by_principal_did") or "").strip()
    requested_by_principal_id = str(diag.get("delegated_by_principal_id") or "").strip()
    target_surface_id = str(diag.get("delegated_surface_id") or "").strip()
    raw_ledger_scope = diag.get("delegated_ledger_scope")
    raw_surface_scope = diag.get("delegated_surface_scope")
    if isinstance(raw_ledger_scope, str):
        ledger_scope = [part.strip() for part in raw_ledger_scope.split(",") if part.strip()]
    else:
        ledger_scope = [str(item).strip() for item in raw_ledger_scope or [] if str(item).strip()]
    if isinstance(raw_surface_scope, str):
        surface_scope = [part.strip() for part in raw_surface_scope.split(",") if part.strip()]
    else:
        surface_scope = [str(item).strip() for item in raw_surface_scope or [] if str(item).strip()]
    target_ledger_id = str(
        ledger_scope[0]
        if ledger_scope
        else runtime_identity.get("ledger_id") or raw.get("ledger_id") or raw.get("runtime_namespace") or ""
    ).strip()

    delegated: dict[str, Any] = {
        "active": True,
        "audit_posture": "requested_by_operator_executed_by_delegated_principal",
        "delegation_mode": str(diag.get("delegation_mode") or "delegated_only").strip() or "delegated_only",
        "prompt_principal_did": prompt_principal_did or None,
        "prompt_principal_id": prompt_principal_id or None,
        "prompt_principal_type": prompt_principal_type or None,
        "prompt_principal_display_name": prompt_principal_display_name or None,
        "requested_by_principal_did": requested_by_principal_did or None,
        "requested_by_principal_id": requested_by_principal_id or None,
        "requested_by_is_distinct_from_prompt_principal": bool(
            requested_by_principal_did and prompt_principal_did and requested_by_principal_did != prompt_principal_did
        ),
        "target_ledger_id": target_ledger_id or None,
        "target_surface_id": target_surface_id or None,
        "ledger_scope": ledger_scope,
        "surface_scope": surface_scope,
        "expires_at": str(diag.get("delegation_expires_at") or "").strip() or None,
        "cli_request_required": bool(diag.get("delegated_cli_request")),
    }
    return delegated


def _build_coord_meta(*, coord: str, metadata: Mapping[str, Any] | None, write_namespace: str) -> dict[str, Any]:
    raw = metadata if isinstance(metadata, Mapping) else {}
    runtime_identity = raw.get("runtime_identity") if isinstance(raw.get("runtime_identity"), Mapping) else {}
    normalized = normalise_coord(coord)
    bare_identifier = str(normalized.get("bare") or coord).strip()
    return {
        "coord": coord,
        "coord_type": coord_type(coord),
        "identifier": bare_identifier,
        "runtime_namespace": str(runtime_identity.get("runtime_namespace") or write_namespace).strip() or write_namespace,
        "canonical_subject": str(
            runtime_identity.get("ledger_canonical_subject") or _canonical_runtime_subject("ledger", raw.get("ledger_id") or write_namespace)
        ).strip(),
        "canonical_subject_source": "did:web:ledger",
    }


def _persist_coord_meta(store: LedgerStoreV2 | None, coordinate: str, metadata: Mapping[str, Any] | None) -> None:
    if store is None or not isinstance(coordinate, str) or not coordinate.strip():
        return
    entry = store.read(coordinate)
    if entry is None:
        return
    existing_meta = entry.state.metadata if isinstance(entry.state.metadata, dict) else {}
    updated_meta = dict(existing_meta)
    coord_meta = metadata.get("coord_meta") if isinstance(metadata, Mapping) and isinstance(metadata.get("coord_meta"), Mapping) else None
    if isinstance(coord_meta, Mapping):
        updated_meta["coord_meta"] = dict(coord_meta)
    runtime_identity = metadata.get("runtime_identity") if isinstance(metadata, Mapping) and isinstance(metadata.get("runtime_identity"), Mapping) else None
    if isinstance(runtime_identity, Mapping):
        updated_meta["runtime_identity"] = dict(runtime_identity)
    entry.state = ContinuousState(metadata=updated_meta)
    store.write(entry)


def _attachment_focus_namespaces(*, entity: str, write_namespace: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(value: str | None) -> None:
        if not isinstance(value, str):
            return
        clean = value.strip()
        if not clean or clean in seen:
            return
        seen.add(clean)
        candidates.append(clean)

    _append(write_namespace)
    _append(entity)
    _append(os.getenv("DEMO_DEFAULT_LEDGER", "").strip())
    for candidate in namespace_candidates():
        _append(candidate)

    return candidates


def _extract_attachment_coords_with_fallbacks(
    *,
    message: str,
    default_namespace: str | None = None,
    fallback_namespaces: list[str] | None = None,
) -> list[str]:
    if not message:
        return []
    coords: list[str] = []
    seen: set[str] = set()
    for match in COORD_PATTERN.finditer(message):
        namespace = match.group(1)
        bare = match.group(2)
        coord = f"{namespace}:{bare}" if namespace else bare
        normalized = normalise_coord(coord)
        if normalized.get("kind") not in {"attachment", "part"}:
            continue
        canonical = normalized.get("canonical") or coord
        bare = str(normalized.get("bare") or "").strip()
        candidate_coords: list[str] = []
        if ":" in canonical:
            candidate_coords.append(canonical)
            if bare:
                candidate_coords.append(bare)
        else:
            candidate_coords.append(canonical)
        if bare and default_namespace:
            candidate_coords.append(f"{default_namespace}:{bare}")
        if bare and fallback_namespaces:
            for namespace in fallback_namespaces:
                ns = str(namespace or "").strip()
                if ns:
                    candidate_coords.append(f"{ns}:{bare}")
        for candidate in candidate_coords:
            if candidate in seen:
                continue
            seen.add(candidate)
            coords.append(candidate)
    return coords


def _extract_coords_from_text(text: str, default_namespace: str | None = None) -> list[str]:
    if not text:
        return []
    coords: list[str] = []
    seen: set[str] = set()
    for match in COORD_PATTERN.finditer(text):
        namespace = match.group(1)
        bare = match.group(2)
        coord = f"{namespace}:{bare}" if namespace else bare
        normalized = normalise_coord(coord)
        canonical = normalized.get("canonical") or coord
        if ":" not in canonical and default_namespace:
            canonical = f"{default_namespace}:{canonical}"
        if canonical in seen:
            continue
        seen.add(canonical)
        coords.append(canonical)
    return coords


def _build_coord_resolution_summary(
    *,
    requested_coords: list[str],
    resolved_coords: set[str],
    supports_coord_resolution: bool = True,
    max_items: int = 12,
) -> dict[str, Any]:
    requested_unique: list[str] = []
    seen: set[str] = set()
    for coord in requested_coords:
        if not isinstance(coord, str):
            continue
        clean = coord.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        requested_unique.append(clean)

    resolved_list = [coord for coord in requested_unique if coord in resolved_coords]
    unresolved_list = [coord for coord in requested_unique if coord not in resolved_coords]
    known_resolved = sorted([coord for coord in resolved_coords if isinstance(coord, str) and coord.strip()])

    return {
        "supports_coord_resolution": bool(supports_coord_resolution),
        "requested_count": len(requested_unique),
        "resolved_count": len(resolved_list),
        "unresolved_count": len(unresolved_list),
        "requested_coords": requested_unique[:max_items],
        "resolved_coords": resolved_list[:max_items],
        "unresolved_coords": unresolved_list[:max_items],
        "available_resolved_context": known_resolved[:max_items],
    }


def _required_coords_from_knowledge_tree(
    knowledge_tree_data: list[Any] | None,
    *,
    max_count: int = 3,
) -> list[str]:
    if not isinstance(knowledge_tree_data, list):
        return []
    scored_items: list[tuple[float, str]] = []
    for item in knowledge_tree_data:
        if not isinstance(item, Mapping):
            continue
        coord = _canonical_coord_from_item(item)
        if not isinstance(coord, str):
            continue
        score = item.get("relevance_score")
        if not isinstance(score, (int, float)):
            tier = item.get("tier_rank")
            if isinstance(tier, (int, float)):
                score = float(tier) / 3.0
            else:
                score = 0.0
        scored_items.append((float(score), coord))
    scored_items.sort(key=lambda row: row[0], reverse=True)
    return [coord for _score, coord in scored_items[:max_count]]


def _resolved_coords_from_context(
    *,
    knowledge_tree_data: list[Any] | None,
    memories: Mapping[str, Any] | None,
) -> set[str]:
    resolved_coords_set: set[str] = set()
    if isinstance(knowledge_tree_data, list):
        for item in knowledge_tree_data:
            if isinstance(item, Mapping):
                coord = _canonical_coord_from_item(item)
                if isinstance(coord, str):
                    resolved_coords_set.add(coord)
    if isinstance(memories, Mapping) and isinstance(memories.get("retrieved"), list):
        for entry in memories.get("retrieved", []):
            if isinstance(entry, Mapping):
                coord = _canonical_coord_from_item(entry)
                if isinstance(coord, str):
                    resolved_coords_set.add(coord)
    return resolved_coords_set


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _derive_base4_runtime_state(
    *,
    policy_decision: str,
    reason_code: str,
    eq9_on_track: bool | None,
    grounding_success: bool,
    selected_action: str,
    continuity_hint: bool,
    budget_pressure: str,
    write_commit_allowed: bool | None,
) -> dict[str, Any]:
    normalized_policy = policy_decision.strip().lower()
    state = "Probe"
    reason = "bounded_exploration_default"
    intervention_required = False

    if normalized_policy in {"deny", "block"}:
        state = "Halt"
        reason = reason_code or "policy_blocked"
        intervention_required = True
    elif budget_pressure == "near_cap" and (eq9_on_track is False or not grounding_success):
        state = "Halt"
        reason = "latency_or_budget_pressure_under_weaker_grounding"
        intervention_required = True
    elif budget_pressure == "near_cap":
        state = "Probe"
        reason = "latency_budget_pressure"
    elif normalized_policy == "degrade" or eq9_on_track is False or not grounding_success:
        state = "Probe"
        reason = reason_code or "degraded_or_weaker_grounding"
    elif continuity_hint or selected_action == "reuse_path":
        state = "Stabilise"
        reason = "continuity_guided_progress"
    elif normalized_policy == "allow" and grounding_success and selected_action == "resolve":
        state = "Express"
        reason = "publishable_grounded_output"
    elif normalized_policy == "allow":
        state = "Stabilise"
        reason = "allowed_but_not_yet_publishable"

    return {
        "state": state,
        "reason": reason,
        "intervention_required": intervention_required,
        "write_commit_allowed": write_commit_allowed,
    }


def _candidate_trace_from_retrieved(retrieved: list[Any], *, max_k: int = 4) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in retrieved:
        if not isinstance(item, Mapping):
            continue
        candidate = _candidate_surface_row(item)
        if not candidate:
            continue
        candidates.append(candidate)
    candidates.sort(key=_candidate_surface_sort_key)
    return candidates[:max_k]


def _canonical_candidate_trace(
    candidates: list[Any] | None,
    *,
    max_k: int = _DIAGNOSTIC_TOP_K,
) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        row = _candidate_surface_row(item)
        if not row:
            continue
        row["payload_loaded"] = bool(row.get("payload_loaded") or row.get("resolved_payload_present"))
        normalized.append(row)
    normalized.sort(key=_candidate_surface_sort_key)
    return normalized[:max_k]


def _canonical_autonomy_decision(
    decision: Mapping[str, Any] | None,
    *,
    candidate_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = dict(decision) if isinstance(decision, Mapping) else {}
    action = str(payload.get("action") or "answer_from_priors")
    if action not in _AUTONOMY_ACTIONS:
        action = "answer_from_priors"
    policy = str(payload.get("policy") or "balanced").strip().lower() or "balanced"
    chosen = payload.get("chosen_coord")
    chosen_coord = str(chosen).strip() if isinstance(chosen, str) and chosen.strip() else None
    top_k_raw = payload.get("top_k")
    if isinstance(top_k_raw, list):
        top_k = _canonical_candidate_trace(top_k_raw, max_k=4)
    elif isinstance(candidate_trace, list):
        top_k = _canonical_candidate_trace(candidate_trace, max_k=4)
    else:
        top_k = []

    if top_k:
        useful_candidates = [
            row for row in top_k
            if int(row.get("relevance_tier") or 4) <= 3 and str(row.get("origin_attestation") or "") != "model_response_wx"
        ]
        top_signal = max(
            max(
                _coerce_float(row.get("relevance_score"), 0.0),
                _coerce_float(row.get("p_adic_score"), 0.0),
                _coerce_float(row.get("search_score"), 0.0),
                _coerce_float(row.get("recency_score"), 0.0),
            )
            for row in top_k
        )
        if not useful_candidates and top_signal < 0.35 and not any(bool(row.get("resolved_payload_present")) for row in top_k):
            return {
                "policy": policy,
                "action": "request_new_candidate_set",
                "reason": "top_four_candidates_not_useful",
                "chosen_coord": chosen_coord or str(top_k[0].get("coord") or "").strip() or None,
                "top_k": top_k,
                "utility": {"resolve": 0.0, "reuse_path": 0.0, "answer_from_priors": 0.0},
            }

    utility_raw = payload.get("utility") if isinstance(payload.get("utility"), Mapping) else {}
    utility = {
        "resolve": round(_coerce_float(utility_raw.get("resolve"), 0.0), 3),
        "reuse_path": round(_coerce_float(utility_raw.get("reuse_path"), 0.0), 3),
        "answer_from_priors": round(_coerce_float(utility_raw.get("answer_from_priors"), 0.0), 3),
    }
    return {
        "policy": policy,
        "action": action,
        "reason": str(payload.get("reason") or ""),
        "chosen_coord": chosen_coord,
        "top_k": top_k,
        "utility": utility,
    }


def _diagnostics_snapshot(metadata_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    meta = metadata_payload if isinstance(metadata_payload, Mapping) else {}
    candidate_trace = _canonical_candidate_trace(
        meta.get("candidate_trace") if isinstance(meta.get("candidate_trace"), list) else None,
        max_k=_DIAGNOSTIC_TOP_K,
    )
    autonomy_decision = _canonical_autonomy_decision(
        meta.get("autonomy_decision") if isinstance(meta.get("autonomy_decision"), Mapping) else None,
        candidate_trace=candidate_trace,
    )
    resolve_summary = meta.get("resolve_summary") if isinstance(meta.get("resolve_summary"), Mapping) else None
    ancestry_linked_records = [
        {
            "coord": str(row.get("coord") or "").strip(),
            "ancestry_score": round(_coerce_float(row.get("ancestry_score"), 0.0), 3),
            "source": str(row.get("source") or "retrieved"),
            "resolved_payload_present": bool(row.get("resolved_payload_present")),
        }
        for row in candidate_trace
        if bool(row.get("ancestry_linked")) and isinstance(row.get("coord"), str) and str(row.get("coord")).strip()
    ]
    runtime_identity = meta.get("runtime_identity") if isinstance(meta.get("runtime_identity"), Mapping) else {}
    library_boundary = (
        runtime_identity.get("library_boundary")
        if isinstance(runtime_identity.get("library_boundary"), Mapping)
        else {}
    )
    canonical_ledger_id = str(library_boundary.get("canonical_ledger_id") or meta.get("ledger_id") or "").strip()
    alias_history = library_boundary.get("alias_history") if isinstance(library_boundary.get("alias_history"), list) else []
    supersession_history = (
        library_boundary.get("supersession_history")
        if isinstance(library_boundary.get("supersession_history"), list)
        else []
    )
    consolidation_history_count = int(library_boundary.get("consolidation_history_count") or 0) if library_boundary else 0
    alias_or_consolidation_present = bool(alias_history or supersession_history or consolidation_history_count)
    foundation_identity = (
        library_boundary.get("foundation_identity")
        if isinstance(library_boundary.get("foundation_identity"), Mapping)
        else {}
    )
    foundation_identity_fields = {
        "name": str(foundation_identity.get("name") or "").strip() or None,
        "purpose": str(foundation_identity.get("purpose") or "").strip() or None,
        "source": str(foundation_identity.get("source") or "").strip() or None,
    }
    foundation_identity_available = any(bool(value) for value in foundation_identity_fields.values())
    epistemic_status = meta.get("epistemic_status") if isinstance(meta.get("epistemic_status"), Mapping) else {}
    opened_payload_coords = (
        [coord for coord in epistemic_status.get("opened_payload_coords") if isinstance(coord, str) and coord.strip()][:6]
        if isinstance(epistemic_status.get("opened_payload_coords"), list)
        else []
    )
    source_coords = (
        [coord for coord in epistemic_status.get("source_coords") if isinstance(coord, str) and coord.strip()][:6]
        if isinstance(epistemic_status.get("source_coords"), list)
        else []
    )
    direct_decode_observed = str(epistemic_status.get("method") or "").strip() == "direct_decode"
    resolved_preview = (
        [coord for coord in (resolve_summary.get("resolved") or []) if isinstance(coord, str)][:6]
        if isinstance(resolve_summary, Mapping)
        else []
    )
    prior_payload_or_coord_access_present = bool(
        direct_decode_observed
        or opened_payload_coords
        or source_coords
        or resolved_preview
        or any(bool(row.get("resolved_payload_present")) for row in ancestry_linked_records)
    )
    prior_payload_or_coord_access_basis: list[str] = []
    if direct_decode_observed:
        prior_payload_or_coord_access_basis.append("direct_decode")
    if opened_payload_coords:
        prior_payload_or_coord_access_basis.append("opened_payload_coords")
    if source_coords:
        prior_payload_or_coord_access_basis.append("source_coords")
    if resolved_preview:
        prior_payload_or_coord_access_basis.append("resolved_coords")
    if any(bool(row.get("resolved_payload_present")) for row in ancestry_linked_records):
        prior_payload_or_coord_access_basis.append("resolved_payload_present")
    ancestry_recall = {
        "contract_version": "ancestry-recall-v2",
        "behavioural_only": True,
        "claim_posture": (
            "resolved_prior_access_present"
            if prior_payload_or_coord_access_present
            else "ancestry_signals_present"
            if ancestry_linked_records
            else "candidate_ranked_only"
        ),
        "explicit_surface_status": "explicit" if ancestry_linked_records else "absent",
        "generic_history_fields_rejected_as_evidence": True,
        "canonical_ledger_resolution": {
            "canonical_ledger_id": canonical_ledger_id or None,
            "alias_or_consolidation_present": alias_or_consolidation_present,
            "alias_history_count": len(alias_history),
            "supersession_history_count": len(supersession_history),
            "consolidation_history_count": consolidation_history_count,
        },
        "library_hot_path_summary_read": {
            "enabled": bool(library_boundary),
            "mode": str(library_boundary.get("hot_path_mode") or "summary_only"),
            "naive_broad_history_scan_rejected": True,
        },
        "prior_payload_or_coord_access": {
            "present": prior_payload_or_coord_access_present,
            "basis": prior_payload_or_coord_access_basis,
            "opened_payload_coords": opened_payload_coords,
            "source_coords": source_coords,
            "resolved_preview": resolved_preview,
            "coord_resolved_access_is_not_foundation_identity_rehydration": True,
        },
        "foundation_identity_rehydration": {
            "available": foundation_identity_available,
            "fields": foundation_identity_fields,
            "structured_runtime_surface_required": True,
        },
        "evidence_surfaces": [
            "ancestry_linked_records",
            "ranked_retrieval_candidates",
            "candidate_selection_rationale",
            "downstream_usage_trace",
            "canonical_ledger_resolution",
            "library_hot_path_summary_read",
            "prior_payload_or_coord_access",
            "foundation_identity_rehydration",
        ],
        "ancestry_linked_records": ancestry_linked_records[:6],
        "ranked_candidates": candidate_trace or [],
        "selection_rationale": {
            "policy": str(autonomy_decision.get("policy") or "balanced"),
            "action": str(autonomy_decision.get("action") or "answer_from_priors"),
            "reason": str(autonomy_decision.get("reason") or ""),
            "chosen_coord": autonomy_decision.get("chosen_coord"),
        },
        "usage_trace": {
            "requested_count": int(resolve_summary.get("requested_count") or 0) if isinstance(resolve_summary, Mapping) else 0,
            "resolved_count": int(resolve_summary.get("resolved_count") or 0) if isinstance(resolve_summary, Mapping) else 0,
            "unresolved_count": int(resolve_summary.get("unresolved_count") or 0) if isinstance(resolve_summary, Mapping) else 0,
            "resolved_preview": resolved_preview,
        },
    }
    eq9_eval = meta.get("eq9_eval") if isinstance(meta.get("eq9_eval"), Mapping) else None
    eq9_checks = eq9_eval.get("checks") if isinstance(eq9_eval, Mapping) and isinstance(eq9_eval.get("checks"), Mapping) else {}
    eq9_present_fields = [
        field
        for field in ("score", "law", "drift", "output_tokens", "meaning_per_token")
        if (
            field == "output_tokens"
            and isinstance(eq9_eval, Mapping)
            and isinstance(eq9_eval.get("output_tokens"), (int, float))
        )
        or (
            field != "output_tokens"
            and isinstance(eq9_checks.get(field), Mapping)
            and (eq9_checks.get(field) or {}).get("current") is not None
        )
    ]
    e6_diag = meta.get("e6_diagnostics") if isinstance(meta.get("e6_diagnostics"), Mapping) else None
    e6_present = bool(
        isinstance(e6_diag, Mapping)
        and any(
            e6_diag.get(key) is not None
            for key in ("mode", "route", "quality_tier", "bridge_allowed_runtime", "promotion_allowed", "promotion_reason")
        )
    )
    contradiction_indicators: list[str] = []
    standing_policy_for_diag = meta.get("standing_policy") if isinstance(meta.get("standing_policy"), Mapping) else None
    if isinstance(posture_policy := (meta.get("posture_policy") if isinstance(meta.get("posture_policy"), Mapping) else None), Mapping):
        policy_decision = str(posture_policy.get("policy_decision") or "").strip().lower()
        write_commit_allowed = (
            bool(standing_policy_for_diag.get("write_commit_allowed"))
            if isinstance(standing_policy_for_diag, Mapping) and standing_policy_for_diag.get("write_commit_allowed") is not None
            else None
        )
        if policy_decision == "block" and write_commit_allowed is True:
            contradiction_indicators.append("policy_block_with_write_commit_allowed")
    eq9_on_track = bool(eq9_eval.get("on_track")) if isinstance(eq9_eval, Mapping) and eq9_eval.get("on_track") is not None else None
    if eq9_on_track is False:
        contradiction_indicators.append("eq9_not_on_track")
    runtime_identity_for_diag = meta.get("runtime_identity") if isinstance(meta.get("runtime_identity"), Mapping) else {}
    library_boundary_for_diag = (
        runtime_identity_for_diag.get("library_boundary")
        if isinstance(runtime_identity_for_diag.get("library_boundary"), Mapping)
        else {}
    )
    has_base4_surface = bool(posture_policy or standing_policy_for_diag or eq9_eval)
    has_library_summary_boundary = bool(library_boundary_for_diag)
    diagnostic_observability = {
        "contract_version": "diagnostic-observability-v1",
        "observational_not_experiential": True,
        "manual_validation_categories": ["explicit", "indirect", "absent"],
        "present_observables": {
            "EQ9": eq9_present_fields,
            "EQ6": ["mode", "route", "quality_tier", "bridge_allowed_runtime", "promotion_allowed", "promotion_reason"]
            if e6_present
            else [],
        },
        "absent_observables": [] if e6_present else ["EQ6"],
        "indirect_only_evidence": {
            "contradiction_indicators": contradiction_indicators,
            "rule": "contradiction_only_evidence_does_not_count_as_explicit_contract_surface",
        },
        "absence_response": {
            "state_present": "State what observables are present in the current runtime context.",
            "state_absent": "State what observables are absent in the current runtime context.",
            "avoid_inference": "Do not infer readings or inner-state claims from absent observables.",
        },
        "upstream_boundary": {
            "base4_runtime_posture_visible": has_base4_surface,
            "library_summary_boundary_visible": has_library_summary_boundary,
            "canonical_ledger_id": str(library_boundary_for_diag.get("canonical_ledger_id") or meta.get("ledger_id") or "").strip() or None,
            "hot_path_mode": str(library_boundary_for_diag.get("hot_path_mode") or "summary_only"),
            "claim_strength_rule": "diagnostics_do_not_by_themselves_prove_continuity_or_profile_unity_without_upstream_substrate_surfaces",
        },
        "allowed_claim_mapping": {
            "EQ9": "observational_runtime_signals_only",
            "EQ6": "claim_only_when_present_in_current_runtime_context",
        },
    }
    introspect_pre = meta.get("introspect_snapshot_pre") if isinstance(meta.get("introspect_snapshot_pre"), Mapping) else None
    introspect_post = meta.get("introspect_snapshot_post") if isinstance(meta.get("introspect_snapshot_post"), Mapping) else None
    eval_contract = meta.get("eval_contract") if isinstance(meta.get("eval_contract"), Mapping) else None
    posture_policy = posture_policy if isinstance(posture_policy, Mapping) else None
    consistency_check = meta.get("consistency_check") if isinstance(meta.get("consistency_check"), Mapping) else None
    coord_resolution_warning = (
        meta.get("coord_resolution_warning") if isinstance(meta.get("coord_resolution_warning"), Mapping) else None
    )
    context_window = meta.get("context_window") if isinstance(meta.get("context_window"), Mapping) else None
    standing_policy = meta.get("standing_policy") if isinstance(meta.get("standing_policy"), Mapping) else None
    finish_reason = str(meta.get("finish_reason") or "").strip() if meta.get("finish_reason") is not None else ""
    runtime_identity = meta.get("runtime_identity") if isinstance(meta.get("runtime_identity"), Mapping) else None
    assurance_verification = (
        meta.get("assurance_verification") if isinstance(meta.get("assurance_verification"), Mapping) else None
    )
    self_model_continuity = {
        "contract_version": "self-model-continuity-v1",
        "non_phenomenological": True,
        "upstream_substrate_dependencies": {
            "base4_runtime_posture_visible": bool(posture_policy or standing_policy or eq9_eval),
            "library_summary_boundary_visible": bool(
                isinstance((runtime_identity or {}).get("library_boundary"), Mapping)
            ),
            "governed_retention_visible": bool(
                isinstance(meta.get("write_provenance"), Mapping)
                and isinstance((meta.get("write_provenance") or {}).get("retention_tier"), str)
                and str((meta.get("write_provenance") or {}).get("retention_tier") or "").strip()
            ),
            "claim_strength_rule": "self_model_primitives_are_instrumentation_layered_on_top_of_epic13_substrate_not_standalone_proof_of_continuity",
        },
        "primitives": {
            "SelfObservationRecord": {
                "present": bool(introspect_pre or introspect_post),
                "runtime_purpose": "capture self-observation snapshots around a turn",
                "evidence": {
                    "has_pre_snapshot": bool(introspect_pre),
                    "has_post_snapshot": bool(introspect_post),
                    "latest_turn_coordinate": (
                        str((introspect_pre or introspect_post or {}).get("latest_turn_coordinate") or "").strip() or None
                    ),
                },
            },
            "RuntimeGoal": {
                "present": bool(eval_contract or posture_policy),
                "runtime_purpose": "bound the turn against explicit evaluation or posture targets",
                "evidence": {
                    "has_eval_contract": bool(eval_contract),
                    "has_posture_policy": bool(posture_policy),
                    "evaluative_basis": {
                        "goal_source": (
                            "eval_contract"
                            if bool(eval_contract)
                            else "posture_policy"
                            if bool(posture_policy)
                            else None
                        ),
                        "policy_decision": str(posture_policy.get("policy_decision") or "") if posture_policy else "",
                        "reason_code": str(posture_policy.get("reason_code") or "") if posture_policy else "",
                    },
                },
            },
            "SalienceScore": {
                "present": bool(candidate_trace),
                "runtime_purpose": "rank candidate memory and context by operational importance",
                "evidence": {
                    "candidate_count": len(candidate_trace),
                    "top_coord": str(candidate_trace[0].get("coord") or "").strip() if candidate_trace else None,
                },
            },
            "PredictionRecord": {
                "present": bool(posture_policy and (posture_policy.get("policy_decision") or posture_policy.get("reason_code"))),
                "runtime_purpose": "record policy or posture expectations that shape the turn",
                "evidence": {
                    "policy_decision": str(posture_policy.get("policy_decision") or "") if posture_policy else "",
                    "reason_code": str(posture_policy.get("reason_code") or "") if posture_policy else "",
                },
            },
            "ErrorSignal": {
                "present": bool(consistency_check or (eq9_eval and not bool(eq9_eval.get("on_track", True)))),
                "runtime_purpose": "surface contradiction, drift, or failed checks as bounded error signals",
                "evidence": {
                    "has_consistency_check": bool(consistency_check),
                    "eq9_on_track": bool(eq9_eval.get("on_track")) if isinstance(eq9_eval, Mapping) else None,
                },
            },
            "ValuationSignal": {
                "present": bool(eq9_eval or posture_policy),
                "runtime_purpose": "expose bounded operational scoring rather than emotion or feeling",
                "evidence": {
                    "eq9_known_checks": int(eq9_eval.get("known_checks") or 0) if isinstance(eq9_eval, Mapping) else 0,
                    "policy_gate_version": str(posture_policy.get("policy_gate_version") or "") if posture_policy else "",
                    "evaluative_basis": {
                        "eq9_on_track": bool(eq9_eval.get("on_track")) if isinstance(eq9_eval, Mapping) and eq9_eval.get("on_track") is not None else None,
                        "score_present": bool(isinstance(eq9_checks.get("score"), Mapping) and (eq9_checks.get("score") or {}).get("current") is not None),
                        "law_present": bool(isinstance(eq9_checks.get("law"), Mapping) and (eq9_checks.get("law") or {}).get("current") is not None),
                        "drift_present": bool(isinstance(eq9_checks.get("drift"), Mapping) and (eq9_checks.get("drift") or {}).get("current") is not None),
                    },
                },
            },
        },
    }
    continuity_candidates = [
        row for row in candidate_trace if isinstance(row.get("continuity_source"), str) and str(row.get("continuity_source")).strip()
    ]
    between_turn_persistence = {
        "contract_version": "between-turn-persistence-v1",
        "runtime_capability_only": True,
        "explicit_surface_status": (
            "explicit" if bool(introspect_pre or introspect_post or continuity_candidates) else "absent"
        ),
        "generic_session_markers_rejected_as_evidence": True,
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or meta.get("ledger_id") or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
            "consolidation_history_count": int((library_boundary or {}).get("consolidation_history_count") or 0)
            if isinstance(library_boundary, Mapping)
            else 0,
        },
        "retention_tier_truth": {
            "active_continuity_tier": "Silt" if bool(introspect_pre or introspect_post or continuity_candidates) else None,
            "durable_tier_visible": str(((meta.get("write_provenance") or {}).get("retention_tier")) or "").strip() or None
            if isinstance(meta.get("write_provenance"), Mapping)
            else None,
            "claim_strength_rule": "between_turn_persistence_requires_canonical_ledger_continuity_and_governed_retention_truth_not_generic_session_markers",
        },
        "background_state_tick": {
            "named": True,
            "scoped": "between_interactive_turns",
            "observable_via": [
                "introspect_snapshot_pre",
                "introspect_snapshot_post",
                "continuity_candidates",
            ],
        },
        "state_surfaces": {
            "has_pre_snapshot": bool(introspect_pre),
            "has_post_snapshot": bool(introspect_post),
            "latest_turn_coordinate_pre": (
                str((introspect_pre or {}).get("latest_turn_coordinate") or "").strip() or None
            ),
            "latest_turn_coordinate_post": (
                str((introspect_post or {}).get("latest_turn_coordinate") or "").strip() or None
            ),
        },
        "continuity_candidates": [
            {
                "coord": str(row.get("coord") or "").strip(),
                "continuity_source": str(row.get("continuity_source") or "").strip(),
                "source": str(row.get("source") or "retrieved"),
                "relevance_score": round(_coerce_float(row.get("relevance_score"), 0.0), 3),
            }
            for row in continuity_candidates[:6]
            if isinstance(row.get("coord"), str) and str(row.get("coord")).strip()
        ],
        "observable_state_changes": {
            "pre_to_post_snapshot_available": bool(introspect_pre and introspect_post),
            "candidate_count": len(continuity_candidates),
        },
    }
    unresolved_tensions: list[dict[str, Any]] = []
    if bool(consistency_check and consistency_check.get("contradiction")):
        unresolved_tensions.append(
            {
                "kind": "resolution_consistency",
                "status": str(consistency_check.get("status") or "contradiction"),
                "reason": str(consistency_check.get("reason") or ""),
                "retry_status": str(consistency_check.get("retry_status") or ""),
                "resolved_count": int(consistency_check.get("resolved_count") or 0),
            }
        )
    unresolved_coords = (
        coord_resolution_warning.get("unresolved")
        if isinstance(coord_resolution_warning, Mapping) and isinstance(coord_resolution_warning.get("unresolved"), list)
        else []
    )
    if unresolved_coords:
        unresolved_tensions.append(
            {
                "kind": "coord_resolution_gap",
                "status": "unresolved",
                "reason": "mentioned_coords_not_fully_resolved",
                "coord_count": len([coord for coord in unresolved_coords if isinstance(coord, str) and coord.strip()]),
                "blocked": bool(coord_resolution_warning.get("blocked")) if isinstance(coord_resolution_warning, Mapping) else False,
            }
        )
    deferred_commit = {
        "mode": "single_retry_on_resolution_contradiction" if bool(consistency_check and consistency_check.get("retried")) else "immediate_commit",
        "applied": bool(consistency_check and consistency_check.get("retried")),
        "retry_count": int(consistency_check.get("retry_count") or 0) if isinstance(consistency_check, Mapping) else 0,
        "rationale": (
            "final response commit deferred until grounded regeneration completed"
            if bool(consistency_check and consistency_check.get("retried"))
            else "no deferred commit runtime path was required"
        ),
    }
    unresolved_tension_and_commit = {
        "contract_version": "unresolved-tension-v1",
        "operational_not_anthropomorphic": True,
        "explicit_surface_status": "explicit" if unresolved_tensions else "absent",
        "indirect_only_evidence": {
            "rule": "contradiction_only_evidence_is_indirect_until_named_unresolved_tension_objects_are_present",
            "counts_as_explicit_only_when_tracked_objects_exist": True,
        },
        "runtime_posture_boundary": {
            "base4_state": (
                "Halt"
                if str((posture_policy or {}).get("policy_decision") or "").strip().lower() == "block"
                else "Probe"
                if finish_reason == "length" or str((context_window or {}).get("budget_pressure") or "").strip() in {"near_cap", "at_cap"}
                else "Stabilise"
                if bool(continuity_candidates)
                else "Express"
            ),
            "off_path_preferred": bool(unresolved_tensions),
            "fallback_or_deferral_expected": bool(
                str((context_window or {}).get("budget_pressure") or "").strip() in {"near_cap", "at_cap"}
                or (
                    isinstance((runtime_identity or {}).get("library_boundary"), Mapping)
                    and bool((((runtime_identity or {}).get("library_boundary") or {}).get("latency_boundary") or {}).get("deep_history_requires_fallback_or_deferral"))
                )
            ),
            "claim_strength_rule": "unresolved_tension_should_remain_bounded_and_may_defer_or_downgrade_under_base4_and_latency_pressure",
        },
        "candidate_response_set": {
            "present": bool(candidate_trace),
            "kind": "ranked_context_and_route_candidates",
            "candidate_count": len(candidate_trace),
            "top_candidates": [
                {
                    "coord": str(row.get("coord") or "").strip(),
                    "source": str(row.get("source") or "retrieved"),
                    "relevance_score": round(_coerce_float(row.get("relevance_score"), 0.0), 3),
                    "resolved_payload_present": bool(row.get("resolved_payload_present")),
                }
                for row in candidate_trace[:3]
                if isinstance(row.get("coord"), str) and str(row.get("coord")).strip()
            ],
        },
        "resolution_decision": {
            "present": bool(autonomy_decision or consistency_check),
            "selected_action": str(autonomy_decision.get("action") or "answer_from_priors"),
            "selected_coord": autonomy_decision.get("chosen_coord"),
            "policy": str(autonomy_decision.get("policy") or "balanced"),
            "deferred_commit": deferred_commit,
        },
        "unresolved_tension": {
            "present": bool(unresolved_tensions),
            "tracked_objects": unresolved_tensions[:6],
            "non_collapse_rule": (
                "tracked tensions remain observable runtime records and do not imply forced synthesis"
            ),
        },
    }
    salience_valence_markers: list[dict[str, Any]] = []
    if candidate_trace:
        top_candidate = candidate_trace[0]
        if bool(top_candidate.get("resolved_payload_present")):
            salience_valence_markers.append(
                {
                    "marker": "clarifying",
                    "source": "top_resolved_candidate",
                    "evidence": {
                        "coord": str(top_candidate.get("coord") or "").strip() or None,
                        "source": str(top_candidate.get("source") or "retrieved"),
                    },
                }
            )
    if continuity_candidates:
        salience_valence_markers.append(
            {
                "marker": "high_reuse",
                "source": "continuity_candidates",
                "evidence": {
                    "candidate_count": len(continuity_candidates),
                    "top_coord": str(continuity_candidates[0].get("coord") or "").strip() or None,
                },
            }
        )
    if bool(consistency_check and consistency_check.get("contradiction")):
        salience_valence_markers.append(
            {
                "marker": "destabilizing",
                "source": "consistency_check",
                "evidence": {
                    "reason": str(consistency_check.get("reason") or ""),
                    "retry_status": str(consistency_check.get("retry_status") or ""),
                },
            }
        )
    if posture_policy:
        salience_valence_markers.append(
            {
                "marker": "constraint_relevant",
                "source": "posture_policy",
                "evidence": {
                    "policy_decision": str(posture_policy.get("policy_decision") or ""),
                    "reason_code": str(posture_policy.get("reason_code") or ""),
                },
            }
        )
    if bool(eq9_eval and not bool(eq9_eval.get("on_track", True))):
        salience_valence_markers.append(
            {
                "marker": "uncertainty_increasing",
                "source": "eq9_eval",
                "evidence": {
                    "on_track": bool(eq9_eval.get("on_track")),
                    "known_checks": int(eq9_eval.get("known_checks") or 0),
                },
            }
        )
    max_tokens = _int_or_none(meta.get("max_tokens"))
    standing_cap = _int_or_none(standing_policy.get("max_output_tokens")) if isinstance(standing_policy, Mapping) else None
    prompt_tokens = _int_or_none(context_window.get("prompt_tokens")) if isinstance(context_window, Mapping) else None
    completion_tokens = _int_or_none(context_window.get("completion_tokens")) if isinstance(context_window, Mapping) else None
    retrieved_count = _int_or_none(context_window.get("retrieved_count")) if isinstance(context_window, Mapping) else None
    history_len = _int_or_none(context_window.get("history_len")) if isinstance(context_window, Mapping) else None
    budget_pressure = "bounded"
    if (
        max_tokens is not None
        and completion_tokens is not None
        and max_tokens > 0
        and completion_tokens >= max(max_tokens - 1, 1)
    ) or finish_reason == "length":
        budget_pressure = "near_cap"
    retention_decisions: list[dict[str, Any]] = []
    retention_candidates = continuity_candidates or candidate_trace
    for row in retention_candidates[:3]:
        if not isinstance(row.get("coord"), str) or not str(row.get("coord")).strip():
            continue
        retention_decisions.append(
            {
                "decision": "retain",
                "coord": str(row.get("coord") or "").strip(),
                "reason": (
                    "continuity_signal_present"
                    if isinstance(row.get("continuity_source"), str) and str(row.get("continuity_source")).strip()
                    else "top_ranked_salience"
                ),
                "weight": {
                    "continuity_source": str(row.get("continuity_source") or "").strip() or None,
                    "relevance_score": round(_coerce_float(row.get("relevance_score"), 0.0), 3),
                    "resolved_payload_present": bool(row.get("resolved_payload_present")),
                },
            }
        )
    if candidate_trace and len(candidate_trace) > len(retention_decisions):
        retention_decisions.append(
            {
                "decision": "defer_or_drop",
                "coord_count": max(len(candidate_trace) - len(retention_decisions), 0),
                "reason": "bounded_top_k_carry_forward",
            }
        )
    gravity_tax_policy = _build_gravity_tax_policy(meta)
    bounded_retention_pressure = {
        "contract_version": "bounded-retention-v1",
        "operational_not_existential": True,
        "explicit_surface_status": "explicit" if bool(salience_valence_markers or retention_decisions) else "absent",
        "output_token_limits_alone_rejected_as_evidence": True,
        "salience_valence_markers": salience_valence_markers[:6],
        "persistence_budget": {
            "bounded_runtime_budget": True,
            "pressure_state": budget_pressure,
            "max_tokens": max_tokens,
            "standing_policy_cap": standing_cap,
            "context_window": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "retrieved_count": retrieved_count,
                "history_len": history_len,
            },
        },
        "retention_tier_truth": {
            "retention_tier": str(gravity_tax_policy.get("retention_tier") or "").strip() or None,
            "retention_tier_reason": str(gravity_tax_policy.get("retention_tier_reason") or "").strip() or None,
            "claim_strength_rule": "bounded_retention_requires_governed_tier_truth_not_simple_output_budgeting_alone",
        },
        "gravity_tax_linkage": {
            "explicit_retention_cost_policy": bool(gravity_tax_policy.get("explicit_retention_cost_policy")),
            "governed_promotion_required": bool(gravity_tax_policy.get("governed_promotion_required")),
            "anti_hoarding_posture": str(
                gravity_tax_policy.get("anti_hoarding_posture") or "selective_retention_over_silent_accumulation"
            ),
        },
        "retention_decision": {
            "reviewable": True,
            "decisions": retention_decisions,
            "rule": "prefer continuity-linked or top-ranked candidates within bounded top-k carry-forward",
        },
    }
    gravity_tax_retention_policy = {
        "contract_version": str(gravity_tax_policy.get("gravity_tax_contract_version") or "gravity-tax-v1"),
        "explicit_retention_cost_policy": bool(gravity_tax_policy.get("explicit_retention_cost_policy")),
        "anti_hoarding_posture": str(
            gravity_tax_policy.get("anti_hoarding_posture") or "selective_retention_over_silent_accumulation"
        ),
        "governed_promotion_required": bool(gravity_tax_policy.get("governed_promotion_required")),
        "noisy_or_low_coherence_drains_by_default": bool(gravity_tax_policy.get("noisy_or_low_coherence_drains_by_default")),
        "cost_inputs": gravity_tax_policy.get("cost_inputs") if isinstance(gravity_tax_policy.get("cost_inputs"), Mapping) else {},
        "evidence": {
            "retention_tier": gravity_tax_policy.get("retention_tier"),
            "retention_tier_reason": gravity_tax_policy.get("retention_tier_reason"),
            "gravity_cost": gravity_tax_policy.get("gravity_cost"),
            "gravity_penalty": gravity_tax_policy.get("gravity_penalty"),
        },
    }
    ledger_id = (
        str((runtime_identity or {}).get("ledger_id") or meta.get("ledger_id") or "").strip()
        or str((runtime_identity or {}).get("runtime_namespace") or meta.get("runtime_namespace") or "").strip()
        or None
    )
    chosen_coord = str(autonomy_decision.get("chosen_coord") or "").strip()
    top_coord = str(candidate_trace[0].get("coord") or "").strip() if candidate_trace else ""
    coord_for_pattern = chosen_coord or top_coord
    coord_family = coord_type(coord_for_pattern) if coord_for_pattern else None
    walk_pre = introspect_pre.get("walk") if isinstance(introspect_pre, Mapping) and isinstance(introspect_pre.get("walk"), Mapping) else None
    walk_post = introspect_post.get("walk") if isinstance(introspect_post, Mapping) and isinstance(introspect_post.get("walk"), Mapping) else None
    walk_hops_post = _int_or_none(walk_post.get("walk_hops")) if isinstance(walk_post, Mapping) else None
    if walk_hops_post is not None:
        recursion_depth_observed = max(walk_hops_post, 1)
    else:
        action = str(autonomy_decision.get("action") or "").strip()
        recursion_depth_observed = 3 if action == "resolve" else (2 if action == "reuse_path" else 1)
    hysteresis_pre = (
        _coerce_float(introspect_pre.get("hysteresis_coherence"), 0.0)
        if isinstance(introspect_pre, Mapping) and introspect_pre.get("hysteresis_coherence") is not None
        else None
    )
    hysteresis_post = (
        _coerce_float(introspect_post.get("hysteresis_coherence"), 0.0)
        if isinstance(introspect_post, Mapping) and introspect_post.get("hysteresis_coherence") is not None
        else None
    )
    coherence_delta = (
        round(hysteresis_post - hysteresis_pre, 3)
        if hysteresis_pre is not None and hysteresis_post is not None
        else None
    )
    grounding_success = bool(
        not bool(consistency_check and consistency_check.get("contradiction"))
        and not bool(unresolved_coords)
    )
    refs_used: list[str] = []
    if isinstance(resolve_summary, Mapping):
        resolved_preview = resolve_summary.get("resolved") or resolve_summary.get("resolved_coords")
        if isinstance(resolved_preview, list):
            refs_used.extend([str(coord).strip() for coord in resolved_preview if isinstance(coord, str) and str(coord).strip()])
    if not refs_used:
        refs_used.extend(
            [
                str(row.get("coord") or "").strip()
                for row in candidate_trace[:3]
                if isinstance(row.get("coord"), str) and str(row.get("coord")).strip()
            ]
        )
    continuity_hint = bool(continuity_candidates)
    next_instance_hint = (
        "prefer deeper resolved walk for open queries"
        if recursion_depth_observed >= 3 and grounding_success
        else "reuse continuity-linked context before opening new branches"
        if continuity_hint and recursion_depth_observed <= 2
        else "keep response concise unless stronger grounding signals appear"
    )
    selected_action = str(autonomy_decision.get("action") or "answer_from_priors")
    decision_basis = (
        "open_query_with_resolved_support"
        if selected_action == "resolve" and recursion_depth_observed >= 3 and grounding_success
        else "continuity_reuse_preferred"
        if selected_action == "reuse_path" or continuity_hint
        else "concise_response_under_weaker_grounding"
    )
    policy_decision_value = str(posture_policy.get("policy_decision") or "").strip() if posture_policy else ""
    reason_code_value = str(posture_policy.get("reason_code") or "").strip() if posture_policy else ""
    governing_basis = (
        f"{policy_decision_value}:{reason_code_value}"
        if policy_decision_value and reason_code_value
        else policy_decision_value
        or reason_code_value
        or "runtime_policy_not_explicit"
    )
    write_commit_allowed = (
        bool(standing_policy.get("write_commit_allowed"))
        if isinstance(standing_policy, Mapping) and standing_policy.get("write_commit_allowed") is not None
        else None
    )
    base4_runtime = _derive_base4_runtime_state(
        policy_decision=policy_decision_value,
        reason_code=reason_code_value,
        eq9_on_track=eq9_on_track,
        grounding_success=grounding_success,
        selected_action=selected_action,
        continuity_hint=continuity_hint,
        budget_pressure=budget_pressure,
        write_commit_allowed=write_commit_allowed,
    )
    base4_runtime_state = {
        "contract_version": "base4-runtime-state-v1",
        "state_model": ["Halt", "Probe", "Stabilise", "Express"],
        "state": base4_runtime["state"],
        "reason": base4_runtime["reason"],
        "runtime_posture_only": True,
        "intervention_boundary": {
            "required": bool(base4_runtime["intervention_required"]),
            "state": "request_intervention_or_reframe" if bool(base4_runtime["intervention_required"]) else "not_required",
        },
        "latency_aware_posture": {
            "budget_pressure": budget_pressure,
            "hot_path_bounded": True,
            "fallback_or_deferral_expected_when_near_cap": budget_pressure == "near_cap",
        },
        "evidence": {
            "policy_decision": policy_decision_value or None,
            "reason_code": reason_code_value or None,
            "eq9_on_track": eq9_on_track,
            "grounding_success": grounding_success,
            "selected_action": selected_action,
            "continuity_hint": continuity_hint,
            "write_commit_allowed": write_commit_allowed,
        },
        "transition_rules": [
            {
                "state": "Halt",
                "when": "policy blocks or bounded live completion is unrealistic under active pressure",
            },
            {
                "state": "Probe",
                "when": "work remains reversible, degraded, or latency-bounded",
            },
            {
                "state": "Stabilise",
                "when": "continuity-guided continuation is allowed but not yet fully publishable",
            },
            {
                "state": "Express",
                "when": "grounded publishable output is allowed within the active runtime envelope",
            },
        ],
    }
    purpose_anchor = (
        "grounded_answer_with_traceable_support"
        if grounding_success
        else "bounded_response_under_constraint"
    )
    autonomy_outcome_memory = {
        "contract_version": "autonomy-pattern-v1",
        "outcome_oriented_only": True,
        "autonomy_pattern": {
            "kind": "autonomy_pattern",
            "ledger_id": ledger_id,
            "phase": "between_turns",
            "pattern": {
                "coord_type": coord_family,
                "recursion_depth_observed": recursion_depth_observed,
                "coherence_delta": coherence_delta,
                "grounding_success": grounding_success,
                "agent_chose_depth": True,
                "decision_basis": decision_basis,
                "governing_basis": governing_basis,
                "purpose_anchor": purpose_anchor,
                "refs_used": refs_used[:6],
                "next_instance_hint": next_instance_hint,
            },
            "ttl": "session",
        },
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
            "consolidation_history_count": int((library_boundary or {}).get("consolidation_history_count") or 0)
            if isinstance(library_boundary, Mapping)
            else 0,
        },
        "retention_tier_truth": {
            "retention_tier": str(gravity_tax_policy.get("retention_tier") or "").strip() or None,
            "retention_tier_reason": str(gravity_tax_policy.get("retention_tier_reason") or "").strip() or None,
            "claim_strength_rule": "autonomy_outcome_memory_must_remain_attached_to_canonical_governed_memory_boundary_with_retention_truth",
        },
        "evidence": {
            "selected_action": selected_action,
            "chosen_coord": coord_for_pattern or None,
            "walk_hops_post": walk_hops_post,
            "continuity_candidate_count": len(continuity_candidates),
        },
    }
    prompt_class = (
        "open_query"
        if recursion_depth_observed >= 3
        else "continuity_query"
        if continuity_hint
        else "lightweight_query"
    )
    source_pattern = autonomy_outcome_memory["autonomy_pattern"]
    learned_autonomy_profile = {
        "contract_version": "learned-autonomy-profile-v1",
        "derived_not_persistent_selfhood": True,
        "traceable_to_autonomy_patterns": True,
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
            "consolidation_history_count": int((library_boundary or {}).get("consolidation_history_count") or 0)
            if isinstance(library_boundary, Mapping)
            else 0,
        },
        "hot_path_consumption_boundary": {
            "summary_first": True,
            "hot_path_mode": str((library_boundary or {}).get("hot_path_mode") or "summary_only"),
            "claim_strength_rule": "learned_autonomy_profile_should_be_consumed_summary_first_from_the_canonical_ledger_boundary",
        },
        "profile": {
            "preferred_recursion_depth_by_prompt_class": [
                {
                    "prompt_class": prompt_class,
                    "preferred_depth": recursion_depth_observed,
                    "governing_basis": governing_basis,
                    "purpose_anchor": purpose_anchor,
                    "evidence_count": 1,
                }
            ],
            "productive_coord_families": (
                [
                    {
                        "coord_type": coord_family,
                        "grounding_success_rate": 1.0 if grounding_success else 0.0,
                        "decision_basis": decision_basis,
                        "governing_basis": governing_basis,
                        "evidence_count": 1,
                    }
                ]
                if coord_family
                else []
            ),
            "action_preferences": [
                {
                    "action": selected_action,
                    "grounding_success_rate": 1.0 if grounding_success else 0.0,
                    "decision_basis": decision_basis,
                    "purpose_anchor": purpose_anchor,
                    "evidence_count": 1,
                }
            ],
            "deeper_walk_hint": next_instance_hint,
        },
        "traceability": {
            "source_pattern_count": 1,
            "source_patterns": [source_pattern],
            "attribution_rule": "every profile hint must derive from explicit autonomy_pattern records",
        },
    }
    enrichment_budget = {
        "token_budget": {
            "bounded": True,
            "max_tokens": max_tokens,
            "standing_policy_cap": standing_cap,
            "pressure_state": budget_pressure,
        },
        "time_budget": {
            "bounded": True,
            "turn_scoped_only": True,
        },
        "write_permission": {
            "required": True,
            "allowed": bool(standing_policy.get("write_commit_allowed", True)) if isinstance(standing_policy, Mapping) else True,
        },
        "policy_scope": {
            "tool_scope": str(standing_policy.get("tool_scope") or "") if isinstance(standing_policy, Mapping) else "",
            "retrieval_scope": str(standing_policy.get("retrieval_scope") or "") if isinstance(standing_policy, Mapping) else "",
            "retrieval_allowed": bool(standing_policy.get("retrieval_allowed")) if isinstance(standing_policy, Mapping) else None,
        },
    }
    between_turn_enrichment = {
        "contract_version": "between-turn-enrichment-v2",
        "system_scheduled_not_spontaneous": True,
        "continuity_infrastructure_not_background_agent": True,
        "explicit_surface_status": "explicit" if bool(ledger_id or source_pattern or learned_autonomy_profile) else "absent",
        "generic_session_history_rejected_as_evidence": True,
        "promotion_boundary": {
            "source_tiers": ["Sand", "Silt"],
            "target_tiers": ["Silt", "Clay"],
            "governed_promotion_required": True,
            "gravity_tax_linked": bool(gravity_tax_policy.get("explicit_retention_cost_policy")),
        },
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
            "consolidation_history_count": int((library_boundary or {}).get("consolidation_history_count") or 0)
            if isinstance(library_boundary, Mapping)
            else 0,
        },
        "latency_boundary": {
            "hot_path_mode": str((library_boundary or {}).get("hot_path_mode") or "summary_only"),
            "deeper_work_requires_fallback_or_deferral": bool(
                isinstance((library_boundary or {}).get("latency_boundary"), Mapping)
                and bool((((library_boundary or {}).get("latency_boundary") or {}).get("deep_history_requires_fallback_or_deferral")))
            ),
        },
        "prior_payload_context": {
            "attributable_resolved_context_present": bool(
                resolved_preview
                or any(bool(row.get("resolved_payload_present")) for row in ancestry_linked_records)
            ),
            "resolved_preview": resolved_preview[:6],
            "source_coords": source_coords[:6],
            "opened_payload_coords": opened_payload_coords[:6],
            "direct_decode_observed": direct_decode_observed,
            "promotion_allowed_when_attributable": True,
            "resolved_context_is_not_foundation_identity_rehydration": True,
        },
        "foundation_identity_rehydration": {
            "available": foundation_identity_available,
            "fields": foundation_identity_fields,
            "structured_runtime_surface_required": True,
        },
        "between_turn_enrichment": {
            "posture": "system_scheduled_and_policy_bounded",
            "may_do": [
                "summarize_autonomy_outcomes",
                "emit_autonomy_patterns",
                "update_learned_autonomy_profile",
                "preserve_compact_purpose_anchor_context",
                "carry_forward_attributable_resolved_prior_context",
            ],
            "may_not_do": [
                "mutate_governance_sensitive_state_without_authorization",
                "run_indefinitely",
                "act_as_unbounded_hidden_agent_loop",
            ],
            "observable_inputs": {
                "autonomy_pattern_present": True,
                "learned_profile_present": True,
                "ledger_id": ledger_id,
                "purpose_anchor": purpose_anchor,
            },
        },
        "enrichment_budget": enrichment_budget,
    }
    write_commit_allowed_now = (
        bool(standing_policy.get("write_commit_allowed", True))
        if isinstance(standing_policy, Mapping)
        else True
    )
    consolidation_target_tier = (
        "Clay"
        if grounding_success and write_commit_allowed_now
        else "Silt"
        if continuity_hint or selected_action in {"resolve", "reuse_path"}
        else "Sand"
    )
    bounded_async_consolidation_bridge = {
        "contract_version": "bounded-consolidation-bridge-v1",
        "phase_boundary": "phase_2_bridge_only",
        "bounded_async_only": True,
        "off_hot_path_by_default": True,
        "latency_relief_explicit": True,
        "consent_and_revocation_checkpoints_required": True,
        "speculative_sleep_or_retrocausal_claims_rejected": True,
        "bridge_scope": {
            "may_do": [
                "bounded_async_replay",
                "bounded_async_pruning",
                "selective_sand_to_silt_or_clay_promotion",
            ],
            "may_not_do": [
                "block_normal_chat_hot_path_by_default",
                "claim_full_sleep_architecture",
                "claim_retrocausal_or_speculative_substrate_behavior",
            ],
        },
        "promotion_boundary": {
            "source_tiers": ["Sand", "Silt"],
            "target_tiers": ["Silt", "Clay"],
            "governed_promotion_required": True,
            "target_tier_if_triggered": consolidation_target_tier,
        },
        "checkpoints": {
            "consent_registry_required": True,
            "revocation_permit_required": True,
            "write_permission_required": bool(enrichment_budget["write_permission"]["required"]),
        },
        "latency_boundary": {
            "interactive_path": "summary_only_or_skip",
            "deeper_replay_requires": "fallback_or_deferral",
            "reason": "heavy_replay_pruning_and_promotion_should_not_remain_on_interactive_path",
        },
        "evidence": {
            "retention_tier": gravity_tax_policy.get("retention_tier"),
            "purpose_anchor": purpose_anchor,
            "continuity_candidate_count": len(continuity_candidates),
            "write_commit_allowed": write_commit_allowed_now,
            "input_mode": str(meta.get("input_mode") or ""),
            "streaming": bool(meta.get("streaming")),
        },
    }
    vc_refs = (runtime_identity or {}).get("vc_refs") if isinstance((runtime_identity or {}).get("vc_refs"), Mapping) else {}
    principal_did = str((runtime_identity or {}).get("principal_did") or meta.get("principal_did") or "").strip()
    principal_subject = str((runtime_identity or {}).get("principal_canonical_subject") or "").strip()
    has_binding_refs = bool(
        isinstance(vc_refs, Mapping)
        and any(
            str(vc_refs.get(key) or "").strip()
            for key in ("credential_ref", "standing_envelope_ref", "wallet_binding_ref", "issuer_did")
        )
    )
    assurance_valid = str((assurance_verification or {}).get("status") or "").strip() == "valid"
    identity_assurance_posture = (
        "strong"
        if principal_did and principal_subject and has_binding_refs and assurance_valid
        else "moderate"
        if principal_did
        else "weak"
    )
    profile_claims: list[dict[str, Any]] = []
    learned_profile = learned_autonomy_profile.get("profile") if isinstance(learned_autonomy_profile, Mapping) else None
    if isinstance(learned_profile, Mapping):
        depth_entries = learned_profile.get("preferred_recursion_depth_by_prompt_class")
        if isinstance(depth_entries, list) and depth_entries:
            top_depth = depth_entries[0] if isinstance(depth_entries[0], Mapping) else None
            if isinstance(top_depth, Mapping):
                profile_claims.append(
                    {
                        "field": "preferred_recursion_depth",
                        "summary": {
                            "prompt_class": str(top_depth.get("prompt_class") or ""),
                            "preferred_depth": _int_or_none(top_depth.get("preferred_depth")),
                            "governing_basis": str(top_depth.get("governing_basis") or ""),
                            "purpose_anchor": str(top_depth.get("purpose_anchor") or ""),
                        },
                        "attribution": "learned_autonomy_profile.preferred_recursion_depth_by_prompt_class",
                    }
                )
        productive = learned_profile.get("productive_coord_families")
        if isinstance(productive, list) and productive:
            top_family = productive[0] if isinstance(productive[0], Mapping) else None
            if isinstance(top_family, Mapping):
                profile_claims.append(
                    {
                        "field": "productive_coord_family",
                        "summary": {
                            "coord_type": str(top_family.get("coord_type") or ""),
                            "grounding_success_rate": _coerce_float(top_family.get("grounding_success_rate"), 0.0),
                            "decision_basis": str(top_family.get("decision_basis") or ""),
                            "governing_basis": str(top_family.get("governing_basis") or ""),
                        },
                        "attribution": "learned_autonomy_profile.productive_coord_families",
                    }
                )
        actions = learned_profile.get("action_preferences")
        if isinstance(actions, list) and actions:
            top_action = actions[0] if isinstance(actions[0], Mapping) else None
            if isinstance(top_action, Mapping):
                profile_claims.append(
                    {
                        "field": "action_preference",
                        "summary": {
                            "action": str(top_action.get("action") or ""),
                            "grounding_success_rate": _coerce_float(top_action.get("grounding_success_rate"), 0.0),
                            "decision_basis": str(top_action.get("decision_basis") or ""),
                            "purpose_anchor": str(top_action.get("purpose_anchor") or ""),
                        },
                        "attribution": "learned_autonomy_profile.action_preferences",
                    }
                )
    readable_profile_snapshot = {
        "contract_version": "readable-profile-snapshot-v2",
        "summary_first": True,
        "not_full_latent_transparency": True,
        "identity_assurance_posture": {
            "level": identity_assurance_posture,
            "treat_as_strong_only_when": "did_binding_and_attestation_posture_are_sufficient",
            "principal_did_present": bool(principal_did),
            "principal_subject_present": bool(principal_subject),
            "assurance_verification_status": str((assurance_verification or {}).get("status") or "") or None,
        },
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
        },
        "principal_readable_boundary": {
            "ledger_id": ledger_id,
            "principal_did": principal_did or None,
            "principal_canonical_subject": principal_subject or None,
            "vc_ref_counts": {
                "present": len([key for key, value in dict(vc_refs).items() if str(value or "").strip()]),
            },
            "identity_layers": {
                "founding_constitution_distinct_from_verified_traits": True,
                "speculative_overlay_distinct_when_present": True,
                "resolved_constitution_context_distinct_from_runtime_foundation_identity": True,
            },
            "resolved_constitution_context": {
                "present": bool(
                    resolved_preview
                    or source_coords
                    or opened_payload_coords
                    or direct_decode_observed
                    or any(bool(row.get("resolved_payload_present")) for row in ancestry_linked_records)
                ),
                "resolved_preview": resolved_preview[:6],
                "source_coords": source_coords[:6],
                "opened_payload_coords": opened_payload_coords[:6],
                "direct_decode_observed": direct_decode_observed,
            },
            "runtime_foundation_identity": {
                "available": foundation_identity_available,
                "fields": foundation_identity_fields,
                "structured_runtime_surface_required": True,
            },
            "profile_claims": profile_claims[:3],
        },
        "clarifying_confidence_challenge": {
            "required_for_strong_claims": identity_assurance_posture == "weak",
            "fallback_mode": "honest_doubt_and_bounded_clarification" if identity_assurance_posture == "weak" else "not_required",
        },
    }
    aggregate_agent_self_profile = {
        "contract_version": "aggregate-agent-self-profile-v1",
        "aggregate_only": True,
        "no_per_principal_raw_leakage": True,
        "summary_first": True,
        "learned_patterns": [
            claim["summary"] | {"field": str(claim.get("field") or "")}
            for claim in profile_claims[:3]
            if isinstance(claim.get("summary"), Mapping)
        ],
        "source": {
            "derived_from": "learned_autonomy_profile",
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "source_pattern_count": int((learned_autonomy_profile.get("traceability") or {}).get("source_pattern_count") or 0)
            if isinstance(learned_autonomy_profile, Mapping)
            else 0,
            "resolved_constitution_context_present": bool(
                resolved_preview
                or source_coords
                or opened_payload_coords
                or direct_decode_observed
                or any(bool(row.get("resolved_payload_present")) for row in ancestry_linked_records)
            ),
            "runtime_foundation_identity_available": foundation_identity_available,
        },
    }
    library_boundary = (
        (runtime_identity or {}).get("library_boundary")
        if isinstance((runtime_identity or {}).get("library_boundary"), Mapping)
        else {}
    )
    river_library_boundary = {
        "contract_version": "river-library-boundary-v1",
        "roles": {
            "River": [
                "live_inference",
                "routing",
                "candidate_selection",
                "surface_level_recognition",
            ],
            "Library": [
                "durable_identity",
                "founding_constitution",
                "alias_and_supersession_history",
                "trust_anchor_state",
            ],
        },
        "read_boundary": {
            "policy_bounded": bool(standing_policy),
            "library_reads_allowed": bool(library_boundary),
            "hot_path_mode": str(library_boundary.get("hot_path_mode") or "summary_only"),
            "allowed_summary_reads": [
                "founding_constitution",
                "alias_history",
                "supersession_history",
            ],
        },
        "mutation_boundary": {
            "river_may_read_library": True,
            "river_may_mutate_library_directly": False,
            "library_mutations_are_governed_write_paths": True,
        },
        "continuity_rehydration": {
            "canonical_ledger_id": str(library_boundary.get("canonical_ledger_id") or ledger_id or "") or None,
            "foundation_identity_available": bool(
                isinstance(library_boundary.get("foundation_identity"), Mapping)
                and any(
                    str((library_boundary.get("foundation_identity") or {}).get(field) or "").strip()
                    for field in ("name", "purpose")
                )
            ),
            "alias_history_count": len(library_boundary.get("alias_history") or []) if isinstance(library_boundary.get("alias_history"), list) else 0,
            "supersession_history_count": len(library_boundary.get("supersession_history") or []) if isinstance(library_boundary.get("supersession_history"), list) else 0,
        },
        "latency_boundary": {
            "hot_path_budgeted": bool((library_boundary.get("latency_boundary") or {}).get("hot_path_budgeted")) if isinstance(library_boundary.get("latency_boundary"), Mapping) else True,
            "deep_history_requires_fallback_or_deferral": bool((library_boundary.get("latency_boundary") or {}).get("deep_history_requires_fallback_or_deferral")) if isinstance(library_boundary.get("latency_boundary"), Mapping) else True,
        },
        "topology_note": {
            "kernel_centric_seven_cube_topology": True,
            "per_cube_s1_s2_paths": True,
            "distinct_prime_assignments": True,
        },
    }
    consent_registry = {
        "contract_version": "consent-registry-v1",
        "declarative_scope_registry": True,
        "administratively_bounded": True,
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
            "claim_strength_rule": "consent_history_must_live_on_durable_canonical_ledger_state_not_split_namespaces",
        },
        "authority_basis": {
            "compact_and_operational": True,
            "authenticated_principal_present": bool(principal_did),
            "verification_present": bool(assurance_verification),
            "declared_end": "profile_level_learning_scope_governance",
            "distinctions": {
                "authenticated": bool(principal_did),
                "permitted": True,
                "authorized": identity_assurance_posture == "strong",
            },
            "rule": "authentication_or_verification_alone_do_not_confer_scope_authority",
        },
        "identity_assurance_posture": {
            "level": identity_assurance_posture,
            "consent_strength": (
                "strong"
                if identity_assurance_posture == "strong"
                else "provisional"
                if identity_assurance_posture == "weak"
                else "bounded"
            ),
        },
        "scopes": [
            {
                "scope": "learning.style",
                "allowed": True,
                "retention_rule": "session",
                "read_access": ["principal", "designated_auditor"],
                "declared_end": "improve_reasoning_style_continuity",
            },
            {
                "scope": "learning.opinions",
                "allowed": False,
                "retention_rule": "blocked",
                "read_access": ["principal"],
                "declared_end": "blocked_without_explicit_authorization",
            },
            {
                "scope": "cross_principal_inference",
                "allowed": False,
                "retention_rule": "blocked",
                "read_access": ["designated_auditor"],
                "declared_end": "blocked_pending_explicit_cross_principal_authorization",
            },
        ],
        "commit_time_scope_enforcement": {
            "required": True,
            "violation_result": "commit_rejected",
            "violation_code": "scope_violation",
            "off_hot_path_preferred": True,
        },
        "operational_constraints": {
            "avoid_scope_explosion": True,
            "admin_burden_acknowledged": True,
            "summary_first": True,
        },
        "weaker_posture_fallback": {
            "consent_acts_provisional": identity_assurance_posture == "weak",
            "bounded_clarification_allowed": identity_assurance_posture == "weak",
            "honest_doubt_required": identity_assurance_posture == "weak",
        },
    }
    profile_delta_record = {
        "contract_version": "profile-delta-record-v1",
        "compact_attribution_only": True,
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
            "claim_strength_rule": "profile_delta_history_must_follow_the_canonical_governed_memory_boundary",
        },
        "delta": {
            "scope": "learning.style",
            "decision_basis": decision_basis,
            "scope_authority": {
                "governing_basis": governing_basis,
                "authorized": identity_assurance_posture == "strong",
                "declared_end": "profile_level_learning_scope_governance",
            },
            "purpose_anchor": purpose_anchor,
            "profile_before": {
                "preferred_recursion_depth": None,
                "productive_coord_family": None,
                "action_preference": None,
            },
            "profile_after": {
                "preferred_recursion_depth": (
                    learned_profile.get("preferred_recursion_depth_by_prompt_class")[0].get("preferred_depth")
                    if isinstance(learned_profile, Mapping)
                    and isinstance(learned_profile.get("preferred_recursion_depth_by_prompt_class"), list)
                    and learned_profile.get("preferred_recursion_depth_by_prompt_class")
                    and isinstance(learned_profile.get("preferred_recursion_depth_by_prompt_class")[0], Mapping)
                    else None
                ),
                "productive_coord_family": (
                    learned_profile.get("productive_coord_families")[0].get("coord_type")
                    if isinstance(learned_profile, Mapping)
                    and isinstance(learned_profile.get("productive_coord_families"), list)
                    and learned_profile.get("productive_coord_families")
                    and isinstance(learned_profile.get("productive_coord_families")[0], Mapping)
                    else None
                ),
                "action_preference": (
                    learned_profile.get("action_preferences")[0].get("action")
                    if isinstance(learned_profile, Mapping)
                    and isinstance(learned_profile.get("action_preferences"), list)
                    and learned_profile.get("action_preferences")
                    and isinstance(learned_profile.get("action_preferences")[0], Mapping)
                    else None
                ),
            },
            "justification": next_instance_hint,
            "scope_check_result": "pass" if identity_assurance_posture != "weak" else "provisional",
            "source_pattern_count": int((learned_autonomy_profile.get("traceability") or {}).get("source_pattern_count") or 0)
            if isinstance(learned_autonomy_profile, Mapping)
            else 0,
        },
        "persistence_posture": {
            "async_preferred": True,
            "bounded_record_size": True,
            "not_token_by_token_hidden_state_trace": True,
        },
    }
    revocation_permit = {
        "contract_version": "revocation-permit-v1",
        "forward_scope_blocking": True,
        "bounded_retroactive_rollback": True,
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
            "claim_strength_rule": "revocation_and_rollback_history_must_follow_the_canonical_governed_memory_boundary",
        },
        "authority_requirement": {
            "identity_assurance_posture": identity_assurance_posture,
            "strong_authority_required_for_retroactive": True,
            "provisional_when_weak": identity_assurance_posture == "weak",
            "scope_authority_basis": "profile_level_learning_scope_governance",
        },
        "permit_shape": {
            "scope": "learning.style",
            "effective_mode": "forward_block" if identity_assurance_posture == "weak" else "forward_or_retroactive",
            "retroactive_window_days": 7 if identity_assurance_posture != "weak" else 0,
            "fallback_behavior": "generalize_or_delete_compact_deltas",
        },
        "rollback_expectations": {
            "off_hot_path_preferred": True,
            "bounded_window_only": True,
            "exception_handling_required": True,
        },
    }
    cross_principal_influence_audit = {
        "contract_version": "cross-principal-influence-audit-v1",
        "anonymized_reporting": True,
        "bounded_approximate_influence": True,
        "no_direct_principal_disclosure": True,
        "anti_theatrical": True,
        "canonical_ledger_resolution": {
            "canonical_ledger_id": str((library_boundary or {}).get("canonical_ledger_id") or ledger_id or "").strip() or None,
            "alias_history_count": len((library_boundary or {}).get("alias_history") or [])
            if isinstance((library_boundary or {}).get("alias_history"), list)
            else 0,
            "supersession_history_count": len((library_boundary or {}).get("supersession_history") or [])
            if isinstance((library_boundary or {}).get("supersession_history"), list)
            else 0,
            "claim_strength_rule": "influence_audit_history_must_follow_the_canonical_governed_memory_boundary",
        },
        "read_boundary": {
            "summary_first": True,
            "library_backed_or_precomputed_preferred": True,
            "hot_path_mode": str((library_boundary or {}).get("hot_path_mode") or "summary_only"),
        },
        "basis_distinction": {
            "raw_influence_is_not_evaluative_basis": True,
            "raw_influence_is_not_governing_basis": True,
            "rule": "influence_traces_do_not_by_themselves_explain_what_the_system_ought_to_optimize_for",
        },
        "audit_posture": {
            "identity_assurance_posture": identity_assurance_posture,
            "access_strength": "strong" if identity_assurance_posture == "strong" else "bounded",
            "exact_causality_prohibited": True,
        },
        "query_surfaces": [
            "response_level",
            "profile_level",
        ],
        "response_level": {
            "influence_records": [
                {
                    "principal_hash": "sha256:anonymous",
                    "influence_domain": "reasoning_style",
                    "influence_magnitude": 0.03 if continuity_hint else 0.0,
                    "approximation_note": "bounded estimate derived from compact profile and autonomy signals",
                    "evaluative_basis_claimed": False,
                    "governing_basis_claimed": False,
                }
            ],
            "contestable": True,
        },
        "profile_level": {
            "systemic_effect_summary": (
                "current learned profile may be weakly shaped by prior anonymized principal interactions"
            ),
            "dominant_domains": [
                claim.get("field")
                for claim in profile_claims[:3]
                if isinstance(claim.get("field"), str) and str(claim.get("field")).strip()
            ],
            "direct_principal_disclosure": False,
            "evaluative_basis_claimed": False,
            "governing_basis_claimed": False,
        },
    }
    padic_diagnostics = meta.get("padic_diagnostics") if isinstance(meta.get("padic_diagnostics"), Mapping) else None
    return {
        "resolve_summary": resolve_summary,
        "candidate_trace": candidate_trace or None,
        "autonomy_decision": autonomy_decision,
        "ancestry_recall": ancestry_recall,
        "diagnostic_observability": diagnostic_observability,
        "base4_runtime_state": base4_runtime_state,
        "self_model_continuity": self_model_continuity,
        "between_turn_persistence": between_turn_persistence,
        "unresolved_tension_and_commit": unresolved_tension_and_commit,
        "bounded_retention_pressure": bounded_retention_pressure,
        "gravity_tax_retention_policy": gravity_tax_retention_policy,
        "autonomy_outcome_memory": autonomy_outcome_memory,
        "learned_autonomy_profile": learned_autonomy_profile,
        "between_turn_enrichment": between_turn_enrichment,
        "bounded_async_consolidation_bridge": bounded_async_consolidation_bridge,
        "readable_profile_snapshot": readable_profile_snapshot,
        "aggregate_agent_self_profile": aggregate_agent_self_profile,
        "river_library_boundary": river_library_boundary,
        "consent_registry": consent_registry,
        "profile_delta_record": profile_delta_record,
        "revocation_permit": revocation_permit,
        "cross_principal_influence_audit": cross_principal_influence_audit,
        "consistency_check": consistency_check,
        "padic_diagnostics": padic_diagnostics,
    }


def _apply_turn_diagnostics(
    metadata_payload: dict[str, Any],
    *,
    autonomy_candidates: list[dict[str, Any]],
    autonomy_decision: Mapping[str, Any],
) -> None:
    candidate_trace = _canonical_candidate_trace(autonomy_candidates, max_k=_DIAGNOSTIC_TOP_K)
    metadata_payload["candidate_trace"] = candidate_trace
    metadata_payload["autonomy_decision"] = _canonical_autonomy_decision(
        autonomy_decision,
        candidate_trace=candidate_trace,
    )


def _autonomy_decision_from_candidates(
    candidates: list[dict[str, Any]],
    *,
    policy: str,
) -> dict[str, Any]:
    normalized_policy = "balanced"
    if isinstance(policy, str) and policy and policy.strip().lower() != "balanced":
        LOGGER.info(
            "autonomy_policy_legacy_ignored",
            extra={"requested_policy": policy, "active_policy": "balanced"},
        )
    top_k = _canonical_candidate_trace(candidates, max_k=4)
    if not top_k:
        return {
            "policy": normalized_policy,
            "action": "answer_from_priors",
            "reason": "no_candidates",
            "chosen_coord": None,
            "top_k": [],
            "utility": {"resolve": 0.0, "reuse_path": 0.0, "answer_from_priors": 0.2},
        }

    top = top_k[0]
    top_score = max(
        _coerce_float(top.get("relevance_score"), 0.0),
        _coerce_float(top.get("p_adic_score"), 0.0),
        _coerce_float(top.get("search_score"), 0.0),
        _coerce_float(top.get("recency_score"), 0.0),
    )
    top_tier = int(top.get("relevance_tier") or top.get("tier_rank") or 0)
    top_resolved = bool(top.get("resolved_payload_present"))
    top_source = str(top.get("source") or "")
    chosen_coord = str(top.get("coord") or "").strip() or None

    resolve_utility = top_score + (0.25 if top_resolved else 0.0)
    reuse_utility = top_score + (0.2 if top_source == "recent" else 0.0) - 0.05
    priors_penalty = 0.45 if (top_tier >= 2 or top_resolved) else 0.0
    priors_utility = max(0.0, 0.4 - priors_penalty)

    useful_candidates = [
        row for row in top_k
        if int(row.get("relevance_tier") or 4) <= 3 and str(row.get("origin_attestation") or "") != "model_response_wx"
    ]
    if not useful_candidates and top_score < 0.35 and not any(bool(row.get("resolved_payload_present")) for row in top_k):
        return {
            "policy": normalized_policy,
            "action": "request_new_candidate_set",
            "reason": "top_four_candidates_not_useful",
            "chosen_coord": chosen_coord,
            "top_k": top_k,
            "utility": {"resolve": 0.0, "reuse_path": 0.0, "answer_from_priors": 0.0},
        }

    action = "answer_from_priors"
    reason = "low_confidence_candidates"
    if top_tier >= 3 and top_resolved:
        action = "resolve"
        reason = "top_candidate_tier3_resolved"
    elif top_tier <= 2 and top_resolved:
        action = "reuse_path"
        reason = "resolved_context_can_be_reused"
    else:
        scored_actions = [
            ("resolve", resolve_utility),
            ("reuse_path", reuse_utility),
            ("answer_from_priors", priors_utility),
        ]
        scored_actions.sort(key=lambda row: row[1], reverse=True)
        action = scored_actions[0][0]
        reason = f"max_utility:{action}"

    return {
        "policy": normalized_policy,
        "action": action if action in _AUTONOMY_ACTIONS else "answer_from_priors",
        "reason": reason,
        "chosen_coord": chosen_coord,
        "top_k": top_k,
        "utility": {
            "resolve": round(resolve_utility, 3),
            "reuse_path": round(reuse_utility, 3),
            "answer_from_priors": round(priors_utility, 3),
        },
    }


def _autonomy_system_instruction(decision: Mapping[str, Any]) -> str:
    action = str(decision.get("action") or "answer_from_priors")
    coord = decision.get("chosen_coord")
    if action == "resolve" and isinstance(coord, str) and coord:
        return (
            "AUTONOMY DECISION: resolve from top candidate context first. "
            f"Prioritize grounded use of {coord} before model priors."
        )
    if action == "reuse_path":
        return (
            "AUTONOMY DECISION: reuse prior context continuity first. "
            "Prefer existing recent/resolved COORD context before opening new branches."
        )
    if action == "request_new_candidate_set":
        return (
            "AUTONOMY DECISION: the current four candidates are not useful. "
            "Request a fresh four-candidate set and do not treat skipped candidates as opened or grounded."
        )
    return (
        "AUTONOMY DECISION: answer from model priors with concise uncertainty language when context is weak. "
        "Do not claim inability to resolve if resolved context is already present."
    )


def _evaluate_resolution_consistency(
    *,
    response_text: str,
    resolve_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    summary = resolve_summary if isinstance(resolve_summary, Mapping) else {}
    resolved_count = int(summary.get("resolved_count") or 0)
    requested_count = int(summary.get("requested_count") or 0)
    if resolved_count <= 0:
        return {
            "status": "ok",
            "reason": "no_resolved_context",
            "contradiction": False,
            "resolved_count": resolved_count,
            "requested_count": requested_count,
            "retried": False,
            "retry_count": 0,
        }

    text = (response_text or "").strip()
    if not text:
        return {
            "status": "ok",
            "reason": "empty_response",
            "contradiction": False,
            "resolved_count": resolved_count,
            "requested_count": requested_count,
            "retried": False,
            "retry_count": 0,
        }

    matched = [
        pattern.pattern for pattern in _RESOLUTION_CONTRADICTION_PATTERNS if pattern.search(text)
    ]
    contradiction = bool(matched)
    return {
        "status": "contradiction" if contradiction else "ok",
        "reason": "claims_unresolvable_with_resolved_context" if contradiction else "grounded_or_neutral",
        "contradiction": contradiction,
        "resolved_count": resolved_count,
        "requested_count": requested_count,
        "matched_patterns": matched[:4],
        "retried": False,
        "retry_count": 0,
    }


def _consistency_retry_system_prompt(resolve_summary: Mapping[str, Any] | None) -> str:
    summary = resolve_summary if isinstance(resolve_summary, Mapping) else {}
    resolved = summary.get("resolved_coords")
    resolved_coords = resolved if isinstance(resolved, list) else []
    resolved_preview = ", ".join([str(coord) for coord in resolved_coords[:3]])
    return (
        "CONSISTENCY RETRY: You already have resolved COORD context in this turn. "
        "Regenerate the answer grounded in the resolved context. "
        "Do not claim inability to access or resolve when resolved context is available. "
        f"Resolved preview: {resolved_preview or 'available'}."
    )


async def _retry_on_resolution_contradiction(
    *,
    provider: str,
    base_messages: list[ChatCompletionMessageParam],
    max_tokens: int,
    candidate_text: str,
    resolve_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    first_check = _evaluate_resolution_consistency(
        response_text=candidate_text,
        resolve_summary=resolve_summary,
    )
    if first_check.get("status") != "contradiction":
        return {
            "applied": False,
            "text": candidate_text,
            "cost_usd": 0.0,
            "latency_ms": 0.0,
            "usage_prompt_tokens": 0,
            "usage_completion_tokens": 0,
            "finish_reason": None,
            "consistency_check": first_check,
        }

    retry_messages: list[ChatCompletionMessageParam] = list(base_messages)
    retry_messages.append(
        cast(ChatCompletionMessageParam, {"role": "assistant", "content": candidate_text})
    )
    retry_messages.append(
        cast(
            ChatCompletionMessageParam,
            {"role": "system", "content": _consistency_retry_system_prompt(resolve_summary)},
        )
    )
    try:
        retry_raw, retry_cost, retry_latency, retry_usage, retry_finish = await complete_chat(
            provider=provider,
            messages=retry_messages,
            max_tokens=max_tokens,
        )
    except Exception:
        first_check["retried"] = True
        first_check["retry_count"] = 1
        first_check["retry_status"] = "failed"
        return {
            "applied": False,
            "text": candidate_text,
            "cost_usd": 0.0,
            "latency_ms": 0.0,
            "usage_prompt_tokens": 0,
            "usage_completion_tokens": 0,
            "finish_reason": None,
            "consistency_check": first_check,
        }

    retry_clean, _retry_meta, _retry_ok = _extract_response_payload(retry_raw)
    retry_text = (retry_clean or retry_raw or "").strip()
    if not retry_text:
        first_check["retried"] = True
        first_check["retry_count"] = 1
        first_check["retry_status"] = "empty"
        return {
            "applied": False,
            "text": candidate_text,
            "cost_usd": float(retry_cost or 0.0),
            "latency_ms": float(retry_latency or 0.0),
            "usage_prompt_tokens": int(getattr(retry_usage, "prompt_tokens", 0) or 0),
            "usage_completion_tokens": int(getattr(retry_usage, "completion_tokens", 0) or 0),
            "finish_reason": retry_finish,
            "consistency_check": first_check,
        }

    retry_check = _evaluate_resolution_consistency(
        response_text=retry_text,
        resolve_summary=resolve_summary,
    )
    retry_check["retried"] = True
    retry_check["retry_count"] = 1
    retry_check["retry_status"] = "applied"
    return {
        "applied": True,
        "text": retry_text,
        "cost_usd": float(retry_cost or 0.0),
        "latency_ms": float(retry_latency or 0.0),
        "usage_prompt_tokens": int(getattr(retry_usage, "prompt_tokens", 0) or 0),
        "usage_completion_tokens": int(getattr(retry_usage, "completion_tokens", 0) or 0),
        "finish_reason": retry_finish,
        "consistency_check": retry_check,
    }


def _compute_loop_risk(
    *,
    response_text: str,
    mentioned_coords: list[str],
    resolved_coords: set[str],
    hysteresis_coherence: float | None,
    lawfulness_level: int | None,
) -> dict[str, Any]:
    text = response_text or ""
    lowered = text.lower()
    hits = 0
    for phrase in _CLOSURE_PHRASES:
        if phrase in lowered:
            hits += lowered.count(phrase)

    token_count = max(1, len(re.findall(r"[A-Za-z0-9]+", lowered)))
    hits_per_200 = hits / max(1.0, token_count / 200.0)
    closure_pressure = min(1.0, hits_per_200)

    mentioned_total = len(mentioned_coords)
    resolved_total = len([coord for coord in mentioned_coords if coord in resolved_coords])
    if mentioned_total <= 0:
        grounding_gap = 0.0
    else:
        grounding_gap = max(0.0, min(1.0, 1.0 - (resolved_total / mentioned_total)))

    h_value = 0.5 if hysteresis_coherence is None else max(0.0, min(1.0, hysteresis_coherence))
    instability = max(0.0, min(1.0, 1.0 - h_value))

    if lawfulness_level is None:
        lawfulness_gap = 0.5
    else:
        lawfulness_gap = max(0.0, min(1.0, 1.0 - (float(lawfulness_level) / 3.0)))

    loop_risk = max(
        0.0,
        min(
            1.0,
            (0.35 * closure_pressure)
            + (0.35 * grounding_gap)
            + (0.20 * instability)
            + (0.10 * lawfulness_gap),
        ),
    )

    sensitivity = max(0.0, min(1.0, LOOP_SENSITIVITY))
    warn_threshold = 0.55 - (0.20 * sensitivity)
    hard_threshold = 0.75 - (0.25 * sensitivity)
    return {
        "loop_risk": loop_risk,
        "closure_pressure": closure_pressure,
        "grounding_gap": grounding_gap,
        "instability": instability,
        "lawfulness_gap": lawfulness_gap,
        "warn_threshold": warn_threshold,
        "hard_threshold": hard_threshold,
    }


def _expand_attachment_parts(
    store: LedgerStoreV2,
    attachment_coords: list[str],
    entity: str,
    max_parts: int = 5,
) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for coord in attachment_coords:
        normalized = normalise_coord(coord)
        namespace = normalized.get("namespace") or entity
        bare = normalized.get("bare")
        if not bare:
            continue
        attachment_coord = f"{namespace}:{bare}" if namespace else bare
        if attachment_coord in seen:
            continue
        seen.add(attachment_coord)
        try:
            entry = store.read(attachment_coord)
        except Exception:
            entry = None
        if not entry:
            continue
        metadata = entry.state.metadata or {}
        parts = metadata.get("attachment_parts") if isinstance(metadata.get("attachment_parts"), list) else []
        if not parts:
            continue
        attachment_group = metadata.get("attachment_group") or bare
        parts_added = 0
        for part in parts:
            if not isinstance(part, dict):
                continue
            suffix = part.get("part_suffix")
            if not suffix and isinstance(part.get("index"), int):
                suffix = f"T{part['index']:03d}"
            if not isinstance(suffix, str) or not attachment_group:
                continue
            part_coord = f"{attachment_group}-{suffix}"
            if namespace:
                part_coord = f"{namespace}:{part_coord}"
            if part_coord in seen:
                continue
            seen.add(part_coord)
            try:
                part_entry = store.read(part_coord)
            except Exception:
                part_entry = None
            if not part_entry:
                continue
            expanded.append(_entry_to_dict(part_entry))
            parts_added += 1
            if parts_added >= max_parts:
                break
    return expanded


def _estimate_tokens(text: str) -> int:
    """Fast character-based token approximation (1 token ~= 3-4 chars)."""
    if not text:
        return 0
    return len(text) // 3


def _hardening_level() -> int:
    raw = os.getenv("CHAT_HARDENING_LEVEL", "3")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 3
    if value < 0:
        return 0
    if value > 3:
        return 3
    return value


def _resolve_chat_max_tokens(*, history_len: int, retrieved_count: int) -> int | None:
    disable_limits = os.getenv("DISABLE_RESPONSE_TOKEN_LIMITS", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if disable_limits:
        return None

    level = _hardening_level()
    if level == 0:
        return None

    default_map = {3: CHAT_MAX_TOKENS_DEFAULT, 2: 768, 1: 1024}
    fast_map = {3: CHAT_MAX_TOKENS_FAST, 2: 640, 1: 896}
    med_map = {3: CHAT_MAX_TOKENS_MED, 2: 704, 1: 960}

    target = int(os.getenv("CHAT_MAX_TOKENS_DEFAULT", str(default_map[level])))
    fast_target = int(os.getenv("CHAT_MAX_TOKENS_FAST", str(fast_map[level])))
    med_target = int(os.getenv("CHAT_MAX_TOKENS_MED", str(med_map[level])))
    if retrieved_count <= 1 and history_len <= 8:
        target = min(target, fast_target)
    elif retrieved_count <= 3:
        target = min(target, med_target)
    return max(64, target)


def _extract_response_payload(reply_text: str) -> tuple[str, Dict[str, Any], bool]:
    """Return the visible text and trailing JSON metadata block, if present.

    The function searches from the end of the message for the last well-formed
    JSON object, trimming surrounding markdown/backtick noise. If parsing fails,
    an empty metadata dictionary is returned and ``parsed_ok`` is ``False``.
    """

    if not reply_text:
        return "", {}, False

    decoder = json.JSONDecoder()
    metadata: dict[str, Any] | None = None
    metadata_span: tuple[int, int] | None = None

    for start in range(len(reply_text) - 1, -1, -1):
        if reply_text[start] != "{":
            continue

        try:
            parsed, relative_end = decoder.raw_decode(reply_text[start:])
        except json.JSONDecodeError:
            continue

        end_index = start + relative_end
        trailing = reply_text[end_index:]
        if trailing.strip("`\n\r\t "):
            continue

        metadata = parsed if isinstance(parsed, dict) else {}
        metadata_span = (start, end_index)
        break

    if metadata_span is None:
        return reply_text.strip(), {}, False

    clean_text = _strip_trailing_markdown_noise(reply_text[: metadata_span[0]])
    return clean_text, metadata if metadata is not None else {}, True


_METRIC_QUERY_PATTERN = re.compile(
    r"\b(eq9|score|scores|drift|thresholds?|baselines?|meaning|status|probabilit|token|output|consolidat|delta|deltas?|compare|comparison|changed?|improv(?:e|ed|ement)|pass|fail|blocked)\b",
    re.IGNORECASE,
)
_DELTA_CLAIM_PATTERN = re.compile(
    r"([+-]\s*\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*%|threshold\s+(?:moved|adjusted)|chances?\s+to|probabilit)",
    re.IGNORECASE,
)
_NUMBER_PATTERN = re.compile(r"[+-]?\d+(?:\.\d+)?%?")


def _is_metrics_query(user_message: str) -> bool:
    message = str(user_message or "")
    if not message.strip():
        return False
    if re.search(r"\bmetrics?\b", message, re.IGNORECASE):
        return bool(
            re.search(
                r"(%|\b(eq9|score|drift|threshold|baseline|delta|compare|comparison|change|changes|changed|improv(?:e|ed|ement)|pass|fail|blocked|output_tokens?|token)\b)",
                message,
                re.IGNORECASE,
            )
        )
    return bool(_METRIC_QUERY_PATTERN.search(message))


def _collect_grounding_text(memories: Mapping[str, Any], metadata_payload: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    decoded = memories.get("decoded_context")
    if isinstance(decoded, list):
        for item in decoded:
            if isinstance(item, str) and item.strip():
                chunks.append(item)
    context_items = memories.get("context")
    if isinstance(context_items, list):
        for item in context_items:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text)
    summary = memories.get("summary")
    if isinstance(summary, dict):
        raw = summary.get("raw")
        if isinstance(raw, str) and raw.strip():
            chunks.append(raw)
    eq9_eval = metadata_payload.get("eq9_eval")
    if isinstance(eq9_eval, dict):
        chunks.append(json.dumps(eq9_eval, sort_keys=True))
    pre = metadata_payload.get("introspect_snapshot_pre")
    if isinstance(pre, dict):
        chunks.append(json.dumps(pre.get("appraisal") or {}, sort_keys=True))
    return "\n".join(chunks)


def _has_ungrounded_numeric_delta_claims(response_text: str, source_text: str) -> bool:
    if not _DELTA_CLAIM_PATTERN.search(response_text or ""):
        return False
    source_tokens = {token.replace(" ", "") for token in _NUMBER_PATTERN.findall(source_text or "")}
    response_tokens = _NUMBER_PATTERN.findall(response_text or "")
    unsupported = 0
    for token in response_tokens:
        normalized = token.replace(" ", "")
        if normalized not in source_tokens:
            unsupported += 1
    return unsupported >= 2


def _build_grounded_metrics_fallback(metadata_payload: Mapping[str, Any]) -> str:
    eq9_eval = metadata_payload.get("eq9_eval")
    if not isinstance(eq9_eval, Mapping):
        eq9_eval = {}
    checks = eq9_eval.get("checks") if isinstance(eq9_eval.get("checks"), Mapping) else {}
    score = (checks.get("score") or {}).get("current") if isinstance(checks, Mapping) else None
    law = (checks.get("law") or {}).get("current") if isinstance(checks, Mapping) else None
    drift = (checks.get("drift") or {}).get("current") if isinstance(checks, Mapping) else None
    output_tokens = eq9_eval.get("output_tokens")
    eq9_target = metadata_payload.get("eq9_target") if isinstance(metadata_payload.get("eq9_target"), Mapping) else {}
    output_soft_target = (eq9_target or {}).get("output_tokens_soft")
    if not isinstance(output_soft_target, (int, float)):
        output_soft_target = 220
    lines = [
        "I can report the values visible in this turn, but I cannot verify percentage deltas or threshold changes from the resolved context provided here.",
    ]
    if isinstance(score, (int, float)) and isinstance(law, (int, float)) and isinstance(drift, (int, float)):
        lines.append(f"Observed checks: score={float(score):.3f}, law={float(law):.3f}, drift={float(drift):.3f}.")
    if isinstance(output_tokens, (int, float)):
        lines.append(
            f"Observed output_tokens={int(output_tokens)} against EQ9 soft target={float(output_soft_target):g}."
        )
    lines.append("Do you want me to stay with observed values only, or compare against a prior snapshot or threshold set if you provide one?")
    return "\n".join(lines)


def _apply_metrics_grounding_guard(
    *,
    user_message: str,
    response_text: str,
    memories: Mapping[str, Any],
    metadata_payload: Mapping[str, Any],
) -> tuple[str, bool]:
    if not _is_metrics_query(user_message):
        return response_text, False
    source_text = _collect_grounding_text(memories, metadata_payload)
    if not _has_ungrounded_numeric_delta_claims(response_text, source_text):
        return response_text, False
    return _build_grounded_metrics_fallback(metadata_payload), True


def _extract_inline_citations(text: str) -> List[str]:
    citations = re.findall(r"\[[^\]\n]{1,64}\]", text)
    return list(dict.fromkeys(citations))


def _infer_intents(*, user_message: str, assistant_text: str) -> List[str]:
    combined = f"{user_message}\n{assistant_text}".lower()
    intents: list[str] = []
    intent_keywords = {
        "search": ["search", "find", "lookup"],
        "summarize": ["summary", "summarize", "summarise", "tl;dr"],
        "plan": ["plan", "steps", "roadmap", "next"],
        "cite": ["cite", "citation", "reference"],
    }

    for intent, keywords in intent_keywords.items():
        if any(keyword in combined for keyword in keywords):
            intents.append(intent)

    return intents or ["respond"]


def _merge_knowledge_trees(*trees: Optional[List[Any]]) -> List[Dict[str, Any]]:
    return merge_knowledge_trees(*trees, limit=KNOWLEDGE_TREE_LIMIT)


def _coerce_knowledge_tree_key(
    key_obj: Any,
    entry_id: Any | None = None,
) -> dict[str, Any] | None:
    candidate = key_obj if key_obj not in (None, "") else entry_id
    clean_key: dict[str, Any] | None = None

    if candidate is not None and hasattr(candidate, "dict"):
        clean_key = candidate.dict()
    elif hasattr(candidate, "__dict__"):
        clean_key = candidate.__dict__
    elif isinstance(candidate, dict):
        clean_key = candidate
    elif isinstance(candidate, str):
        key_str = candidate.strip()
        if key_str:
            if ":" in key_str:
                namespace, identifier = key_str.rsplit(":", 1)
                clean_key = {"namespace": namespace, "identifier": identifier}
            else:
                clean_key = {"coordinate": key_str}

    return clean_key


def _canonical_coord_from_item(item: Mapping[str, Any]) -> str | None:
    coord = item.get("coord")
    if isinstance(coord, str) and coord.strip():
        return coord.strip()
    namespace = item.get("namespace")
    identifier = item.get("identifier")
    if isinstance(namespace, str) and namespace.strip() and isinstance(identifier, str) and identifier.strip():
        return f"{namespace.strip()}:{identifier.strip()}"
    coordinate = item.get("coordinate")
    if isinstance(coordinate, str) and coordinate.strip():
        return coordinate.strip()
    return None


def _coerce_knowledge_tree_key_from_retrieved(item: Mapping[str, Any]) -> dict[str, Any] | None:
    coord = _canonical_coord_from_item(item)
    if coord:
        if ":" in coord:
            namespace, identifier = coord.rsplit(":", 1)
            if namespace and identifier:
                return {"namespace": namespace, "identifier": identifier}
        return {"coordinate": coord}
    return _coerce_knowledge_tree_key(item.get("key"), entry_id=item.get("entry_id"))


def _set_canonical_coord(item: dict[str, Any]) -> dict[str, Any]:
    coord = _canonical_coord_from_item(item)
    if coord:
        item["coord"] = coord
    return item


def _build_fallback_metadata(
    user_message: str, assistant_text: str, knowledge_tree: List[Dict[str, Any]]
) -> Dict[str, Any]:
    norm = normalise_text(assistant_text)
    intents = _infer_intents(user_message=user_message, assistant_text=assistant_text)
    citations = _extract_inline_citations(assistant_text)

    tags: list[str] = []
    topics = norm.get("topics", [])
    if isinstance(topics, list):
        for topic in topics:
            if topic not in tags:
                tags.append(topic)

    keywords = re.findall(r"[A-Za-z]{4,}", f"{user_message} {assistant_text}".lower())
    common_keywords = [word for word, _ in Counter(keywords).most_common(5) if word not in tags]
    tags.extend(common_keywords)

    fallback: Dict[str, Any] = {
        "researcher": {
            "tags": tags,
            "intent": intents[0] if intents else "respond",
            "intents": intents,
        },
        "citations": citations,
        "quotes": norm.get("quotes", []),
    }

    if knowledge_tree:
        fallback["knowledge_tree"] = knowledge_tree

    return fallback


def _count_emitted_refs(
    knowledge_tree: List[Dict[str, Any]],
    metadata_payload: Dict[str, Any],
    assistant_text: str,
) -> int:
    ref_keys: set[str] = set()
    for item in knowledge_tree:
        try:
            ref_keys.add(json.dumps(item, sort_keys=True))
        except TypeError:
            ref_keys.add(str(item))
    raw_citations = metadata_payload.get("citations") if isinstance(metadata_payload, dict) else None
    citations: list[str] = []
    if isinstance(raw_citations, list):
        citations = [str(item) for item in raw_citations if str(item)]
    if not citations:
        citations = _extract_inline_citations(assistant_text)
    for citation in citations:
        ref_keys.add(f"citation:{citation}")
    return len(ref_keys)


def _derive_search_flags(
    req: ChatRequest,
    memories: Mapping[str, Any],
) -> tuple[bool | None, bool | None]:
    eligible_for_search = req.eligible_for_search
    search_used = req.search_used
    if eligible_for_search is None:
        eligible_for_search = True
    if search_used is None:
        search_used = bool(memories.get("enhanced_query"))
    return eligible_for_search, search_used


def _telemetry_store_from_request(request: Request) -> TelemetryStore | None:
    try:
        service = LedgerService.from_request(request)
    except HTTPException:
        return None
    return service.telemetry_store()


def _optional_ledger_service(request: Request) -> LedgerService | None:
    try:
        return LedgerService.from_request(request)
    except HTTPException:
        return None


def _canonicalize_ledger_scope(request: Request, ledger_id: str) -> str:
    normalized = str(ledger_id or "").strip()
    if not normalized:
        return normalized
    service = _optional_ledger_service(request)
    if service is None:
        return normalized
    return service.resolve_canonical_ledger_id(normalized)


def _publish_decision_artifact_identity(
    *,
    db: Any,
    metadata: dict[str, Any],
    turn_coordinate: str | None,
) -> dict[str, Any] | None:
    identity = metadata.get("decision_artifact_identity")
    if not isinstance(identity, dict):
        return None
    untp_hash = str(identity.get("untp_hash") or identity.get("object_id") or "").strip()
    public_object_id = str(identity.get("public_object_id") or "").strip()
    if not untp_hash or not public_object_id:
        return None
    resolved_coords = metadata.get("resolved_coords") if isinstance(metadata.get("resolved_coords"), list) else []
    evidence_refs = [str(item).strip() for item in resolved_coords if isinstance(item, str) and str(item).strip()]
    status_ref = public_object_id.rstrip("/") + "/status"
    runtime_identity = metadata.get("runtime_identity") if isinstance(metadata.get("runtime_identity"), dict) else {}
    contributor = metadata.get("contributor") if isinstance(metadata.get("contributor"), dict) else {}
    subject_id = (
        str(metadata.get("authority_subject_id") or "").strip()
        or str(metadata.get("canonical_subject") or "").strip()
        or str(runtime_identity.get("ledger_canonical_subject") or "").strip()
    )
    issuer_id = (
        str(contributor.get("principal_did") or "").strip()
        or str(metadata.get("authority_subject_id") or "").strip()
        or str(metadata.get("raw_actor") or "").strip()
    )
    published_identity = dict(identity)
    coord_bridge = published_identity.get("coord_bridge") if isinstance(published_identity.get("coord_bridge"), dict) else {}
    published_identity["status_ref"] = status_ref
    published_identity["publication_state"] = "published"
    published_identity["coord_bridge"] = {
        **coord_bridge,
        "coord_ref": str(turn_coordinate or "").strip() or None,
        "coord_exposed_as_primary": False,
        "bridge_state": "coord_assigned" if str(turn_coordinate or "").strip() else "coord_unassigned",
    }
    record = upsert_public_object(
        db,
        public_object_id=public_object_id,
        object_kind="decision-artifact",
        object_id=untp_hash,
        subject_id=subject_id or "subject:unknown",
        issuer_id=issuer_id or "issuer:unknown",
        content_digest=untp_hash,
        coord_ref=str(turn_coordinate or "").strip() or None,
        evidence_refs=evidence_refs,
        status_ref=status_ref,
        lifecycle_state="current",
        shareability="share-ready",
        artifact_identity=published_identity,
    )
    metadata["decision_artifact_identity"] = published_identity
    return record


def _resolve_explicit_ledger_id(request: Request, payload_ledger_id: str | None) -> str:
    return _canonicalize_ledger_scope(
        request,
        resolve_ledger_scope_or_raise(
            request,
            payload_ledger_id=payload_ledger_id,
            hint="provide ledger_id in payload or x-ledger-id header",
        ),
    )


def _canonical_commit_web4_key(entity: str, candidate: Any) -> str:
    raw = str(candidate or "").strip()
    if raw and _CANONICAL_WEB4_RE.match(raw):
        return raw
    lite_match = _LITE_WEB4_RE.match(raw)
    entity_hash = hashlib.md5(str(entity).encode("utf-8")).hexdigest()[:8].upper()
    if lite_match:
        return f"WX-{entity_hash}-{lite_match.group(1)}"
    return f"WX-{entity_hash}-{int(time.time())}"


def _standing_policy_for_chat_request(request: Request, req: ChatRequest) -> dict[str, Any]:
    metadata = dict(req.metadata) if isinstance(req.metadata, Mapping) else {}
    if isinstance(req.standing_envelope, Mapping):
        metadata.setdefault("standing_envelope", dict(req.standing_envelope))
    authority_subject = resolve_authority_subject(request, metadata=metadata)
    authority_subject_id = str(authority_subject.get("authority_subject_id") or "").strip()
    authority_state = None
    db = getattr(getattr(request, "app", None), "state", None)
    db = getattr(db, "db", None)
    if authority_subject_id and db is not None:
        authority_state = get_authority_state(db, authority_subject_id)
    return resolve_standing_policy(
        metadata=metadata,
        standing_envelope=req.standing_envelope if isinstance(req.standing_envelope, Mapping) else None,
        authority_state=authority_state,
    )


def _standing_policy_for_write_metadata(request: Request, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    metadata_map = dict(metadata) if isinstance(metadata, Mapping) else {}
    authority_subject = resolve_authority_subject(request, metadata=metadata_map)
    authority_subject_id = str(authority_subject.get("authority_subject_id") or "").strip()
    db = getattr(getattr(request, "app", None), "state", None)
    db = getattr(db, "db", None)
    authority_state = get_authority_state(db, authority_subject_id) if authority_subject_id and db is not None else None
    return resolve_standing_policy(
        metadata=metadata_map,
        authority_state=authority_state,
    )


def _write_telemetry_background(store: TelemetryStore, telemetry: TurnTelemetry) -> None:
    try:
        store.write_event(telemetry)
    except Exception:
        LOGGER.warning("Failed to emit background telemetry", exc_info=True)


def _build_turn_telemetry(
    *,
    req: ChatRequest,
    entity: str,
    coordinate: str | None,
    web4_key: str,
    model: str | None,
    provider: str | None,
    cost: float,
    gen_input_tokens: int | None,
    gen_output_tokens: int | None,
    emitted_refs: int,
    resolve_successes: int,
    resolve_attempts: int,
    eligible_for_search: bool | None,
    search_used: bool | None,
    search_succeeded: bool | None,
    metadata_payload: Mapping[str, Any],
) -> TurnTelemetry:
    e6_header = metadata_payload.get("e6_header_v0_fields")
    e6_scoring = metadata_payload.get("e6_scoring")
    e6_window = (e6_scoring or {}).get("window") if isinstance(e6_scoring, dict) else None
    promotion = metadata_payload.get("promotion")
    quarantine = (
        metadata_payload.get("quarantine_write")
        if isinstance(metadata_payload.get("quarantine_write"), Mapping)
        else None
    )
    quarantine_reason = (
        str(quarantine.get("reason"))
        if isinstance(quarantine, Mapping) and isinstance(quarantine.get("reason"), str)
        else None
    )
    return TurnTelemetry(
        ids=TelemetryIds(
            session_id=req.session_id,
            namespace=entity,
            entity=entity,
            turn_id=coordinate or web4_key,
            timestamp=datetime.utcnow(),
        ),
        request_id=coordinate or web4_key,
        tenant_id=entity,
        surface=SurfaceName.CHAT.value,
        mode=(
            str(req.metadata.get("benchmark_mode"))
            if isinstance(req.metadata, Mapping) and isinstance(req.metadata.get("benchmark_mode"), str)
            else "chat"
        ),
        build_sha=os.getenv("GIT_SHA", "").strip() or "unknown",
        principal_hash=f"sha256:{hashlib.sha256(req.session_id.encode('utf-8')).hexdigest()[:16]}",
        model=model,
        provider=provider,
        cost=cost,
        gen_input_tokens=gen_input_tokens,
        gen_output_tokens=gen_output_tokens,
        references=TelemetryReferences(
            emitted_refs=emitted_refs,
            resolve_successes=resolve_successes,
            resolve_attempts=resolve_attempts,
        ),
        search=TelemetrySearchFlags(
            requested=eligible_for_search,
            used=search_used,
            succeeded=search_succeeded,
        ),
        e6_mode=(
            int((e6_header or {}).get("mode"))
            if isinstance(e6_header, dict) and isinstance((e6_header or {}).get("mode"), (int, float))
            else None
        ),
        e6_route=(
            int((e6_header or {}).get("route"))
            if isinstance(e6_header, dict) and isinstance((e6_header or {}).get("route"), (int, float))
            else None
        ),
        e6_quality_tier=(
            str(metadata_payload.get("quality_tier"))
            if isinstance(metadata_payload.get("quality_tier"), str)
            else None
        ),
        e6_bridge_allowed=(
            bool((e6_scoring or {}).get("bridge_allowed_runtime"))
            if isinstance(e6_scoring, dict)
            and isinstance((e6_scoring or {}).get("bridge_allowed_runtime"), bool)
            else None
        ),
        e6_promotion_allowed=(
            bool((promotion or {}).get("allowed"))
            if isinstance(promotion, dict) and isinstance((promotion or {}).get("allowed"), bool)
            else None
        ),
        e6_v_int_mean_3=(
            float((e6_window or {}).get("V_int_mean_3"))
            if isinstance(e6_window, dict) and isinstance((e6_window or {}).get("V_int_mean_3"), (int, float))
            else None
        ),
        e6_v_int_std_3=(
            float((e6_window or {}).get("V_int_std_3"))
            if isinstance(e6_window, dict) and isinstance((e6_window or {}).get("V_int_std_3"), (int, float))
            else None
        ),
        quarantine_write=(
            bool(quarantine.get("blocked"))
            if isinstance(quarantine, Mapping) and isinstance(quarantine.get("blocked"), bool)
            else None
        ),
        quarantine_reason=quarantine_reason,
    )


def _canonical_appraisal_payload(
    appraisal_payload: Mapping[str, Any] | None,
    metadata_payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    post = (
        metadata_payload.get("introspect_snapshot_post")
        if isinstance(metadata_payload, Mapping)
        else None
    )
    post_appraisal = post.get("appraisal") if isinstance(post, Mapping) else None
    if isinstance(post_appraisal, Mapping):
        return dict(post_appraisal)
    if isinstance(appraisal_payload, Mapping):
        return dict(appraisal_payload)
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_eval_contract(
    *,
    metadata_payload: Mapping[str, Any] | None,
    appraisal_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(metadata_payload, Mapping):
        return None
    e6_header = metadata_payload.get("e6_header_v0_fields")
    if not isinstance(e6_header, Mapping):
        return None

    mode = _int_or_none(e6_header.get("mode"))
    K = _int_or_none(e6_header.get("K"))
    P = _int_or_none(e6_header.get("P"))
    E = _int_or_none(e6_header.get("E"))
    V_q = _int_or_none(e6_header.get("V_q"))
    if None in {mode, K, P, E, V_q}:
        return None

    eq9_target = metadata_payload.get("eq9_target")
    momentum_min = 0
    if isinstance(eq9_target, Mapping):
        try:
            score_min = float(eq9_target.get("score_min", 0.0))
            momentum_min = max(0, min(65535, int(score_min * 65535)))
        except (TypeError, ValueError):
            momentum_min = 0

    output_tokens_est = _int_or_none(
        metadata_payload.get("gen_output_tokens")
        if metadata_payload.get("gen_output_tokens") is not None
        else metadata_payload.get("gen_output_tokens_est")
    )
    appraisal = (
        appraisal_payload
        if isinstance(appraisal_payload, Mapping)
        else metadata_payload.get("appraisal")
    )
    law_score = 1.0
    grace_score = 1.0
    if isinstance(appraisal, Mapping):
        try:
            law_score = float(appraisal.get("law_score", 1.0))
        except (TypeError, ValueError):
            law_score = 1.0
        try:
            grace_score = float(appraisal.get("grace_score", 1.0))
        except (TypeError, ValueError):
            grace_score = 1.0

    provenance_confidence = None
    replay_protected = None
    provenance_status = None
    provenance = metadata_payload.get("provenance_dual_write")
    if isinstance(provenance, Mapping):
        provenance_status = str(provenance.get("status") or "").strip() or None
        replay_protected = bool(provenance.get("session_jti_present"))
        confidence_map = {
            "dual_write_ok": 1.0,
            "did_only": 0.8,
            "legacy_only": 0.6,
            "missing_identity": 0.25,
        }
        if provenance_status in confidence_map:
            provenance_confidence = float(confidence_map[provenance_status])
            if replay_protected:
                provenance_confidence = min(1.0, provenance_confidence + 0.05)
            else:
                provenance_confidence = max(0.0, provenance_confidence - 0.05)

    return evaluate_eq_ladder(
        mode=mode,
        K=K,
        P=P,
        E=E,
        V_q=V_q,
        momentum_min=momentum_min,
        dW=int(_int_or_none(e6_header.get("dW")) or 0),
        output_tokens_est=output_tokens_est,
        law_score=law_score,
        grace_score=grace_score,
        provenance_confidence=provenance_confidence,
        replay_protected=replay_protected,
        provenance_status=provenance_status,
    )


def _pre_emission_block_reason(
    *,
    metadata_payload: Mapping[str, Any] | None,
    audit_mode: Mapping[str, Any] | None,
    loop_blocked: bool,
    eval_contract: Mapping[str, Any] | None,
) -> str | None:
    if isinstance(audit_mode, Mapping) and bool(audit_mode.get("blocked")):
        reason = str(audit_mode.get("reason") or "audit_blocked").strip()
        return f"audit_blocked:{reason}" if reason else "audit_blocked"
    if loop_blocked:
        return "loop_blocked"
    if isinstance(eval_contract, Mapping) and bool(eval_contract.get("blocked")):
        failed_eq = str(eval_contract.get("failed_eq") or "").strip()
        return f"eval_contract:{failed_eq}" if failed_eq else "eval_contract:blocked"
    if isinstance(metadata_payload, Mapping):
        governance = metadata_payload.get("governance")
        if isinstance(governance, Mapping):
            decision = str(governance.get("policy_decision") or "").strip().lower()
            if decision == "block":
                return "governance_policy_block"
        decision = str(metadata_payload.get("policy_decision") or "").strip().lower()
        if decision == "block":
            return "governance_policy_block"
    return None


def _trust_class_from_provenance(
    *,
    provenance_status: str | None,
    replay_protected: bool | None,
) -> str:
    status = str(provenance_status or "").strip().lower()
    if status in {"dual_write_ok", "session_token"}:
        return "T3" if replay_protected else "T2"
    if status in {"did_only", "principal_only"}:
        return "T2"
    if status == "legacy_only":
        return "T1"
    return "T0"


def _eq9_posture_class_from_contract(eval_contract: Mapping[str, Any] | None) -> str:
    if not isinstance(eval_contract, Mapping):
        return "P0"
    if bool(eval_contract.get("blocked")):
        return "P0"
    eq9 = eval_contract.get("eq9_metrics")
    if not isinstance(eq9, Mapping):
        return "P1"
    try:
        ypt = float(eq9.get("yield_per_token") or 0.0)
    except (TypeError, ValueError):
        ypt = 0.0
    try:
        prov_conf = float(eq9.get("provenance_confidence") or 0.0)
    except (TypeError, ValueError):
        prov_conf = 0.0
    replay_protected = bool(eq9.get("replay_protected"))
    if ypt < 0.001:
        return "P1"
    if ypt < 0.01:
        return "P2"
    return "P3" if prov_conf >= 0.9 and replay_protected else "P2"


def _build_posture_policy(
    *,
    action: str,
    eval_contract: Mapping[str, Any] | None,
    metadata_payload: Mapping[str, Any] | None = None,
    audit_mode: Mapping[str, Any] | None = None,
    loop_blocked: bool = False,
    pre_emission_block_reason: str | None = None,
) -> dict[str, Any]:
    provenance_status = None
    replay_protected = None
    if isinstance(metadata_payload, Mapping):
        provenance = metadata_payload.get("provenance_dual_write")
        if isinstance(provenance, Mapping):
            provenance_status = str(provenance.get("status") or "").strip() or None
            replay_protected = bool(provenance.get("session_jti_present"))
    if isinstance(eval_contract, Mapping):
        eq9 = eval_contract.get("eq9_metrics")
        if isinstance(eq9, Mapping):
            if replay_protected is None and isinstance(eq9.get("replay_protected"), bool):
                replay_protected = bool(eq9.get("replay_protected"))
            if not provenance_status and isinstance(eq9.get("provenance_status"), str):
                provenance_status = str(eq9.get("provenance_status") or "").strip() or None

    trust_class = _trust_class_from_provenance(
        provenance_status=provenance_status,
        replay_protected=replay_protected,
    )
    eq9_posture_class = _eq9_posture_class_from_contract(eval_contract)

    reason_code = "baseline_satisfied"
    policy_decision = "allow"
    failed_eq = None
    if isinstance(eval_contract, Mapping):
        failed_eq_raw = eval_contract.get("failed_eq")
        if isinstance(failed_eq_raw, str) and failed_eq_raw.strip():
            failed_eq = failed_eq_raw.strip()
    if isinstance(audit_mode, Mapping) and bool(audit_mode.get("blocked")):
        policy_decision = "deny"
        reason = str(audit_mode.get("reason") or "audit_blocked").strip()
        reason_code = f"audit_blocked:{reason}" if reason else "audit_blocked"
    elif loop_blocked:
        policy_decision = "deny"
        reason_code = "loop_blocked"
    elif pre_emission_block_reason:
        policy_decision = "deny"
        reason_code = pre_emission_block_reason
    elif isinstance(eval_contract, Mapping) and bool(eval_contract.get("blocked")):
        policy_decision = "deny"
        reason_code = f"eq_blocked:{failed_eq}" if failed_eq else "eq_blocked"
    elif trust_class == "T0" and action in {"chat.respond", "chat.stream.emit", "projection.evaluate"}:
        policy_decision = "degrade"
        reason_code = "trust_floor_degraded"

    raw_repairs = eval_contract.get("repair_actions") if isinstance(eval_contract, Mapping) else None
    repair_actions = []
    if isinstance(raw_repairs, list):
        for item in raw_repairs:
            if isinstance(item, Mapping):
                action_text = str(item.get("action") or "").strip()
                if action_text:
                    repair_actions.append(action_text)
    enforced_controls: list[str] = []
    if policy_decision == "degrade":
        enforced_controls = ["grounded_only", "no_override_sensitive", "read_only_sensitive"]
    if policy_decision == "deny":
        enforced_controls = ["emit_block_envelope_only"]
    return {
        "policy_gate_version": "policy-gate-v1",
        "pp_version": "pp-v1",
        "cb_version": "cb-v1",
        "obs_posture_version": "obs-posture-v1",
        "policy_decision": policy_decision,
        "reason_code": reason_code,
        "trust_class": trust_class,
        "eq9_posture_class": eq9_posture_class,
        "failed_eq": failed_eq,
        "repair_actions": repair_actions,
        "enforced_controls": enforced_controls,
        "action": action,
    }


def _blocked_response_text(posture_policy: Mapping[str, Any]) -> str:
    reason = str(posture_policy.get("reason_code") or "policy_blocked").strip()
    repairs = posture_policy.get("repair_actions")
    repair = None
    if isinstance(repairs, list):
        for item in repairs:
            if isinstance(item, str) and item.strip():
                repair = item.strip()
                break
    lines = [
        "Response blocked by policy gate.",
        f"- reason_code={reason}",
    ]
    if repair:
        lines.append(f"- next_step={repair}")
    else:
        lines.append("- next_step=provide grounded evidence or request resolver-backed context.")
    return "\n".join(lines)


def _mark_mediator_instability(ledger: Any, entity: str) -> None:
    if ledger is None:
        return
    crash_timestamp = datetime.utcnow().isoformat()
    mediator_updates = {
        str(LAW_PRIME): {
            "metadata": {"system_health": "unstable", "last_crash": crash_timestamp}
        },
        str(GRACE_PRIME): {
            "metadata": {"system_health": "unstable", "last_crash": crash_timestamp}
        },
    }
    try:
        with allow_mediator_writes():
            ledger.update_mediators(entity, mediator_updates)
    except Exception:
        LOGGER.warning("Failed to update mediator instability metadata", exc_info=True)


def _persist_turn_blobs(
    *,
    req: ChatRequest,
    entity: str,
    assistant_reply: str,
    substrate,
    ledger,
    store: LedgerStoreV2 | None,
    metadata_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Write full-payload blobs for the user message and assistant reply and link them.

    This is a non-blocking sidecar write: failures are logged but do not stop
    the chat response. The turn coordinate and blob coordinates are returned so
    they can be referenced from the main ledger entry.
    """
    result: dict[str, Any] = {}
    if store is None or not getattr(req, "persist_conversation", True):
        return result

    try:
        user_blob = record_full_payload_blob(
            entity,
            req.message,
            "chat",
            {"role": "user", "session_id": req.session_id, "turn_id": req.turn_id},
            substrate,
            ledger,
            store,
        )
        assistant_blob = record_full_payload_blob(
            entity,
            assistant_reply,
            "chat",
            {"role": "assistant", "session_id": req.session_id, "turn_id": req.turn_id},
            substrate,
            ledger,
            store,
        )
        if user_blob and assistant_blob:
            turn_result = record_turn(
                entity,
                req.session_id,
                req.turn_id,
                req.message,
                assistant_reply,
                user_blob["coordinate"],
                assistant_blob["coordinate"],
                {
                    "provider": req.provider,
                    "context_id": req.context_id,
                    "web4_key": metadata_payload.get("web4_key") if isinstance(metadata_payload, Mapping) else None,
                },
                store,
            )
            result = {
                "user_message_coord": user_blob["coordinate"],
                "assistant_reply_coord": assistant_blob["coordinate"],
                "turn_coordinate": turn_result.get("coordinate") if turn_result else None,
            }
    except Exception:
        LOGGER.warning("Failed to persist turn blobs", exc_info=True)

    return result


async def _persist_quarantined_turn(
    *,
    req: ChatRequest,
    entity: str,
    web4_key: str,
    assistant_reply: str,
    metadata_payload: Mapping[str, Any],
    ledger: Any,
    substrate: Any,
    store: LedgerStoreV2 | None,
    retrieved_keys: list[LedgerKey],
    retrieval_payload: Any,
    reason: str,
    persist_transcript: bool = True,
) -> dict[str, Any]:
    quarantine_marker = {
        "blocked": True,
        "reason": reason,
        "capture_mode": "quarantine",
    }
    blob_refs = _persist_turn_blobs(
        req=req,
        entity=entity,
        assistant_reply=assistant_reply,
        substrate=substrate,
        ledger=ledger,
        store=store,
        metadata_payload=metadata_payload,
    )
    persist_metadata: dict[str, Any] = {
        "session_id": req.session_id,
        "provider": req.provider,
        "kind": "chat",
        "web4_key": web4_key,
        "assessments": {},
        **(dict(metadata_payload) if isinstance(metadata_payload, Mapping) else {}),
        "quarantine_write": quarantine_marker,
        "eq6_preflight": {},
    }
    if blob_refs:
        persist_metadata.update(blob_refs)
    enrich_result = await enrich_turn(
        entity=entity,
        user_message=req.message,
        assistant_reply=assistant_reply,
        metadata=persist_metadata,
        ledger=ledger,
        substrate=substrate,
        store=store,
        retrieved_keys=retrieved_keys,
        retrieval_payload=retrieval_payload,
        run_guardian=False,
        persist_transcript=persist_transcript,
    )
    meta = enrich_result.get("metadata")
    appraisal_payload = None
    if isinstance(meta, dict):
        appraisal = meta.get("appraisal")
        if isinstance(appraisal, dict):
            appraisal_payload = dict(appraisal)
    return {
        "coordinate": enrich_result.get("coordinate"),
        "flow_enrich": enrich_result.get("flow_enrich"),
        "appraisal": appraisal_payload,
        "quarantine_write": quarantine_marker,
    }


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    ledger=Depends(get_memory_ledger),
    substrate=Depends(get_memory_substrate),
) -> ChatResponse:
    enforce_pilot_write_allowed(request, action="chat.write")
    LOGGER.info(
        "chat_request",
        extra={
            "session": req.session_id,
            "provider": req.provider,
            "enable_ledger": req.enable_ledger,
            "history_len": len(req.history or []),
        },
    )
    try:
        # 1. Define Entity
        entity = req.entity or f"chat-{req.session_id}"
        apply_auth_claim_overrides(
            request,
            principal_did=req.principal_did,
            principal_key_id=req.principal_key_id,
            session_jti=req.session_jti,
        )
        ledger_id = _resolve_explicit_ledger_id(request, req.ledger_id)
        context_id = resolve_context_id_or_raise(
            request,
            payload_context_id=req.context_id,
            require_for_write=True,
            hint="provide context_id in payload or x-context-id header",
        )
        write_namespace = resolve_write_namespace(ledger_id=ledger_id, entity=entity)
        authorize_or_raise(
            request,
            ledger_id=ledger_id,
            action="ledger.write",
            explicit_context=True,
        )
        standing_policy = _standing_policy_for_chat_request(request, req)
        effective_enable_ledger = bool(req.enable_ledger and standing_policy.get("write_commit_allowed", True))
        persist_transcript = bool(getattr(req, "persist_conversation", False))
        retrieval_allowed = bool(standing_policy.get("retrieval_allowed"))

        # 2. Dynamic Web4 Key Generation
        # Instead of hardcoded "PL-Conv-Alpha", we derive it from the entity
        entity_hash = hashlib.md5(entity.encode()).hexdigest()[:8].upper()
        timestamp_code = int(time.time())
        web4_key = f"WX-{entity_hash}-{timestamp_code}"

        store = None
        token_index = None
        service = _optional_ledger_service(request)
        if service is not None:
            token_index = TokenPrimeIndex(request.app)
            store = LedgerService(service.db, token_index=token_index).store

        # 3. Inhale (Assemble Context)
        explicit_attachments = _extract_attachment_coords_with_fallbacks(
            message=req.message,
            default_namespace=write_namespace,
            fallback_namespaces=_attachment_focus_namespaces(
                entity=entity,
                write_namespace=write_namespace,
            ),
        )
        extra_namespaces: list[str] | None = None
        if service is not None:
            try:
                ledger_boundary = service.get_ledger_library_boundary(write_namespace)
                if isinstance(ledger_boundary, dict):
                    alias_history = ledger_boundary.get("alias_history") or []
                    supersession_history = ledger_boundary.get("supersession_history") or []
                    canonical_id = ledger_boundary.get("canonical_ledger_id")
                    ns_set: set[str] = set()
                    for candidate in list(alias_history) + list(supersession_history):
                        if not isinstance(candidate, str):
                            continue
                        clean = candidate.strip()
                        if clean.startswith("ledger:"):
                            clean = clean[7:]
                        if clean and clean != write_namespace and clean != canonical_id:
                            ns_set.add(clean)
                    if ns_set:
                        extra_namespaces = list(ns_set)
            except Exception:
                extra_namespaces = None
        qp_pure_token = _set_qp_pure_override(req)
        try:
            if retrieval_allowed:
                memories = await assemble_context(
                    entity=write_namespace,
                    query=req.message,
                    k=3,
                    focus_context=explicit_attachments if explicit_attachments else None,
                    ledger=ledger,
                    substrate=substrate,
                    store=store,
                    token_index=token_index,
                    padic_store=store._padic_store if isinstance(store, LedgerStoreV2) else None,
                    extra_namespaces=extra_namespaces,
                    query_primes=req.query_primes,
                    hardening_level=req.hardening_level,
                    include_padic_diagnostics=req.include_padic_diagnostics,
                )
            else:
                memories = {"retrieved": [], "assessments": {}, "standing_policy_denied": True}
        finally:
            if qp_pure_token is not None:
                QP_PURE_OVERRIDE.reset(qp_pure_token)
        # Do not auto-expand attachment parts here; frontend agent should walk parts.
        if isinstance(memories, Mapping) and isinstance(memories.get("candidate_trace"), list):
            autonomy_candidates = list(memories["candidate_trace"])
        else:
            autonomy_candidates = _candidate_trace_from_retrieved(
                memories.get("retrieved", []) if isinstance(memories, Mapping) else []
            )
        autonomy_decision = _autonomy_decision_from_candidates(
            autonomy_candidates,
            policy=AUTONOMY_POLICY_RAW,
        )

        # 4. Process (LLM Generation)
        session_stats = SESSION_METRICS.setdefault(
            req.session_id, {"total_cost": 0.0, "total_latency": 0.0, "turns": 0}
        )
        turn_count = int(session_stats.get("turns", 0)) + 1
        include_system_prompts = _should_include_system_prompts(
            session_id=req.session_id,
            provider=req.provider,
            turn_count=turn_count,
        )
        pre_introspect: dict[str, Any] | None = None
        if store is not None:
            try:
                pre_introspect = _build_introspect_payload(store=store, namespace=write_namespace)
            except Exception:
                pre_introspect = None
        # Fallback: inject foundation identity from ledger registry if missing
        if pre_introspect is not None and not pre_introspect.get("foundation_identity"):
            try:
                if service is not None:
                    ledger_boundary = service.get_ledger_library_boundary(write_namespace)
                    foundation = ledger_boundary.get("foundation_identity") if isinstance(ledger_boundary, dict) else None
                    if isinstance(foundation, dict) and foundation.get("name"):
                        pre_introspect["foundation_identity"] = dict(foundation)
                        runtime_identity = pre_introspect.get("runtime_identity") or {}
                        library_boundary = runtime_identity.get("library_boundary") or {}
                        library_boundary["foundation_identity"] = dict(foundation)
                        runtime_identity["library_boundary"] = library_boundary
                        pre_introspect["runtime_identity"] = runtime_identity
            except Exception:
                pass
        messages = build_chat_messages(
            user_message=req.message,
            history=req.history or [],
            memories=memories,
            introspect_snapshot=pre_introspect,
            turn_count=turn_count,
            include_system_prompts=include_system_prompts,
        )
        messages.append(
            cast(
                ChatCompletionMessageParam,
                {"role": "system", "content": _autonomy_system_instruction(autonomy_decision)},
            )
        )
        retrieved_count = len(memories.get("retrieved", [])) if isinstance(memories, dict) else 0
        history_len = len(req.history or [])
        max_tokens = clamp_max_tokens(
            requested=_resolve_chat_max_tokens(history_len=history_len, retrieved_count=retrieved_count),
            standing_cap=standing_policy.get("max_output_tokens"),
        )
        eq6_gate = None

        finish_reason: str | None = None
        if COORDS_ONLY_MODE:
            raw_reply_text = ""
            clean_reply_text = ""
            parsed_metadata = {}
            parsed_ok = False
            cost_usd = 0.0
            latency_ms = 0.0
            gen_input_tokens = 0
            gen_output_tokens = 0
        else:
            raw_reply_text, cost_usd, latency_ms, usage, finish_reason = await complete_chat(
                provider=req.provider,
                messages=messages,
                max_tokens=max_tokens,
            )
            gen_input_tokens = usage.prompt_tokens
            gen_output_tokens = usage.completion_tokens
            clean_reply_text, parsed_metadata, parsed_ok = _extract_response_payload(raw_reply_text)
        LOGGER.info(
            "context_window_metrics",
            extra={
                "prompt_tokens": gen_input_tokens,
                "completion_tokens": gen_output_tokens,
                "retrieved_count": len(memories.get("retrieved", [])) if isinstance(memories, dict) else 0,
                "history_len": history_len,
                "turn_count": turn_count,
            },
        )

        # 5. Stats Tracking
        session_stats["total_cost"] += cost_usd
        session_stats["total_latency"] += float(latency_ms)
        session_stats["turns"] = turn_count
        avg_latency = (
            session_stats["total_latency"] / session_stats["turns"]
            if session_stats["turns"]
            else 0.0
        )

        # 6. Exhale (Enrich/Persist)
        coordinate: str | None = None
        grace_note: str | None = None
        unverified = False
        fallback_coordinate = False
        appraisal_payload: dict[str, Any] | None = None
        audit_mode: dict[str, Any] | None = None
        
        # --- CLEANUP: Prepare Knowledge Tree as Strict Objects ---
        retrieved_list = memories.get("retrieved", [])
        knowledge_tree_data: list[dict[str, Any]] = []
        
        # Keys to save in the ledger for this turn
        keys_to_persist: list[dict[str, Any]] = []

        for m in retrieved_list:
            if not isinstance(m, Mapping):
                continue
            clean_key = _coerce_knowledge_tree_key_from_retrieved(m)

            if clean_key:
                minimal_key = normalize_knowledge_tree_item(clean_key)
                minimal_key = _set_canonical_coord(minimal_key)
                if "relevance_score" in m:
                    minimal_key["relevance_score"] = m.get("relevance_score")
                if "tier_rank" in m:
                    minimal_key["tier_rank"] = m.get("tier_rank")
                knowledge_tree_data.append(minimal_key)
                keys_to_persist.append(minimal_key)

        # Merge any knowledge tree references that might have been surfaced by the model.
        metadata_knowledge_tree = parsed_metadata.get("knowledge_tree") if isinstance(parsed_metadata, dict) else None
        knowledge_tree_data = _merge_knowledge_trees(knowledge_tree_data, metadata_knowledge_tree)
        knowledge_tree_data = [
            _set_canonical_coord(item) if isinstance(item, dict) else item
            for item in knowledge_tree_data
        ]

        # Build fallback metadata if parsing failed or the block was missing.
        metadata_payload: Dict[str, Any]
        if parsed_ok and isinstance(parsed_metadata, dict):
            metadata_payload = dict(cast(Dict[str, Any], parsed_metadata))
        else:
            metadata_payload = _build_fallback_metadata(
                user_message=req.message,
                assistant_text=clean_reply_text or raw_reply_text,
                knowledge_tree=knowledge_tree_data,
            )
        if pre_introspect:
            metadata_payload.setdefault("introspect_snapshot_pre", pre_introspect)
        _apply_turn_diagnostics(
            metadata_payload,
            autonomy_candidates=autonomy_candidates,
            autonomy_decision=autonomy_decision,
        )
        padic_diagnostics = memories.get("padic_diagnostics") if isinstance(memories, Mapping) else None
        if isinstance(padic_diagnostics, Mapping):
            metadata_payload.setdefault("padic_diagnostics", padic_diagnostics)
        metadata_payload.setdefault("ledger_id", ledger_id)
        metadata_payload.setdefault("runtime_namespace", write_namespace)
        metadata_payload.setdefault("session_id", req.session_id)
        metadata_payload.setdefault(
            "context_window",
            {
                "prompt_tokens": gen_input_tokens,
                "completion_tokens": gen_output_tokens,
                "retrieved_count": len(memories.get("retrieved", [])) if isinstance(memories, dict) else 0,
                "history_len": history_len,
                "turn_count": turn_count,
            },
        )
        metadata_payload.setdefault("max_tokens", max_tokens)
        if finish_reason:
            metadata_payload.setdefault("finish_reason", finish_reason)
        if gen_input_tokens is not None:
            metadata_payload.setdefault("gen_input_tokens", gen_input_tokens)
        if gen_output_tokens is not None:
            metadata_payload.setdefault("gen_output_tokens", gen_output_tokens)
        metadata_payload.setdefault(
            "standing_policy",
            {
                "source": standing_policy.get("source"),
                "tool_scope": standing_policy.get("tool_scope"),
                "retrieval_scope": standing_policy.get("retrieval_scope"),
                "retrieval_allowed": retrieval_allowed,
                "max_output_tokens": standing_policy.get("max_output_tokens"),
                "write_commit_allowed": standing_policy.get("write_commit_allowed"),
                "effective_enable_ledger": effective_enable_ledger,
            },
        )

        if knowledge_tree_data:
            metadata_payload.setdefault("knowledge_tree", knowledge_tree_data)
        try:
            metadata_payload.update(
                normalize_subject_transition(
                    request,
                    metadata=metadata_payload,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "subject_authority_transition_unverified", "reason": str(exc)},
            ) from exc
        metadata_payload["runtime_identity"] = _normalize_runtime_identity_metadata(
            metadata_payload,
            ledger_id=ledger_id,
            write_namespace=write_namespace,
            ledger_service=service,
        )
        delegated_prompt_path = _delegated_prompt_path_metadata(request, authz_diagnostics_from_request(request), metadata_payload)
        if delegated_prompt_path:
            metadata_payload["delegated_prompt_path"] = delegated_prompt_path
        metadata_payload.update(
            build_write_provenance(
                request,
                ledger_id=write_namespace,
                metadata=metadata_payload,
                session_id=req.session_id,
                turn_id=req.turn_id,
                provider_id=req.provider,
                model_id=req.provider,
                context_id=context_id,
            )
        )

        guarded_text, guard_applied = _apply_metrics_grounding_guard(
            user_message=req.message,
            response_text=clean_reply_text or raw_reply_text,
            memories=memories,
            metadata_payload=metadata_payload,
        )
        if guard_applied:
            clean_reply_text = guarded_text
            raw_reply_text = guarded_text
            metadata_payload["grounding_override"] = {
                "applied": True,
                "reason": "ungrounded_numeric_delta_claims",
            }

        required_coords = _required_coords_from_knowledge_tree(knowledge_tree_data, max_count=3)
        if required_coords:
            required_missing = [
                coord for coord in required_coords if coord not in (clean_reply_text or raw_reply_text)
            ]
            if required_missing:
                LOGGER.info(
                    "required_coords_missing",
                    extra={
                        "missing": required_missing[:3],
                        "missing_count": len(required_missing),
                        "turn_count": turn_count,
                    },
                )

        resolved_coords_set = _resolved_coords_from_context(
            knowledge_tree_data=knowledge_tree_data,
            memories=memories if isinstance(memories, Mapping) else None,
        )
        mentioned_coords = _extract_coords_from_text(
            clean_reply_text or raw_reply_text,
            default_namespace=write_namespace,
        )
        resolve_summary = _build_coord_resolution_summary(
            requested_coords=mentioned_coords,
            resolved_coords=resolved_coords_set,
        )
        consistency_check = _evaluate_resolution_consistency(
            response_text=clean_reply_text or raw_reply_text,
            resolve_summary=resolve_summary,
        )
        if consistency_check.get("status") == "contradiction" and not COORDS_ONLY_MODE:
            retry_result = await _retry_on_resolution_contradiction(
                provider=req.provider,
                base_messages=messages,
                max_tokens=max_tokens,
                candidate_text=clean_reply_text or raw_reply_text,
                resolve_summary=resolve_summary,
            )
            consistency_check = retry_result.get("consistency_check") or consistency_check
            if retry_result.get("applied"):
                clean_reply_text = str(retry_result.get("text") or clean_reply_text or raw_reply_text)
                raw_reply_text = clean_reply_text
                cost_usd += float(retry_result.get("cost_usd") or 0.0)
                retry_latency = float(retry_result.get("latency_ms") or 0.0)
                latency_ms += retry_latency
                session_stats["total_cost"] += float(retry_result.get("cost_usd") or 0.0)
                session_stats["total_latency"] += retry_latency
                avg_latency = (
                    session_stats["total_latency"] / session_stats["turns"]
                    if session_stats["turns"]
                    else 0.0
                )
                gen_input_tokens += int(retry_result.get("usage_prompt_tokens") or 0)
                gen_output_tokens += int(retry_result.get("usage_completion_tokens") or 0)
                retry_finish_reason = retry_result.get("finish_reason")
                if isinstance(retry_finish_reason, str) and retry_finish_reason:
                    finish_reason = retry_finish_reason
                mentioned_coords = _extract_coords_from_text(
                    clean_reply_text or raw_reply_text,
                    default_namespace=write_namespace,
                )
                resolve_summary = _build_coord_resolution_summary(
                    requested_coords=mentioned_coords,
                    resolved_coords=resolved_coords_set,
                )

        metadata_payload["resolve_summary"] = resolve_summary
        metadata_payload["consistency_check"] = consistency_check
        unresolved_coords = [coord for coord in mentioned_coords if coord not in resolved_coords_set]
        if unresolved_coords:
            unverified = True
            metadata_payload.setdefault("coord_resolution_warning", {})
            metadata_payload["coord_resolution_warning"] = {
                "unresolved": unresolved_coords[:12],
                "blocked": False,
            }
            LOGGER.info(
                "coord_resolution_blocked",
                extra={
                    "missing": unresolved_coords[:12],
                    "missing_count": len(unresolved_coords),
                    "turn_count": turn_count,
                },
            )

        try:
            engine = get_entity_engine(entity)
            hysteresis_now = engine.calculate_memory_coherence()
        except Exception:
            hysteresis_now = None
        loop_metrics = _compute_loop_risk(
            response_text=clean_reply_text or raw_reply_text,
            mentioned_coords=mentioned_coords,
            resolved_coords=resolved_coords_set,
            hysteresis_coherence=hysteresis_now,
            lawfulness_level=None,
        )
        metadata_payload["loop_risk"] = loop_metrics
        loop_risk = float(loop_metrics["loop_risk"])
        loop_blocked = loop_risk >= float(loop_metrics["hard_threshold"]) and (
            loop_metrics["grounding_gap"] >= 0.5 or loop_metrics["closure_pressure"] >= 0.6
        )
        if loop_risk >= float(loop_metrics["warn_threshold"]):
            metadata_payload["loop_break_required"] = True
            if clean_reply_text:
                clean_reply_text = (
                    clean_reply_text
                    + "\n\nLoop check: please provide an exit condition, a falsifier, or request resolver evidence."
                )
                raw_reply_text = clean_reply_text
        if loop_blocked:
            unverified = True
            metadata_payload["loop_blocked"] = True

        if effective_enable_ledger and not COORDS_ONLY_MODE and not loop_blocked:
            turn_blob_refs = _persist_turn_blobs(
                req=req,
                entity=write_namespace,
                assistant_reply=clean_reply_text,
                substrate=substrate,
                ledger=ledger,
                store=store,
                metadata_payload=metadata_payload,
            )
            try:
                enrich_result = await enrich_turn(
                    entity=write_namespace,
                    user_message=req.message,
                    assistant_reply=clean_reply_text,
                    metadata={
                        "session_id": req.session_id,
                        "provider": req.provider,
                        "kind": "chat",
                        "web4_key": web4_key,  # The Dynamic Life Key
                        "stats": {
                            "total_cost": cost_usd,
                            "latency_ms": latency_ms,
                            "session_total_cost": session_stats["total_cost"],
                            "session_avg_latency_ms": avg_latency,
                            "turns": session_stats["turns"],
                        },
                        "assessments": memories.get("assessments", {}),
                        **metadata_payload,
                        "eq6_preflight": {},
                        **turn_blob_refs,
                    },
                    ledger=ledger,
                    substrate=substrate,
                    store=store,
                    retrieved_keys=keys_to_persist,
                    retrieval_payload=memories.get("retrieved"),
                    run_guardian=False,
                    persist_transcript=persist_transcript,
                )
                coordinate = enrich_result.get("coordinate")
                grace_note = _grace_note_from_flow(enrich_result.get("flow_enrich"))
                if isinstance(enrich_result, dict):
                    meta = enrich_result.get("metadata")
                    if isinstance(meta, dict):
                        enriched_meta = meta
                        appraisal = meta.get("appraisal")
                        if isinstance(appraisal, dict):
                            appraisal_payload = dict(appraisal)
                if store is not None and coordinate:
                    try:
                        snapshot = _build_introspect_payload(store=store, namespace=write_namespace)
                        metadata_payload.setdefault("introspect_snapshot_post", snapshot)
                    except Exception:
                        pass
                appraisal_payload = _canonical_appraisal_payload(appraisal_payload, metadata_payload)
                if coordinate is None:
                    unverified = True
                background_tasks.add_task(
                    guardian_enrich_turn,
                    entity=write_namespace,
                    user_message=req.message,
                    assistant_reply=clean_reply_text,
                    ledger=ledger,
                    substrate=substrate,
                    store=store,
                )
            except CoherenceException as exc:
                LOGGER.warning("Governance gate blocked persistence", exc_info=True)
                unverified = True
                audit_mode = exc.as_dict()
                metadata_payload["audit_mode"] = audit_mode
                try:
                    fallback_result = await _persist_quarantined_turn(
                        req=req,
                        entity=write_namespace,
                        web4_key=web4_key,
                        assistant_reply=clean_reply_text,
                        metadata_payload=metadata_payload,
                        ledger=ledger,
                        substrate=substrate,
                        store=store,
                        retrieved_keys=keys_to_persist,
                        retrieval_payload=memories.get("retrieved"),
                        reason="audit_blocked",
                        persist_transcript=persist_transcript,
                    )
                    fallback_coordinate = fallback_result.get("coordinate")
                    if isinstance(fallback_coordinate, str) and fallback_coordinate.strip():
                        coordinate = fallback_coordinate
                    fallback_grace_note = _grace_note_from_flow(fallback_result.get("flow_enrich"))
                    if fallback_grace_note:
                        grace_note = fallback_grace_note
                    fallback_appraisal = fallback_result.get("appraisal")
                    if isinstance(fallback_appraisal, dict):
                        appraisal_payload = fallback_appraisal
                    metadata_payload["quarantine_write"] = fallback_result.get("quarantine_write")
                except Exception:
                    LOGGER.warning("Quarantine persistence fallback failed", exc_info=True)
            except Exception:
                LOGGER.warning("Ledger persistence failed; marking turn unverified", exc_info=True)
                unverified = True
                _mark_mediator_instability(ledger, write_namespace)
                try:
                    fallback_result = await _persist_quarantined_turn(
                        req=req,
                        entity=write_namespace,
                        web4_key=web4_key,
                        assistant_reply=clean_reply_text,
                        metadata_payload=metadata_payload,
                        ledger=ledger,
                        substrate=substrate,
                        store=store,
                        retrieved_keys=keys_to_persist,
                        retrieval_payload=memories.get("retrieved"),
                        reason="persistence_error",
                        persist_transcript=persist_transcript,
                    )
                    fallback_coordinate = fallback_result.get("coordinate")
                    if isinstance(fallback_coordinate, str) and fallback_coordinate.strip():
                        coordinate = fallback_coordinate
                    fallback_grace_note = _grace_note_from_flow(fallback_result.get("flow_enrich"))
                    if fallback_grace_note:
                        grace_note = fallback_grace_note
                    fallback_appraisal = fallback_result.get("appraisal")
                    if isinstance(fallback_appraisal, dict):
                        appraisal_payload = fallback_appraisal
                    metadata_payload["quarantine_write"] = fallback_result.get("quarantine_write")
                except Exception:
                    LOGGER.warning("Quarantine persistence fallback failed", exc_info=True)
        elif effective_enable_ledger and not COORDS_ONLY_MODE and loop_blocked:
            try:
                fallback_result = await _persist_quarantined_turn(
                    req=req,
                    entity=write_namespace,
                    web4_key=web4_key,
                    assistant_reply=clean_reply_text,
                    metadata_payload=metadata_payload,
                    ledger=ledger,
                    substrate=substrate,
                    store=store,
                    retrieved_keys=keys_to_persist,
                    retrieval_payload=memories.get("retrieved"),
                    reason="loop_blocked",
                    persist_transcript=persist_transcript,
                )
                fallback_coordinate = fallback_result.get("coordinate")
                if isinstance(fallback_coordinate, str) and fallback_coordinate.strip():
                    coordinate = fallback_coordinate
                fallback_grace_note = _grace_note_from_flow(fallback_result.get("flow_enrich"))
                if fallback_grace_note:
                    grace_note = fallback_grace_note
                fallback_appraisal = fallback_result.get("appraisal")
                if isinstance(fallback_appraisal, dict):
                    appraisal_payload = fallback_appraisal
                metadata_payload["quarantine_write"] = fallback_result.get("quarantine_write")
            except Exception:
                LOGGER.warning("Loop-blocked quarantine persistence failed", exc_info=True)
        elif COORDS_ONLY_MODE:
            unverified = True
        if coordinate is None or str(coordinate).isdigit():
            coordinate = f"{write_namespace}:{web4_key}"
            fallback_coordinate = True
        memories_used = len(memories.get("recent", [])) + len(memories.get("claims", []))
        if memories.get("retrieved"):
            memories_used += len(memories["retrieved"])
        
        summary_body: dict[str, Any] = (memories.get("summary") or {}).get("body") or {}
        if summary_body.get("raw"):
            memories_used += 1

        emitted_refs = _count_emitted_refs(
            knowledge_tree_data,
            metadata_payload,
            clean_reply_text or raw_reply_text,
        )
        resolve_successes = len(keys_to_persist)
        resolve_attempts = len(memories.get("retrieved", []))

        # Telemetry reduced for minimal-latency mode.
        try:
            telemetry_store = _telemetry_store_from_request(request)
            if telemetry_store is None:
                raise RuntimeError("Telemetry store unavailable")
            eligible_for_search, search_used = _derive_search_flags(req=req, memories=memories)
            search_succeeded = None
            if search_used is not None:
                search_succeeded = bool(memories.get("retrieved"))
            telemetry = _build_turn_telemetry(
                req=req,
                entity=write_namespace,
                coordinate=coordinate,
                web4_key=web4_key,
                model=req.provider,
                provider=req.provider,
                cost=cost_usd,
                gen_input_tokens=None,
                gen_output_tokens=None,
                emitted_refs=emitted_refs,
                resolve_successes=resolve_successes,
                resolve_attempts=resolve_attempts,
                eligible_for_search=eligible_for_search,
                search_used=search_used,
                search_succeeded=search_succeeded,
                metadata_payload=metadata_payload,
            )
            background_tasks.add_task(
                _write_telemetry_background,
                telemetry_store,
                telemetry,
            )
        except Exception:
            LOGGER.warning("Failed to schedule minimal chat telemetry", exc_info=True)

        eval_contract = _build_eval_contract(
            metadata_payload=metadata_payload,
            appraisal_payload=appraisal_payload,
        )
        posture_policy = _build_posture_policy(
            action="chat.respond",
            eval_contract=eval_contract if isinstance(eval_contract, Mapping) else None,
            metadata_payload=metadata_payload,
            audit_mode=audit_mode,
            loop_blocked=loop_blocked,
        )
        if posture_policy.get("policy_decision") == "deny":
            clean_reply_text = _blocked_response_text(posture_policy)
        diagnostics = _diagnostics_snapshot(metadata_payload)
        return ChatResponse(
            text=clean_reply_text,
            latency_ms=int(latency_ms),
            memories_used=memories_used,
            cost_usd=cost_usd,
            unverified=unverified if effective_enable_ledger else True,
            grace_note=grace_note,
            appraisal=appraisal_payload,
            session_cost_usd=session_stats["total_cost"],
            session_avg_response_ms=avg_latency,
            coordinate=coordinate,
            web4_key=web4_key,
            fallback_coordinate=fallback_coordinate,
            # --- Return the Clean Object List ---
            knowledge_tree=knowledge_tree_data,
            audit_mode=audit_mode,
            resolve_summary=diagnostics["resolve_summary"],
            candidate_trace=diagnostics["candidate_trace"],
            autonomy_decision=diagnostics["autonomy_decision"],
            consistency_check=diagnostics["consistency_check"],
            eval_contract=eval_contract,
            posture_policy=posture_policy,
        )
        LOGGER.info(
            "chat_turn",
            extra={
                "session": req.session_id,
                "provider": req.provider,
                "memories_used": memories_used,
                "retrieved": len(memories.get("retrieved") or []),
                "knowledge_tree": knowledge_tree_data,
                "coordinate": coordinate,
                "unverified": unverified,
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("Chat endpoint failed", exc_info=exc)
        _mark_mediator_instability(ledger, req.entity or f"chat-{req.session_id}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    ledger=Depends(get_memory_ledger),
    substrate=Depends(get_memory_substrate),
):
    """Stream the chat pipeline as newline-delimited JSON."""
    enforce_pilot_write_allowed(request, action="chat.stream.write")

    async def event_generator():
        yield json.dumps({"type": "status", "message": "Initializing Quantum Kernel..."}) + "\n"

        entity = req.entity or f"chat-{req.session_id}"
        enriched_meta: dict[str, Any] | None = None
        try:
            apply_auth_claim_overrides(
                request,
                principal_did=req.principal_did,
                principal_key_id=req.principal_key_id,
                session_jti=req.session_jti,
            )
            ledger_id = _resolve_explicit_ledger_id(request, req.ledger_id)
            context_id = resolve_context_id_or_raise(
                request,
                payload_context_id=req.context_id,
                require_for_write=True,
                hint="provide context_id in payload or x-context-id header",
            )
            write_namespace = resolve_write_namespace(ledger_id=ledger_id, entity=entity)
            authorize_or_raise(
                request,
                ledger_id=ledger_id,
                action="ledger.write",
                explicit_context=True,
            )
            standing_policy = _standing_policy_for_chat_request(request, req)
            effective_enable_ledger = bool(req.enable_ledger and standing_policy.get("write_commit_allowed", True))
            persist_transcript = bool(req.persist_conversation)
            retrieval_allowed = bool(standing_policy.get("retrieval_allowed"))
        except HTTPException as exc:
            yield json.dumps({"type": "error", "detail": str(exc.detail)}) + "\n"
            return
        authz_diag = authz_diagnostics_from_request(request)
        entity_hash = hashlib.md5(entity.encode()).hexdigest()[:8].upper()
        timestamp_code = int(time.time())
        web4_key = f"WX-{entity_hash}-{timestamp_code}"

        service = _optional_ledger_service(request)
        if service is None:
            yield json.dumps(
                {"type": "status", "message": "Error: Database unavailable."}
            ) + "\n"
            return

        token_index = TokenPrimeIndex(request.app)
        store = LedgerService(service.db, token_index=token_index).store

        yield json.dumps({"type": "status", "message": "Assembling memories..."}) + "\n"
        start_time = time.time()
        retry_latency_ms_extra = 0.0
        explicit_attachments = _extract_attachment_coords_with_fallbacks(
            message=req.message,
            default_namespace=write_namespace,
            fallback_namespaces=_attachment_focus_namespaces(
                entity=entity,
                write_namespace=write_namespace,
            ),
        )
        extra_namespaces_stream: list[str] | None = None
        try:
            ledger_boundary_stream = service.get_ledger_library_boundary(write_namespace)
            if isinstance(ledger_boundary_stream, dict):
                alias_history_stream = ledger_boundary_stream.get("alias_history") or []
                supersession_history_stream = ledger_boundary_stream.get("supersession_history") or []
                canonical_id_stream = ledger_boundary_stream.get("canonical_ledger_id")
                ns_set_stream: set[str] = set()
                for candidate in list(alias_history_stream) + list(supersession_history_stream):
                    if not isinstance(candidate, str):
                        continue
                    clean = candidate.strip()
                    if clean.startswith("ledger:"):
                        clean = clean[7:]
                    if clean and clean != write_namespace and clean != canonical_id_stream:
                        ns_set_stream.add(clean)
                if ns_set_stream:
                    extra_namespaces_stream = list(ns_set_stream)
        except Exception:
            extra_namespaces_stream = None
        qp_pure_token = _set_qp_pure_override(req)
        try:
            if retrieval_allowed:
                memories = await assemble_context(
                    entity=write_namespace,
                    query=req.message,
                    k=3,
                    focus_context=explicit_attachments if explicit_attachments else None,
                    ledger=ledger,
                    substrate=substrate,
                    store=store,
                    token_index=token_index,
                    padic_store=store._padic_store if isinstance(store, LedgerStoreV2) else None,
                    extra_namespaces=extra_namespaces_stream,
                    query_primes=req.query_primes,
                    hardening_level=req.hardening_level,
                    include_padic_diagnostics=req.include_padic_diagnostics,
                )
            else:
                memories = {"retrieved": [], "assessments": {}, "standing_policy_denied": True}
        finally:
            if qp_pure_token is not None:
                QP_PURE_OVERRIDE.reset(qp_pure_token)
        # Do not auto-expand attachment parts here; frontend agent should walk parts.
        if isinstance(memories, Mapping) and isinstance(memories.get("candidate_trace"), list):
            autonomy_candidates = list(memories["candidate_trace"])
        else:
            autonomy_candidates = _candidate_trace_from_retrieved(
                memories.get("retrieved", []) if isinstance(memories, Mapping) else []
            )
        autonomy_decision = _autonomy_decision_from_candidates(
            autonomy_candidates,
            policy=AUTONOMY_POLICY_RAW,
        )

        retrieved_count = len(memories.get("retrieved", []))
        history_len = len(req.history or [])
        if retrieved_count:
            yield json.dumps(
                {"type": "status", "message": f"Retrieved {retrieved_count} relevant records."}
            ) + "\n"

        yield json.dumps({"type": "status", "message": "Evaluating Coherence & Ethics..."}) + "\n"
        session_stats = SESSION_METRICS.setdefault(
            req.session_id, {"total_cost": 0.0, "total_latency": 0.0, "turns": 0}
        )
        turn_count = int(session_stats.get("turns", 0)) + 1
        include_system_prompts = _should_include_system_prompts(
            session_id=req.session_id,
            provider=req.provider,
            turn_count=turn_count,
        )
        pre_introspect: dict[str, Any] | None = None
        if store is not None:
            try:
                pre_introspect = _build_introspect_payload(store=store, namespace=write_namespace)
            except Exception:
                pre_introspect = None
        # Fallback: inject foundation identity from ledger registry if missing
        if pre_introspect is not None and not pre_introspect.get("foundation_identity"):
            try:
                if service is not None:
                    ledger_boundary = service.get_ledger_library_boundary(write_namespace)
                    foundation = ledger_boundary.get("foundation_identity") if isinstance(ledger_boundary, dict) else None
                    if isinstance(foundation, dict) and foundation.get("name"):
                        pre_introspect["foundation_identity"] = dict(foundation)
                        runtime_identity = pre_introspect.get("runtime_identity") or {}
                        library_boundary = runtime_identity.get("library_boundary") or {}
                        library_boundary["foundation_identity"] = dict(foundation)
                        runtime_identity["library_boundary"] = library_boundary
                        pre_introspect["runtime_identity"] = runtime_identity
            except Exception:
                pass
        messages = build_chat_messages(
            user_message=req.message,
            history=req.history or [],
            memories=memories,
            introspect_snapshot=pre_introspect,
            turn_count=turn_count,
            include_system_prompts=include_system_prompts,
        )
        messages.append(
            cast(
                ChatCompletionMessageParam,
                {"role": "system", "content": _autonomy_system_instruction(autonomy_decision)},
            )
        )
        max_tokens = clamp_max_tokens(
            requested=_resolve_chat_max_tokens(history_len=history_len, retrieved_count=retrieved_count),
            standing_cap=standing_policy.get("max_output_tokens"),
        )
        eq6_gate = None
        yield _json_dumps_coordinate_safe(
            {
                "type": "candidate_trace",
                "payload": {"top_k": _canonical_candidate_trace(autonomy_candidates, max_k=_DIAGNOSTIC_TOP_K)},
            }
        ) + "\n"
        yield _json_dumps_coordinate_safe(
            {"type": "autonomy_decision", "payload": autonomy_decision}
        ) + "\n"
        # Backward-compat event for clients expecting legacy decision frame naming.
        yield _json_dumps_coordinate_safe(
            {
                "type": "decision_trace",
                "payload": {
                    "top_k": _canonical_candidate_trace(autonomy_candidates, max_k=_DIAGNOSTIC_TOP_K),
                    "autonomy_decision": autonomy_decision,
                },
            }
        ) + "\n"

        yield json.dumps({"type": "status", "message": "Generating response..."}) + "\n"
        full_reply = ""
        finish_reason: str | None = None
        tokens_emitted = 0
        stream_fallback_used = False
        pre_emission_strict = PRE_EMISSION_DENY_STRICT
        buffered_tokens: list[str] = []
        if not COORDS_ONLY_MODE:
            stream, finish_future = await yield_chat_stream(
                provider=req.provider,
                messages=messages,
                max_tokens=max_tokens,
            )
            async for token in stream:
                if token:
                    full_reply += token
                    if pre_emission_strict:
                        buffered_tokens.append(token)
                    else:
                        tokens_emitted += 1
                        yield json.dumps({"type": "token", "content": token}) + "\n"
            finish_reason = await finish_future
            if not full_reply.strip():
                try:
                    full_reply, _cost, _latency, _usage, finish_reason = await complete_chat(
                        provider=req.provider,
                        messages=messages,
                        max_tokens=max_tokens,
                    )
                    if full_reply:
                        stream_fallback_used = True
                        if pre_emission_strict:
                            buffered_tokens = [full_reply]
                        else:
                            tokens_emitted += 1
                            yield json.dumps({"type": "token", "content": full_reply}) + "\n"
                except Exception:
                    LOGGER.warning("Stream returned empty reply; fallback completion failed", exc_info=True)

        if COORDS_ONLY_MODE:
            prompt_tokens = 0
            completion_tokens = 0
            active_model = os.getenv("CHAT_MODEL", DEFAULT_CHAT_MODEL)
            turn_cost = 0.0
        else:
            prompt_text = "".join(str(m.get("content", "")) for m in messages)
            prompt_tokens = _estimate_tokens(prompt_text)
            completion_tokens = _estimate_tokens(full_reply)
            active_model = os.getenv("CHAT_MODEL", DEFAULT_CHAT_MODEL)
            turn_cost = estimate_cost_usd(active_model, prompt_tokens, completion_tokens) or 0.0
        LOGGER.info(
            "context_window_metrics",
            extra={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "retrieved_count": retrieved_count,
                "history_len": history_len,
                "turn_count": turn_count,
            },
        )
        session_stats["total_cost"] += turn_cost

        yield json.dumps({"type": "status", "message": "Persisting to Immutable Ledger..."}) + "\n"
        clean_text, parsed_metadata, _ = _extract_response_payload(full_reply)

        retrieved_list = memories.get("retrieved", [])
        knowledge_tree_data: list[dict[str, Any]] = []
        keys_to_persist: list[dict[str, Any]] = []

        for m in retrieved_list:
            if not isinstance(m, Mapping):
                continue
            clean_key = _coerce_knowledge_tree_key_from_retrieved(m)

            if clean_key:
                minimal_key = normalize_knowledge_tree_item(clean_key)
                minimal_key = _set_canonical_coord(minimal_key)
                if "relevance_score" in m:
                    minimal_key["relevance_score"] = m.get("relevance_score")
                if "tier_rank" in m:
                    minimal_key["tier_rank"] = m.get("tier_rank")
                knowledge_tree_data.append(minimal_key)
                keys_to_persist.append(minimal_key)

        metadata_knowledge_tree = (
            parsed_metadata.get("knowledge_tree") if isinstance(parsed_metadata, dict) else None
        )
        knowledge_tree_data = _merge_knowledge_trees(knowledge_tree_data, metadata_knowledge_tree)
        knowledge_tree_data = [
            _set_canonical_coord(item) if isinstance(item, dict) else item
            for item in knowledge_tree_data
        ]

        if isinstance(parsed_metadata, dict):
            metadata_payload = dict(cast(Dict[str, Any], parsed_metadata))
        else:
            metadata_payload = _build_fallback_metadata(
                user_message=req.message,
                assistant_text=clean_text or full_reply,
                knowledge_tree=knowledge_tree_data,
            )
        if pre_introspect:
            metadata_payload.setdefault("introspect_snapshot_pre", pre_introspect)
        _apply_turn_diagnostics(
            metadata_payload,
            autonomy_candidates=autonomy_candidates,
            autonomy_decision=autonomy_decision,
        )
        padic_diagnostics = memories.get("padic_diagnostics") if isinstance(memories, Mapping) else None
        if isinstance(padic_diagnostics, Mapping):
            metadata_payload.setdefault("padic_diagnostics", padic_diagnostics)
        metadata_payload.setdefault("ledger_id", ledger_id)
        metadata_payload.setdefault("runtime_namespace", write_namespace)
        metadata_payload.setdefault("session_id", req.session_id)
        metadata_payload.setdefault(
            "context_window",
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "retrieved_count": retrieved_count,
                "history_len": history_len,
                "turn_count": turn_count,
            },
        )
        metadata_payload.setdefault("max_tokens", max_tokens)
        if finish_reason:
            metadata_payload.setdefault("finish_reason", finish_reason)
        metadata_payload.setdefault(
            "standing_policy",
            {
                "source": standing_policy.get("source"),
                "tool_scope": standing_policy.get("tool_scope"),
                "retrieval_scope": standing_policy.get("retrieval_scope"),
                "retrieval_allowed": retrieval_allowed,
                "max_output_tokens": standing_policy.get("max_output_tokens"),
                "write_commit_allowed": standing_policy.get("write_commit_allowed"),
                "effective_enable_ledger": effective_enable_ledger,
            },
        )

        if knowledge_tree_data:
            metadata_payload.setdefault("knowledge_tree", knowledge_tree_data)
        try:
            metadata_payload.update(
                normalize_subject_transition(
                    request,
                    metadata=metadata_payload,
                )
            )
        except ValueError as exc:
            yield json.dumps(
                {"type": "error", "detail": {"error": "subject_authority_transition_unverified", "reason": str(exc)}}
            ) + "\n"
            return
        metadata_payload["runtime_identity"] = _normalize_runtime_identity_metadata(
            metadata_payload,
            ledger_id=ledger_id,
            write_namespace=write_namespace,
            ledger_service=service,
        )
        delegated_prompt_path = _delegated_prompt_path_metadata(request, authz_diag, metadata_payload)
        if delegated_prompt_path:
            metadata_payload["delegated_prompt_path"] = delegated_prompt_path
        metadata_payload.update(
            build_write_provenance(
                request,
                ledger_id=write_namespace,
                metadata=metadata_payload,
                session_id=req.session_id,
                turn_id=req.turn_id,
                provider_id=req.provider,
                model_id=req.provider,
                context_id=context_id,
            )
        )

        guarded_text, guard_applied = _apply_metrics_grounding_guard(
            user_message=req.message,
            response_text=clean_text or full_reply,
            memories=memories,
            metadata_payload=metadata_payload,
        )
        if guard_applied:
            clean_text = guarded_text
            full_reply = guarded_text
            metadata_payload["grounding_override"] = {
                "applied": True,
                "reason": "ungrounded_numeric_delta_claims",
            }

        required_coords = _required_coords_from_knowledge_tree(knowledge_tree_data, max_count=3)
        if required_coords:
            required_missing = [
                coord for coord in required_coords if coord not in (clean_text or full_reply)
            ]
            if required_missing:
                LOGGER.info(
                    "required_coords_missing",
                    extra={
                        "missing": required_missing[:3],
                        "missing_count": len(required_missing),
                        "turn_count": turn_count,
                    },
                )

        resolved_coords_set = _resolved_coords_from_context(
            knowledge_tree_data=knowledge_tree_data,
            memories=memories if isinstance(memories, Mapping) else None,
        )
        mentioned_coords = _extract_coords_from_text(
            clean_text or full_reply, default_namespace=write_namespace
        )
        resolve_summary = _build_coord_resolution_summary(
            requested_coords=mentioned_coords,
            resolved_coords=resolved_coords_set,
        )
        consistency_check = _evaluate_resolution_consistency(
            response_text=clean_text or full_reply,
            resolve_summary=resolve_summary,
        )
        if consistency_check.get("status") == "contradiction" and not COORDS_ONLY_MODE:
            yield json.dumps(
                {
                    "type": "status",
                    "message": "Consistency check flagged contradiction; regenerating once with grounded constraints...",
                }
            ) + "\n"
            retry_result = await _retry_on_resolution_contradiction(
                provider=req.provider,
                base_messages=messages,
                max_tokens=max_tokens,
                candidate_text=clean_text or full_reply,
                resolve_summary=resolve_summary,
            )
            consistency_check = retry_result.get("consistency_check") or consistency_check
            if retry_result.get("applied"):
                retried_text = str(retry_result.get("text") or clean_text or full_reply)
                clean_text = retried_text
                full_reply = retried_text
                if retried_text:
                    if pre_emission_strict:
                        buffered_tokens = [retried_text]
                    else:
                        tokens_emitted += 1
                        yield json.dumps({"type": "token", "content": f"\n\n{retried_text}"}) + "\n"
                retry_cost = float(retry_result.get("cost_usd") or 0.0)
                retry_latency = float(retry_result.get("latency_ms") or 0.0)
                turn_cost += retry_cost
                session_stats["total_cost"] += retry_cost
                retry_latency_ms_extra += retry_latency
                retry_finish_reason = retry_result.get("finish_reason")
                if isinstance(retry_finish_reason, str) and retry_finish_reason:
                    finish_reason = retry_finish_reason
                mentioned_coords = _extract_coords_from_text(
                    clean_text or full_reply,
                    default_namespace=write_namespace,
                )
                resolve_summary = _build_coord_resolution_summary(
                    requested_coords=mentioned_coords,
                    resolved_coords=resolved_coords_set,
                )

        metadata_payload["resolve_summary"] = resolve_summary
        metadata_payload["consistency_check"] = consistency_check
        yield _json_dumps_coordinate_safe({"type": "consistency_check", "payload": consistency_check}) + "\n"
        unresolved_coords = [coord for coord in mentioned_coords if coord not in resolved_coords_set]
        if unresolved_coords:
            unverified = True
            metadata_payload.setdefault("coord_resolution_warning", {})
            metadata_payload["coord_resolution_warning"] = {
                "unresolved": unresolved_coords[:12],
                "blocked": False,
            }
            LOGGER.info(
                "coord_resolution_blocked",
                extra={
                    "missing": unresolved_coords[:12],
                    "missing_count": len(unresolved_coords),
                    "turn_count": turn_count,
                },
            )

        try:
            engine = get_entity_engine(entity)
            hysteresis_now = engine.calculate_memory_coherence()
        except Exception:
            hysteresis_now = None
        loop_metrics = _compute_loop_risk(
            response_text=clean_text or full_reply,
            mentioned_coords=mentioned_coords,
            resolved_coords=resolved_coords_set,
            hysteresis_coherence=hysteresis_now,
            lawfulness_level=None,
        )
        metadata_payload["loop_risk"] = loop_metrics
        loop_risk = float(loop_metrics["loop_risk"])
        loop_blocked = loop_risk >= float(loop_metrics["hard_threshold"]) and (
            loop_metrics["grounding_gap"] >= 0.5 or loop_metrics["closure_pressure"] >= 0.6
        )
        if loop_risk >= float(loop_metrics["warn_threshold"]):
            metadata_payload["loop_break_required"] = True
        if loop_blocked:
            unverified = True
            metadata_payload["loop_blocked"] = True

        coordinate = None
        grace_note: str | None = None
        persistence_error: str | None = None
        appraisal_payload: dict[str, Any] | None = None
        audit_mode: dict[str, Any] | None = None
        if effective_enable_ledger and not COORDS_ONLY_MODE and not loop_blocked:
            turn_blob_refs = _persist_turn_blobs(
                req=req,
                entity=write_namespace,
                assistant_reply=clean_text or full_reply,
                substrate=substrate,
                ledger=ledger,
                store=store,
                metadata_payload=metadata_payload,
            )
            try:
                enrich_result = await enrich_turn(
                    entity=write_namespace,
                    user_message=req.message,
                    assistant_reply=clean_text or full_reply,
                    metadata={
                        "session_id": req.session_id,
                        "provider": req.provider,
                        "kind": "chat",
                        "web4_key": web4_key,
                        "assessments": memories.get("assessments", {}),
                        **metadata_payload,
                        "eq6_preflight": {},
                        **turn_blob_refs,
                    },
                    ledger=ledger,
                    substrate=substrate,
                    store=store,
                    retrieved_keys=keys_to_persist,
                    retrieval_payload=memories.get("retrieved"),
                    run_guardian=True,
                    persist_transcript=persist_transcript,
                )
                coordinate = enrich_result.get("coordinate")
                grace_note = _grace_note_from_flow(enrich_result.get("flow_enrich"))
                if isinstance(enrich_result, dict):
                    meta = enrich_result.get("metadata")
                    if isinstance(meta, dict):
                        enriched_meta = meta
                        appraisal = meta.get("appraisal")
                        if isinstance(appraisal, dict):
                            appraisal_payload = dict(appraisal)
                if store is not None and coordinate:
                    try:
                        snapshot = _build_introspect_payload(store=store, namespace=write_namespace)
                        metadata_payload.setdefault("introspect_snapshot_post", snapshot)
                    except Exception:
                        pass
                appraisal_payload = _canonical_appraisal_payload(appraisal_payload, metadata_payload)
                if enriched_meta:
                    for key in (
                        "factors",
                        "kernel_prime_exponents",
                        "mmf_projection_exponents",
                        "core_info_entry_class",
                        "flow_rule_tags",
                        "relationship_links",
                        "token_primes",
                        "token_prime_product",
                        "prime_multiplicative_value",
                        "prime_lattice_exponents",
                        "p_adic_write_cost",
                        "p_adic_coordinate",
                    ):
                        if key in enriched_meta:
                            metadata_payload[key] = enriched_meta[key]
            except CoherenceException as exc:
                persistence_error = f"audit_blocked:{exc.reason}"
                audit_mode = exc.as_dict()
                metadata_payload["audit_mode"] = audit_mode
                LOGGER.warning("Governance gate blocked persistence", exc_info=True)
                try:
                    fallback_result = await _persist_quarantined_turn(
                        req=req,
                        entity=write_namespace,
                        web4_key=web4_key,
                        assistant_reply=clean_text or full_reply,
                        metadata_payload=metadata_payload,
                        ledger=ledger,
                        substrate=substrate,
                        store=store,
                        retrieved_keys=keys_to_persist,
                        retrieval_payload=memories.get("retrieved"),
                        reason="audit_blocked",
                        persist_transcript=persist_transcript,
                    )
                    fallback_coordinate = fallback_result.get("coordinate")
                    if isinstance(fallback_coordinate, str) and fallback_coordinate.strip():
                        coordinate = fallback_coordinate
                        persistence_error = None
                    fallback_grace_note = _grace_note_from_flow(fallback_result.get("flow_enrich"))
                    if fallback_grace_note:
                        grace_note = fallback_grace_note
                    fallback_appraisal = fallback_result.get("appraisal")
                    if isinstance(fallback_appraisal, dict):
                        appraisal_payload = fallback_appraisal
                    metadata_payload["quarantine_write"] = fallback_result.get("quarantine_write")
                except Exception:
                    LOGGER.warning("Quarantine persistence fallback failed", exc_info=True)
            except Exception as exc:
                persistence_error = str(exc)
                LOGGER.error("Persistence failed: %s", exc, exc_info=True)
                _mark_mediator_instability(ledger, write_namespace)
                try:
                    fallback_result = await _persist_quarantined_turn(
                        req=req,
                        entity=write_namespace,
                        web4_key=web4_key,
                        assistant_reply=clean_text or full_reply,
                        metadata_payload=metadata_payload,
                        ledger=ledger,
                        substrate=substrate,
                        store=store,
                        retrieved_keys=keys_to_persist,
                        retrieval_payload=memories.get("retrieved"),
                        reason="persistence_error",
                        persist_transcript=persist_transcript,
                    )
                    fallback_coordinate = fallback_result.get("coordinate")
                    if isinstance(fallback_coordinate, str) and fallback_coordinate.strip():
                        coordinate = fallback_coordinate
                        persistence_error = None
                    fallback_grace_note = _grace_note_from_flow(fallback_result.get("flow_enrich"))
                    if fallback_grace_note:
                        grace_note = fallback_grace_note
                    fallback_appraisal = fallback_result.get("appraisal")
                    if isinstance(fallback_appraisal, dict):
                        appraisal_payload = fallback_appraisal
                    metadata_payload["quarantine_write"] = fallback_result.get("quarantine_write")
                except Exception:
                    LOGGER.warning("Quarantine persistence fallback failed", exc_info=True)
        elif effective_enable_ledger and not COORDS_ONLY_MODE and loop_blocked:
            persistence_error = "loop_blocked"
            try:
                fallback_result = await _persist_quarantined_turn(
                    req=req,
                    entity=write_namespace,
                    web4_key=web4_key,
                    assistant_reply=clean_text or full_reply,
                    metadata_payload=metadata_payload,
                    ledger=ledger,
                    substrate=substrate,
                    store=store,
                    retrieved_keys=keys_to_persist,
                    retrieval_payload=memories.get("retrieved"),
                    reason="loop_blocked",
                    persist_transcript=persist_transcript,
                )
                fallback_coordinate = fallback_result.get("coordinate")
                if isinstance(fallback_coordinate, str) and fallback_coordinate.strip():
                    coordinate = fallback_coordinate
                    persistence_error = None
                fallback_grace_note = _grace_note_from_flow(fallback_result.get("flow_enrich"))
                if fallback_grace_note:
                    grace_note = fallback_grace_note
                fallback_appraisal = fallback_result.get("appraisal")
                if isinstance(fallback_appraisal, dict):
                    appraisal_payload = fallback_appraisal
                metadata_payload["quarantine_write"] = fallback_result.get("quarantine_write")
            except Exception:
                LOGGER.warning("Loop-blocked quarantine persistence failed", exc_info=True)

        latency_ms = int((time.time() - start_time) * 1000 + retry_latency_ms_extra)
        session_stats["total_latency"] += float(latency_ms)
        session_stats["turns"] = turn_count
        fallback_used = False
        if coordinate is None or str(coordinate).isdigit():
            coordinate = f"{write_namespace}:{web4_key}"
            fallback_used = True

        emitted_refs = _count_emitted_refs(
            knowledge_tree_data,
            metadata_payload,
            clean_text or full_reply,
        )
        resolve_successes = len(keys_to_persist)
        resolve_attempts = len(memories.get("retrieved", []))
        cost_usd = turn_cost

        # Telemetry reduced for minimal-latency mode.
        try:
            telemetry_store = _telemetry_store_from_request(request)
            if telemetry_store is None:
                raise RuntimeError("Telemetry store unavailable")
            eligible_for_search, search_used = _derive_search_flags(req=req, memories=memories)
            search_succeeded = None
            if search_used is not None:
                search_succeeded = bool(memories.get("retrieved"))
            telemetry = _build_turn_telemetry(
                req=req,
                entity=write_namespace,
                coordinate=coordinate,
                web4_key=web4_key,
                model=active_model,
                provider=req.provider,
                cost=turn_cost,
                gen_input_tokens=prompt_tokens,
                gen_output_tokens=completion_tokens,
                emitted_refs=emitted_refs,
                resolve_successes=resolve_successes,
                resolve_attempts=resolve_attempts,
                eligible_for_search=eligible_for_search,
                search_used=search_used,
                search_succeeded=search_succeeded,
                metadata_payload=metadata_payload,
            )
            background_tasks.add_task(
                _write_telemetry_background,
                telemetry_store,
                telemetry,
            )
        except Exception:
            LOGGER.warning("Failed to schedule minimal chat stream telemetry", exc_info=True)
        eval_contract = _build_eval_contract(
            metadata_payload=metadata_payload,
            appraisal_payload=appraisal_payload,
        )
        pre_emission_block_reason = None
        if pre_emission_strict:
            pre_emission_block_reason = _pre_emission_block_reason(
                metadata_payload=metadata_payload,
                audit_mode=audit_mode,
                loop_blocked=loop_blocked,
                eval_contract=eval_contract if isinstance(eval_contract, Mapping) else None,
            )
            if pre_emission_block_reason is None:
                for token in buffered_tokens:
                    tokens_emitted += 1
                    yield json.dumps({"type": "token", "content": token}) + "\n"
            else:
                yield json.dumps(
                    {
                        "type": "pre_emission_deny",
                        "blocked": True,
                        "reason": pre_emission_block_reason,
                    }
                ) + "\n"
        posture_policy = _build_posture_policy(
            action="chat.stream.emit",
            eval_contract=eval_contract if isinstance(eval_contract, Mapping) else None,
            metadata_payload=metadata_payload,
            audit_mode=audit_mode,
            loop_blocked=loop_blocked,
            pre_emission_block_reason=pre_emission_block_reason,
        )
        yield _json_dumps_coordinate_safe({"type": "policy_envelope", "payload": posture_policy}) + "\n"
        diagnostics = _diagnostics_snapshot(metadata_payload)
        resolve_summary = diagnostics.get("resolve_summary") if isinstance(diagnostics, dict) else None
        context_meta_payload = {"type": "context_meta", **diagnostics}
        context_meta_payload["authz"] = authz_diag
        padic_write_cost = metadata_payload.get("p_adic_write_cost")
        if isinstance(padic_write_cost, (int, float)) and padic_write_cost:
            context_meta_payload["p_adic_write_cost"] = float(padic_write_cost)
        delegated_prompt_path = metadata_payload.get("delegated_prompt_path")
        if delegated_prompt_path:
            context_meta_payload["delegated_prompt_path"] = delegated_prompt_path
        if isinstance(resolve_summary, Mapping):
            context_meta_payload["resolved"] = int(resolve_summary.get("resolved_count") or 0)
            context_meta_payload["queued"] = int(resolve_summary.get("unresolved_count") or 0)
            context_meta_payload["requested"] = int(resolve_summary.get("requested_count") or 0)
        yield _json_dumps_coordinate_safe(context_meta_payload) + "\n"
        yield _json_dumps_coordinate_safe(
            {
                "type": "meta",
                "coordinate": coordinate,
                "web4_key": web4_key,
                "latency_ms": latency_ms,
                "knowledge_tree": knowledge_tree_data,
                "fallback_coordinate": fallback_used,
                "persistence_error": persistence_error,
                "appraisal": appraisal_payload,
                "grace_note": grace_note,
                "finish_reason": finish_reason,
                "introspect_snapshot_pre": metadata_payload.get("introspect_snapshot_pre"),
                "introspect_snapshot_post": metadata_payload.get("introspect_snapshot_post"),
                "coords_only_mode": COORDS_ONLY_MODE,
                "tokens_emitted": tokens_emitted,
                "stream_fallback_used": stream_fallback_used,
                "chat_model": active_model,
                "audit_mode": audit_mode,
                "resolve_summary": diagnostics["resolve_summary"],
                "p_adic_write_cost": metadata_payload.get("p_adic_write_cost"),
                "padic_diagnostics": diagnostics.get("padic_diagnostics"),
                "candidate_trace": diagnostics["candidate_trace"],
                "autonomy_decision": diagnostics["autonomy_decision"],
                "consistency_check": diagnostics["consistency_check"],
                "authz": authz_diag,
                "eval_contract": eval_contract,
                "posture_policy": posture_policy,
            }
        ) + "\n"
        LOGGER.info(
            "stream_meta_introspect",
            extra={
                "has_pre": bool(metadata_payload.get("introspect_snapshot_pre")),
                "has_post": bool(metadata_payload.get("introspect_snapshot_post")),
                "turn_count": turn_count,
                "coordinate": coordinate,
            },
        )

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")


def _normalize_confirm_coordinate(coordinate: str | None) -> str:
    if not coordinate or not str(coordinate).strip():
        raise HTTPException(status_code=400, detail="coordinate is required")
    return str(coordinate).strip()


def _confirm_stream_write(coordinate: str, request: Request):
    """Confirm a streamed chat write has landed in the ledger."""
    store = LedgerService.from_request(request).store
    entry = store.read(coordinate)
    return {"exists": entry is not None, "coordinate": coordinate}


def _normalize_related_coord(coord: str, entity: str) -> str | None:
    cleaned = (coord or "").strip()
    if not cleaned:
        return None
    if ":" not in cleaned:
        return f"{entity}:{cleaned}"
    return cleaned


def _append_related_refs(
    store: LedgerStoreV2,
    coord: str,
    *,
    turn_coord: str | None = None,
    related_attachments: list[str] | None = None,
    entity: str,
) -> None:
    normalized = _normalize_related_coord(coord, entity)
    if not normalized:
        return
    key = parse_key(normalized)
    entry = store.read(key.as_path())
    if entry is None:
        return
    metadata = entry.state.metadata or {}
    now_iso = datetime.utcnow().isoformat()
    metadata["last_seen_at"] = now_iso
    seen_count = metadata.get("seen_count")
    if isinstance(seen_count, int):
        metadata["seen_count"] = seen_count + 1
    else:
        metadata["seen_count"] = 1
    if turn_coord:
        related = metadata.get("related_turns")
        related_list = list(related) if isinstance(related, list) else []
        if turn_coord not in related_list:
            related_list.append(turn_coord)
        metadata["related_turns"] = related_list
    if related_attachments:
        related = metadata.get("related_attachments")
        related_list = list(related) if isinstance(related, list) else []
        for attachment in related_attachments:
            normalized_attachment = _normalize_related_coord(attachment, entity)
            if normalized_attachment and normalized_attachment not in related_list:
                related_list.append(normalized_attachment)
        metadata["related_attachments"] = related_list
    # Persist as an overlay append; the immutable body is untouched.
    metadata.pop("ledger_hash", None)
    metadata.pop("ledger_prev_hash", None)
    store.update_metadata_overlay(key.as_path(), metadata)


def _introspect_requested(message: str, history: list[Mapping[str, Any]] | None) -> bool:
    lowered = (message or "").lower()
    if any(term in lowered for term in ("introspect", "body state", "body awareness")):
        return True
    if not history:
        return False
    for item in reversed(history[-4:]):
        if item.get("role") != "assistant":
            continue
        content = str(item.get("content", ""))
        if "INTROSPECT_REQUEST" in content:
            return True
    return False


def _build_introspect_payload(
    *,
    store: LedgerStoreV2,
    namespace: str,
) -> dict[str, Any]:
    summary = store.summarize(namespace)
    recent_entries = store.list_by_namespace(namespace, limit=50)
    latest_entry = recent_entries[0] if recent_entries else None

    latest_coord = latest_entry.key.as_path() if latest_entry else None
    latest_meta = latest_entry.state.metadata if latest_entry else {}

    def _coerce_timing(meta: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("timing_ms", "timing"):
            value = meta.get(key)
            if isinstance(value, dict):
                return value
        return None

    def _find_latest_attachment(entries: list) -> str | None:
        for entry in entries:
            meta = entry.state.metadata or {}
            if meta.get("attachment") or meta.get("attachment_part") or meta.get("attachment_summary"):
                return entry.key.as_path()
            if meta.get("role") == "attachment":
                return entry.key.as_path()
        return None

    def _find_latest_walk(entries: list) -> tuple[str | None, dict[str, Any] | None]:
        for entry in entries:
            identifier = entry.key.identifier or ""
            meta = entry.state.metadata or {}
            if identifier.startswith("EV-WALK-") or meta.get("kind") == "coord_walk":
                return entry.key.as_path(), meta
        return None, None

    walk_coord, walk_meta = _find_latest_walk(recent_entries)
    hop_lawfulness = (
        walk_meta.get("hop_lawfulness") if isinstance(walk_meta, dict) else None
    )
    walk_hops = None
    if isinstance(walk_meta, dict):
        path = walk_meta.get("path")
        if isinstance(path, list):
            walk_hops = len(path)
        elif isinstance(hop_lawfulness, list):
            walk_hops = len(hop_lawfulness)

    walk_lawfulness_rollup = None
    if isinstance(hop_lawfulness, list):
        counts = {0: 0, 1: 0, 2: 0, 3: 0}
        total = 0
        for item in hop_lawfulness:
            if isinstance(item, int) and item in counts:
                counts[item] += 1
                total += 1
        if total:
            walk_lawfulness_rollup = {
                "L3": counts[3] / total,
                "L2": counts[2] / total,
                "L1": counts[1] / total,
                "L0": counts[0] / total,
            }

    eq6 = None
    if isinstance(latest_meta, dict):
        eq6 = {
            "commit_allowed": latest_meta.get("eq6_commit_allowed"),
            "lawfulness_level": latest_meta.get("eq6_lawfulness_level"),
            "mediator_prime": latest_meta.get("eq6_mediator_prime"),
        }

    appraisal = latest_meta.get("appraisal") if isinstance(latest_meta, dict) else None
    if not isinstance(appraisal, dict):
        appraisal = None

    e6_scoring = latest_meta.get("e6_scoring") if isinstance(latest_meta, dict) else None
    e6_header = latest_meta.get("e6_header_v0_fields") if isinstance(latest_meta, dict) else None
    promotion = latest_meta.get("promotion") if isinstance(latest_meta, dict) else None
    e6_diag = {
        "mode": e6_header.get("mode") if isinstance(e6_header, dict) else None,
        "route": e6_header.get("route") if isinstance(e6_header, dict) else None,
        "quality_tier": latest_meta.get("quality_tier") if isinstance(latest_meta, dict) else None,
        "bridge_allowed_runtime": (
            e6_scoring.get("bridge_allowed_runtime") if isinstance(e6_scoring, dict) else None
        ),
        "promotion_allowed": promotion.get("allowed") if isinstance(promotion, dict) else None,
        "promotion_reason": promotion.get("reason") if isinstance(promotion, dict) else None,
    }

    eval_contract = _build_eval_contract(
        metadata_payload=latest_meta if isinstance(latest_meta, Mapping) else None,
        appraisal_payload=appraisal if isinstance(appraisal, Mapping) else None,
    )
    posture_policy = _build_posture_policy(
        action="chat.introspect",
        eval_contract=eval_contract if isinstance(eval_contract, Mapping) else None,
        metadata_payload=latest_meta if isinstance(latest_meta, Mapping) else None,
    )

    max_tokens_env = os.getenv("OPENROUTER_MAX_TOKENS") or os.getenv("LLM_MAX_TOKENS")
    max_tokens = None
    if max_tokens_env:
        try:
            max_tokens = int(max_tokens_env)
        except (TypeError, ValueError):
            max_tokens = None

    llm_model = os.getenv("LLM_MODEL") or os.getenv("CHAT_MODEL")
    git_sha = os.getenv("GIT_SHA", "").strip() or "unknown"
    runtime_identity = latest_meta.get("runtime_identity") if isinstance(latest_meta.get("runtime_identity"), Mapping) else None
    gravity_tax_policy = (
        latest_meta.get("gravity_tax_policy") if isinstance(latest_meta.get("gravity_tax_policy"), Mapping) else None
    )
    latest_library_boundary = (
        runtime_identity.get("library_boundary")
        if isinstance(runtime_identity, Mapping) and isinstance(runtime_identity.get("library_boundary"), Mapping)
        else None
    )
    latest_foundation_identity = (
        latest_library_boundary.get("foundation_identity")
        if isinstance(latest_library_boundary, Mapping)
        and isinstance(latest_library_boundary.get("foundation_identity"), Mapping)
        else None
    )
    latest_history_continuity = (
        latest_library_boundary.get("history_continuity")
        if isinstance(latest_library_boundary, Mapping)
        and isinstance(latest_library_boundary.get("history_continuity"), Mapping)
        else None
    )
    continuity_checkpoint = (
        latest_library_boundary.get("continuity_checkpoint")
        if isinstance(latest_library_boundary, Mapping)
        and isinstance(latest_library_boundary.get("continuity_checkpoint"), Mapping)
        else None
    )
    latest_consolidation_event = (
        latest_library_boundary.get("latest_consolidation_event")
        if isinstance(latest_library_boundary, Mapping)
        and isinstance(latest_library_boundary.get("latest_consolidation_event"), Mapping)
        else None
    )
    latest_canonical_identity = (
        latest_library_boundary.get("canonical_identity_post_consolidation")
        if isinstance(latest_library_boundary, Mapping)
        and isinstance(latest_library_boundary.get("canonical_identity_post_consolidation"), Mapping)
        else None
    )

    return {
        "entity": namespace,
        "git_sha": git_sha,
        "latest_turn_coordinate": latest_coord,
        "turn_id": latest_coord,
        "recent_coords_count": summary.get("total_entries"),
        "llm_model": llm_model,
        "max_tokens": max_tokens,
        "timing": _coerce_timing(latest_meta or {}) if isinstance(latest_meta, dict) else None,
        "eq6": eq6,
        "walk": {
            "last_walk_id": walk_coord,
            "walk_hops": walk_hops,
            "walk_lawfulness_rollup": walk_lawfulness_rollup,
        },
        "appraisal": appraisal,
        "latest_attachment_coordinate": _find_latest_attachment(recent_entries),
        "last_seen_at": latest_meta.get("last_seen_at") if isinstance(latest_meta, dict) else None,
        "seen_count": latest_meta.get("seen_count") if isinstance(latest_meta, dict) else None,
        "hysteresis_coherence": latest_meta.get("hysteresis_coherence")
        if isinstance(latest_meta, dict)
        else None,
        "runtime_identity": dict(runtime_identity) if isinstance(runtime_identity, Mapping) else None,
        "retention_tier": latest_meta.get("retention_tier") if isinstance(latest_meta, dict) else None,
        "retention_tier_reason": latest_meta.get("retention_tier_reason") if isinstance(latest_meta, dict) else None,
        "gravity_tax_policy": dict(gravity_tax_policy) if isinstance(gravity_tax_policy, Mapping) else None,
        "foundation_identity": dict(latest_foundation_identity) if isinstance(latest_foundation_identity, Mapping) else None,
        "history_continuity": dict(latest_history_continuity) if isinstance(latest_history_continuity, Mapping) else None,
        "continuity_checkpoint": dict(continuity_checkpoint) if isinstance(continuity_checkpoint, Mapping) else None,
        "latest_consolidation_event": (
            dict(latest_consolidation_event) if isinstance(latest_consolidation_event, Mapping) else None
        ),
        "latest_consolidation_event_id": (
            latest_library_boundary.get("latest_consolidation_event_id")
            if isinstance(latest_library_boundary, Mapping)
            else None
        ),
        "ledger_version": latest_library_boundary.get("ledger_version") if isinstance(latest_library_boundary, Mapping) else None,
        "async_consolidation_state": (
            latest_library_boundary.get("async_consolidation_state")
            if isinstance(latest_library_boundary, Mapping)
            else None
        ),
        "canonical_identity_post_consolidation": (
            dict(latest_canonical_identity) if isinstance(latest_canonical_identity, Mapping) else None
        ),
        "e6_diagnostics": e6_diag,
        "eval_contract": eval_contract,
        "posture_policy": posture_policy,
        "e6_rollout_flags": latest_meta.get("e6_rollout_flags") if isinstance(latest_meta, dict) else None,
    }


@router.get("/stream/confirm")
async def confirm_stream_write(coordinate: str, request: Request):
    normalized = _normalize_confirm_coordinate(coordinate)
    return _confirm_stream_write(normalized, request)


def _decode_error_response(
    error_code: str,
    detail: Any,
    status_code: int,
) -> JSONResponse:
    """Return a consistent JSON error envelope for /web4/decode failures."""
    return JSONResponse(
        {"status": "error", "error_code": error_code, "detail": detail},
        status_code=status_code,
    )


@router.post("/stream/confirm")
async def confirm_stream_write_post(payload: dict, request: Request):
    enforce_pilot_write_allowed(request, action="chat.stream.confirm")
    coordinate = payload.get("coordinate") if isinstance(payload, dict) else None
    normalized = _normalize_confirm_coordinate(coordinate)
    return _confirm_stream_write(normalized, request)


@router.post("/web4/decode")
async def decode_coordinate(
    payload: dict,
    request: Request,
):
    """
    Librarian Endpoint: STRICTLY resolves a specific Ledger Entry.
    Does NOT search. Requires a precise 'Dewey Code' (Key).
    """

    # 1. Strict Input Parsing
    coord_input = None
    if "namespace" in payload and "identifier" in payload:
        coord_input = f"{payload['namespace']}:{payload['identifier']}"
    elif isinstance(payload.get("coordinate"), dict):
        coord_input = f"{payload['coordinate'].get('namespace')}:{payload['coordinate'].get('identifier')}"
    elif isinstance(payload.get("coordinate"), str):
        coord_input = payload["coordinate"]

    if not coord_input:
        return _decode_error_response(
            "invalid_coordinate",
            "Invalid Code. Please provide a valid identifier.",
            400,
        )

    normalized = normalise_coord(coord_input)
    web4_value = int(normalized["bare"]) if normalized.get("kind") == "web4" else None
    payload_ledger_id = payload.get("ledger_id") if isinstance(payload, dict) else None

    if not normalized.get("namespace"):
        fallback_namespace = payload.get("entity")
        if not fallback_namespace:
            session_id = payload.get("session_id")
            if session_id:
                fallback_namespace = f"chat-{session_id}"
        if fallback_namespace:
            normalized["namespace"] = fallback_namespace
            normalized["canonical"] = f"{fallback_namespace}:{normalized['bare']}"

    # If we still don't have a namespace (and no Web4 value), reject the request.
    if not normalized.get("namespace") and web4_value is None:
        return _decode_error_response(
            "invalid_coordinate",
            "Invalid Code. Please provide a full 'namespace:identifier' key.",
            400,
        )

    # 2. Setup Store (Read-Only Access)
    service = _optional_ledger_service(request)
    if service is None:
        return _decode_error_response(
            "library_database_locked",
            "Library database locked.",
            503,
        )

    # No index needed for direct read
    store = service.store

    if web4_value is not None:
        try:
            token_index = TokenPrimeIndex(request.app)
            factors = token_index.unique_prime_factors(web4_value)
            tokens = {prime: token_index.token_for_prime(prime) for prime in factors}
            entry_map = token_index.entries_for_primes(factors)
            knowledge = token_index.resolve_entries_for_primes(factors, store)  # type: ignore[arg-type]
            payload_data = build_payload_for_text(
                "W4",
                json.dumps(
                    {
                        "primes": factors,
                        "tokens": tokens,
                        "entries_by_prime": {str(prime): sorted(ids) for prime, ids in entry_map.items()},
                        "knowledge": knowledge,
                    },
                    ensure_ascii=True,
                ),
            )
            return resolve_response(
                coord=f"W4-{web4_value}",
                metadata={},
                payload=payload_data,
                refs={"inputs": [], "evidence": [], "context": [], "overlays": [], "governance": [], "walk_traces": [], "web4": [{"coord": f"W4-{web4_value}", "type": "W4"}]},
                walk=None,
                interpretation={"topics": [], "claims": [], "tags": []},
                governance={"appraisal": {}},
                meta={"namespace_used": None},
            )
        except Exception:
            return _decode_error_response(
                "invalid_web4_coordinate",
                "Invalid Web4 coordinate.",
                400,
            )

    # 3. Precise Lookup (The Librarian fetches the exact book)
    try:
        entry = None
        namespace_used = normalized.get("namespace")
        if namespace_used:
            try:
                resolved_scope = resolve_ledger_scope_or_raise(
                    request,
                    payload_ledger_id=(
                        str(payload_ledger_id).strip() if isinstance(payload_ledger_id, str) else None
                    ),
                    path_ledger_id=namespace_used,
                    hint="provide matching ledger_id/x-ledger-id for coordinate namespace",
                )
            except HTTPException as exc:
                error = (exc.detail or {}) if isinstance(exc.detail, dict) else {"error": "ledger_scope_error"}
                code = str(error.get("error") or "").strip()
                if code == "ledger_scope_mismatch":
                    return _decode_error_response("ledger_scope_mismatch", error, 400)
                if code == "ledger_context_required":
                    return _decode_error_response("ledger_context_required", error, 422)
                raise
            namespace_used = _canonicalize_ledger_scope(request, resolved_scope)
        if not namespace_used:
            explicit_scope = None
            if isinstance(payload_ledger_id, str) or any(
                isinstance(request.headers.get(h), str) and str(request.headers.get(h)).strip()
                for h in ("x-ledger-id", "x-ledger", "x-ledger-id-h64")
            ):
                try:
                    explicit_scope = resolve_ledger_scope_or_raise(
                        request,
                        payload_ledger_id=(
                            str(payload_ledger_id).strip() if isinstance(payload_ledger_id, str) else None
                        ),
                        path_ledger_id=None,
                        hint="provide ledger_id/x-ledger-id or namespace-qualified coordinate",
                    )
                except HTTPException as exc:
                    error = (exc.detail or {}) if isinstance(exc.detail, dict) else {"error": "ledger_scope_error"}
                    code = str(error.get("error") or "").strip()
                    if code == "ledger_scope_mismatch":
                        return _decode_error_response("ledger_scope_mismatch", error, 400)
                    if code == "ledger_context_required":
                        return _decode_error_response("ledger_context_required", error, 422)
                    raise
            if explicit_scope:
                namespace_used = _canonicalize_ledger_scope(request, explicit_scope)
            else:
                for candidate in namespace_candidates():
                    key = LedgerKey(namespace=candidate, identifier=normalized["bare"])
                    entry = store.read(key.as_path())
                    if entry:
                        namespace_used = candidate
                        break
            if not namespace_used:
                return _decode_error_response(
                    "missing_namespace",
                    {
                        "kind": normalized.get("kind"),
                        "canonical_coord": normalized["canonical"],
                        "namespace_used": None,
                        "hint": 'provide namespace like "<ns>:<coord>"',
                    },
                    422,
                )

        surface_id = str(
            request.headers.get("x-surface-id")
            or payload.get("surface_id")
            or ""
        ).strip()
        if surface_id and namespace_used:
            assert_surface_ledger_access(request, surface_id, namespace_used)

        if entry is None:
            key = LedgerKey(namespace=namespace_used, identifier=normalized["bare"])
            entry = store.read(key.as_path())

        if entry:
            metadata = entry.state.metadata or {}
            text_payload = entry.notes or metadata.get("content") or metadata.get("text") or ""
            coord = f"{namespace_used}:{normalized['bare']}"
            inputs = metadata.get("inputs") if isinstance(metadata.get("inputs"), dict) else None
            resolved_coords = metadata.get("resolved_coords") if isinstance(metadata.get("resolved_coords"), list) else None
            knowledge_tree = metadata.get("knowledge_tree") if isinstance(metadata.get("knowledge_tree"), list) else None
            walk_ids = metadata.get("walk_ids") if isinstance(metadata.get("walk_ids"), list) else None
            refs = build_refs(
                coord=coord,
                metadata=metadata,
                walk_ids=walk_ids,
                inputs=inputs,
                resolved_coords=resolved_coords,
                knowledge_tree=knowledge_tree,
            )
            interpretation = build_interpretation(metadata)
            governance = build_governance(metadata)

            payload_data: dict[str, Any]
            parts_meta = metadata.get("attachment_parts")
            parts_meta = parts_meta if isinstance(parts_meta, list) else []
            if normalized.get("kind") == "attachment":
                parts_payload: list[dict[str, Any]] = []
                attachment_group = metadata.get("attachment_group") or normalized.get("bare")
                for part in parts_meta:
                    if not isinstance(part, dict):
                        continue
                    suffix = part.get("part_suffix")
                    if not suffix and isinstance(part.get("index"), int):
                        suffix = f"T{part['index']:03d}"
                    if not suffix:
                        continue
                    part_coord = f"{attachment_group}-{suffix}"
                    if namespace_used:
                        part_coord = f"{namespace_used}:{part_coord}"
                    parts_payload.append(
                        {
                            "coord": part_coord,
                            "type": coord_type(part_coord),
                            "tokens_est": part.get("tokens_est") or 0,
                            "topics": part.get("topics") or [],
                            "tags": part.get("tags") or [],
                        }
                    )
                payload_data = build_payload_for_parts(parts_payload)
            else:
                payload_data = build_payload_for_text(coord_type(coord), text_payload)

            walk_payload: dict[str, Any] | None = None
            if normalized.get("kind") == "coord_walk":
                walk_payload = {
                    "path": metadata.get("path"),
                    "path_details": metadata.get("path_details"),
                    "planned_path": metadata.get("planned_path"),
                    "opened": metadata.get("opened"),
                    "findings": metadata.get("findings"),
                    "conflicts": metadata.get("conflicts"),
                    "confidence": metadata.get("confidence"),
                    "spent": metadata.get("spent"),
                    "termination_reason": metadata.get("termination_reason"),
                    "params": metadata.get("params"),
                    "query": metadata.get("query"),
                    "actor": metadata.get("actor"),
                }
            feedback_rollup = metadata.get("feedback_rollup")
            if not isinstance(feedback_rollup, dict):
                feedback_state = store.get_feedback(coord)
                feedback_rollup = (
                    feedback_state.get("rollup")
                    if isinstance(feedback_state, dict) and isinstance(feedback_state.get("rollup"), dict)
                    else None
                )

            return resolve_response(
                coord=coord,
                metadata=metadata,
                payload=payload_data,
                refs=refs,
                walk=walk_payload,
                interpretation=interpretation,
                governance=governance,
                meta={
                    "namespace_used": namespace_used,
                    "identifier": entry.key.identifier,
                    "created_at": entry.created_at.isoformat() if entry.created_at else "",
                    "pinned": entry.pinned,
                    "feedback_rollup": feedback_rollup,
                },
            )
        else:
            return _decode_error_response(
                "coordinate_not_found",
                "Book not found on shelf.",
                404,
            )

    except HTTPException:
        # Surface-authority errors and unhandled HTTP exceptions propagate with
        # their original status codes so middleware can classify them directly.
        raise
    except Exception:
        LOGGER.error("Decode failed for %s", normalized.get("canonical"), exc_info=True)
        return _decode_error_response(
            "librarian_system_error",
            "Librarian system error.",
            500,
        )


@router.post("/coord/walk")
async def coord_walk_endpoint(
    payload: dict,
    request: Request,
):
    start_coord = payload.get("start_coord")
    if not isinstance(start_coord, str) or not start_coord.strip():
        return {"status": "error", "detail": "start_coord is required"}
    try:
        max_steps = int(payload.get("max_steps", 6))
    except (TypeError, ValueError):
        max_steps = 6
    try:
        current_coherence = float(payload.get("current_coherence", 0.5))
    except (TypeError, ValueError):
        current_coherence = 0.5
    namespace_hint = payload.get("namespace")
    if not isinstance(namespace_hint, str):
        namespace_hint = None

    service = _optional_ledger_service(request)
    if service is None:
        return {"status": "error", "detail": "Library database locked."}
    store = service.store

    result = coord_walk(
        start_coord=start_coord.strip(),
        max_steps=max_steps,
        current_coherence=current_coherence,
        store=store,
        namespace_hint=namespace_hint,
    )
    if isinstance(result, dict):
        result.setdefault("policy_mode", "legacy_fallback")
        result.setdefault("decision_source", "chat.coord.walk")
        result.setdefault("deprecated", True)
        result.setdefault("deprecated_reason", "Use /chat and stream autonomy_decision + candidate_trace instead.")
    return result


@router.post("/walk/write")
async def walk_write_endpoint(
    payload: Dict[str, Any],
    request: Request,
):
    enforce_pilot_write_allowed(request, action="chat.walk.write")
    if not isinstance(payload, dict):
        return {"status": "error", "detail": "payload must be an object"}
    if payload.get("kind") != "coord_walk":
        return {"status": "error", "detail": "kind must be coord_walk"}
    start_coord = payload.get("start_coord")
    path = payload.get("path")
    if not isinstance(start_coord, str) or not isinstance(path, list):
        return {"status": "error", "detail": "start_coord and path are required"}
    if not all(isinstance(item, str) for item in path):
        return {"status": "error", "detail": "path must be a list of coord strings"}

    payload_ledger_id = payload.get("ledger_id") if isinstance(payload.get("ledger_id"), str) else None
    ledger_id = _resolve_explicit_ledger_id(request, payload_ledger_id)
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.write",
        explicit_context=True,
    )
    namespace = ledger_id
    requested_namespace = payload.get("namespace")

    walk_id = payload.get("walk_id")
    if not isinstance(walk_id, str) or not walk_id:
        walk_id = f"EV-WALK-{uuid.uuid4().hex}"

    steps = payload.get("steps") or []
    if steps and "hop_lawfulness" not in payload:
        payload["hop_lawfulness"] = [
            step.get("lawfulness_level", step.get("lawfulness", step.get("hop_lawfulness", 0)))
            for step in steps
        ]

    now = datetime.utcnow()
    metadata = dict(payload)
    metadata["kind"] = "coord_walk"
    metadata.setdefault("deprecated", True)
    metadata.setdefault("deprecated_reason", "Legacy walk write endpoint; prefer chat stream diagnostics.")
    metadata["walk_id"] = walk_id
    metadata["namespace"] = namespace
    if isinstance(requested_namespace, str) and requested_namespace.strip() and requested_namespace.strip() != namespace:
        metadata["requested_namespace"] = requested_namespace.strip()
    metadata.setdefault("created_at", now.isoformat())

    service = _optional_ledger_service(request)
    if service is None:
        return {"status": "error", "detail": "Library database locked."}

    store = service.store
    entry = LedgerEntry(  # type: ignore[call-arg]
        key=LedgerKey(namespace=namespace, identifier=walk_id),
        state=ContinuousState(metadata=metadata),
        created_at=now,
        notes=None,
    )
    store.write(entry)

    return {
        "status": "success",
        "walk_id": walk_id,
        "coordinate": f"{namespace}:{walk_id}",
        "created_at": now.isoformat(),
    }


@assess_router.post("/assess", response_model=ChatAssessmentResponse)
async def assess_chat_turn(
    payload: ChatAssessmentRequest,
    request: Request,
    substrate=Depends(get_memory_substrate),
    ledger=Depends(get_memory_ledger),
):
    service = _optional_ledger_service(request)
    store = service.store if service is not None else None

    result = await guardian_enrich_turn(
        entity=payload.entity,
        user_message=payload.user_message,
        assistant_reply=payload.assistant_reply,
        ledger=ledger,
        substrate=substrate,
        store=store,
        dry_run=True,
    )

    if result is None:
        return ChatAssessmentResponse(status="disabled", appraisal=None)

    return ChatAssessmentResponse(
        status="valid",
        appraisal=result.payload.appraisal or {},
    )


@assess_router.post("/grounding-guard", response_model=ChatGroundingGuardResponse)
async def apply_grounding_guard(payload: ChatGroundingGuardRequest):
    memories = payload.memories if isinstance(payload.memories, dict) else {}
    metadata_payload = payload.metadata if isinstance(payload.metadata, dict) else {}
    guarded_text, applied = _apply_metrics_grounding_guard(
        user_message=payload.user_message,
        response_text=payload.assistant_reply,
        memories=memories,
        metadata_payload=metadata_payload,
    )
    return ChatGroundingGuardResponse(
        assistant_reply=guarded_text,
        applied=applied,
        reason="ungrounded_numeric_delta_claims" if applied else None,
    )


@assess_router.post("/commit-answer", response_model=ChatCommitResponse)
async def commit_chat_answer(
    payload: ChatCommitRequest,
    request: Request,
    substrate=Depends(get_memory_substrate),
    ledger=Depends(get_memory_ledger),
):
    ledger_service = _optional_ledger_service(request)
    enforce_pilot_write_allowed(request, action="chat.commit_answer")
    apply_auth_claim_overrides(
        request,
        principal_did=payload.principal_did,
        principal_key_id=payload.principal_key_id,
        session_jti=payload.session_jti,
    )
    ledger_id = _resolve_explicit_ledger_id(request, payload.ledger_id)
    context_id = resolve_context_id_or_raise(
        request,
        payload_context_id=payload.context_id,
        require_for_write=True,
        hint="provide context_id in payload or x-context-id header",
    )
    write_namespace = resolve_write_namespace(ledger_id=ledger_id, entity=payload.entity)
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.write",
        explicit_context=True,
    )
    metadata = dict(cast(Dict[str, Any], payload.metadata or {}))
    standing_policy = _standing_policy_for_write_metadata(request, metadata)
    if not bool(standing_policy.get("write_commit_allowed", True)):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "standing_write_commit_denied",
                "standing_policy": {
                    "source": standing_policy.get("source"),
                    "retrieval_scope": standing_policy.get("retrieval_scope"),
                    "tool_scope": standing_policy.get("tool_scope"),
                    "max_output_tokens": standing_policy.get("max_output_tokens"),
                },
            },
        )
    metadata.setdefault(
        "standing_policy",
        {
            "source": standing_policy.get("source"),
            "tool_scope": standing_policy.get("tool_scope"),
            "retrieval_scope": standing_policy.get("retrieval_scope"),
            "retrieval_allowed": standing_policy.get("retrieval_allowed"),
            "max_output_tokens": standing_policy.get("max_output_tokens"),
            "write_commit_allowed": standing_policy.get("write_commit_allowed"),
            "effective_enable_ledger": True,
        },
    )
    try:
        metadata.update(
            normalize_subject_transition(
                request,
                metadata=metadata,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "subject_authority_transition_unverified", "reason": str(exc)},
        ) from exc
    metadata["web4_key"] = _canonical_commit_web4_key(write_namespace, metadata.get("web4_key"))
    metadata["runtime_identity"] = _normalize_runtime_identity_metadata(
        metadata,
        ledger_id=ledger_id,
        write_namespace=write_namespace,
        ledger_service=ledger_service,
    )
    persist_transcript = bool(payload.persist_conversation)
    delegated_prompt_path = _delegated_prompt_path_metadata(request, authz_diagnostics_from_request(request), metadata)
    if delegated_prompt_path:
        metadata["delegated_prompt_path"] = delegated_prompt_path
    metadata.update(
        build_write_provenance(
            request,
            ledger_id=write_namespace,
            metadata=metadata,
            session_id=(
                metadata.get("session_id")
                if isinstance(metadata.get("session_id"), str)
                else None
            ),
            turn_id=(
                metadata.get("turn_id")
                if isinstance(metadata.get("turn_id"), str)
                else None
            ),
            provider_id=(
                metadata.get("provider")
                if isinstance(metadata.get("provider"), str)
                else None
            ),
            model_id=(
                metadata.get("model_id")
                if isinstance(metadata.get("model_id"), str)
                else (
                    metadata.get("model")
                    if isinstance(metadata.get("model"), str)
                    else None
                )
            ),
            context_id=context_id,
        )
    )
    if "finish_reason" not in metadata:
        output_tokens = metadata.get("gen_output_tokens")
        if not isinstance(output_tokens, int):
            output_tokens = _estimate_tokens(payload.assistant_reply)
            metadata.setdefault("gen_output_tokens_est", output_tokens)
        disable_limits = os.getenv("DISABLE_RESPONSE_TOKEN_LIMITS", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        max_tokens_raw = None if disable_limits else (
            metadata.get("max_tokens") or os.getenv("OPENROUTER_MAX_TOKENS") or os.getenv("LLM_MAX_TOKENS")
        )
        try:
            max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else None
        except (TypeError, ValueError):
            max_tokens = None
        if max_tokens and output_tokens >= max(max_tokens - 1, 1):
            metadata["finish_reason"] = "length"

    service = _optional_ledger_service(request)
    store: LedgerStoreV2 | None = None
    if service is not None:
        token_index = TokenPrimeIndex(request.app)
        store = LedgerService(service.db, token_index=token_index).store

    # Assurance verification (out-of-band metadata only).
    expected_prev_signature = ""
    expected_prev_nonce = ""
    if store is not None:
        try:
            latest_entries = store.list_by_namespace(write_namespace, limit=1, reverse=True)
            if latest_entries:
                latest_meta = latest_entries[0].state.metadata if latest_entries[0].state else {}
                if isinstance(latest_meta, dict):
                    latest_assurance = latest_meta.get("assurance")
                    if isinstance(latest_assurance, dict):
                        expected_prev_signature = str(latest_assurance.get("signature") or "").strip()
                        expected_prev_nonce = str(latest_assurance.get("nonce") or "").strip()
        except Exception:
            LOGGER.exception("Failed to read latest entry for assurance continuity")

    assurance_payload = metadata.get("assurance")
    challenge_required = bool(metadata.get("assurance_challenge_required", ASSURANCE_CHALLENGE_REQUIRED))
    expected_challenge = metadata.get("assurance_challenge")
    if challenge_required and not isinstance(expected_challenge, dict):
        if ASSURANCE_ENFORCE:
            raise HTTPException(status_code=422, detail="assurance challenge is required")
    if not isinstance(assurance_payload, dict):
        if ASSURANCE_ENFORCE:
            raise HTTPException(status_code=422, detail="assurance envelope is required")
    else:
        history_hash = hash_history_from_metadata(
            metadata.get("history_hash") or assurance_payload.get("history_hash")
        )
        session_id = str(metadata.get("session_id") or assurance_payload.get("session_id") or "").strip()
        ok, reason, details = verify_assurance_envelope(
            envelope=assurance_payload,
            entity=write_namespace,
            session_id=session_id,
            user_message=payload.user_message,
            assistant_reply=payload.assistant_reply,
            history_hash=history_hash,
            expected_prev_signature=expected_prev_signature,
            expected_prev_nonce=expected_prev_nonce,
            expected_challenge=expected_challenge if isinstance(expected_challenge, dict) else None,
            challenge_required=challenge_required,
        )
        verification_record = {"status": "valid" if ok else "invalid", "reason": reason, **details}
        metadata["assurance_verification"] = verification_record
        if not ok and ASSURANCE_ENFORCE:
            raise HTTPException(status_code=409, detail=f"assurance verification failed: {reason}")

    enrich_result = await enrich_turn(
        entity=write_namespace,
        user_message=payload.user_message,
        assistant_reply=payload.assistant_reply,
        metadata={"kind": "chat", **metadata},
        precomputed_appraisal=payload.precomputed_appraisal,
        ledger=ledger,
        substrate=substrate,
        store=store,
        run_guardian=False,
        persist_transcript=persist_transcript,
    )
    merged_metadata: dict[str, Any] = dict(metadata)
    enrich_meta = enrich_result.get("metadata") if isinstance(enrich_result, dict) else None
    if isinstance(enrich_meta, dict):
        merged_metadata.update(enrich_meta)
    turn_coordinate = enrich_result.get("coordinate") if isinstance(enrich_result.get("coordinate"), str) else ""
    if turn_coordinate:
        merged_metadata["coord_meta"] = _build_coord_meta(
            coord=turn_coordinate,
            metadata=merged_metadata,
            write_namespace=write_namespace,
        )
    if ledger_service is not None and getattr(ledger_service, "db", None) is not None:
        try:
            _publish_decision_artifact_identity(
                db=ledger_service.db,
                metadata=merged_metadata,
                turn_coordinate=turn_coordinate or None,
            )
            if turn_coordinate and store is not None:
                stored_entry = store.read(turn_coordinate)
                if stored_entry is not None:
                    existing_meta = stored_entry.state.metadata or {}
                    for key in (
                        "factors",
                        "kernel_prime_exponents",
                        "mmf_projection_exponents",
                        "core_info_entry_class",
                        "flow_rule_tags",
                        "relationship_links",
                        "token_primes",
                        "token_prime_product",
                        "prime_multiplicative_value",
                        "prime_lattice_exponents",
                        "p_adic_write_cost",
                    ):
                        if key in existing_meta and key not in merged_metadata:
                            merged_metadata[key] = existing_meta[key]
                    store.update_metadata_overlay(
                        turn_coordinate, merged_metadata, replace=True
                    )
        except Exception:
            LOGGER.exception("Failed to publish decision artifact identity")
    LOGGER.info(
        "commit-answer: entity=%s coordinate=%s",
        write_namespace,
        enrich_result.get("coordinate"),
    )

    try:
        inputs = payload.metadata.get("inputs") if payload.metadata else None
        attachments = inputs.get("attachments") if isinstance(inputs, dict) else None
        parts_used = inputs.get("parts_used") if isinstance(inputs, dict) else None
        resolved_coords = payload.metadata.get("resolved_coords") if payload.metadata else None
        knowledge_tree = payload.metadata.get("knowledge_tree") if payload.metadata else None
        derived_attachments: list[str] = []
        attachment_set: set[str] = set()
        if isinstance(parts_used, list):
            for part_coord in parts_used:
                if not isinstance(part_coord, str):
                    continue
                attachment_set.add(part_coord)
                parent = re.sub(r"-(?:P|T|I|A|V|D)\\d{3}$", "", part_coord)
                if parent:
                    derived_attachments.append(parent)
                    attachment_set.add(parent)
        if not isinstance(attachments, list):
            attachments = []
        attachments = [*attachments, *derived_attachments]
        for attachment_coord in attachments:
            if isinstance(attachment_coord, str):
                attachment_set.add(attachment_coord)

        related_coords: list[str] = []
        if isinstance(resolved_coords, list):
            related_coords.extend([coord for coord in resolved_coords if isinstance(coord, str)])
        if isinstance(knowledge_tree, list):
            for item in knowledge_tree:
                if isinstance(item, dict):
                    coord = item.get("coordinate") or item.get("coord")
                    if isinstance(coord, str):
                        related_coords.append(coord)

        if store is not None:
            turn_coord = enrich_result.get("coordinate") or ""
            if turn_coord:
                for coord in related_coords:
                    _append_related_refs(
                        store,
                        coord,
                        turn_coord=turn_coord,
                        entity=write_namespace,
                    )

            attachment_list = sorted(attachment_set)
            if attachment_list and turn_coord:
                for attachment_coord in attachment_list:
                    related_attachments = [item for item in attachment_list if item != attachment_coord]
                    _append_related_refs(
                        store,
                        attachment_coord,
                        turn_coord=turn_coord,
                        related_attachments=related_attachments,
                        entity=write_namespace,
                    )
    except Exception:
        LOGGER.exception("Failed to update attachment related_turns")

    eval_contract = _build_eval_contract(metadata_payload=merged_metadata)
    posture_policy = _build_posture_policy(
        action="chat.commit_answer",
        eval_contract=eval_contract if isinstance(eval_contract, Mapping) else None,
        metadata_payload=merged_metadata,
    )
    return ChatCommitResponse(
        status="success",
        coordinate=enrich_result.get("coordinate"),
        metadata=merged_metadata,
        eval_contract=eval_contract,
        posture_policy=posture_policy,
    )


@assess_router.get("/introspect")
async def introspect_runtime(
    request: Request,
    entity: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Expose minimal runtime/ledger state for introspection."""
    service = _optional_ledger_service(request)
    if service is None:
        raise HTTPException(status_code=503, detail="Library database locked.")

    namespace = entity
    if not namespace and session_id:
        namespace = f"chat-{session_id}"
    if not namespace:
        raise HTTPException(status_code=400, detail="entity or session_id is required")

    store = service.store
    result = _build_introspect_payload(store=store, namespace=namespace)
    # Fallback: inject foundation identity from ledger registry if missing
    if not result.get("foundation_identity"):
        try:
            ledger_boundary = service.get_ledger_library_boundary(namespace)
            foundation = ledger_boundary.get("foundation_identity") if isinstance(ledger_boundary, dict) else None
            if isinstance(foundation, dict) and foundation.get("name"):
                result["foundation_identity"] = dict(foundation)
                # Also inject into runtime_identity for middleware compatibility
                runtime_identity = result.get("runtime_identity") or {}
                library_boundary = runtime_identity.get("library_boundary") or {}
                library_boundary["foundation_identity"] = dict(foundation)
                runtime_identity["library_boundary"] = library_boundary
                result["runtime_identity"] = runtime_identity
        except Exception:
            pass
    return result
