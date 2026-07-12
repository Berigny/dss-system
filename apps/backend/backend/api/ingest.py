"""Create new body primes and populate S1/S2."""

from __future__ import annotations

import hashlib
import gc
import os
import json
import logging
import time
from datetime import datetime
from io import BytesIO
from html.parser import HTMLParser
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, Sequence, cast

from openai.types.chat import ChatCompletionMessageParam

from backend.api.agent_writes import (
    record_attachment_fast,
    record_attachment_finalize,
    record_full_payload_blob,
)
from backend.api.http import (
    get_ledger_store,
    get_memory_ledger,
    get_memory_substrate,
)
from backend.fieldx_kernel.kernel_origin_equations import (
    calculate_alpha_from_primes,
    calculate_persistence_cost,
)
from backend.fieldx_kernel.orchestrator import complete_chat
from backend.fieldx_kernel.temporal import get_entity_engine
from backend.services.authz import authorize_or_raise
from backend.services.context_scope import resolve_context_id_or_raise
from backend.services.ledger_scope import resolve_ledger_scope_or_raise
from backend.services.namespace_policy import resolve_write_namespace
from backend.services.provenance import build_write_provenance, normalize_subject_transition
from backend.services.ledger_service import LedgerService

logger = logging.getLogger(__name__)
from backend.metrics.benchmark_context import attach_request_benchmark_context
from backend.metrics.prod_benchmark_contract import SurfaceName
from backend.metrics.telemetry import RetrievalPath, TelemetryIds, TurnTelemetry
from backend.search.token_index import normalise_text


router = APIRouter(tags=["ingest"])

MAX_UPLOAD_BYTES = int(os.getenv("ATTACHMENT_MAX_BYTES", str(50 * 1024 * 1024)))


@router.get("/ingest/limits")
async def ingest_limits() -> dict[str, int]:
    return {"attachment_max_bytes": MAX_UPLOAD_BYTES}
SUMMARY_SNIPPET_CHARS = 10_000
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".log",
    ".yaml",
    ".yml",
    ".xml",
}
_ALPHA_VAL = calculate_alpha_from_primes()
_DEFAULT_COHERENCE = float(os.getenv("INGEST_DEFAULT_COHERENCE", "0.9999"))
_SUMMARY_COST_THRESHOLD = float(os.getenv("ATTACHMENT_SUMMARY_COST_THRESHOLD", "0.8"))
_ALWAYS_SUMMARIZE_ATTACHMENTS = os.getenv("ATTACHMENT_SUMMARY_ALWAYS", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_CHUNK_COST_HIGH = float(os.getenv("ATTACHMENT_CHUNK_COST_HIGH", "1.0"))
_CHUNK_BASE = int(os.getenv("ATTACHMENT_CHUNK_CHARS", "12000"))
_CHUNK_MIN = int(os.getenv("ATTACHMENT_CHUNK_MIN", "4000"))
_CHUNK_MAX = int(os.getenv("ATTACHMENT_CHUNK_MAX", "50000"))

_DEFAULT_SESSION_ID = "unknown"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(part.strip() for part in self._parts if part.strip())


def _normalize_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


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


def _estimate_persistence_cost(text_len: int, coherence: float) -> float:
    return calculate_persistence_cost(_ALPHA_VAL, coherence, text_len)


def _choose_chunk_chars(cost: float, text_len: int) -> int:
    base = _CHUNK_BASE
    if text_len >= 1_000_000:
        base = 50_000
    elif text_len >= 500_000:
        base = 30_000
    elif text_len >= 100_000:
        base = 16_000
    elif text_len >= 20_000:
        base = 8_000
    else:
        base = 4_000

    return max(_CHUNK_MIN, min(_CHUNK_MAX, base))


def _should_summarize(cost: float) -> bool:
    return cost <= _SUMMARY_COST_THRESHOLD


def _should_summarize_attachment(cost: float) -> bool:
    if _ALWAYS_SUMMARIZE_ATTACHMENTS:
        return True
    return _should_summarize(cost)


def _parse_summary_reply(reply: str) -> Dict[str, Any]:
    cleaned = reply.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        summary = str(parsed.get("summary") or parsed.get("text") or "").strip()
        topics = _normalize_list(parsed.get("topics"))
        salient_points = _normalize_list(parsed.get("salient_points"))
        return {"summary": summary, "topics": topics, "salient_points": salient_points}

    return {"summary": cleaned, "topics": [], "salient_points": []}


def _heuristic_summary(text: str, limit: int = 240) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    for marker in (".", "!", "?"):
        idx = cleaned.find(marker)
        if 0 < idx < limit:
            return cleaned[: idx + 1]
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit].rstrip()}..."


async def _summarize_text(raw_text: str) -> Dict[str, Any]:
    snippet = raw_text[:SUMMARY_SNIPPET_CHARS].strip()
    if not snippet:
        return {"summary": "", "topics": [], "salient_points": []}

    system_prompt = (
        "You are a summarization assistant. Provide a 3-4 sentence summary of the text. "
        "If helpful, include optional topics and salient_points lists. "
        "Respond ONLY with JSON: {\"summary\": str, \"topics\": [..], "
        "\"salient_points\": [..]}."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": snippet},
    ]

    try:
        chat_messages = cast(Sequence[ChatCompletionMessageParam], messages)
        reply, _, _, _, _ = await complete_chat(provider="openrouter", messages=chat_messages)
    except Exception:
        return {"summary": "", "topics": [], "salient_points": []}

    return _parse_summary_reply(reply)


def _build_enriched_metadata(
    metadata: Dict[str, Any] | None,
    summary_payload: Dict[str, Any],
    *,
    snippet_length: int,
) -> Dict[str, Any]:
    enriched = dict(metadata) if metadata else {}
    enriched.pop("raw_text", None)
    enriched.pop("full_text", None)

    summary = str(summary_payload.get("summary") or "").strip()
    if summary:
        enriched["summary"] = summary
    topics = _normalize_list(summary_payload.get("topics"))
    if topics:
        enriched["summary_topics"] = topics
    salient_points = _normalize_list(summary_payload.get("salient_points"))
    if salient_points:
        enriched["summary_salient_points"] = salient_points
    enriched["summary_source_chars"] = snippet_length
    return enriched


def _normalize_coordinate(entity: str, coordinate: str | None) -> str | None:
    if not coordinate:
        return None
    if ":" not in coordinate and entity:
        return f"{entity}:{coordinate}"
    return coordinate


def _mark_summary_pending(store, coordinate: str | None, pending: bool) -> None:
    if store is None:
        return
    if not coordinate:
        return
    entry = store.read(coordinate)
    if entry is None:
        return
    metadata = dict(entry.state.metadata or {})
    metadata["summary_pending"] = pending
    if pending:
        metadata.pop("summary_skipped", None)
    # Clear chain hashes so LedgerStoreV2 can recompute for this metadata update.
    metadata.pop("ledger_hash", None)
    metadata.pop("ledger_prev_hash", None)
    entry.state.metadata = metadata
    store.write(entry)


def _finalize_attachment_background(
    *,
    attachment_job: Dict[str, Any],
    ledger,
    store,
    summary_pending: bool,
) -> None:
    result = record_attachment_finalize(
        **attachment_job,
        ledger=ledger,
        store=store,
    )
    coordinate = result.get("coordinate")
    attachment_metadata = attachment_job.get("metadata") or {}
    attachment_block = attachment_metadata.get("attachment", {})
    sha256 = None
    if isinstance(attachment_block, dict):
        sha256 = attachment_block.get("sha256")
    if not sha256:
        sha256 = attachment_metadata.get("sha256")
    if summary_pending:
        _mark_summary_pending(store, coordinate, True)
    if store is not None and isinstance(sha256, str) and coordinate:
        store.set_attachment_coordinate(sha256, coordinate)


async def _summarize_attachment_background(
    *,
    coordinate: str | None,
    raw_text: str,
    store,
    snippet_length: int,
) -> None:
    summary_payload = await _summarize_text(raw_text)
    summary = str(summary_payload.get("summary") or "").strip()
    topics = _normalize_list(summary_payload.get("topics"))
    salient_points = _normalize_list(summary_payload.get("salient_points"))

    if store is None or not coordinate:
        return
    entry = store.read(coordinate)
    if entry is None:
        return
    metadata = dict(entry.state.metadata or {})
    if summary:
        metadata["summary"] = summary
    if topics:
        metadata["summary_topics"] = topics
    if salient_points:
        metadata["summary_salient_points"] = salient_points
    metadata["summary_source_chars"] = snippet_length
    metadata["summary_pending"] = False
    if not summary:
        metadata["summary_skipped"] = True
    # Clear chain hashes so LedgerStoreV2 can recompute for this metadata update.
    metadata.pop("ledger_hash", None)
    metadata.pop("ledger_prev_hash", None)
    entry.state.metadata = metadata
    store.write(entry)


def _extract_pdf(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""

    try:
        reader = PdfReader(BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()
    except Exception:
        return ""


def _extract_docx(raw: bytes) -> str:
    try:
        from docx import Document  # type: ignore[import-not-found]
    except Exception:
        return ""

    try:
        doc = Document(BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
    except Exception:
        return ""


def _extract_html(raw: bytes) -> str:
    try:
        parser = _HTMLTextExtractor()
        parser.feed(raw.decode("utf-8", errors="replace"))
        return parser.text()
    except Exception:
        return ""


def _count_ingest_words(text: str) -> int:
    return len([word for word in text.split() if word])


def _count_memory_tokens(text: str) -> int:
    return len(normalise_text(text))


def _emit_ingest_telemetry(
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
                session_id=(session_id or _DEFAULT_SESSION_ID),
                namespace=entity,
                entity=entity,
                turn_id=(turn_id or f"ingest-{int(time.time() * 1000)}"),
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
            mode="ingest",
            tenant_id=entity,
        )
        telemetry_store.write_event(telemetry)
    except Exception:
        logger = logging.getLogger(__name__)
        logger.warning("Failed to emit ingest telemetry", exc_info=True)


class IngestRequest(BaseModel):
    entity: str = Field(..., description="Entity identifier")
    context_id: str | None = Field(None, description="Optional context identifier")
    ledger_id: str | None = Field(
        None,
        description="Explicit ledger identifier for authorization/scoping.",
    )
    session_id: str | None = Field(None, description="Optional session identifier")
    turn_id: str | None = Field(None, description="Optional turn identifier")
    raw_text: str = Field(..., description="Raw text to ingest")
    kind: str = Field(..., description="Attachment kind or label")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    entity: str
    s1: Dict[str, Any]
    s2: Dict[str, Any]
    flow_diagnostics: Dict[str, Any]
    coordinate: str | None = None
    parent_coordinate: str | None = None
    full_payload_coordinate: str | None = None
    part_coordinates: list[str] = Field(default_factory=list)
    duplicate: bool | None = None
    summary_pending: bool = False
    ingest_diagnostics: Dict[str, Any] = Field(default_factory=dict)


def _resolve_explicit_ledger_id(request: Request, payload_ledger_id: str | None) -> str:
    return resolve_ledger_scope_or_raise(
        request,
        payload_ledger_id=payload_ledger_id,
        hint="provide ledger_id in payload/form or x-ledger-id header",
    )


def _extract_text(file: UploadFile, raw: bytes) -> str:
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "attachment").lower()

    if filename.endswith(".pdf") or content_type == "application/pdf":
        extracted = _extract_pdf(raw)
        if extracted:
            return extracted

    if filename.endswith(".docx") or content_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }:
        extracted = _extract_docx(raw)
        if extracted:
            return extracted

    if filename.endswith(".html") or filename.endswith(".htm") or content_type == "text/html":
        extracted = _extract_html(raw)
        if extracted:
            return extracted

    if content_type.startswith("text/") or content_type in {"application/json", "application/xml"}:
        return raw.decode("utf-8", errors="replace")

    if any(filename.endswith(ext) for ext in TEXT_EXTENSIONS):
        return raw.decode("utf-8", errors="replace")

    try:
        return raw.decode("utf-8")
    except Exception:
        return ""


def _normalize_coordinate_list(entity: str, coordinates: Sequence[Any] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in coordinates or []:
        value = _normalize_coordinate(entity, str(raw or "").strip())
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _attachment_coordinate_contract(
    entity: str,
    *,
    parent_coordinate: str | None,
    part_coordinates: Sequence[Any] | None = None,
) -> tuple[str | None, list[str]]:
    normalized_parent = _normalize_coordinate(entity, parent_coordinate)
    normalized_parts = _normalize_coordinate_list(entity, part_coordinates)
    if normalized_parent:
        return normalized_parent, normalized_parts
    if normalized_parts:
        return normalized_parts[0], normalized_parts
    raise HTTPException(status_code=500, detail="attachment_coordinate_unavailable")


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    payload: IngestRequest,
    request: Request,
    background_tasks: BackgroundTasks,
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
    coherence = _coerce_coherence(payload.metadata)
    if payload.entity:
        engine = get_entity_engine(payload.entity)
        engine.update_memory(coherence)
        temporal_state = getattr(engine, "temporal_state", 0)
        engine.temporal_state = engine.equation_2_temporalization(temporal_state)
        coherence = engine.calculate_memory_coherence()
    persistence_cost = _estimate_persistence_cost(len(payload.raw_text), coherence)
    summary_pending = bool(payload.raw_text.strip()) and _should_summarize_attachment(persistence_cost)
    summary_override = _heuristic_summary(payload.raw_text)
    summary_payload = {"summary": summary_override, "topics": [], "salient_points": []}
    enriched_metadata = _build_enriched_metadata(
        payload.metadata,
        summary_payload,
        snippet_length=min(len(payload.raw_text), SUMMARY_SNIPPET_CHARS),
    )
    try:
        enriched_metadata.update(
            normalize_subject_transition(
                request,
                metadata=enriched_metadata,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "subject_authority_transition_unverified", "reason": str(exc)},
        ) from exc
    enriched_metadata.update(
        build_write_provenance(
            request,
            ledger_id=write_namespace,
            metadata=enriched_metadata,
            session_id=payload.session_id,
            turn_id=payload.turn_id,
            provider_id=(
                payload.metadata.get("provider")
                if isinstance(payload.metadata.get("provider"), str)
                else None
            ),
            model_id=(
                payload.metadata.get("model_id")
                if isinstance(payload.metadata.get("model_id"), str)
                else (
                    payload.metadata.get("model")
                    if isinstance(payload.metadata.get("model"), str)
                    else None
                )
            ),
            context_id=context_id,
        )
    )
    if summary_pending:
        enriched_metadata["summary_pending"] = True
    else:
        enriched_metadata["summary_skipped"] = True
    chunk_chars = _choose_chunk_chars(persistence_cost, len(payload.raw_text))
    full_payload_coordinate: str | None = None
    blob_result = record_full_payload_blob(
        write_namespace,
        payload.raw_text,
        payload.kind,
        enriched_metadata,
        substrate,
        ledger,
        store,
    )
    if blob_result:
        full_payload_coordinate = blob_result["coordinate"]
        enriched_metadata["full_payload_coord"] = full_payload_coordinate
        enriched_metadata["blob_hash"] = blob_result["blob_hash"]
        enriched_metadata["full_payload"] = True

    result = record_attachment_fast(
        write_namespace,
        payload.raw_text,
        payload.kind,
        enriched_metadata,
        summary_override,
        chunk_chars,
        substrate,
        ledger,
        store,
    )
    finalize_result = record_attachment_finalize(
        **result["attachment_job"],
        ledger=ledger,
        store=store,
    )
    coordinate, part_coordinates = _attachment_coordinate_contract(
        write_namespace,
        parent_coordinate=finalize_result.get("coordinate") or result.get("coordinate"),
        part_coordinates=finalize_result.get("part_coordinates") or result.get("part_coordinates"),
    )
    if summary_pending:
        background_tasks.add_task(
            _summarize_attachment_background,
            coordinate=coordinate,
            raw_text=payload.raw_text,
            store=store,
            snippet_length=min(len(payload.raw_text), SUMMARY_SNIPPET_CHARS),
        )

    _emit_ingest_telemetry(
        request,
        entity=write_namespace,
        session_id=payload.session_id,
        turn_id=payload.turn_id,
        memory_cost=persistence_cost,
        memory_tokens=_count_memory_tokens(payload.raw_text),
        ingest_words=_count_ingest_words(payload.raw_text),
        latency_ms=(time.perf_counter() - start_time) * 1000.0,
    )

    return IngestResponse(
        entity=payload.entity,
        s1=result["s1"],
        s2=finalize_result.get("s2") or {},
        flow_diagnostics=finalize_result.get("flow_diagnostics") or result["flow_diagnostics"],
        coordinate=coordinate,
        parent_coordinate=coordinate,
        full_payload_coordinate=full_payload_coordinate,
        part_coordinates=part_coordinates,
        summary_pending=summary_pending,
        ingest_diagnostics={
            "text_length": len(payload.raw_text or ""),
            "chunk_chars": chunk_chars,
            "part_count": int(result.get("part_count") or 0),
            "summary_pending": summary_pending,
            "full_payload": bool(full_payload_coordinate),
            "kernel_projection_count": len(blob_result.get("projections") or []) if blob_result else 0,
        },
    )


@router.post("/ingest/stream")
async def ingest_stream(
    payload: IngestRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    substrate=Depends(get_memory_substrate),
    ledger=Depends(get_memory_ledger),
    store=Depends(get_ledger_store),
):
    ledger_scope = _resolve_explicit_ledger_id(request, payload.ledger_id)
    context_id = resolve_context_id_or_raise(
        request,
        payload_context_id=payload.context_id,
        require_for_write=True,
        hint="provide context_id in payload or x-context-id header",
    )
    write_namespace = resolve_write_namespace(ledger_id=ledger_scope, entity=payload.entity)
    authorize_or_raise(
        request,
        ledger_id=ledger_scope,
        action="ledger.write",
        explicit_context=True,
    )

    async def event_stream():
        start_time = time.perf_counter()
        if payload.raw_text:
            yield json.dumps({"type": "status", "message": "Extracting text..."}) + "\n"

        entity_engine: Any | None = None
        coherence = _coerce_coherence(payload.metadata)
        if payload.entity:
            entity_engine = get_entity_engine(payload.entity)
            entity_engine.update_memory(coherence)
            temporal_state = getattr(entity_engine, "temporal_state", 0)
            entity_engine.temporal_state = entity_engine.equation_2_temporalization(temporal_state)
            coherence = entity_engine.calculate_memory_coherence()
        persistence_cost = _estimate_persistence_cost(len(payload.raw_text), coherence)
        summary_pending = bool(payload.raw_text.strip()) and _should_summarize_attachment(persistence_cost)
        summary_override = _heuristic_summary(payload.raw_text)
        summary_payload = {"summary": summary_override, "topics": [], "salient_points": []}
        enriched_metadata = _build_enriched_metadata(
            payload.metadata,
            summary_payload,
            snippet_length=min(len(payload.raw_text), SUMMARY_SNIPPET_CHARS),
        )
        try:
            enriched_metadata.update(
                normalize_subject_transition(
                    request,
                    metadata=enriched_metadata,
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "subject_authority_transition_unverified", "reason": str(exc)},
            ) from exc
        enriched_metadata.update(
            build_write_provenance(
                request,
                ledger_id=write_namespace,
                metadata=enriched_metadata,
                session_id=payload.session_id,
                turn_id=payload.turn_id,
                provider_id=(
                    payload.metadata.get("provider")
                    if isinstance(payload.metadata.get("provider"), str)
                    else None
                ),
                model_id=(
                    payload.metadata.get("model_id")
                    if isinstance(payload.metadata.get("model_id"), str)
                    else (
                        payload.metadata.get("model")
                        if isinstance(payload.metadata.get("model"), str)
                        else None
                    )
                ),
                context_id=context_id,
            )
        )
        if summary_pending:
            enriched_metadata["summary_pending"] = True
            yield json.dumps({"type": "status", "message": "Scheduling summary..."}) + "\n"
        else:
            enriched_metadata["summary_skipped"] = True

        yield json.dumps({"type": "status", "message": "Indexing to Substrate..."}) + "\n"
        chunk_chars = _choose_chunk_chars(persistence_cost, len(payload.raw_text))
        yield json.dumps(
            {
                "type": "status",
                "message": f"Chunk size set to {chunk_chars} chars.",
            }
        ) + "\n"
        full_payload_coordinate: str | None = None
        blob_result = record_full_payload_blob(
            write_namespace,
            payload.raw_text,
            payload.kind,
            enriched_metadata,
            substrate,
            ledger,
            store,
        )
        if blob_result:
            full_payload_coordinate = blob_result["coordinate"]
            enriched_metadata["full_payload_coord"] = full_payload_coordinate
            enriched_metadata["blob_hash"] = blob_result["blob_hash"]
            enriched_metadata["full_payload"] = True
            yield json.dumps(
                {
                    "type": "status",
                    "message": f"Full payload blob stored at {full_payload_coordinate}.",
                }
            ) + "\n"

        result = record_attachment_fast(
            write_namespace,
            payload.raw_text,
            payload.kind,
            enriched_metadata,
            summary_override,
            chunk_chars,
            substrate,
            ledger,
            store,
        )
        if result.get("part_count"):
            yield json.dumps(
                {
                    "type": "status",
                    "message": f"Chunked into {result['part_count']} parts.",
                }
            ) + "\n"
        finalize_result = record_attachment_finalize(
            **result["attachment_job"],
            ledger=ledger,
            store=store,
        )
        coordinate, part_coordinates = _attachment_coordinate_contract(
            write_namespace,
            parent_coordinate=finalize_result.get("coordinate") or result.get("coordinate"),
            part_coordinates=finalize_result.get("part_coordinates") or result.get("part_coordinates"),
        )
        if summary_pending:
            background_tasks.add_task(
                _summarize_attachment_background,
                coordinate=coordinate,
                raw_text=payload.raw_text,
                store=store,
                snippet_length=min(len(payload.raw_text), SUMMARY_SNIPPET_CHARS),
            )

        _emit_ingest_telemetry(
            request,
            entity=write_namespace,
            session_id=payload.session_id,
            turn_id=payload.turn_id,
            memory_cost=persistence_cost,
            memory_tokens=_count_memory_tokens(payload.raw_text),
            ingest_words=_count_ingest_words(payload.raw_text),
            latency_ms=(time.perf_counter() - start_time) * 1000.0,
        )

        coordinate_value = coordinate
        yield json.dumps(
            {
                "type": "meta",
                "coordinate": coordinate_value,
                "parent_coordinate": coordinate_value,
                "full_payload_coordinate": full_payload_coordinate,
                "part_coordinates": part_coordinates,
                "summary": summary_override or "",
                "summary_pending": summary_pending,
                "entity": payload.entity,
                "s1": result["s1"],
                "s2": finalize_result.get("s2") or {},
                "flow_diagnostics": finalize_result.get("flow_diagnostics") or result["flow_diagnostics"],
                "ingest_diagnostics": {
                    "text_length": len(payload.raw_text or ""),
                    "chunk_chars": chunk_chars,
                    "part_count": int(result.get("part_count") or 0),
                    "summary_pending": summary_pending,
                    "full_payload": bool(full_payload_coordinate),
                    "kernel_projection_count": len(blob_result.get("projections") or []) if blob_result else 0,
                },
            }
        ) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        background=background_tasks,
    )


@router.post("/ingest/stream-file")
async def ingest_stream_file(
    request: Request,
    background_tasks: BackgroundTasks,
    entity: str = Form(..., description="Entity identifier"),
    context_id: str | None = Form(None, description="Optional context identifier"),
    ledger_id: str | None = Form(None, description="Explicit ledger identifier"),
    kind: str = Form("attachment", description="Attachment kind or label"),
    metadata: str | None = Form(None, description="Optional JSON metadata"),
    session_id: str | None = Form(None, description="Optional session identifier"),
    turn_id: str | None = Form(None, description="Optional turn identifier"),
    file: UploadFile = File(...),
    substrate=Depends(get_memory_substrate),
    ledger=Depends(get_memory_ledger),
    store=Depends(get_ledger_store),
):
    ledger_scope = _resolve_explicit_ledger_id(request, ledger_id)
    resolved_context_id = resolve_context_id_or_raise(
        request,
        payload_context_id=context_id,
        require_for_write=True,
        hint="provide context_id in form or x-context-id header",
    )
    write_namespace = resolve_write_namespace(ledger_id=ledger_scope, entity=normalized_entity)
    authorize_or_raise(
        request,
        ledger_id=ledger_scope,
        action="ledger.write",
        explicit_context=True,
    )

    async def event_stream():
        start_time = time.perf_counter()
        raw: bytes = b""
        stage = "init"
        try:
            if entity and ":" not in entity and not entity.startswith("chat-"):
                normalized_entity = f"chat-{entity}"
            else:
                normalized_entity = entity
            stage = "read_file"
            yield json.dumps({"type": "status", "message": "Reading file..."}) + "\n"
            raw = await file.read()
            if not raw:
                yield json.dumps({"type": "error", "detail": "Empty file upload"}) + "\n"
                return
            if len(raw) > MAX_UPLOAD_BYTES:
                max_mb = MAX_UPLOAD_BYTES / (1024 * 1024)
                yield json.dumps(
                    {
                        "type": "error",
                        "detail": f"File exceeds {max_mb:.0f}MB limit",
                    }
                ) + "\n"
                return
            sha256 = hashlib.sha256(raw).hexdigest()
            existing = store.get_attachment_coordinate(sha256)
            if existing:
                yield json.dumps(
                    {
                        "type": "meta",
                        "coordinate": existing,
                        "parent_coordinate": existing,
                        "part_coordinates": [],
                        "duplicate": True,
                        "entity": normalized_entity,
                    }
                ) + "\n"
                return

            stage = "extract_text"
            yield json.dumps({"type": "status", "message": "Extracting text..."}) + "\n"
            raw_size = len(raw)
            extracted_text = _extract_text(file, raw)
            try:
                await file.close()
            except Exception:
                pass
            del raw
            gc.collect()
            filename = file.filename or "attachment"
            if not extracted_text:
                extracted_text = f"[Attachment: {filename}]"

            try:
                user_metadata = json.loads(metadata) if metadata else {}
                if not isinstance(user_metadata, dict):
                    user_metadata = {}
            except json.JSONDecodeError:
                user_metadata = {}

            attachment_meta = {
                "attachment": {
                    "filename": filename,
                    "content_type": file.content_type or "application/octet-stream",
                    "size_bytes": raw_size,
                    "sha256": sha256,
                    "extracted": bool(extracted_text),
                    "text_length": len(extracted_text),
                },
                "source": "upload",
            }
            attachment_meta.update(user_metadata)
            try:
                attachment_meta.update(
                    normalize_subject_transition(
                        request,
                        metadata=attachment_meta,
                    )
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "subject_authority_transition_unverified", "reason": str(exc)},
                ) from exc
            attachment_meta.update(
                build_write_provenance(
                    request,
                    ledger_id=write_namespace,
                    metadata=attachment_meta,
                    session_id=session_id,
                    turn_id=turn_id,
                    provider_id=(
                        attachment_meta.get("provider")
                        if isinstance(attachment_meta.get("provider"), str)
                        else None
                    ),
                    model_id=(
                        attachment_meta.get("model_id")
                        if isinstance(attachment_meta.get("model_id"), str)
                        else (
                            attachment_meta.get("model")
                            if isinstance(attachment_meta.get("model"), str)
                            else None
                        )
                    ),
                    context_id=resolved_context_id,
                )
            )

            coherence = _coerce_coherence(attachment_meta)
            if normalized_entity:
                engine = get_entity_engine(normalized_entity)
                engine.update_memory(coherence)
                temporal_state = getattr(engine, "temporal_state", 0)
                engine.temporal_state = engine.equation_2_temporalization(temporal_state)
                coherence = engine.calculate_memory_coherence()
            persistence_cost = _estimate_persistence_cost(len(extracted_text), coherence)
            summary_pending = bool(extracted_text.strip()) and _should_summarize_attachment(persistence_cost)
            summary_override = _heuristic_summary(extracted_text)
            summary_payload = {"summary": summary_override, "topics": [], "salient_points": []}
            enriched_metadata = _build_enriched_metadata(
                attachment_meta,
                summary_payload,
                snippet_length=min(len(extracted_text), SUMMARY_SNIPPET_CHARS),
            )
            if summary_pending:
                enriched_metadata["summary_pending"] = True
                yield json.dumps({"type": "status", "message": "Scheduling summary..."}) + "\n"
            else:
                enriched_metadata["summary_skipped"] = True

            stage = "index_attachment"
            yield json.dumps({"type": "status", "message": "Indexing attachment..."}) + "\n"
            chunk_chars = _choose_chunk_chars(persistence_cost, len(extracted_text))
            yield json.dumps(
                {
                    "type": "status",
                    "message": f"Chunk size set to {chunk_chars} chars.",
                }
            ) + "\n"
            result = record_attachment_fast(
                write_namespace,
                extracted_text,
                kind,
                enriched_metadata,
                summary_override,
                chunk_chars,
                substrate,
                ledger,
                store,
            )
            if result.get("part_count"):
                yield json.dumps(
                    {
                        "type": "status",
                        "message": f"Chunked into {result['part_count']} parts.",
                    }
                ) + "\n"
            stage = "finalize_attachment"
            finalize_result = record_attachment_finalize(
                **result["attachment_job"],
                ledger=ledger,
                store=store,
            )
            coordinate, part_coordinates = _attachment_coordinate_contract(
                write_namespace,
                parent_coordinate=finalize_result.get("coordinate") or result.get("coordinate"),
                part_coordinates=finalize_result.get("part_coordinates") or result.get("part_coordinates"),
            )
            if summary_pending:
                background_tasks.add_task(
                    _summarize_attachment_background,
                    coordinate=coordinate,
                    raw_text=extracted_text,
                    store=store,
                    snippet_length=min(len(extracted_text), SUMMARY_SNIPPET_CHARS),
                )

            if request is not None:
                _emit_ingest_telemetry(
                    request,
                    entity=write_namespace,
                    session_id=session_id,
                    turn_id=turn_id,
                    memory_cost=persistence_cost,
                    memory_tokens=_count_memory_tokens(extracted_text),
                    ingest_words=_count_ingest_words(extracted_text),
                    latency_ms=(time.perf_counter() - start_time) * 1000.0,
                )

            coordinate_value = coordinate
            yield json.dumps(
                {
                    "type": "meta",
                    "coordinate": coordinate_value,
                    "parent_coordinate": coordinate_value,
                    "part_coordinates": part_coordinates,
                    "summary": summary_override or "",
                    "summary_pending": summary_pending,
                    "entity": normalized_entity,
                    "s1": result["s1"],
                    "s2": finalize_result.get("s2") or {},
                    "flow_diagnostics": finalize_result.get("flow_diagnostics") or result["flow_diagnostics"],
                    "ingest_diagnostics": {
                        "raw_size_bytes": raw_size,
                        "text_length": len(extracted_text or ""),
                        "chunk_chars": chunk_chars,
                        "part_count": int(result.get("part_count") or 0),
                        "summary_pending": summary_pending,
                    },
                }
            ) + "\n"
        except Exception as exc:
            logger.exception("Streamed ingest failed")
            yield json.dumps(
                {
                    "type": "error",
                    "status_code": 500,
                    "detail": f"{type(exc).__name__} during {stage}: {exc}",
                }
            ) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        background=background_tasks,
    )


@router.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(
    request: Request,
    background_tasks: BackgroundTasks,
    entity: str = Form(..., description="Entity identifier"),
    context_id: str | None = Form(None, description="Optional context identifier"),
    ledger_id: str | None = Form(None, description="Explicit ledger identifier"),
    kind: str = Form("attachment", description="Attachment kind or label"),
    metadata: str | None = Form(None, description="Optional JSON metadata"),
    session_id: str | None = Form(None, description="Optional session identifier"),
    turn_id: str | None = Form(None, description="Optional turn identifier"),
    file: UploadFile = File(...),
    substrate=Depends(get_memory_substrate),
    ledger=Depends(get_memory_ledger),
    store=Depends(get_ledger_store),
):
    ledger_scope = _resolve_explicit_ledger_id(request, ledger_id)
    resolved_context_id = resolve_context_id_or_raise(
        request,
        payload_context_id=context_id,
        require_for_write=True,
        hint="provide context_id in form or x-context-id header",
    )
    write_namespace = resolve_write_namespace(ledger_id=ledger_scope, entity=entity)
    authorize_or_raise(
        request,
        ledger_id=ledger_scope,
        action="ledger.write",
        explicit_context=True,
    )
    start_time = time.perf_counter()
    if entity and ":" not in entity and not entity.startswith("chat-"):
        entity = f"chat-{entity}"
    raw: bytes = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Empty file upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        max_mb = MAX_UPLOAD_BYTES / (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File exceeds {max_mb:.0f}MB limit")
    sha256 = hashlib.sha256(raw).hexdigest()
    existing = store.get_attachment_coordinate(sha256)
    if existing:
        return IngestResponse(
            entity=entity,
            s1={},
            s2={},
            flow_diagnostics={"duplicate": True, "sha256": sha256},
            coordinate=existing,
            parent_coordinate=existing,
            part_coordinates=[],
            duplicate=True,
            ingest_diagnostics={"duplicate": True, "sha256": sha256, "raw_size_bytes": len(raw)},
        )

    raw_size = len(raw)
    extracted_text = _extract_text(file, raw)
    try:
        await file.close()
    except Exception:
        pass
    del raw
    gc.collect()
    filename = file.filename or "attachment"
    if not extracted_text:
        extracted_text = f"[Attachment: {filename}]"

    try:
        user_metadata = json.loads(metadata) if metadata else {}
        if not isinstance(user_metadata, dict):
            user_metadata = {}
    except json.JSONDecodeError:
        user_metadata = {}

    attachment_meta = {
        "attachment": {
            "filename": filename,
            "content_type": file.content_type or "application/octet-stream",
            "size_bytes": raw_size,
            "sha256": sha256,
            "extracted": bool(extracted_text),
            "text_length": len(extracted_text),
        },
        "source": "upload",
    }
    attachment_meta.update(user_metadata)
    try:
        attachment_meta.update(
            normalize_subject_transition(
                request,
                metadata=attachment_meta,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "subject_authority_transition_unverified", "reason": str(exc)},
        ) from exc
    attachment_meta.update(
        build_write_provenance(
            request,
            ledger_id=write_namespace,
            metadata=attachment_meta,
            session_id=session_id,
            turn_id=turn_id,
            provider_id=(
                attachment_meta.get("provider")
                if isinstance(attachment_meta.get("provider"), str)
                else None
            ),
            model_id=(
                attachment_meta.get("model_id")
                if isinstance(attachment_meta.get("model_id"), str)
                else (
                    attachment_meta.get("model")
                    if isinstance(attachment_meta.get("model"), str)
                    else None
                )
            ),
            context_id=resolved_context_id,
        )
    )

    coherence = _coerce_coherence(attachment_meta)
    if entity:
        engine = get_entity_engine(entity)
        engine.update_memory(coherence)
        temporal_state = getattr(engine, "temporal_state", 0)
        engine.temporal_state = engine.equation_2_temporalization(temporal_state)
        coherence = engine.calculate_memory_coherence()
    persistence_cost = _estimate_persistence_cost(len(extracted_text), coherence)
    summary_pending = bool(extracted_text.strip()) and _should_summarize_attachment(persistence_cost)
    summary_override = _heuristic_summary(extracted_text)
    summary_payload = {"summary": summary_override, "topics": [], "salient_points": []}
    enriched_metadata = _build_enriched_metadata(
        attachment_meta,
        summary_payload,
        snippet_length=min(len(extracted_text), SUMMARY_SNIPPET_CHARS),
    )
    if summary_pending:
        enriched_metadata["summary_pending"] = True
    else:
        enriched_metadata["summary_skipped"] = True

    chunk_chars = _choose_chunk_chars(persistence_cost, len(extracted_text))
    result = record_attachment_fast(
        write_namespace,
        extracted_text,
        kind,
        enriched_metadata,
        summary_override,
        chunk_chars,
        substrate,
        ledger,
        store,
    )
    finalize_result = record_attachment_finalize(
        **result["attachment_job"],
        ledger=ledger,
        store=store,
    )
    coordinate, part_coordinates = _attachment_coordinate_contract(
        write_namespace,
        parent_coordinate=finalize_result.get("coordinate") or result.get("coordinate"),
        part_coordinates=finalize_result.get("part_coordinates") or result.get("part_coordinates"),
    )
    if summary_pending:
        background_tasks.add_task(
            _summarize_attachment_background,
            coordinate=coordinate,
            raw_text=extracted_text,
            store=store,
            snippet_length=min(len(extracted_text), SUMMARY_SNIPPET_CHARS),
        )

    if request is not None:
        _emit_ingest_telemetry(
            request,
            entity=write_namespace,
            session_id=session_id,
            turn_id=turn_id,
            memory_cost=persistence_cost,
            memory_tokens=_count_memory_tokens(extracted_text),
            ingest_words=_count_ingest_words(extracted_text),
            latency_ms=(time.perf_counter() - start_time) * 1000.0,
        )

    return IngestResponse(
        entity=entity,
        s1=result["s1"],
        s2=finalize_result.get("s2") or {},
        flow_diagnostics=finalize_result.get("flow_diagnostics") or result["flow_diagnostics"],
        coordinate=coordinate,
        parent_coordinate=coordinate,
        part_coordinates=part_coordinates,
        duplicate=False,
        summary_pending=summary_pending,
        ingest_diagnostics={
            "raw_size_bytes": raw_size,
            "text_length": len(extracted_text or ""),
            "chunk_chars": chunk_chars,
            "part_count": int(result.get("part_count") or 0),
            "summary_pending": summary_pending,
        },
    )


__all__ = ["router"]
