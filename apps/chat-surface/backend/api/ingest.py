from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, UploadFile
from fastapi.responses import StreamingResponse

router = APIRouter()


def _to_ndjson(payload: dict[str, Any]) -> bytes:
    return f"{json.dumps(payload, ensure_ascii=False)}\n".encode("utf-8")


async def _extract_text(
    file_bytes: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    return file_bytes.decode("utf-8", errors="ignore")


async def _summarize_text(
    text: str,
    *,
    entity: str,
    kind: str,
) -> str:
    summary = text.strip().replace("\n", " ")
    if len(summary) > 280:
        return f"{summary[:277]}..."
    return summary


async def record_attachment(
    *,
    entity: str,
    kind: str,
    file_bytes: bytes,
    text: str,
    summary: str,
    filename: str | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    return {
        "coordinate": f"{entity}:{kind}:{filename or 'attachment'}",
        "size_bytes": len(file_bytes),
        "content_type": content_type or "application/octet-stream",
    }


@router.post("/api/ingest/stream-file")
async def ingest_stream_file(
    file: UploadFile,
    entity: str,
    kind: str,
) -> StreamingResponse:
    async def _generator() -> AsyncIterator[bytes]:
        yield _to_ndjson({"type": "status", "message": "Receiving file..."})

        file_bytes = await file.read()
        extracted_text = await _extract_text(
            file_bytes,
            filename=file.filename,
            content_type=file.content_type,
        )

        yield _to_ndjson(
            {"type": "status", "message": "Analyzing text structure..."}
        )
        summary = await _summarize_text(extracted_text, entity=entity, kind=kind)

        yield _to_ndjson({"type": "status", "message": "Indexing content..."})
        record = await record_attachment(
            entity=entity,
            kind=kind,
            file_bytes=file_bytes,
            text=extracted_text,
            summary=summary,
            filename=file.filename,
            content_type=file.content_type,
        )

        yield _to_ndjson(
            {
                "type": "meta",
                "coordinate": record.get("coordinate"),
                "summary": summary,
            }
        )

    return StreamingResponse(_generator(), media_type="application/x-ndjson")
