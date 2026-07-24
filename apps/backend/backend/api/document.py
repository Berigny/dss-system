"""Document surface v0.1 for LOAM.

Append-only, event-sourced document composer:
  - Chunk content coords are immutable (DOC-<doc>-C<n>-T<nnn>).
  - Document state (position, active version, selection span, visibility) lives in
    MD-DocState- meta events.
  - Current document is a folded projection of the latest meta events.

Routes:
  POST   /v1/documents
  POST   /v1/documents/{doc_id}/chunks
  POST   /v1/documents/chunks/{chunk_coord}/reprompt
  PATCH  /v1/documents/chunks/{chunk_coord}
  GET    /v1/documents/{doc_id}
  GET    /v1/documents/chunks/{chunk_coord}/versions
  GET    /v1/documents/{doc_id}/export
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from backend.services.session_tokens import (
    apply_auth_claim_overrides,
    mint_surface_session_bundle,
)
from backend.services.surface_scope import assert_surface_ledger_access
from backend.utils.coord import normalise_coord

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/documents", tags=["documents"])

DOCUMENT_SURFACE_ID = os.getenv("DOCUMENT_SURFACE_ID", "surface:document:primary")
DOCUMENT_LEDGER_ID = os.getenv("DOCUMENT_LEDGER_ID", "pilot")
DOCUMENT_CHAT_MODEL = os.getenv("DOCUMENT_CHAT_MODEL", "gpt-4o-mini")

# RocksDB keys.
_DOCUMENTS_STATE_KEY = b"__documents_state_v1__"
_DOCUMENT_COORD_COUNTER_KEY = b"__document_coord_counter_v1__"


class CreateDocumentRequest(BaseModel):
    title: str = Field(default="Untitled")


class CreateChunkRequest(BaseModel):
    prompt: str = Field(..., min_length=1)


class RepromptChunkRequest(BaseModel):
    prompt: str = Field(..., min_length=1)


class PatchChunkRequest(BaseModel):
    position: int | None = None
    active_version: str | None = None
    sel_start: int | None = None
    sel_end: int | None = None
    visible: bool | None = None

    @field_validator("active_version")
    @classmethod
    def validate_version_coord(cls, v: str | None) -> str | None:
        if v is None:
            return None
        parsed = normalise_coord(v)
        if parsed.get("kind") != "document":
            raise ValueError("active_version must be a DOC- coord")
        return v


def _db(request: Request) -> Any:
    return getattr(getattr(request, "app", None), "state", None).db


def _load_state(db: Any) -> dict[str, Any]:
    raw = db.get(_DOCUMENTS_STATE_KEY)
    if raw is None:
        return {}
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return {}


def _save_state(db: Any, state: dict[str, Any]) -> None:
    db[_DOCUMENTS_STATE_KEY] = json.dumps(state, separators=(",", ":"), sort_keys=True).encode()


def _next_chunk_number(state: dict[str, Any], doc_id: str) -> int:
    doc = state.get(doc_id, {})
    chunks = doc.get("chunks", {})
    if not chunks:
        return 1
    numbers = []
    for coord in chunks:
        match = re.search(r"-C(\d+)$", coord)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _next_version_number(chunk: dict[str, Any]) -> int:
    versions = chunk.get("versions", [])
    if not versions:
        return 1
    numbers = []
    for v in versions:
        coord = v.get("coord", "")
        match = re.search(r"-T(\d+)$", coord)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _fold_chunk_state(events: list[dict[str, Any]], chunk_coord: str) -> dict[str, Any]:
    """Return the latest meta state for a single chunk."""
    state: dict[str, Any] = {
        "position": 0,
        "active_version": None,
        "sel_start": 0,
        "sel_end": 0,
        "visible": True,
    }
    for event in events:
        if event.get("chunk_coord") != chunk_coord:
            continue
        if event.get("type") != "state":
            continue
        for key in ("position", "active_version", "sel_start", "sel_end", "visible"):
            if key in event:
                state[key] = event[key]
    return state


def _fold_document_projection(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ordered visible chunks with selected text slices."""
    chunks = doc.get("chunks", {})
    events = doc.get("events", [])
    projected: list[dict[str, Any]] = []

    for chunk_coord, chunk in chunks.items():
        state = _fold_chunk_state(events, chunk_coord)
        if not state.get("visible", True):
            continue
        active_version = state.get("active_version")
        if not active_version:
            # Default to latest version if none selected.
            versions = chunk.get("versions", [])
            active_version = versions[-1]["coord"] if versions else None
        version_text = ""
        for v in chunk.get("versions", []):
            if v.get("coord") == active_version:
                version_text = v.get("text", "")
                break

        sel_start = max(0, min(state.get("sel_start", 0), len(version_text)))
        sel_end = max(sel_start, min(state.get("sel_end", len(version_text)), len(version_text)))
        visible_text = version_text[sel_start:sel_end]

        projected.append({
            "chunk_coord": chunk_coord,
            "position": state.get("position", 0),
            "active_version": active_version,
            "sel_start": sel_start,
            "sel_end": sel_end,
            "visible": True,
            "text": visible_text,
            "full_text": version_text,
        })

    projected.sort(key=lambda c: c["position"])
    return projected


def _set_principal_on_request(request: Request, principal_did: str) -> None:
    request.state.auth_claim_principal_did = principal_did
    request.state.auth_claim_principal_key_id = None
    request.state.auth_claim_session_jti = None
    apply_auth_claim_overrides(
        request,
        principal_did=principal_did,
        principal_key_id=None,
        session_jti=None,
    )


async def _generate_text(request: Request, principal_did: str, prompt: str) -> str:
    """Call the internal /chat endpoint and return the generated text."""
    bundle = mint_surface_session_bundle(
        principal_did=principal_did,
        ledger_ids=[DOCUMENT_LEDGER_ID],
        access_ttl_seconds=300,
    )
    token = bundle["session"]["token"]

    chat_payload = {
        "session_id": f"document-{principal_did}",
        "message": prompt,
        "principal_did": principal_did,
        "ledger_id": DOCUMENT_LEDGER_ID,
        "entity": f"document-{principal_did}",
        "provider": DOCUMENT_CHAT_MODEL,
        "enable_ledger": True,
        "persist_conversation": True,
    }

    try:
        transport = httpx.ASGITransport(app=request.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/chat",
                headers={"Authorization": f"Bearer {token}"},
                json=chat_payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        LOGGER.exception("Internal chat call failed for document generation")
        raise HTTPException(status_code=502, detail=f"Chat generation failed: {exc}") from exc

    return str(data.get("text") or "")


@router.post("")
async def create_document(request: Request, req: CreateDocumentRequest) -> dict[str, Any]:
    principal = _principal_from_request(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    _set_principal_on_request(request, principal)
    assert_surface_ledger_access(request, DOCUMENT_SURFACE_ID, DOCUMENT_LEDGER_ID)

    doc_id = f"doc-{int(time.time() * 1000)}"
    db = _db(request)
    state = _load_state(db)
    state[doc_id] = {
        "title": req.title,
        "principal_did": principal,
        "created_at": time.time(),
        "chunks": {},
        "events": [],
    }
    _save_state(db, state)
    return {"doc_id": doc_id, "title": req.title}


@router.get("")
async def list_documents(request: Request) -> dict[str, Any]:
    principal = _principal_from_request(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = _db(request)
    state = _load_state(db)
    docs = [
        {"doc_id": doc_id, "title": doc.get("title", ""), "created_at": doc.get("created_at")}
        for doc_id, doc in state.items()
        if isinstance(doc, dict) and doc.get("principal_did") == principal
    ]
    return {"documents": docs}


@router.post("/{doc_id}/chunks")
async def create_chunk(request: Request, doc_id: str, req: CreateChunkRequest) -> dict[str, Any]:
    principal = _principal_from_request(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = _db(request)
    state = _load_state(db)
    doc = state.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("principal_did") != principal:
        raise HTTPException(status_code=403, detail="Not document owner")

    _set_principal_on_request(request, principal)
    assert_surface_ledger_access(request, DOCUMENT_SURFACE_ID, DOCUMENT_LEDGER_ID)

    generated = await _generate_text(request, principal, req.prompt)
    chunk_num = _next_chunk_number(state, doc_id)
    chunk_coord = f"DOC-{doc_id}-C{chunk_num}"
    version_coord = f"{chunk_coord}-T001"

    doc["chunks"][chunk_coord] = {
        "versions": [
            {
                "coord": version_coord,
                "text": generated,
                "prompt": req.prompt,
                "created_at": time.time(),
            }
        ]
    }

    position = len([e for e in doc.get("events", []) if e.get("type") == "chunk_added"])
    doc["events"].append({
        "type": "chunk_added",
        "chunk_coord": chunk_coord,
        "version_coord": version_coord,
        "created_at": time.time(),
    })
    doc["events"].append({
        "type": "state",
        "chunk_coord": chunk_coord,
        "position": position,
        "active_version": version_coord,
        "sel_start": 0,
        "sel_end": len(generated),
        "visible": True,
        "created_at": time.time(),
    })

    _save_state(db, state)
    return {"chunk_coord": chunk_coord, "version_coord": version_coord, "text": generated}


@router.post("/chunks/{chunk_coord}/reprompt")
async def reprompt_chunk(request: Request, chunk_coord: str, req: RepromptChunkRequest) -> dict[str, Any]:
    principal = _principal_from_request(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    parsed = normalise_coord(chunk_coord)
    if parsed.get("kind") != "document":
        raise HTTPException(status_code=400, detail="Invalid chunk coord")

    doc_id_match = re.match(r"^DOC-(doc-\d+)-C\d+$", chunk_coord)
    if not doc_id_match:
        raise HTTPException(status_code=400, detail="Invalid chunk coord format")
    doc_id = doc_id_match.group(1)

    db = _db(request)
    state = _load_state(db)
    doc = state.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("principal_did") != principal:
        raise HTTPException(status_code=403, detail="Not document owner")

    chunk = doc.get("chunks", {}).get(chunk_coord)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    _set_principal_on_request(request, principal)
    assert_surface_ledger_access(request, DOCUMENT_SURFACE_ID, DOCUMENT_LEDGER_ID)

    generated = await _generate_text(request, principal, req.prompt)
    version_num = _next_version_number(chunk)
    version_coord = f"{chunk_coord}-T{version_num:03d}"

    chunk["versions"].append({
        "coord": version_coord,
        "text": generated,
        "prompt": req.prompt,
        "created_at": time.time(),
    })

    doc["events"].append({
        "type": "state",
        "chunk_coord": chunk_coord,
        "active_version": version_coord,
        "sel_start": 0,
        "sel_end": len(generated),
        "visible": True,
        "created_at": time.time(),
    })

    _save_state(db, state)
    return {"chunk_coord": chunk_coord, "version_coord": version_coord, "text": generated}


@router.patch("/chunks/{chunk_coord}")
async def patch_chunk(request: Request, chunk_coord: str, req: PatchChunkRequest) -> dict[str, Any]:
    principal = _principal_from_request(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    parsed = normalise_coord(chunk_coord)
    if parsed.get("kind") != "document":
        raise HTTPException(status_code=400, detail="Invalid chunk coord")

    doc_id_match = re.match(r"^DOC-(doc-\d+)-C\d+$", chunk_coord)
    if not doc_id_match:
        raise HTTPException(status_code=400, detail="Invalid chunk coord format")
    doc_id = doc_id_match.group(1)

    db = _db(request)
    state = _load_state(db)
    doc = state.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("principal_did") != principal:
        raise HTTPException(status_code=403, detail="Not document owner")

    chunk = doc.get("chunks", {}).get(chunk_coord)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    _set_principal_on_request(request, principal)
    assert_surface_ledger_access(request, DOCUMENT_SURFACE_ID, DOCUMENT_LEDGER_ID)

    # Validate active_version and selection bounds before emitting event.
    active_version = req.active_version
    version_text = ""
    if active_version is not None:
        found = False
        for v in chunk.get("versions", []):
            if v.get("coord") == active_version:
                version_text = v.get("text", "")
                found = True
                break
        if not found:
            raise HTTPException(status_code=400, detail="active_version not found in chunk")

    if req.sel_start is not None or req.sel_end is not None:
        if not version_text:
            # Use currently active version text for bounds check.
            current_state = _fold_chunk_state(doc.get("events", []), chunk_coord)
            active = current_state.get("active_version")
            for v in chunk.get("versions", []):
                if v.get("coord") == active:
                    version_text = v.get("text", "")
                    break
        sel_start = req.sel_start if req.sel_start is not None else 0
        sel_end = req.sel_end if req.sel_end is not None else len(version_text)
        if sel_start < 0 or sel_end < 0 or sel_start > len(version_text) or sel_end > len(version_text) or sel_start > sel_end:
            raise HTTPException(status_code=400, detail="Invalid selection span")

    event: dict[str, Any] = {
        "type": "state",
        "chunk_coord": chunk_coord,
        "created_at": time.time(),
    }
    if req.position is not None:
        event["position"] = req.position
    if active_version is not None:
        event["active_version"] = active_version
    if req.sel_start is not None:
        event["sel_start"] = req.sel_start
    if req.sel_end is not None:
        event["sel_end"] = req.sel_end
    if req.visible is not None:
        event["visible"] = req.visible

    if len(event) > 3:  # type + chunk_coord + created_at
        doc["events"].append(event)
        _save_state(db, state)

    return {"chunk_coord": chunk_coord, "updated": True}


@router.get("/{doc_id}")
async def get_document(request: Request, doc_id: str) -> dict[str, Any]:
    principal = _principal_from_request(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = _db(request)
    state = _load_state(db)
    doc = state.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("principal_did") != principal:
        raise HTTPException(status_code=403, detail="Not document owner")

    projection = _fold_document_projection(doc)
    return {
        "doc_id": doc_id,
        "title": doc.get("title", ""),
        "created_at": doc.get("created_at"),
        "chunks": projection,
    }


@router.get("/chunks/{chunk_coord}/versions")
async def list_chunk_versions(request: Request, chunk_coord: str) -> dict[str, Any]:
    principal = _principal_from_request(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    parsed = normalise_coord(chunk_coord)
    if parsed.get("kind") != "document":
        raise HTTPException(status_code=400, detail="Invalid chunk coord")

    doc_id_match = re.match(r"^DOC-(doc-\d+)-C\d+$", chunk_coord)
    if not doc_id_match:
        raise HTTPException(status_code=400, detail="Invalid chunk coord format")
    doc_id = doc_id_match.group(1)

    db = _db(request)
    state = _load_state(db)
    doc = state.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("principal_did") != principal:
        raise HTTPException(status_code=403, detail="Not document owner")

    chunk = doc.get("chunks", {}).get(chunk_coord)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    return {
        "chunk_coord": chunk_coord,
        "versions": [v.get("coord") for v in chunk.get("versions", [])],
    }


@router.get("/{doc_id}/export")
async def export_document(request: Request, doc_id: str) -> dict[str, Any]:
    principal = _principal_from_request(request)
    if not principal:
        raise HTTPException(status_code=401, detail="Authentication required")

    db = _db(request)
    state = _load_state(db)
    doc = state.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("principal_did") != principal:
        raise HTTPException(status_code=403, detail="Not document owner")

    projection = _fold_document_projection(doc)
    parts = [c["text"] for c in projection]
    text = "\n\n".join(parts)
    return {
        "doc_id": doc_id,
        "title": doc.get("title", ""),
        "text": text,
    }


def _principal_from_request(request: Request) -> str | None:
    """Extract principal_did from request state set by session middleware."""
    state = getattr(request, "state", None)
    if state is None:
        return None
    return getattr(state, "auth_claim_principal_did", None)
