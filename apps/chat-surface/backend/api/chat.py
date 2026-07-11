from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None
    enable_ledger: bool = True
    entity: str | None = None


async def assemble_context(request: ChatRequest) -> dict[str, Any]:
    """Assemble context for the chat turn."""
    return {}


async def complete_chat(
    request: ChatRequest,
    context: dict[str, Any],
) -> AsyncIterator[str] | Iterable[str] | str:
    """Complete the chat request, optionally yielding content chunks."""
    return ""


async def enrich_turn(
    request: ChatRequest,
    content: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Persist the turn and return metadata for the stream."""
    return {}


def _to_ndjson(payload: dict[str, Any]) -> bytes:
    return f"{json.dumps(payload, ensure_ascii=False)}\n".encode("utf-8")


async def _iter_chunks(
    stream: AsyncIterator[Any] | Iterable[Any] | Any,
) -> AsyncIterator[str]:
    if stream is None:
        return
    if hasattr(stream, "__aiter__"):
        async for chunk in stream:  # type: ignore[misc]
            if chunk:
                yield _normalize_chunk(chunk)
        return
    if isinstance(stream, dict):
        content = stream.get("content") or stream.get("text") or stream.get("reply")
        if content:
            yield str(content)
        return
    if isinstance(stream, (str, bytes)):
        yield stream.decode("utf-8") if isinstance(stream, bytes) else stream
        return
    if isinstance(stream, Iterable):
        for chunk in stream:
            if chunk:
                yield _normalize_chunk(chunk)
        return
    yield str(stream)


def _normalize_chunk(chunk: Any) -> str:
    if isinstance(chunk, dict):
        content = chunk.get("content") or chunk.get("text") or chunk.get("reply")
        return str(content) if content is not None else json.dumps(chunk, ensure_ascii=False)
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8")
    return str(chunk)


@router.post("/chat/stream")
async def stream_chat(request: ChatRequest) -> StreamingResponse:
    async def _generator() -> AsyncIterator[bytes]:
        yield _to_ndjson({"type": "status", "message": "Initializing..."})

        context = await assemble_context(request)
        yield _to_ndjson({"type": "status", "message": "Assembling memories..."})

        yield _to_ndjson({"type": "status", "message": "Reasoning..."})

        full_content = ""
        chat_result = complete_chat(request, context)
        if inspect.isawaitable(chat_result):
            chat_result = await chat_result

        async for chunk in _iter_chunks(chat_result):
            full_content += chunk
            yield _to_ndjson({"type": "token", "content": chunk})

        yield _to_ndjson({"type": "status", "message": "Saving to Ledger..."})
        enrichment = await enrich_turn(request, full_content, context)

        yield _to_ndjson(
            {
                "type": "meta",
                "coordinate": enrichment.get("coordinate"),
                "knowledge_tree": enrichment.get("knowledge_tree", []),
            }
        )

    return StreamingResponse(_generator(), media_type="application/x-ndjson")
