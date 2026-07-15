"""
API endpoints for raw ledger interaction and history retrieval.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request, Query
from starlette.responses import JSONResponse

from backend.api.history_utils import history_sort_key
from backend.api.history_utils import _parse_timestamp as parse_timestamp
from backend.services.authz import authorize_or_raise
from backend.services.ledger_service import LedgerService

router = APIRouter(tags=["ledger"])
LOGGER = logging.getLogger(__name__)
_ENTITY_HEX_PATTERN = re.compile(r"^[0-9a-f]{8}:[0-9a-f]{8}$", re.IGNORECASE)


def _request_ledger_id(request: Request) -> str:
    """Resolve ledger context from explicit headers/query, fallback to default."""
    for header in ("x-ledger-id", "x-ledger"):
        value = request.headers.get(header)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for param in ("ledger_id", "ledger"):
        value = request.query_params.get(param)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "default"


def _history_namespace_candidates(
    request: Request,
    entity: str,
    service: LedgerService | None = None,
) -> list[str]:
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

    clean_entity = (entity or "").strip()
    if clean_entity:
        if ":" in clean_entity:
            _append(clean_entity)
        else:
            namespaced = clean_entity if clean_entity.startswith("chat-") else f"chat-{clean_entity}"
            _append(namespaced)
            if namespaced.endswith("-session"):
                _append(namespaced[: -len("-session")])

    ledger_scope = _request_ledger_id(request)
    if ledger_scope and ledger_scope != "default":
        if service is not None:
            ledger_scope = service.resolve_canonical_ledger_id(ledger_scope) or ledger_scope
        _append(ledger_scope)

    demo_default = os.getenv("DEMO_GOD_DEFAULT_LEDGER", "").strip()
    if demo_default:
        _append(demo_default)

    return candidates or ["chat-default"]


def _load_history_window(
    *,
    store: Any,
    namespace_candidates: list[str],
    limit: int,
) -> tuple[str, list[Any], int]:
    fetch_limit = max(limit * 200, limit)
    if fetch_limit > 5000:
        fetch_limit = 5000

    selected = namespace_candidates[0]
    for namespace in namespace_candidates:
        selected = namespace
        try:
            sliced_entries = store.list_by_namespace(namespace, limit=fetch_limit, reverse=True)
        except Exception:
            continue
        try:
            summary = store.summarize(namespace=namespace)
            total_count = int(summary.get("total_entries", 0) or 0)
        except Exception:
            total_count = len(sliced_entries)
        if total_count > 0 or sliced_entries:
            return namespace, sliced_entries, total_count

    return selected, [], 0


def _iter_db_keys(service: LedgerService):
    db = service.db
    try:
        with db.iter() as iterator:  # type: ignore[attr-defined]
            for raw_key, _ in iterator:
                yield raw_key
        return
    except Exception:
        pass
    try:
        for raw_key in db.keys():  # type: ignore[attr-defined]
            yield raw_key
    except Exception:
        return


def _decode_db_key(raw_key: object) -> str:
    if isinstance(raw_key, (bytes, bytearray)):
        try:
            return raw_key.decode()
        except Exception:
            return ""
    return str(raw_key)


def _compact_entity_name(namespace: str) -> str:
    if namespace.startswith("chat-"):
        return namespace[len("chat-") :]
    return namespace


def _is_chat_history_entity(entity: str) -> bool:
    text = (entity or "").strip()
    if not text:
        return False
    if _ENTITY_HEX_PATTERN.match(text):
        return True
    if text.startswith("chat-"):
        return True
    return False


def _entry_coord_meta(entry: Any) -> dict[str, Any] | None:
    metadata = entry.state.metadata if isinstance(getattr(entry.state, "metadata", None), dict) else {}
    coord_meta = metadata.get("coord_meta") if isinstance(metadata.get("coord_meta"), dict) else None
    if isinstance(coord_meta, dict) and coord_meta:
        return dict(coord_meta)
    runtime_identity = metadata.get("runtime_identity") if isinstance(metadata.get("runtime_identity"), dict) else None
    if not isinstance(runtime_identity, dict):
        return None
    coordinate = entry.key.as_path()
    return {
        "coord": coordinate,
        "coord_type": coordinate.rsplit(":", 1)[-1].split("-", 1)[0] if ":" in coordinate else None,
        "identifier": entry.key.identifier,
        "runtime_namespace": runtime_identity.get("runtime_namespace") or entry.key.namespace,
        "canonical_subject": runtime_identity.get("ledger_canonical_subject"),
        "canonical_subject_source": "did:web:ledger",
    }


def _history_entry_content(entry: Any, metadata: dict[str, Any]) -> str:
    for candidate in (
        metadata.get("content"),
        metadata.get("assistant_reply"),
        metadata.get("user_message"),
        metadata.get("prompt"),
        metadata.get("query"),
        metadata.get("text"),
        metadata.get("raw"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text

    skim = metadata.get("skim") if isinstance(metadata.get("skim"), dict) else {}
    skim_text = str(skim.get("one_line") or "").strip()
    if skim_text:
        return skim_text

    payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
    blobs = payload.get("blobs") if isinstance(payload.get("blobs"), dict) else {}
    for value in blobs.values():
        text = str(value or "").strip()
        if text:
            return text

    return ""


def _history_entry_role_and_kind(entry: Any, metadata: dict[str, Any], allowed_roles: set[str]) -> tuple[str | None, str]:
    kind = str(metadata.get("kind") or "").strip().lower()
    raw_role = str(metadata.get("role") or "").strip().lower()
    identifier = str(getattr(entry.key, "identifier", "") or "").strip()
    coord_meta = metadata.get("coord_meta") if isinstance(metadata.get("coord_meta"), dict) else {}
    coord_type = str(coord_meta.get("coord_type") or "").strip().lower()

    if kind == "coord_walk":
        return None, kind
    if kind == "chat":
        return (raw_role if raw_role in allowed_roles else "system"), kind
    if raw_role in allowed_roles:
        return raw_role, kind
    if identifier.startswith("WX-") or coord_type == "wx":
        return "assistant", "wx"
    return None, kind


def _load_registered_ledgers(service: LedgerService) -> set[str]:
    out: set[str] = set()
    db = service.db
    for reg_key in (b"__ledgers__", b"__ledgers_v1__"):
        raw = db.get(reg_key)
        if raw is None:
            continue
        try:
            decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            payload = json.loads(decoded)
        except Exception:
            continue
        if isinstance(payload, list):
            for item in payload:
                value = str(item).strip()
                if value:
                    out.add(value)
            continue
        if isinstance(payload, dict):
            ledgers = payload.get("ledgers")
            if isinstance(ledgers, dict):
                for ledger_id in ledgers.keys():
                    value = str(ledger_id).strip()
                    if value:
                        out.add(value)
    return out


@router.get("/chat_history")
async def get_chat_history(
    request: Request,
    entity: str,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Retrieve chat history for a specific entity (session) using an
    optimized prefix-scan.
    """
    service = LedgerService.from_request(request)

    requested_entity = entity
    ledger_id = _request_ledger_id(request)
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.read",
        explicit_context=False,
    )

    store = service.store

    try:
        entity_candidates = _history_namespace_candidates(request, requested_entity, service=service)
        entity, sliced_entries, total_count = _load_history_window(
            store=store,
            namespace_candidates=entity_candidates,
            limit=limit,
        )
    except Exception as e:
        LOGGER.error(f"Ledger history retrieval failed: {e}", exc_info=e)
        raise HTTPException(status_code=500, detail=f"Could not load ledger history: {e}")

    # Format specifically for the Frontend 'history_item' component
    history_payload = []
    filtered_entries: list[tuple[Any, str | None, str | None]] = []
    for entry in sliced_entries:
        meta = entry.state.metadata or {}
        if meta.get("attachment") or meta.get("attachment_part") or meta.get("attachment_summary"):
            continue
        if meta.get("role") == "attachment":
            continue
        # Determine Role & Content
        role = meta.get("role", "assistant")
        content = meta.get("content") or meta.get("assistant_reply") or meta.get("text")

        # If content is missing, do not fall back to operator notes or sync metadata.

        # If the entry captured the originating user prompt, include it explicitly
        user_prompt = meta.get("user_message") or meta.get("prompt") or meta.get("query")
        if user_prompt is None:
            nested_meta = meta.get("metadata")
            if isinstance(nested_meta, dict):
                user_prompt = (
                    nested_meta.get("user_message")
                    or nested_meta.get("prompt")
                    or nested_meta.get("query")
                )

        if not content and not user_prompt:
            continue

        filtered_entries.append((entry, content, user_prompt))

    filtered_entries.sort(
        key=lambda item: item[0].created_at.timestamp() if item[0].created_at else 0.0
    )
    if len(filtered_entries) > limit:
        filtered_entries = filtered_entries[-limit:]

    for entry, content, user_prompt in filtered_entries:
        meta = entry.state.metadata or {}
        role = meta.get("role", "assistant")

        if role == "user":
            if content:
                history_payload.append({
                    "role": "user",
                    "content": content,
                    "timestamp": entry.created_at.isoformat(),
                    "entry_id": entry.key.identifier,
                    "coordinate": entry.key.as_path(),
                    "coord_meta": _entry_coord_meta(entry),
                    "metadata": meta,
                })
            continue

        if user_prompt:
            history_payload.append({
                "role": "user",
                "content": user_prompt,
                "timestamp": entry.created_at.isoformat(),
                "entry_id": f"{entry.key.identifier}-user",
                "coordinate": None,
                "metadata": meta,
            })

        if content:
            history_payload.append({
                "role": "assistant",
                "content": content,
                "timestamp": entry.created_at.isoformat(),
                "entry_id": entry.key.identifier,
                "coordinate": entry.key.as_path(),
                "metadata": meta,
            })

    return {
        "history": history_payload,
        "count": total_count,
        "entity": entity,
        "requested_entity": requested_entity,
    }


@router.get("/history/{entity}")
async def get_ordered_history(
    request: Request,
    entity: str,
    limit: int = 50,
) -> JSONResponse:
    service = LedgerService.from_request(request)

    requested_entity = entity
    ledger_id = _request_ledger_id(request)
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.read",
        explicit_context=False,
    )

    store = service.store

    try:
        entity_candidates = _history_namespace_candidates(request, requested_entity, service=service)
        entity, sliced_entries, total_count = _load_history_window(
            store=store,
            namespace_candidates=entity_candidates,
            limit=limit,
        )
    except Exception as e:
        LOGGER.error(f"Ledger ordered history retrieval failed: {e}", exc_info=e)
        raise HTTPException(status_code=500, detail=f"Could not load ordered ledger history: {e}")

    allowed_roles = {"user", "assistant", "system"}
    history_payload = []
    seen_keys: set[tuple[datetime, str, str]] = set()
    user_signatures: set[tuple[datetime, str]] = set()

    for entry in sliced_entries:
        meta = entry.state.metadata or {}
        role, _kind = _history_entry_role_and_kind(entry, meta, allowed_roles)
        if role is None:
            continue
        if role != "user":
            continue
        if meta.get("attachment") or meta.get("attachment_part") or meta.get("attachment_summary"):
            continue
        entry_id = entry.key.identifier
        raw_role = str(meta.get("role") or "").strip().lower()
        if isinstance(entry_id, str) and entry_id.endswith("-user") and raw_role == "assistant":
            continue
        content = _history_entry_content(entry, meta)
        if not content:
            continue
        timestamp_value = entry.created_at or meta.get("timestamp") or meta.get("ts") or meta.get("time")
        ts = parse_timestamp(timestamp_value)
        if ts is None:
            ts = datetime.min.replace(tzinfo=timezone.utc)
        user_signatures.add((ts, str(content)))

    for entry in sliced_entries:
        meta = entry.state.metadata or {}
        role, _kind = _history_entry_role_and_kind(entry, meta, allowed_roles)
        if role is None:
            continue
        if meta.get("attachment") or meta.get("attachment_part") or meta.get("attachment_summary"):
            continue
        entry_id = entry.key.identifier
        raw_role = str(meta.get("role") or "").strip().lower()
        if isinstance(entry_id, str) and entry_id.endswith("-user") and raw_role == "assistant":
            continue

        content = _history_entry_content(entry, meta)
        if not content:
            continue

        user_prompt = meta.get("user_message") or meta.get("prompt") or meta.get("query")
        if user_prompt is None:
            nested_meta = meta.get("metadata")
            if isinstance(nested_meta, dict):
                user_prompt = (
                    nested_meta.get("user_message")
                    or nested_meta.get("prompt")
                    or nested_meta.get("query")
                )

        timestamp_value = entry.created_at or meta.get("timestamp") or meta.get("ts") or meta.get("time")
        ts = parse_timestamp(timestamp_value)
        if ts is None:
            ts = datetime.min.replace(tzinfo=timezone.utc)

        entry_id = entry.key.identifier
        if role == "assistant" and user_prompt:
            user_key = (ts, "user", str(user_prompt))
            if (ts, str(user_prompt)) not in user_signatures and user_key not in seen_keys:
                seen_keys.add(user_key)
                history_payload.append(
                    {
                        "role": "user",
                        "content": user_prompt,
                        "timestamp": ts.isoformat(),
                        "entry_id": f"{entry_id}-user",
                        "coordinate": None,
                        "metadata": {"kind": "chat"},
                    }
                )

        dedupe_key = (ts, role, str(content))
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        history_payload.append(
            {
                "role": role,
                "content": content,
                "timestamp": ts.isoformat(),
                "entry_id": entry_id,
                "coordinate": entry.key.as_path(),
                "metadata": meta,
            }
        )

    history_payload.sort(key=history_sort_key, reverse=True)
    if len(history_payload) > limit:
        history_payload = history_payload[:limit]

    return JSONResponse(
        {
            "history": history_payload,
            "count": total_count,
            "entity": entity,
            "requested_entity": requested_entity,
        }
    )


@router.get("/all")
async def get_all_ledger_entries(
    request: Request,
    namespace: str | None = None,
    limit: int = 100,
):
    """
    Retrieve ledger entries, optionally filtered by namespace.
    Provides a broad view of the ledger for debugging and exploration.
    If no namespace is provided, this performs a reverse scan and is not
    intended for exhaustive data dumps, but for inspecting recent activity.
    """
    service = LedgerService.from_request(request)
    if namespace:
        authorize_or_raise(
            request,
            ledger_id=namespace,
            action="ledger.read",
            explicit_context=True,
        )
    store = service.store
    
    entries = []
    try:
        if namespace:
            # If a namespace is given, use the efficient prefix scan
            entries = store.list_by_namespace(namespace, limit=limit, reverse=True)
        else:
            # If no namespace, use the efficient reverse scan over the whole DB
            entries = store.list_all_entries(limit=limit)

    except Exception as e:
        LOGGER.error(f"Failed to retrieve ledger entries: {e}", exc_info=e)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve ledger entries: {e}")

    # For this endpoint, we will just return the raw entry objects.
    # The Pydantic models in LedgerEntry will be serialized by FastAPI.
    return {"entries": entries, "count": len(entries)}


@router.get("/history_entities")
async def get_history_entities(
    request: Request,
    limit: int = Query(200, ge=1, le=5000),
    include_counts: bool = Query(True, description="Include approximate per-entity counts."),
) -> JSONResponse:
    """
    List known chat/history entities across the local append-only store.

    This endpoint is designed for UI 'ALL' selectors and inventory diagnostics.
    """
    service = LedgerService.from_request(request)
    ledger_id = _request_ledger_id(request)
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.read",
        explicit_context=False,
    )

    internal_prefixes = (
        "bucket:",
        "tp:",
        "ix:",
        "entity:",
        "sync:v0:",
        "attachment:hash:",
        "chain:last:",
        "__",
    )

    entity_counts: dict[str, int] = {}
    scanned = 0
    truncated = False

    for raw_key in _iter_db_keys(service):
        scanned += 1
        decoded = _decode_db_key(raw_key)
        if not decoded or decoded.startswith(internal_prefixes):
            continue
        if ":" not in decoded:
            continue
        namespace = decoded.rsplit(":", 1)[0]
        if not namespace:
            continue
        if not _is_chat_history_entity(namespace):
            continue
        entity = _compact_entity_name(namespace)
        if not entity:
            continue
        entity_counts[entity] = entity_counts.get(entity, 0) + 1
        if len(entity_counts) >= limit:
            truncated = True

    sorted_entities = sorted(
        entity_counts.keys(),
        key=lambda name: entity_counts.get(name, 0),
        reverse=True,
    )
    if len(sorted_entities) > limit:
        sorted_entities = sorted_entities[:limit]
        truncated = True

    body: dict[str, Any] = {
        "current_entity": sorted_entities[0] if sorted_entities else None,
        "entities": sorted_entities,
        "count": len(sorted_entities),
        "scanned_keys": scanned,
        "truncated": truncated,
    }
    if include_counts:
        body["entity_counts"] = {name: entity_counts.get(name, 0) for name in sorted_entities}
    return JSONResponse(body)


@router.get("/ledgers/inventory")
async def get_ledger_inventory(
    request: Request,
    limit: int = Query(500, ge=1, le=5000),
) -> JSONResponse:
    """
    Return a retrievable inventory of ledger IDs and discovered namespaces.
    """
    service = LedgerService.from_request(request)
    ledger_id = _request_ledger_id(request)
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action="ledger.read",
        explicit_context=False,
    )

    discovered: set[str] = set()
    for raw_key in _iter_db_keys(service):
        decoded = _decode_db_key(raw_key)
        if not decoded or ":" not in decoded:
            continue
        if decoded.startswith(("bucket:", "tp:", "ix:", "entity:", "sync:v0:", "attachment:hash:", "chain:last:")):
            continue
        namespace = decoded.rsplit(":", 1)[0].strip()
        if namespace:
            discovered.add(namespace)
            if len(discovered) >= limit:
                break

    registered = _load_registered_ledgers(service)
    ledgers = sorted((registered | {"default"}))[:limit]
    namespaces = sorted(discovered)[:limit]

    return JSONResponse(
        {
            "ledgers": ledgers,
            "registered_ledger_count": len(registered),
            "namespaces": namespaces,
            "namespace_count": len(namespaces),
        }
    )
