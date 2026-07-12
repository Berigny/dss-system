"""REST endpoints for ledger interactions backed by the Field-X kernel."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.api.schemas import LedgerEntrySchema
from backend.api.logging_utils import log_operation
from backend.fieldx_kernel import LedgerKey
from backend.fieldx_kernel.ledger import MemoryLedger
from backend.fieldx_kernel.substrate import LedgerStoreV2, MemorySubstrate
from backend.metrics.benchmark_context import attach_request_benchmark_context
from backend.metrics.prod_benchmark_contract import SurfaceName
from backend.metrics.store import TelemetryStore
from backend.metrics.telemetry import (
    RetrievalPath,
    TelemetryIds,
    TelemetryReferences,
    TelemetrySearchFlags,
    TurnTelemetry,
)
from backend.search import service as search_service
from backend.search.token_index import TokenPrimeIndex, normalise_tokens
from backend.fieldx_kernel.substrate.ledger_store_v2 import _collect_text_fragments
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
from shared_types.coord_schema import sanitize_coordinate_metadata
from backend.services.authz import authorize_or_raise
from backend.services.context_scope import resolve_context_id_or_raise
from backend.services.external_verifier_attestations import (
    append_external_verifier_attestation,
    get_external_verifier_summary,
)
from backend.services.live_verifier_signatures import verify_live_signature_for_attestation
from backend.services.verifier_portals import get_verifier_portal
from backend.services.ledger_scope import resolve_ledger_scope_or_raise
from backend.services.ledger_service import LedgerService
from backend.services.pilot_account import enforce_pilot_write_allowed

# Define the routers here
router = APIRouter(prefix="/ledger", tags=["ledger"])
search_router = APIRouter(tags=["search"])
web4_router = APIRouter(prefix="/web4", tags=["web4"])

debug_ledger_service = LedgerService({})
debug_ledger_store = debug_ledger_service.store
LOGGER = logging.getLogger(__name__)


class CoordFeedbackRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    actor_type: str = Field("human")
    context_id: str | None = None
    rating: int = Field(..., ge=0, le=3)
    reason: str | None = None
    source: str | None = None
    verifier_portal: str | None = None
    verifier_identity: str | None = None
    verification_signature_ref: str | None = None
    verification_signature_b64u: str | None = None
    verification_proof_ref: str | None = None


class CoordAutoRateRequest(BaseModel):
    rating: int = Field(..., ge=0, le=3)
    reason: str | None = None
    context_id: str | None = None
    actor_id: str | None = None
    actor_type: str = Field("model")
    source: str | None = None
    model: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def parse_key(key_path: str) -> LedgerKey:
    """Parse a ledger key path of the form ``namespace:identifier``."""
    if ":" in key_path:
        namespace, identifier = key_path.rsplit(":", 1)
    else:
        namespace, identifier = "default", key_path

    if not namespace or not identifier:
        raise HTTPException(status_code=400, detail="Invalid ledger key")

    return LedgerKey(namespace=namespace, identifier=identifier)


def get_db(request: Request):
    return get_ledger_service(request).db


def get_ledger_service(request: Request) -> LedgerService:
    return LedgerService.from_request(request, with_token_index=False)


def get_ledger_store(request: Request, _service: LedgerService = Depends(get_ledger_service)) -> LedgerStoreV2:
    return LedgerService.from_request(request, with_token_index=True).store


def get_memory_substrate(service: LedgerService = Depends(get_ledger_service)) -> MemorySubstrate:
    return service.memory_substrate()


def get_memory_ledger(service: LedgerService = Depends(get_ledger_service)) -> MemoryLedger:
    return service.memory_ledger()


def get_telemetry_store(service: LedgerService = Depends(get_ledger_service)) -> TelemetryStore:
    return service.telemetry_store()


def _normalise_ledger_id(raw: str) -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        return cleaned
    if ":" in cleaned or cleaned.startswith("chat-"):
        return cleaned
    return f"chat-{cleaned}"


def _entry_text(entry) -> str:
    metadata = entry.state.metadata or {}
    if full_text := metadata.get("full_text"):
        return str(full_text)
    fragments = list(_collect_text_fragments(metadata))
    fragment_text = " ".join(str(fragment) for fragment in fragments if fragment)
    if fragment_text:
        return fragment_text
    notes = getattr(entry, "notes", None)
    return str(notes) if notes else ""


def _coerce_session_id(session_id: str | None, fallback: str) -> str:
    cleaned = (session_id or "").strip()
    return cleaned if cleaned else fallback


def _coerce_turn_id(turn_id: str | None, fallback: str) -> str:
    cleaned = (turn_id or "").strip()
    return cleaned if cleaned else fallback


def _telemetry_store_from_request(request: Request) -> TelemetryStore:
    return LedgerService.from_request(request).telemetry_store()



def _emit_search_telemetry(
    request: Request,
    *,
    session_id: str | None,
    turn_id: str | None,
    namespace: str,
    latency_ms: float,
    search_used: bool,
    search_succeeded: bool,
) -> None:
    try:
        store = _telemetry_store_from_request(request)
        ids = TelemetryIds(
            session_id=_coerce_session_id(session_id, "unknown"),
            namespace=namespace,
            entity=namespace,
            turn_id=_coerce_turn_id(turn_id, f"search-{int(time.time() * 1000)}"),
            timestamp=datetime.utcnow(),
        )
        telemetry = TurnTelemetry(
            ids=ids,
            retrieval_path=RetrievalPath.SEARCH,
            search=TelemetrySearchFlags(
                requested=True,
                used=search_used,
                succeeded=search_succeeded,
            ),
            latency_ms=latency_ms,
        )
        telemetry = attach_request_benchmark_context(
            telemetry,
            request,
            surface=SurfaceName.BACKEND,
            mode="search",
            tenant_id=namespace,
        )
        store.write_event(telemetry)
    except Exception:
        LOGGER.warning("Failed to emit search telemetry", exc_info=True)


def _emit_resolve_telemetry(
    request: Request,
    *,
    session_id: str | None,
    turn_id: str | None,
    namespace: str,
    resolve_success: bool,
) -> None:
    try:
        store = _telemetry_store_from_request(request)
        ids = TelemetryIds(
            session_id=_coerce_session_id(session_id, "unknown"),
            namespace=namespace,
            entity=namespace,
            turn_id=_coerce_turn_id(turn_id, f"resolve-{int(time.time() * 1000)}"),
            timestamp=datetime.utcnow(),
        )
        telemetry = TurnTelemetry(
            ids=ids,
            retrieval_path=RetrievalPath.MEMORY,
            references=TelemetryReferences(
                emitted_refs=0,
                resolve_attempts=1,
                resolve_successes=1 if resolve_success else 0,
            ),
        )
        telemetry = attach_request_benchmark_context(
            telemetry,
            request,
            surface=SurfaceName.BACKEND,
            mode="resolve",
            tenant_id=namespace,
        )
        store.write_event(telemetry)
    except Exception:
        LOGGER.warning("Failed to emit resolve telemetry", exc_info=True)


def _feedback_rollup_for_coord(
    *,
    store: LedgerStoreV2,
    coord: str,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    rollup = metadata.get("feedback_rollup")
    if isinstance(rollup, dict):
        return rollup
    feedback_state = store.get_feedback(coord)
    if isinstance(feedback_state, dict):
        fallback = feedback_state.get("rollup")
        if isinstance(fallback, dict):
            return fallback
    return None


def _submit_feedback_and_rollup(
    *,
    store: LedgerStoreV2,
    key: LedgerKey,
    actor_id: str,
    actor_type: str,
    rating: int,
    reason: str | None,
    source: str | None,
):
    record = store.submit_feedback(
        key.as_path(),
        actor_id=actor_id,
        actor_type=actor_type,
        rating=rating,
        reason=reason,
        source=source,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    feedback = store.get_feedback(key.as_path()) or {}
    rollup = feedback.get("rollup") if isinstance(feedback, dict) else {}
    return record, rollup


def _maybe_append_external_verifier_attestation(
    *,
    db: Any,
    key: LedgerKey,
    actor_id: str,
    actor_type: str,
    rating: int,
    reason: str | None,
    source: str | None,
    verifier_portal: str | None,
    verifier_identity: str | None,
    verification_signature_ref: str | None,
    verification_signature_b64u: str | None,
    verification_proof_ref: str | None,
):
    portal = str(verifier_portal or "").strip()
    identity = str(verifier_identity or "").strip()
    if not portal and str(source or "").strip().lower() in {"decoder_app", "mcp_auto_rate", "chatgpt_mcp"}:
        portal = str(source or "").strip().lower()
    if not identity and portal:
        identity = str(actor_id or "").strip()
    if not portal or not identity:
        return None
    portal_record = get_verifier_portal(db, portal)
    signature_ref = str(verification_signature_ref or "").strip()
    signature_b64u = str(verification_signature_b64u or "").strip()
    if (
        isinstance(portal_record, dict)
        and str(portal_record.get("verification_mode") or "").strip().lower() == "signature_required"
        and signature_ref
        and signature_b64u
    ):
        verify_live_signature_for_attestation(
            db,
            evidence_ref=key.as_path(),
            actor_id=actor_id,
            actor_type=actor_type,
            rating=rating,
            reason=reason,
            source=source,
            verifier_portal=portal,
            verifier_identity=identity,
            verification_signature_ref=signature_ref,
            verification_signature_b64u=signature_b64u,
            verification_proof_ref=verification_proof_ref,
        )
    return append_external_verifier_attestation(
        db,
        evidence_ref=key.as_path(),
        actor_id=actor_id,
        actor_type=actor_type,
        rating=rating,
        verifier_portal=portal,
        verifier_identity=identity,
        reason=reason,
        source=source,
        verification_signature_ref=verification_signature_ref,
        verification_proof_ref=verification_proof_ref,
    )


def _default_auto_actor(model: str | None) -> str:
    cleaned_model = (model or "").strip()
    if cleaned_model:
        return f"model:{cleaned_model}"
    return "model:auto"


@search_router.get("/search")
def search_entries(
    request: Request,
    query: str = Query(..., description="Search query"),
    mode: str = Query("any", description="Search mode: any or all"),
    limit: int = Query(50, ge=1, le=200),
    session_id: str | None = Query(None, description="Optional session identifier"),
    turn_id: str | None = Query(None, description="Optional turn identifier"),
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    start = time.perf_counter()
    token_index = TokenPrimeIndex(request.app)
    results = search_service.search(
        query,
        store=store,
        token_index=token_index,
        mode=mode,
        limit=limit,
    )
    latency_ms = (time.perf_counter() - start) * 1000.0
    namespace = f"chat-{session_id}" if session_id else "search"
    _emit_search_telemetry(
        request,
        session_id=session_id,
        turn_id=turn_id,
        namespace=namespace,
        latency_ms=latency_ms,
        search_used=True,
        search_succeeded=bool(results),
    )
    return {"results": results, "latency_ms": int(latency_ms)}


@router.post("/write", response_model=LedgerEntrySchema)
def write_entry(
    request: Request,
    entry: LedgerEntrySchema,
    store: LedgerStoreV2 = Depends(get_ledger_store),
) -> LedgerEntrySchema:
    """Persist a ledger entry in the shared store."""
    enforce_pilot_write_allowed(request, action="ledger.write")
    authorize_or_raise(
        request,
        ledger_id=entry.key.namespace,
        action="ledger.write",
        explicit_context=True,
    )
    with log_operation(
        LOGGER,
        "ledger_write",
        request=request,
        namespace=entry.key.namespace,
        identifier=entry.key.identifier,
    ) as ctx:
        model_entry = entry.to_model()
        store.write(model_entry)
        ctx.update(
            {
                "ledger_key": model_entry.key.as_path(),
                "entries_written": 1,
            }
        )
        return LedgerEntrySchema.from_model(model_entry)


@router.get("/read/{entry_id}", response_model=LedgerEntrySchema)
def read_entry(
    request: Request,
    entry_id: str,
    verify_chain: bool = Query(False, description="Enable strict read-time chain verification."),
    store: LedgerStoreV2 = Depends(get_ledger_store),
) -> LedgerEntrySchema:
    """Return a ledger entry for the provided identifier."""
    with log_operation(
        LOGGER,
        "ledger_read",
        request=request,
        ledger_key=entry_id,
    ) as ctx:
        key = parse_key(entry_id)
        try:
            record = store.read(key.as_path(), verify_chain=verify_chain)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        ctx.update(
            {
                "namespace": key.namespace,
                "identifier": key.identifier,
                "entries_returned": 1,
            }
        )
        return LedgerEntrySchema.from_model(record)


@router.get("/chain/verify/{namespace}")
def verify_chain_namespace(
    request: Request,
    namespace: str,
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Run read-side chain verification for a namespace and return diagnostics."""
    authorize_or_raise(
        request,
        ledger_id=namespace,
        action="ledger.read",
        explicit_context=False,
    )
    with log_operation(
        LOGGER,
        "ledger_verify_chain_namespace",
        request=request,
        namespace=namespace,
    ) as ctx:
        status = store.verify_namespace_chain(namespace)
        ctx.update(
            {
                "valid": bool(status.get("valid")),
                "entries_checked": int(status.get("entries_checked") or 0),
                "failure_reason": status.get("failure_reason"),
            }
        )
        return status


@router.post("/pin/{entry_id}", response_model=LedgerEntrySchema)
def pin_entry(
    request: Request,
    entry_id: str,
    store: LedgerStoreV2 = Depends(get_ledger_store),
) -> LedgerEntrySchema:
    """Mark a ledger entry as pinned and persist the state."""
    enforce_pilot_write_allowed(request, action="ledger.pin")
    normalised_id = _normalise_ledger_id(entry_id)
    key = parse_key(normalised_id)
    authorize_or_raise(
        request,
        ledger_id=key.namespace,
        action="ledger.pin",
        explicit_context=True,
    )
    with log_operation(
        LOGGER,
        "ledger_pin",
        request=request,
        ledger_key=normalised_id,
    ) as ctx:
        record = store.submit_feedback(
            key.as_path(),
            actor_id="system:pin",
            actor_type="system",
            rating=3,
            reason="compat_pin",
            source="legacy_pin_endpoint",
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        ctx.update({"pinned": True, "namespace": key.namespace, "identifier": key.identifier})
        return LedgerEntrySchema.from_model(record)


@router.post("/unpin/{entry_id}", response_model=LedgerEntrySchema)
def unpin_entry(
    request: Request,
    entry_id: str,
    store: LedgerStoreV2 = Depends(get_ledger_store),
) -> LedgerEntrySchema:
    """Remove the pinned marker from a ledger entry."""
    enforce_pilot_write_allowed(request, action="ledger.unpin")
    normalised_id = _normalise_ledger_id(entry_id)
    key = parse_key(normalised_id)
    authorize_or_raise(
        request,
        ledger_id=key.namespace,
        action="ledger.pin",
        explicit_context=True,
    )
    with log_operation(
        LOGGER,
        "ledger_unpin",
        request=request,
        ledger_key=normalised_id,
    ) as ctx:
        record = store.submit_feedback(
            key.as_path(),
            actor_id="system:pin",
            actor_type="system",
            rating=0,
            reason="compat_unpin",
            source="legacy_unpin_endpoint",
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        ctx.update({"pinned": False, "namespace": key.namespace, "identifier": key.identifier})
        return LedgerEntrySchema.from_model(record)


@router.get("/pinned", response_model=list[LedgerEntrySchema])
def list_pinned_entries(
    request: Request,
    namespace: str | None = Query(None, description="Optional namespace filter"),
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Return all pinned ledger entries, optionally scoped to ``namespace``."""
    with log_operation(
        LOGGER,
        "ledger_list_pinned",
        request=request,
        namespace=namespace,
    ) as ctx:
        records = store.list_pinned_entries(namespace)
        ctx.update({"pinned_count": len(records)})
        return [LedgerEntrySchema.from_model(record) for record in records]


@router.post("/feedback/{entry_id}")
def submit_coord_feedback(
    request: Request,
    entry_id: str,
    payload: CoordFeedbackRequest,
    db=Depends(get_db),
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Submit a 0..3 feedback rating with reason for a coordinate/entry."""
    enforce_pilot_write_allowed(request, action="ledger.feedback")
    normalised_id = _normalise_ledger_id(entry_id)
    key = parse_key(normalised_id)
    ledger_id = resolve_ledger_scope_or_raise(
        request,
        path_ledger_id=key.namespace,
        hint="provide x-ledger-id header or coordinate with explicit namespace",
    )
    resolve_context_id_or_raise(
        request,
        payload_context_id=payload.context_id,
        require_for_write=True,
        hint="provide context_id in payload or x-context-id header",
    )
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.feedback",
        explicit_context=True,
    )
    with log_operation(
        LOGGER,
        "ledger_feedback_submit",
        request=request,
        ledger_key=normalised_id,
    ) as ctx:
        record, rollup = _submit_feedback_and_rollup(
            store=store,
            key=key,
            actor_id=payload.actor_id,
            actor_type=payload.actor_type,
            rating=payload.rating,
            reason=payload.reason,
            source=payload.source,
        )
        verifier_attestation = _maybe_append_external_verifier_attestation(
            db=db,
            key=key,
            actor_id=payload.actor_id,
            actor_type=payload.actor_type,
            rating=payload.rating,
            reason=payload.reason,
            source=payload.source,
            verifier_portal=payload.verifier_portal,
            verifier_identity=payload.verifier_identity,
            verification_signature_ref=payload.verification_signature_ref,
            verification_signature_b64u=payload.verification_signature_b64u,
            verification_proof_ref=payload.verification_proof_ref,
        )
        external_verification = get_external_verifier_summary(db, key.as_path())
        ctx.update(
            {
                "namespace": key.namespace,
                "identifier": key.identifier,
                "rating": payload.rating,
                "pinned": bool(record.pinned),
                "score": (rollup or {}).get("score") if isinstance(rollup, dict) else None,
                "external_verification": bool(external_verification),
            }
        )
        return {
            "status": "ok",
            "entry_id": key.as_path(),
            "pinned": bool(record.pinned),
            "rollup": rollup,
            "verifier_attestation": verifier_attestation,
            "external_verification": external_verification,
        }


@router.post("/feedback/auto/{entry_id}")
def auto_rate_coord(
    request: Request,
    entry_id: str,
    payload: CoordAutoRateRequest,
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Submit model-driven feedback for a coordinate/entry."""
    enforce_pilot_write_allowed(request, action="ledger.feedback.auto")
    normalised_id = _normalise_ledger_id(entry_id)
    key = parse_key(normalised_id)
    ledger_id = resolve_ledger_scope_or_raise(
        request,
        path_ledger_id=key.namespace,
        hint="provide x-ledger-id header or coordinate with explicit namespace",
    )
    resolve_context_id_or_raise(
        request,
        payload_context_id=payload.context_id,
        require_for_write=True,
        hint="provide context_id in payload or x-context-id header",
    )
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.feedback",
        explicit_context=True,
    )
    actor_id = (payload.actor_id or "").strip() or _default_auto_actor(payload.model)
    source = (payload.source or "").strip() or "mcp_auto_rate"
    with log_operation(
        LOGGER,
        "ledger_feedback_auto_submit",
        request=request,
        ledger_key=normalised_id,
    ) as ctx:
        record, rollup = _submit_feedback_and_rollup(
            store=store,
            key=key,
            actor_id=actor_id,
            actor_type=payload.actor_type,
            rating=payload.rating,
            reason=payload.reason,
            source=source,
        )
        ctx.update(
            {
                "namespace": key.namespace,
                "identifier": key.identifier,
                "rating": payload.rating,
                "source": source,
                "model": payload.model,
                "confidence": payload.confidence,
                "score": (rollup or {}).get("score") if isinstance(rollup, dict) else None,
            }
        )
        return {
            "status": "ok",
            "entry_id": key.as_path(),
            "pinned": bool(record.pinned),
            "rollup": rollup,
            "applied": {
                "actor_id": actor_id,
                "actor_type": payload.actor_type,
                "rating": payload.rating,
                "reason": payload.reason,
                "source": source,
                "model": payload.model,
                "confidence": payload.confidence,
            },
        }


@router.get("/feedback/{entry_id}")
def get_coord_feedback(
    request: Request,
    entry_id: str,
    context_id: str | None = Query(None, description="Optional context identifier"),
    db=Depends(get_db),
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Get feedback rollup and recent feedback history for a coordinate/entry."""
    normalised_id = _normalise_ledger_id(entry_id)
    key = parse_key(normalised_id)
    ledger_id = resolve_ledger_scope_or_raise(
        request,
        path_ledger_id=key.namespace,
        hint="provide x-ledger-id header or coordinate with explicit namespace",
    )
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.feedback",
        explicit_context=True,
    )
    resolve_context_id_or_raise(
        request,
        payload_context_id=context_id,
        require_for_write=False,
        hint="provide context_id in query or x-context-id header",
    )
    with log_operation(
        LOGGER,
        "ledger_feedback_get",
        request=request,
        ledger_key=normalised_id,
    ) as ctx:
        feedback = store.get_feedback(key.as_path())
        if feedback is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        external_verification = get_external_verifier_summary(db, key.as_path())
        rollup = feedback.get("rollup") if isinstance(feedback, dict) else {}
        ctx.update(
            {
                "namespace": key.namespace,
                "identifier": key.identifier,
                "actors": (rollup or {}).get("actors") if isinstance(rollup, dict) else None,
                "score": (rollup or {}).get("score") if isinstance(rollup, dict) else None,
                "external_verification": bool(external_verification),
            }
        )
        enriched = dict(feedback)
        enriched["external_verification"] = external_verification
        return enriched


@router.get("/summary/{namespace}")
def get_ledger_summary(
    request: Request,
    namespace: str,
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Return lightweight metadata for entries within ``namespace``."""
    with log_operation(
        LOGGER,
        "ledger_summary",
        request=request,
        namespace=namespace,
    ) as ctx:
        summary = store.summarize(namespace=namespace)
        total_entries = summary.get("total_entries", 0)
        last_updated = summary.get("last_updated")
        response = {
            **summary,
            "entry_count": total_entries,
            "memory_count": total_entries,
            "updated_at": last_updated,
            "last_update": last_updated,
        }
        ctx.update(response)
        return response


@router.post("/debug/ledger/write", response_model=LedgerEntrySchema)
def debug_write_entry(request: Request, entry: LedgerEntrySchema) -> LedgerEntrySchema:
    """Persist a ledger entry using the in-memory store for debugging."""
    enforce_pilot_write_allowed(request, action="ledger.debug.write")
    authorize_or_raise(
        request,
        ledger_id=entry.key.namespace,
        action="ledger.write",
        explicit_context=True,
    )
    model_entry = entry.to_model()
    debug_ledger_store.upsert(model_entry)
    return LedgerEntrySchema.from_model(model_entry)


@web4_router.get("/encode")
def encode_coordinate(
    request: Request,
    entry_id: str | None = Query(
        None, description="Ledger entry identifier to encode. Required if snippet absent."
    ),
    snippet: str | None = Query(
        None, description="Raw text snippet to encode instead of a stored entry."
    ),
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Encode ledger knowledge into a prime-derived coordinate."""
    token_index = TokenPrimeIndex(request.app)
    target_entry = None
    text_source = "snippet" if snippet else "entry"

    if snippet:
        text = snippet.strip()
    elif entry_id:
        key = parse_key(entry_id)
        target_entry = store.read(key.as_path())
        if target_entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        text = _entry_text(target_entry)
    else:
        raise HTTPException(
            status_code=422, detail="Provide either entry_id or snippet for encoding"
        )

    cleaned_text = (text or "").strip()
    if not cleaned_text:
        raise HTTPException(status_code=422, detail="No text available to encode")

    tokens = normalise_tokens(cleaned_text)
    if not tokens:
        raise HTTPException(status_code=422, detail="No tokens found for encoding")

    primes = token_index.primes_for_tokens(tokens)
    try:
        coordinate = token_index.product_for_primes(primes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "coordinate": coordinate,
        "primes": primes,
        "tokens": tokens,
        "source": text_source,
        "entry_id": target_entry.key.as_path() if target_entry else None,
    }


@web4_router.get("/decode")
def decode_coordinate(
    request: Request,
    coordinate: int = Query(..., description="Prime product coordinate to decode", gt=0),
    session_id: str | None = Query(None, description="Optional session identifier"),
    turn_id: str | None = Query(None, description="Optional turn identifier"),
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Decode a coordinate back into ledger knowledge via the inverted index."""
    token_index = TokenPrimeIndex(request.app)
    resolve_success = False
    try:
        factors = token_index.unique_prime_factors(int(coordinate))
        resolve_success = True
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        _emit_resolve_telemetry(
            request,
            session_id=session_id,
            turn_id=turn_id,
            namespace="web4",
            resolve_success=resolve_success,
        )

    tokens = {prime: token_index.token_for_prime(prime) for prime in factors}
    entry_map = token_index.entries_for_primes(factors)
    knowledge = token_index.resolve_entries_for_primes(factors, cast(Any, store))

    payload = build_payload_for_text(
        "W4",
        json.dumps(
            sanitize_coordinate_metadata({
                "primes": factors,
                "tokens": tokens,
                "entries_by_prime": {str(prime): sorted(ids) for prime, ids in entry_map.items()},
                "knowledge": knowledge,
            }),
            ensure_ascii=True,
        ),
    )
    return resolve_response(
        coord=f"W4-{coordinate}",
        metadata={},
        payload=payload,
        refs={"inputs": [], "evidence": [], "context": [], "overlays": [], "governance": [], "walk_traces": [], "web4": [{"coord": f"W4-{coordinate}", "type": "W4"}]},
        walk=None,
        interpretation={"topics": [], "claims": [], "tags": []},
        governance={"appraisal": {}},
        meta={"namespace_used": None},
    )


@web4_router.post("/decode")
def decode_coordinate_post(
    request_payload: dict[str, Any],
    request: Request,
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Decode a ledger coordinate or web4 key back into stored knowledge."""
    coord_input = None
    if isinstance(request_payload, dict):
        if "namespace" in request_payload and "identifier" in request_payload:
            coord_input = f"{request_payload.get('namespace')}:{request_payload.get('identifier')}"
        elif isinstance(request_payload.get("coordinate"), dict):
            coord_field = request_payload.get("coordinate") or {}
            coord_input = f"{coord_field.get('namespace')}:{coord_field.get('identifier')}"
        elif isinstance(request_payload.get("coordinate"), str):
            coord_input = request_payload.get("coordinate")
    coord = (coord_input or "").strip()
    if not coord:
        raise HTTPException(status_code=422, detail="coordinate is required")
    payload_ledger_id = request_payload.get("ledger_id") if isinstance(request_payload, dict) else None

    normalized = normalise_coord(coord)
    if normalized.get("kind") == "web4":
        resolve_success = False
        try:
            coordinate = int(normalized["bare"])
            token_index = TokenPrimeIndex(request.app)
            factors = token_index.unique_prime_factors(coordinate)
            resolve_success = True
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
                _emit_resolve_telemetry(
                    request,
                    session_id=(request_payload or {}).get("session_id"),
                    turn_id=(request_payload or {}).get("turn_id"),
                    namespace="web4",
                    resolve_success=resolve_success,
                )
        tokens = {prime: token_index.token_for_prime(prime) for prime in factors}
        entry_map = token_index.entries_for_primes(factors)
        knowledge = token_index.resolve_entries_for_primes(factors, cast(Any, store))
        payload = build_payload_for_text(
            "W4",
            json.dumps(
                sanitize_coordinate_metadata({
                    "primes": factors,
                    "tokens": tokens,
                    "entries_by_prime": {str(prime): sorted(ids) for prime, ids in entry_map.items()},
                    "knowledge": knowledge,
                }),
                ensure_ascii=True,
            ),
        )
        return resolve_response(
            coord=f"W4-{coordinate}",
            metadata={},
            payload=payload,
            refs={"inputs": [], "evidence": [], "context": [], "overlays": [], "governance": [], "walk_traces": [], "web4": [{"coord": f"W4-{coordinate}", "type": "W4"}]},
            walk=None,
            interpretation={"topics": [], "claims": [], "tags": []},
            governance={"appraisal": {}},
            meta={"namespace_used": None},
        )

    resolve_success = False
    namespace = normalized.get("namespace")
    if namespace:
        namespace = resolve_ledger_scope_or_raise(
            request,
            payload_ledger_id=(str(payload_ledger_id).strip() if isinstance(payload_ledger_id, str) else None),
            path_ledger_id=namespace,
            hint="provide matching ledger_id/x-ledger-id for coordinate namespace",
        )
    if not namespace:
        explicit_scope = resolve_ledger_scope_or_raise(
            request,
            payload_ledger_id=(str(payload_ledger_id).strip() if isinstance(payload_ledger_id, str) else None),
            path_ledger_id=None,
            hint="provide ledger_id/x-ledger-id or namespace-qualified coordinate",
        ) if isinstance(payload_ledger_id, str) or any(
            isinstance(request.headers.get(h), str) and str(request.headers.get(h)).strip()
            for h in ("x-ledger-id", "x-ledger", "x-ledger-id-h64")
        ) else None
        if explicit_scope:
            namespace = explicit_scope
        else:
            for candidate in namespace_candidates():
                key = parse_key(f"{candidate}:{normalized['bare']}")
                entry = store.read(key.as_path())
                if entry is None:
                    continue
                namespace = candidate
                break
        if not namespace:
            return {
                "status": "error",
                "kind": normalized.get("kind"),
                "canonical_coord": normalized["canonical"],
                "namespace_used": None,
                "error_code": "missing_namespace",
                "hint": 'provide namespace like "<ns>:<coord>"',
            }

    key = parse_key(f"{namespace}:{normalized['bare']}")
    entry = store.read(key.as_path())
    if entry is None:
        _emit_resolve_telemetry(
            request,
            session_id=(request_payload or {}).get("session_id"),
            turn_id=(request_payload or {}).get("turn_id"),
            namespace="web4",
            resolve_success=False,
        )
        raise HTTPException(status_code=404, detail="Entry not found for coordinate")
    resolve_success = True

    metadata = entry.state.metadata or {}
    content_text = metadata.get("content") or metadata.get("full_text")
    if isinstance(content_text, str) and content_text.strip():
        full_text = content_text.strip()
    else:
        fragments = list(_collect_text_fragments(metadata))
        full_text = " ".join(str(f) for f in fragments if f) or ""

    coord = f"{key.namespace}:{normalized['bare']}"
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

    response_payload: dict[str, Any]
    parts_meta_raw = metadata.get("attachment_parts")
    parts_meta: list[Any] = parts_meta_raw if isinstance(parts_meta_raw, list) else []
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
            if key.namespace:
                part_coord = f"{key.namespace}:{part_coord}"
            parts_payload.append(
                {
                    "coord": part_coord,
                    "type": coord_type(part_coord),
                    "tokens_est": part.get("tokens_est") or 0,
                    "topics": part.get("topics") or [],
                    "tags": part.get("tags") or [],
                }
            )
        response_payload = build_payload_for_parts(parts_payload)
    else:
        response_payload = build_payload_for_text(coord_type(coord), full_text)

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

    response = resolve_response(
        coord=coord,
        metadata=metadata,
        payload=response_payload,
        refs=refs,
        walk=walk_payload,
        interpretation=interpretation,
        governance=governance,
        meta={
            "namespace_used": key.namespace,
            "identifier": key.identifier,
            "created_at": entry.created_at.isoformat(),
            "pinned": entry.pinned,
            "feedback_rollup": _feedback_rollup_for_coord(
                store=store,
                coord=coord,
                metadata=metadata,
            ),
        },
    )
    _emit_resolve_telemetry(
        request,
        session_id=(request_payload or {}).get("session_id"),
        turn_id=(request_payload or {}).get("turn_id"),
        namespace="web4",
        resolve_success=resolve_success,
    )
    return response


@web4_router.post("/decode/batch")
def decode_coordinate_batch(
    payload: Any,
    request: Request,
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """Decode multiple ledger coordinates (or web4 keys) in one request."""
    items = payload
    if isinstance(payload, dict):
        items = payload.get("coordinates")
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="coordinates must be a list")

    token_index = TokenPrimeIndex(request.app)
    results: list[dict[str, Any]] = []
    payload_ledger_id = payload.get("ledger_id") if isinstance(payload, dict) else None
    header_scope_present = any(
        isinstance(request.headers.get(h), str) and str(request.headers.get(h)).strip()
        for h in ("x-ledger-id", "x-ledger", "x-ledger-id-h64")
    )

    for item in items:
        coord_input: str | None = None
        if isinstance(item, str):
            coord_input = item
        elif isinstance(item, dict):
            namespace = item.get("namespace")
            identifier = item.get("identifier")
            if isinstance(namespace, str) and isinstance(identifier, str):
                coord_input = f"{namespace}:{identifier}"

        if not coord_input or not coord_input.strip():
            results.append(
                {
                    "status": "error",
                    "input": item,
                    "detail": "Invalid coordinate. Provide a string or {namespace, identifier}.",
                }
            )
            continue

        normalized = normalise_coord(coord_input)
        if normalized.get("kind") == "web4":
            try:
                coordinate = int(normalized["bare"])
                factors = token_index.unique_prime_factors(coordinate)
            except (TypeError, ValueError):
                results.append(
                    {
                        "status": "error",
                        "input": item,
                        "detail": "Invalid Web4 coordinate.",
                    }
                )
                continue

            tokens = {prime: token_index.token_for_prime(prime) for prime in factors}
            entry_map = token_index.entries_for_primes(factors)
            knowledge = token_index.resolve_entries_for_primes(factors, cast(Any, store))
            payload_data = build_payload_for_text(
                "W4",
                json.dumps(
                    sanitize_coordinate_metadata({
                        "primes": factors,
                        "tokens": tokens,
                        "entries_by_prime": {str(prime): sorted(ids) for prime, ids in entry_map.items()},
                        "knowledge": knowledge,
                    }),
                    ensure_ascii=True,
                ),
            )
            results.append(
                {
                    "status": "success",
                    "input": item,
                    "result": resolve_response(
                        coord=f"W4-{coordinate}",
                        metadata={},
                        payload=payload_data,
                        refs={
                            "inputs": [],
                            "evidence": [],
                            "context": [],
                            "overlays": [],
                            "governance": [],
                            "walk_traces": [],
                            "web4": [{"coord": f"W4-{coordinate}", "type": "W4"}],
                        },
                        walk=None,
                        interpretation={"topics": [], "claims": [], "tags": []},
                        governance={"appraisal": {}},
                        meta={"namespace_used": None},
                    ),
                }
            )
            continue

        namespace = normalized.get("namespace")
        if namespace:
            try:
                namespace = resolve_ledger_scope_or_raise(
                    request,
                    payload_ledger_id=(str(payload_ledger_id).strip() if isinstance(payload_ledger_id, str) else None),
                    path_ledger_id=namespace,
                    hint="provide matching ledger_id/x-ledger-id for coordinate namespace",
                )
            except HTTPException as exc:
                results.append(
                    {
                        "status": "error",
                        "input": item,
                        "detail": exc.detail,
                    }
                )
                continue
        entry = None
        if not namespace:
            explicit_scope = None
            if isinstance(payload_ledger_id, str) or header_scope_present:
                try:
                    explicit_scope = resolve_ledger_scope_or_raise(
                        request,
                        payload_ledger_id=(str(payload_ledger_id).strip() if isinstance(payload_ledger_id, str) else None),
                        path_ledger_id=None,
                        hint="provide ledger_id/x-ledger-id or namespace-qualified coordinate",
                    )
                except HTTPException as exc:
                    results.append(
                        {
                            "status": "error",
                            "input": item,
                            "detail": exc.detail,
                        }
                    )
                    continue
            if explicit_scope:
                namespace = explicit_scope
            else:
                for candidate in namespace_candidates():
                    key = parse_key(f"{candidate}:{normalized['bare']}")
                    entry = store.read(key.as_path())
                    if entry is None:
                        continue
                    namespace = candidate
                    break
            if not namespace:
                results.append(
                    {
                        "status": "error",
                        "input": item,
                        "kind": normalized.get("kind"),
                        "canonical_coord": normalized["canonical"],
                        "namespace_used": None,
                        "error_code": "missing_namespace",
                        "hint": 'provide namespace like "<ns>:<coord>"',
                    }
                )
                continue

        if entry is None:
            key = parse_key(f"{namespace}:{normalized['bare']}")
            entry = store.read(key.as_path())

        if entry is None:
            results.append(
                {
                    "status": "error",
                    "input": item,
                    "detail": "Entry not found for coordinate",
                }
            )
            continue

        metadata = entry.state.metadata or {}
        content_text = metadata.get("content") or metadata.get("full_text")
        if isinstance(content_text, str) and content_text.strip():
            full_text = content_text.strip()
        else:
            fragments = list(_collect_text_fragments(metadata))
            full_text = " ".join(str(f) for f in fragments if f) or ""

        coord = f"{namespace}:{normalized['bare']}"
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

        entry_payload: dict[str, Any]
        parts_meta_raw = metadata.get("attachment_parts")
        parts_meta: list[Any] = parts_meta_raw if isinstance(parts_meta_raw, list) else []
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
                if namespace:
                    part_coord = f"{namespace}:{part_coord}"
                parts_payload.append(
                    {
                        "coord": part_coord,
                        "type": coord_type(part_coord),
                        "tokens_est": part.get("tokens_est") or 0,
                        "topics": part.get("topics") or [],
                        "tags": part.get("tags") or [],
                    }
                )
            entry_payload = build_payload_for_parts(parts_payload)
        else:
            entry_payload = build_payload_for_text(coord_type(coord), full_text)

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

        results.append(
            {
                "status": "success",
                "input": item,
                "result": resolve_response(
                    coord=coord,
                    metadata=metadata,
                    payload=entry_payload,
                    refs=refs,
                    walk=walk_payload,
                    interpretation=interpretation,
                    governance=governance,
                    meta={
                        "namespace_used": namespace,
                        "identifier": entry.key.identifier,
                        "created_at": entry.created_at.isoformat(),
                        "pinned": entry.pinned,
                        "feedback_rollup": _feedback_rollup_for_coord(
                            store=store,
                            coord=coord,
                            metadata=metadata,
                        ),
                    },
                ),
            }
        )

    return {"results": results}

# --- CRITICAL FIX: DO NOT IMPORT OTHER ROUTERS HERE ---
__all__ = [
    "debug_ledger_store",
    "get_ledger_store",
    "parse_key",
    "router",
    "search_router",
    "web4_router",
    "get_memory_ledger",
    "get_memory_substrate",
    "get_telemetry_store",
]
