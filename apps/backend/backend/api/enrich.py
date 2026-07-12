"""Append new body primes and update S2 metadata (Backward Compatible)."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from typing import Any, Dict, Optional

from backend.api.agent_writes import record_message
from backend.api.http import get_ledger_store, get_memory_ledger, get_memory_substrate
from backend.fieldx_kernel.kernel_origin_equations import (
    calculate_alpha_from_primes,
    calculate_persistence_cost,
)
from backend.fieldx_kernel.guardian import guardian_enrich_turn
from backend.fieldx_kernel.e6_packet import FORMAT_V0, MAGIC_V0, unpack_header_v0
from backend.metrics.benchmark_context import attach_request_benchmark_context
from backend.metrics.prod_benchmark_contract import SurfaceName
from backend.metrics.telemetry import RetrievalPath, TelemetryIds, TurnTelemetry
from backend.search.reindex import reindex_all
from backend.search.token_index import normalise_text
from backend.services.authz import authorize_or_raise
from backend.services.context_scope import resolve_context_id_or_raise
from backend.services.ledger_scope import resolve_ledger_scope_or_raise
from backend.services.namespace_policy import resolve_write_namespace
from backend.services.provenance import build_write_provenance, normalize_subject_transition, resolve_authority_subject
from backend.services.ledger_service import LedgerService
from backend.services.standing_policy import resolve_standing_policy
from backend.services.authority_events import get_authority_state


router = APIRouter(tags=["enrich"])
logger = logging.getLogger(__name__)

_DEFAULT_COHERENCE = 0.9999
_ALPHA_VAL = calculate_alpha_from_primes()

class EnrichBody(BaseModel):
    role: str = Field(..., description="Message role (e.g., user/assistant)")
    content: str = Field(..., description="Message body text")
    kind: str = Field(..., description="Kind or channel (e.g. 'text', 'curation')")
    metadata: Dict[str, Any] | None = Field(default_factory=dict)


def _coerce_coherence(metadata: Dict[str, Any] | None) -> float:
    if not metadata:
        return _DEFAULT_COHERENCE
    appraisal = metadata.get("appraisal")
    if isinstance(appraisal, dict):
        score = appraisal.get("score")
        if isinstance(score, (int, float)):
            return float(score)
    assessments = metadata.get("assessments")
    if isinstance(assessments, dict):
        score = assessments.get("score")
        if isinstance(score, (int, float)):
            return float(score)
    return _DEFAULT_COHERENCE


def _count_ingest_words(text: str) -> int:
    return len([word for word in text.split() if word])


def _count_memory_tokens(text: str) -> int:
    return len(normalise_text(text))


def _validate_ingress_e6_header(metadata: Dict[str, Any] | None) -> Dict[str, Any]:
    """Validate optional ingress E6 header and return status payload."""
    result: Dict[str, Any] = {"mode": "off", "status": "skipped"}
    if not isinstance(metadata, dict):
        return result

    ingress_mode = os.getenv("E6_PACKET_INGRESS_MODE", "soft").strip().lower()
    if ingress_mode not in {"off", "soft", "hard"}:
        ingress_mode = "soft"
    result["mode"] = ingress_mode
    if ingress_mode == "off":
        return result

    raw_hex = metadata.get("e6_header_v0_hex")
    if not isinstance(raw_hex, str) or not raw_hex.strip():
        result["status"] = "missing"
        return result

    try:
        packed = bytes.fromhex(raw_hex.strip())
        fields = unpack_header_v0(packed)
    except Exception as exc:
        result["status"] = "invalid"
        result["reason"] = "decode_error"
        result["detail"] = str(exc)
        return result

    errors: list[str] = []
    if fields.get("magic") != MAGIC_V0:
        errors.append("bad_magic")
    if fields.get("fmt") != FORMAT_V0:
        errors.append("bad_format")
    if not bool(fields.get("crc_ok")):
        errors.append("bad_crc")

    expected_fields = metadata.get("e6_header_v0_fields")
    if isinstance(expected_fields, dict):
        for key in ("mode", "ptype", "law", "route", "node", "K", "P", "E", "valid", "dW", "seq", "V_q"):
            expected = expected_fields.get(key)
            actual = fields.get(key)
            if expected is None or actual is None:
                continue
            try:
                if int(expected) != int(actual):
                    errors.append(f"mismatch_{key}")
            except Exception:
                errors.append(f"mismatch_{key}")

    if errors:
        result["status"] = "invalid"
        result["reason"] = ",".join(errors)
    else:
        result["status"] = "valid"
        result["fields"] = fields
    return result


def _emit_enrich_telemetry(
    request: Request,
    *,
    entity: str,
    session_id: str | None,
    turn_id: str | None,
    memory_cost: float,
    memory_tokens: int,
    ingest_words: int,
    latency_ms: float,
) -> None:
    try:
        telemetry_store = LedgerService.from_request(request).telemetry_store()
        telemetry = TurnTelemetry(
            ids=TelemetryIds(
                session_id=session_id or "unknown",
                namespace=entity,
                entity=entity,
                turn_id=turn_id or f"enrich-{int(time.time() * 1000)}",
                timestamp=datetime.utcnow(),
            ),
            retrieval_path=RetrievalPath.MEMORY,
            memory_cost=memory_cost,
            memory_tokens=memory_tokens,
            ingest_words=ingest_words,
            latency_ms=latency_ms,
        )
        telemetry = attach_request_benchmark_context(
            telemetry,
            request,
            surface=SurfaceName.BACKEND,
            mode="enrich",
            tenant_id=entity,
        )
        telemetry_store.write_event(telemetry)
    except Exception:
        logger = logging.getLogger(__name__)
        logger.warning("Failed to emit enrich telemetry", exc_info=True)

class EnrichRequest(BaseModel):
    entity: str = Field(..., description="Entity identifier")
    context_id: str | None = Field(None, description="Optional context identifier")
    ledger_id: str | None = Field(
        None,
        description="Explicit ledger identifier for authorization/scoping.",
    )
    session_id: str | None = Field(None, description="Optional session identifier")
    turn_id: str | None = Field(None, description="Optional turn identifier")
    
    # --- Option A: Nested Body (New Frontend) ---
    body: Optional[EnrichBody] = None
    
    # --- Option B: Flat Fields (Legacy Frontend) ---
    role: Optional[str] = None
    content: Optional[str] = None
    kind: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    # Common optionals
    s2: Dict[str, Any] | None = None 
    prime: int | None = None

    @model_validator(mode='after')
    def consolidate_body(self) -> EnrichRequest:
        """Ensure we have a valid body object regardless of input format."""
        if self.body is not None:
            return self
            
        # If body is missing, try to build it from flat fields
        if self.role and self.content and self.kind:
            self.body = EnrichBody(
                role=self.role,
                content=self.content,
                kind=self.kind,
                metadata=self.metadata or {}
            )
            return self
            
        raise ValueError("Invalid payload: Must provide either a 'body' object or flat 'role/content/kind' fields.")

class EnrichResponse(BaseModel):
    entity: str
    prime: int
    s1: Dict[str, Any]
    s2: Dict[str, Any]
    body: Dict[str, Any]
    flow_enrich: Dict[str, Any]
    coordinate: str | None = None
    metadata: Dict[str, Any] | None = None


def _resolve_explicit_ledger_id(request: Request, payload_ledger_id: str | None) -> str:
    return resolve_ledger_scope_or_raise(
        request,
        payload_ledger_id=payload_ledger_id,
        hint="provide ledger_id in payload or x-ledger-id header",
    )


def _existing_entry_identifier(
    entity: str,
    *,
    turn_id: str | None,
    metadata: Dict[str, Any] | None,
) -> str | None:
    candidate = turn_id or (metadata or {}).get("web4_key")
    if not candidate:
        return None
    candidate = str(candidate).strip()
    if not candidate:
        return None
    if ":" in candidate:
        return candidate
    return f"{entity}:{candidate}"


def _existing_entry_prime(metadata: Dict[str, Any] | None) -> int:
    if not metadata:
        return 0
    for key in ("full_text_pointer", "prime"):
        value = metadata.get(key)
        if isinstance(value, int):
            return value
    return 0


class GuardianEnrichRequest(BaseModel):
    entity: str = Field(..., description="Entity identifier")
    context_id: str | None = Field(None, description="Optional context identifier")
    ledger_id: str | None = Field(
        None,
        description="Explicit ledger identifier for authorization/scoping.",
    )
    user_message: str = Field(..., description="User message text")
    assistant_reply: str = Field(..., description="Assistant reply text")


class GuardianEnrichResponse(BaseModel):
    entity: str
    teleology_alignment: float | None = None
    appraisal: Dict[str, Any] | None = None
    appraisal_reasoning: str | None = None
    summary: str | None = None
    summary_prime: int | None = None
    maintenance_request: str | None = None

@router.post("/enrich", response_model=EnrichResponse)
async def enrich(
    payload: EnrichRequest,
    request: Request,
    substrate=Depends(get_memory_substrate),
    ledger=Depends(get_memory_ledger),
    store=Depends(get_ledger_store),
):
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
    start_time = time.perf_counter()
    # We can safely access payload.body here because the validator guarantees it exists
    body_data = payload.body
    if not body_data:
        raise HTTPException(status_code=422, detail="Payload normalization failed")

    if body_data.metadata is not None and body_data.metadata.get("appraisal") is None:
        body_data.metadata["appraisal"] = {}
    if body_data.metadata is None:
        body_data.metadata = {}
    authority_subject = resolve_authority_subject(request, metadata=body_data.metadata)
    authority_subject_id = str(authority_subject.get("authority_subject_id") or "").strip()
    db = getattr(getattr(request, "app", None), "state", None)
    db = getattr(db, "db", None)
    authority_state = get_authority_state(db, authority_subject_id) if authority_subject_id and db is not None else None
    standing_policy = resolve_standing_policy(metadata=body_data.metadata, authority_state=authority_state)
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
    body_data.metadata.setdefault(
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
        body_data.metadata.update(
            normalize_subject_transition(
                request,
                metadata=body_data.metadata,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "subject_authority_transition_unverified", "reason": str(exc)},
        ) from exc
    body_data.metadata.update(
        build_write_provenance(
            request,
            ledger_id=write_namespace,
            metadata=body_data.metadata,
            session_id=payload.session_id,
            turn_id=payload.turn_id,
            provider_id=(
                body_data.metadata.get("provider")
                if isinstance(body_data.metadata.get("provider"), str)
                else None
            ),
            model_id=(
                body_data.metadata.get("model_id")
                if isinstance(body_data.metadata.get("model_id"), str)
                else (
                    body_data.metadata.get("model")
                    if isinstance(body_data.metadata.get("model"), str)
                    else None
                )
            ),
            context_id=context_id,
        )
    )
    validation = _validate_ingress_e6_header(body_data.metadata or {})
    if body_data.metadata is not None:
        body_data.metadata["e6_ingress_validation"] = {
            "mode": validation.get("mode"),
            "status": validation.get("status"),
            "reason": validation.get("reason"),
        }
    if validation.get("status") == "invalid":
        message = f"Invalid ingress e6 header ({validation.get('reason') or 'unknown'})"
        if validation.get("mode") == "hard":
            raise HTTPException(status_code=400, detail=message)
        logger.warning(message)

    coherence = _coerce_coherence(body_data.metadata or {})
    memory_cost = calculate_persistence_cost(_ALPHA_VAL, coherence, len(body_data.content))
    memory_tokens = _count_memory_tokens(body_data.content)
    ingest_words = _count_ingest_words(body_data.content)

    existing_identifier = _existing_entry_identifier(
        write_namespace,
        turn_id=payload.turn_id,
        metadata=body_data.metadata,
    )
    if store is not None and existing_identifier:
        existing_entry = store.read(existing_identifier)
        if existing_entry is not None:
            entry_metadata = existing_entry.state.metadata or {}
            return EnrichResponse(
                entity=payload.entity,
                prime=_existing_entry_prime(entry_metadata),
                s1={},
                s2={},
                body={},
                flow_enrich={},
                coordinate=existing_entry.key.as_path(),
                metadata=entry_metadata,
            )

    result = record_message(
        write_namespace,
        body_data.role,
        body_data.content,
        body_data.kind,
        body_data.metadata,
        substrate,
        ledger,
        store=store,
        draft_text=body_data.content,
    )
    if result.get("skipped"):
        raise HTTPException(status_code=409, detail=result.get("reason", "record_message skipped"))

    _emit_enrich_telemetry(
        request=request,
        entity=payload.entity,
        session_id=payload.session_id,
        turn_id=payload.turn_id,
        memory_cost=memory_cost,
        memory_tokens=memory_tokens,
        ingest_words=ingest_words,
        latency_ms=(time.perf_counter() - start_time) * 1000.0,
    )

    return EnrichResponse(
        entity=payload.entity,
        prime=result["prime"],
        s1=result["s1"],
        s2=result["s2"],
        body=result["body"],
        flow_enrich=result["flow_enrich"],
        coordinate=result.get("coordinate"),
        metadata=result.get("metadata") if isinstance(result.get("metadata"), dict) else (body_data.metadata or None),
    )


@router.post("/enrich/guardian", response_model=GuardianEnrichResponse)
async def enrich_guardian(
    payload: GuardianEnrichRequest,
    request: Request,
    substrate=Depends(get_memory_substrate),
    ledger=Depends(get_memory_ledger),
):
    ledger_id = _resolve_explicit_ledger_id(request, payload.ledger_id)
    resolve_context_id_or_raise(
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
    store = LedgerService.from_request(request).store

    result = await guardian_enrich_turn(
        entity=write_namespace,
        user_message=payload.user_message,
        assistant_reply=payload.assistant_reply,
        ledger=ledger,
        substrate=substrate,
        store=store,
    )

    if result and result.payload.maintenance_request == "reindex":
        logger.warning("🛡️ Guardian triggered emergency re-indexing.")
        # Consider running in BackgroundTasks or a task queue if latency is a concern.
        reindex_all(request.app, entity=write_namespace)

    if result is None:
        return GuardianEnrichResponse(entity=payload.entity)

    payload_out = result.payload
    return GuardianEnrichResponse(
        entity=payload.entity,
        teleology_alignment=payload_out.teleology_alignment,
        appraisal=payload_out.appraisal,
        appraisal_reasoning=payload_out.appraisal_reasoning,
        summary=payload_out.summary,
        summary_prime=result.summary_prime,
        maintenance_request=payload_out.maintenance_request,
    )

__all__ = ["router"]
