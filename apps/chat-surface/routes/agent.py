"""Simplified endpoints for OpenClaw interaction."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from api.client import api


def register_agent_routes(rt):
    @rt("/api/agent/search", methods=["POST"])
    async def agent_search(request: Request):
        data = await request.json()
        query = data.get("query", "")
        results = await api.search_any(query=query, limit=5)
        return JSONResponse(results)

    @rt("/api/agent/save", methods=["POST"])
    async def agent_save(request: Request):
        data = await request.json()
        content = data.get("content")
        result = await api.enrich(
            entity="openclaw-local",
            role="assistant",
            content=content,
            kind="observation",
            metadata={"source": "agent_local"},
        )
        return JSONResponse(result)
