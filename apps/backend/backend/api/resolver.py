"""
Deterministic Resolver Endpoint (The Discrete Lane).
Strictly implements the 'Valuation/Check' logic from the Patent (Fig 3).
"""

import logging
import json
import os
import time
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.api.schemas import LedgerKeySchema, LedgerEntrySchema
from backend.fieldx_kernel.p_adic import PAdicInteger
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.fieldx_kernel.substrate.padic_ledger_store import PAdicLedgerStore
from backend.api.http import get_ledger_store
from backend.metrics.benchmark_context import attach_request_benchmark_context
from backend.metrics.prod_benchmark_contract import SurfaceName
from backend.metrics.telemetry import (
    RetrievalPath,
    TelemetryIds,
    TelemetryReferences,
    TurnTelemetry,
)
from backend.services.authz import authz_diagnostics_from_request, authorize_or_raise
from backend.services.ledger_service import LedgerService
from backend.services.ledger_scope import resolve_ledger_scope_or_raise
from backend.utils.resolve_format import build_payload_for_blob, build_payload_for_projections

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/resolve", tags=["discrete-lane"])
RESOLVER_AUDIT_V1_KEY = b"__resolver_read_audit_v1__"
READ_TIERS = {"public_skim", "verifier_full", "operator_full", "internal_diagnostic", "blob_full", "kernel_projections"}


class TieredResolveRequest(BaseModel):
    namespace: str = Field(..., min_length=1)
    identifier: str = Field(..., min_length=1)
    read_tier: str = Field("public_skim")
    precision: Optional[int] = Field(default=None, ge=1)


def _redaction_marker(reason: str) -> dict[str, str]:
    return {"state": "withheld", "reason": reason}


def _load_resolver_audit(db: Any) -> list[dict[str, Any]]:
    raw = db.get(RESOLVER_AUDIT_V1_KEY)
    if raw is None:
        return []
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        payload = json.loads(decoded)
    except Exception:
        return []
    rows = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [dict(item) for item in rows if isinstance(item, dict)]


def _persist_resolver_audit(db: Any, rows: list[dict[str, Any]]) -> None:
    db[RESOLVER_AUDIT_V1_KEY] = json.dumps(
        {"version": 1, "records": rows[-500:]},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _record_resolver_audit(
    *,
    request: Request,
    namespace: str,
    identifier: str,
    read_tier: str,
) -> dict[str, Any]:
    db = getattr(getattr(request, "app", None), "state", None)
    backing = getattr(db, "db", None)
    if backing is None:
        return {"recorded": False}
    audit_id = f"resolver-audit:{uuid4()}"
    record = {
        "audit_id": audit_id,
        "namespace": namespace,
        "identifier": identifier,
        "read_tier": read_tier,
        "recorded_at": datetime.utcnow().isoformat() + "Z",
        "principal_id": request.headers.get("x-principal-id") or "anonymous",
        "ledger_id": request.headers.get("x-ledger-id") or namespace,
        "authz": authz_diagnostics_from_request(request),
    }
    rows = _load_resolver_audit(backing)
    rows.append(record)
    _persist_resolver_audit(backing, rows)
    return {"recorded": True, "audit_id": audit_id}


def _content_preview(metadata: dict[str, Any]) -> str | None:
    text = str(metadata.get("content") or metadata.get("text") or "").strip()
    if not text:
        return None
    compact = " ".join(text.split())
    return compact[:160]


# --- p-adic graded nearest-state fallback (DSS-175) -------------------------

_PADIC_RESOLVER_PRIME = int(os.getenv("PADIC_RESOLVER_PRIME", "5"))
_PADIC_RESOLVER_PRECISION = int(os.getenv("PADIC_RESOLVER_PRECISION", "4"))


def _padic_store_for_request(request: Request) -> PAdicLedgerStore:
    """Return a ``PAdicLedgerStore`` backed by the request's RocksDB instance."""
    app_state = getattr(getattr(request, "app", None), "state", None)
    backing = getattr(app_state, "db", None)
    if backing is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return PAdicLedgerStore(backing, _PADIC_RESOLVER_PRIME, _PADIC_RESOLVER_PRECISION)


def _try_padic_nearest(
    request: Request,
    store: LedgerStoreV2,
    namespace: str,
    identifier: str,
    precision: int,
) -> Any | None:
    """
    Try graded p-adic nearest-state retrieval.

    Non-integer identifiers cannot be interpreted as p-adic residues and are
    ignored.  When a nearest ball is found, the payload is treated as the exact
    storage path of the candidate entry, which is then read from the canonical
    ledger store.  This keeps entry encoding/decoding in one place.

    The returned entry has p-adic resolution metadata attached so callers can
    see the ball precision and confidence.
    """
    try:
        n = int(identifier)
    except ValueError:
        # CLAIM(definite): p-adic fallback applies only to integer identifiers.
        # EVIDENCE: DSS-175
        return None

    padic_store = _padic_store_for_request(request)
    query = PAdicInteger.from_int(_PADIC_RESOLVER_PRIME, n, _PADIC_RESOLVER_PRECISION)
    payload, k_found, distance = padic_store.nearest_with_distance(
        namespace, query, min_k=precision
    )
    if payload is None:
        return None

    nearest_path = payload.decode() if isinstance(payload, bytes) else str(payload)
    entry = store.read(nearest_path)
    if entry is None:
        return None

    metadata = dict(entry.state.metadata or {})
    metadata["p_adic_resolution"] = {
        "mode": "nearest_ball",
        "precision_requested": precision,
        "precision_found": k_found,
        "distance": distance,
        "confidence": 1.0 / (1.0 + distance) if distance is not None else None,
    }
    entry.state.metadata = metadata
    return entry


def _project_entry(entry: LedgerEntrySchema, *, read_tier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = dict(entry.state.metadata or {})
    withheld_fields: list[str] = []
    absent_fields: list[str] = []
    content_preview = _content_preview(metadata)
    base_entry: dict[str, Any] = {
        "key": {"namespace": entry.key.namespace, "identifier": entry.key.identifier},
        "phase": entry.state.phase,
        "created_at": entry.created_at.isoformat().replace("+00:00", "Z") if entry.created_at is not None else None,
        "pinned": bool(entry.pinned),
        "summary": content_preview,
    }
    visible_metadata: dict[str, Any] = {}
    canonical_subject = metadata.get("canonical_subject")
    if canonical_subject is not None:
        visible_metadata["canonical_subject"] = canonical_subject
    else:
        absent_fields.append("metadata.canonical_subject")

    if read_tier == "public_skim":
        visible_metadata["content"] = _redaction_marker("tier_restricted")
        base_entry["coordinates"] = _redaction_marker("native_coord_hidden")
        withheld_fields.extend(["metadata.content", "state.coordinates"])
    elif read_tier == "verifier_full":
        visible_metadata["content"] = _redaction_marker("operator_only_content")
        visible_metadata["trust_refs"] = {
            "credential_ref": metadata.get("credential_ref"),
            "evidence_manifest_ref": metadata.get("evidence_manifest_ref"),
            "standing_envelope_ref": metadata.get("standing_envelope_ref"),
        }
        base_entry["coordinates"] = _redaction_marker("native_coord_hidden")
        withheld_fields.extend(["metadata.content", "state.coordinates"])
    elif read_tier == "operator_full":
        visible_metadata = dict(metadata)
        visible_metadata["resolved_coords"] = _redaction_marker("internal_diagnostic_only")
        base_entry["coordinates"] = _redaction_marker("native_coord_hidden")
        withheld_fields.extend(["metadata.resolved_coords", "state.coordinates"])
    else:
        visible_metadata = dict(metadata)
        base_entry["coordinates"] = dict(entry.state.coordinates or {})

    if "content" not in visible_metadata and read_tier == "internal_diagnostic":
        absent_fields.append("metadata.content")
    base_entry["metadata"] = visible_metadata
    return base_entry, {
        "withheld_fields": withheld_fields,
        "absent_fields": absent_fields,
        "native_coord_policy": "allowed" if read_tier == "internal_diagnostic" else "internal_only",
    }


def _cache_contract(read_tier: str) -> dict[str, Any]:
    if read_tier == "public_skim":
        return {"visibility": "public", "max_age_seconds": 300}
    if read_tier == "internal_diagnostic":
        return {"visibility": "private", "max_age_seconds": 0}
    return {"visibility": "private", "max_age_seconds": 60}

@router.post(
    "",
    response_model=LedgerEntrySchema,
    summary="Deterministic State Retrieval",
    description="Atomic read of the Discrete Prime Ledger. No inference. No hallucination."
)
async def resolve_state(
    key: LedgerKeySchema,
    request: Request,
    session_id: Optional[str] = None, # Passed via dependency or header in real impl
    turn_id: Optional[str] = None,
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    """
    Executes the 'Read' operation of the Discrete Lane.
    1. Validates Identity (Key Integrity)
    2. Validates Access (Namespace Gating)
    3. Returns Exact State or Null (Valuation Check)
    """
    
    # --- 1. Access Control (The Gatekeeper) ---
    # Enforce that the requester can only read from their own timeline or shared reality.
    # This prevents 'state leakage' between distinct user sessions.
    
    # Coordinate namespace is an explicit ledger scope; enforce consistency with
    # any provided payload/header ledger context instead of session-derived gates.
    resolve_ledger_scope_or_raise(
        request,
        path_ledger_id=key.namespace,
        hint="provide matching ledger_id/x-ledger-id for coordinate namespace",
    )
    authorize_or_raise(
        request,
        ledger_id=key.namespace,
        action="ledger.read",
        explicit_context=True,
    )

    # --- 2. Deterministic Lookup (The Valuation) ---
    # This maps to the p-adic valuation check in the patent[cite: 23].
    # We do not search. We do not guess. We measure.
    
    def _emit(resolve_success: bool) -> None:
        if request is None:
            return
        try:
            telemetry_store = LedgerService.from_request(request).telemetry_store()
            ids = TelemetryIds(
                session_id=(session_id or "unknown"),
                namespace=key.namespace,
                entity=key.namespace,
                turn_id=(turn_id or f"resolve-{int(time.time() * 1000)}"),
                timestamp=datetime.utcnow(),
            )
            telemetry = TurnTelemetry(
                ids=ids,
                retrieval_path=RetrievalPath.MEMORY,
                references=TelemetryReferences(
                    resolve_attempts=1,
                    resolve_successes=1 if resolve_success else 0,
                ),
            )
            telemetry = attach_request_benchmark_context(
                telemetry,
                request,
                surface=SurfaceName.BACKEND,
                mode="resolve",
                tenant_id=key.namespace,
            )
            telemetry_store.write_event(telemetry)
        except Exception:
            logger.warning("Failed to emit resolve telemetry", exc_info=True)

    try:
        # Convert schema to internal path format (e.g., "namespace:identifier")
        storage_path = key.to_model().as_path()
        
        # Atomic Read from RocksDB (The Prime Ledger)
        entry = store.read(storage_path)

        # CLAIM(definite): Optional p-adic ball fallback is attempted only when
        # the caller supplies a precision parameter and the identifier is an
        # integer that can be interpreted as a p-adic residue.
        # EVIDENCE: claim-register.yaml epic-22-claim-007, DSS-175
        if entry is None and key.precision is not None:
            entry = _try_padic_nearest(
                request, store, key.namespace, key.identifier, key.precision
            )
        
        if not entry:
            # CLAIM(definite): Missing coordinate => no representation in ledger.
            # In p-adic terms v_p(0) = infinity (norm 0), or exponent < tau.
            # EVIDENCE: claim-register.yaml epic-22-claim-007, DSS-175
            logger.info("resolve_state_miss", extra={"coordinate": storage_path, "session": session_id})
            _emit(resolve_success=False)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"State undefined at coordinate: {storage_path}"
            )
            
        # --- 3. Return Immutable State ---
        # If the entry references a full-payload blob, attach the intact text
        # to the returned metadata so the coordinate resolves to the original.
        if entry is not None:
            metadata = dict(entry.state.metadata or {})
            blob_coord = str(metadata.get("full_payload_coord") or "").strip()
            if blob_coord:
                blob_text = store.read_blob_text(blob_coord)
                if blob_text is not None:
                    metadata["full_payload"] = blob_text
                    entry.state.metadata = metadata
            elif metadata.get("full_payload") and metadata.get("blob_hash"):
                # Legacy/fallback: the coordinate itself is a blob entry.
                blob_text = store.read_blob_text(storage_path)
                if blob_text is not None:
                    metadata["full_payload"] = blob_text
                    entry.state.metadata = metadata

        logger.info("resolve_state_hit", extra={"coordinate": storage_path, "session": session_id})
        _emit(resolve_success=True)
        return LedgerEntrySchema.from_model(entry)

    except HTTPException:
        raise
    except Exception as e:
        # Catch low-level RocksDB or encoding errors
        _emit(resolve_success=False)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Ledger Valuation Error: {str(e)}"
        )


@router.post(
    "/tiered",
    summary="Tiered deterministic state retrieval",
    description="Deterministic resolver read with explicit read tiers, redaction, cache hints, and privileged-read audit.",
)
async def resolve_state_tiered(
    payload: TieredResolveRequest,
    request: Request,
    store: LedgerStoreV2 = Depends(get_ledger_store),
):
    read_tier = str(payload.read_tier or "").strip().lower() or "public_skim"
    if read_tier not in READ_TIERS:
        raise HTTPException(status_code=422, detail={"error": "unsupported_read_tier", "read_tier": read_tier})

    key = LedgerKeySchema(namespace=payload.namespace, identifier=payload.identifier)
    resolve_ledger_scope_or_raise(
        request,
        path_ledger_id=key.namespace,
        hint="provide matching ledger_id/x-ledger-id for coordinate namespace",
    )
    authorize_or_raise(
        request,
        ledger_id=key.namespace,
        action="ledger.read",
        explicit_context=True,
    )

    storage_path = key.to_model().as_path()
    entry = store.read(storage_path)
    if entry is None and payload.precision is not None:
        entry = _try_padic_nearest(
            request, store, payload.namespace, payload.identifier, payload.precision
        )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "outcome": "not_found",
                "read_tier": read_tier,
                "namespace": key.namespace,
                "identifier": key.identifier,
            },
        )

    metadata = dict(entry.state.metadata or {})

    if read_tier == "blob_full":
        blob_coord = str(metadata.get("full_payload_coord") or "").strip() or storage_path
        blob_text = store.read_blob_text(blob_coord)
        if blob_text is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "outcome": "blob_not_found",
                    "read_tier": read_tier,
                    "namespace": key.namespace,
                    "identifier": key.identifier,
                    "blob_coord": blob_coord,
                },
            )
        audit = _record_resolver_audit(
            request=request,
            namespace=key.namespace,
            identifier=key.identifier,
            read_tier=read_tier,
        )
        return {
            "status": "ok",
            "read_tier": read_tier,
            "resolution": {
                "outcome": "resolved",
                "namespace": key.namespace,
                "identifier": key.identifier,
            },
            "cache": _cache_contract(read_tier),
            "payload": build_payload_for_blob(blob_text, coordinate=blob_coord),
            "audit": audit,
        }

    if read_tier == "kernel_projections":
        from backend.kernel.rocksdb_layer_store import RocksDBLayerStore

        projection_coords = metadata.get("kernel_projections") or []
        layer_store = RocksDBLayerStore(store._db, provision_id="default")
        projections: list[dict[str, Any]] = []
        for coord in projection_coords:
            if not isinstance(coord, str):
                continue
            matches = layer_store.retrieve_by_coord(coord)
            if not matches:
                continue
            layer, block_height, data = matches[-1]
            projections.append(
                {
                    "coord": coord,
                    "layer": layer,
                    "block_height": block_height,
                    "v_awareness": data.get("v_awareness"),
                    "v_unity": data.get("v_unity"),
                    "v_ethics": data.get("v_ethics"),
                    "merkle_path": data.get("merkle_path"),
                }
            )
        parent_summary = {
            "quaternary_layer": metadata.get("quaternary_layer"),
            "checksum_336_satisfied": metadata.get("checksum_336_satisfied"),
            "composite_coord": metadata.get("composite_coord"),
        }
        audit = _record_resolver_audit(
            request=request,
            namespace=key.namespace,
            identifier=key.identifier,
            read_tier=read_tier,
        )
        return {
            "status": "ok",
            "read_tier": read_tier,
            "resolution": {
                "outcome": "resolved",
                "namespace": key.namespace,
                "identifier": key.identifier,
            },
            "cache": _cache_contract(read_tier),
            "parent": parent_summary,
            "payload": build_payload_for_projections(projections),
            "audit": audit,
        }

    schema = LedgerEntrySchema.from_model(entry)
    projected_entry, redaction = _project_entry(schema, read_tier=read_tier)
    audit = {"recorded": False}
    if read_tier in {"verifier_full", "operator_full", "internal_diagnostic"}:
        audit = _record_resolver_audit(
            request=request,
            namespace=key.namespace,
            identifier=key.identifier,
            read_tier=read_tier,
        )
    return {
        "status": "ok",
        "read_tier": read_tier,
        "resolution": {
            "outcome": "resolved",
            "namespace": key.namespace,
            "identifier": key.identifier,
        },
        "cache": _cache_contract(read_tier),
        "entry": projected_entry,
        "redaction": redaction,
        "audit": audit,
    }
