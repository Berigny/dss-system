"""FastAPI wrapper for middleware with OpenAPI docs and legacy compatibility."""

from __future__ import annotations

from typing import Any

import json
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator


MAX_QUERY_PRIMES: int = 16


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    d = 3
    while d * d <= n:
        if n % d == 0:
            return False
        d += 2
    return True

from app import (
    MIDDLEWARE_CORS_ORIGINS,
    app as legacy_app,
    start_github_principal_link,
    verify_github_principal_link,
    walt_id_callback,
    entra_oidc_login,
    entra_oidc_callback,
)


class SmartStreamRequest(BaseModel):
    message: str = Field(..., min_length=1)
    provider: str | None = None
    agent: str | None = None
    model: str | None = None
    entity: str | None = None
    ledger_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    enable_ledger: bool = True
    backend_stream: bool | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[str] | None = None
    context_coords: list[str] | None = None
    time_range: dict[str, Any] | None = None
    eligible_for_search: bool | None = None
    search_used: bool | None = None
    query_primes: list[int] | None = None
    hardening_level: int | None = None
    include_padic_diagnostics: bool | None = None
    qp_pure: bool | None = None
    include_pipeline_events: bool | None = None
    include_post_introspect_snapshot: bool | None = None
    metadata: dict[str, Any] | None = None
    delegated_principal: dict[str, Any] | None = None
    s_mode: str | None = None
    query_factors: list[dict[str, Any]] | None = None
    padic_config: dict[str, Any] | None = None
    mmf_domain: str | None = None

    @field_validator("query_primes")
    @classmethod
    def _validate_query_primes(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("query_primes must be a list of prime integers")
        if len(value) > MAX_QUERY_PRIMES:
            raise ValueError(f"query_primes may contain at most {MAX_QUERY_PRIMES} primes")
        seen: set[int] = set()
        for item in value:
            if not isinstance(item, int):
                raise ValueError("query_primes must contain integers")
            if item in seen:
                raise ValueError("query_primes must contain unique primes")
            seen.add(item)
            if not _is_prime(item):
                raise ValueError(f"query_primes must contain prime numbers, got {item}")
        return value


class OpenAIChatCompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


class IngestLimitsResponse(BaseModel):
    max_upload_bytes: int
    max_upload_mb: float
    accepted_types: list[str]
    max_files: int


class LedgerHistoryResponse(BaseModel):
    history: list[dict[str, Any]] = Field(default_factory=list)
    count: int | None = None
    entity: str | None = None


app = FastAPI(
    title="Dual Substrate Middleware API",
    version="0.1.0",
    description=(
        "FastAPI compatibility shell for ds-middleware. "
        "Routes are forwarded to the existing middleware runtime."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=MIDDLEWARE_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _forward_headers(request: Request) -> dict[str, str]:
    blocked = {"host", "content-length", "connection", "transfer-encoding"}
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in blocked
    }


def _content_type(resp: httpx.Response, default: str = "application/json") -> str:
    return resp.headers.get("content-type", default).split(";")[0].strip() or default


async def _send_to_legacy(
    request: Request,
    method: str,
    path: str,
    *,
    json_payload: dict[str, Any] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=legacy_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://legacy.local",
        timeout=None,
    ) as client:
        query_items = list(request.query_params.multi_items())
        if json_payload is not None:
            resp = await client.request(
                method,
                path,
                params=query_items,
                json=json_payload,
                headers=_forward_headers(request),
            )
        else:
            body = await request.body()
            resp = await client.request(
                method,
                path,
                params=query_items,
                content=body,
                headers=_forward_headers(request),
            )
        return resp


async def _stream_from_legacy(
    request: Request,
    method: str,
    path: str,
    *,
    json_payload: dict[str, Any],
) -> Response:
    transport = httpx.ASGITransport(app=legacy_app)
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://legacy.local",
        timeout=None,
    )
    resp: httpx.Response | None = None
    try:
        req = client.build_request(
            method,
            path,
            json=json_payload,
            headers=_forward_headers(request),
        )
        resp = await client.send(req, stream=True)
    except Exception:
        await client.aclose()
        raise

    if resp.status_code >= 400:
        detail = await resp.aread()
        await resp.aclose()
        await client.aclose()
        return JSONResponse(
            {"detail": detail.decode("utf-8", errors="ignore") or "Upstream request failed"},
            status_code=resp.status_code,
        )

    media_type = _content_type(resp, default="application/x-ndjson")
    status_code = resp.status_code
    stream_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate, no-transform",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Accel-Buffering": "no",
    }

    async def iterator():
        assert resp is not None
        try:
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        iterator(),
        status_code=status_code,
        media_type=media_type,
        headers=stream_headers,
    )


@app.get("/health", tags=["meta"])
async def health_check(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/health")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get("/metrics", tags=["meta"])
async def metrics_check(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/metrics")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get("/prometheus", tags=["meta"])
async def prometheus_metrics(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/prometheus")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "text/plain"),
    )


@app.get("/", tags=["meta"], include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "ds-middleware-fastapi", "docs": "/docs", "openapi": "/openapi.json"}


@app.post(
    "/api/chat/smart_stream",
    tags=["chat"],
    summary="Smart orchestrated chat stream (NDJSON)",
)
async def smart_stream(payload: SmartStreamRequest, request: Request) -> Response:
    return await _stream_from_legacy(
        request,
        "POST",
        "/api/chat/smart_stream",
        json_payload=payload.model_dump(exclude_none=True),
    )


@app.post(
    "/v1/chat/completions",
    tags=["chat"],
    summary="OpenAI-compatible chat completions",
)
async def openai_chat_completions(payload: OpenAIChatCompletionRequest, request: Request) -> Response:
    resp = await _send_to_legacy(
        request,
        "POST",
        "/v1/chat/completions",
        json_payload=payload.model_dump(exclude_none=True),
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/ingest/limits",
    tags=["ingest"],
    response_model=IngestLimitsResponse,
    summary="Get ingest limits",
)
async def ingest_limits(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/ingest/limits")
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/account/current/model-library",
    tags=["account"],
    summary="List the current onboarding model library",
)
async def account_model_library(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/account/current/model-library")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/account/current/model-library/select",
    tags=["account"],
    summary="Select the preferred onboarding model principal",
)
async def account_model_library_select(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/account/current/model-library/select")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/account/current/principals",
    tags=["account"],
    summary="List account-scoped onboarding principals",
)
async def account_principals(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/account/current/principals")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/account/current/principals/agent/bootstrap",
    tags=["account"],
    summary="Bootstrap the account agent principal and bindings",
)
async def account_agent_principal_bootstrap(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/account/current/principals/agent/bootstrap")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/account/current/connections",
    tags=["account"],
    summary="List account-scoped principal connection graph edges",
)
async def account_connections(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/account/current/connections")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/ledgers",
    tags=["control-plane"],
    summary="List control-plane ledger records",
)
async def control_plane_ledgers_list(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/ledgers")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/ledgers",
    tags=["control-plane"],
    summary="Create or update a control-plane ledger record",
)
async def control_plane_ledgers_upsert(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/ledgers")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/providers",
    tags=["control-plane"],
    summary="List control-plane provider credential records",
)
async def cp_list_providers(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/providers")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/providers",
    tags=["control-plane"],
    summary="Create or update a control-plane provider credential record",
)
async def cp_upsert_provider(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/providers")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/providers/openrouter/key",
    tags=["control-plane"],
    summary="OpenRouter API key status (masked)",
)
async def cp_openrouter_key_status(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/providers/openrouter/key")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/providers/openrouter/status",
    tags=["control-plane"],
    summary="OpenRouter API key configuration status",
)
async def cp_openrouter_key_status_only(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/providers/openrouter/status")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/providers/openrouter/key",
    tags=["control-plane"],
    summary="Set or update the OpenRouter API key override",
)
async def cp_openrouter_key_update(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/providers/openrouter/key")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/model-bindings",
    tags=["control-plane"],
    summary="List control-plane model binding records",
)
async def cp_list_model_bindings(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/model-bindings")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/model-bindings",
    tags=["control-plane"],
    summary="Create or update a control-plane model binding record",
)
async def cp_upsert_model_bindings(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/model-bindings")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/principals",
    tags=["control-plane"],
    summary="List control-plane principal records",
)
async def control_plane_principals_list(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/principals")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/principals",
    tags=["control-plane"],
    summary="Create or update a control-plane principal record",
)
async def control_plane_principals_upsert(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/principals")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/principals/codex/provision",
    tags=["control-plane"],
    summary="Provision or refresh the governed Codex delegated principal",
)
async def control_plane_codex_principal_provision(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/principals/codex/provision")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/principals/kimi/provision",
    tags=["control-plane"],
    summary="Provision or refresh the governed Kimi Code delegated principal",
)
async def control_plane_kimi_principal_provision(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/principals/kimi/provision")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/principals/{principal_did:path}/status",
    tags=["control-plane"],
    summary="Update control-plane principal status",
)
async def control_plane_principal_status_update(principal_did: str, request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", f"/api/control-plane/principals/{principal_did}/status")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/submissions",
    tags=["control-plane"],
    summary="List control-plane governed submissions",
)
async def control_plane_submissions_list(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/submissions")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/submissions/{submission_ref}/review",
    tags=["control-plane"],
    summary="Approve or reject a control-plane governed submission",
)
async def control_plane_submission_review(request: Request, submission_ref: str) -> Response:
    resp = await _send_to_legacy(request, "POST", f"/api/control-plane/submissions/{submission_ref}/review")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/surfaces",
    tags=["control-plane"],
    summary="List control-plane surface records",
)
async def control_plane_surfaces_list(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/surfaces")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/surfaces",
    tags=["control-plane"],
    summary="Create or update a control-plane surface record",
)
async def control_plane_surfaces_upsert(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/surfaces")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/control-plane/relationships",
    tags=["control-plane"],
    summary="List control-plane relationship records",
)
async def control_plane_relationships_list(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/control-plane/relationships")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/relationships",
    tags=["control-plane"],
    summary="Create or update a control-plane relationship record",
)
async def control_plane_relationships_upsert(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/relationships")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/entities/activate",
    tags=["control-plane"],
    summary="Activate a control-plane governed entity",
)
async def control_plane_entities_activate(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/entities/activate")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/control-plane/entities/remove",
    tags=["control-plane"],
    summary="Remove a control-plane governed entity",
)
async def control_plane_entities_remove(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/control-plane/entities/remove")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/principals",
    tags=["iam"],
    summary="List actor-registry principal records",
)
async def principals_list(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/principals")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/principals/resolve",
    tags=["iam"],
    summary="Resolve principal by key reference",
)
async def principals_resolve(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/principals/resolve")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/principals",
    tags=["iam"],
    summary="Create or update an actor-registry principal record",
)
async def principals_upsert(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/principals")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/principals/link/github/start",
    tags=["iam"],
    summary="Start GitHub principal link flow",
)
async def principals_link_github_start(request: Request) -> Response:
    return JSONResponse(await start_github_principal_link(request))


@app.post(
    "/api/principals/link/github/verify",
    tags=["iam"],
    summary="Verify GitHub principal link flow",
)
async def principals_link_github_verify(request: Request) -> Response:
    return JSONResponse(await verify_github_principal_link(request))


@app.get(
    "/api/principals/{principal_did:path}",
    tags=["iam"],
    summary="Get actor-registry principal record",
)
async def principals_get(principal_did: str, request: Request) -> Response:
    path = f"/api/principals/{httpx.URL(path=f'/{principal_did}').path.lstrip('/')}"
    resp = await _send_to_legacy(request, "GET", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/principals/{principal_did:path}/bindings",
    tags=["iam"],
    summary="Bind governed external actor identity to actor-registry principal",
)
async def principals_bindings(principal_did: str, request: Request) -> Response:
    encoded = httpx.URL(path=f"/{principal_did}").path.lstrip("/")
    path = f"/api/principals/{encoded}/bindings"
    resp = await _send_to_legacy(request, "POST", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/principals/{principal_did:path}/subject/events",
    tags=["iam"],
    summary="List subject transition events for a principal",
)
async def principals_subject_events_get(principal_did: str, request: Request) -> Response:
    encoded = httpx.URL(path=f"/{principal_did}").path.lstrip("/")
    path = f"/api/principals/{encoded}/subject/events"
    resp = await _send_to_legacy(request, "GET", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/principals/{principal_did:path}/subject/events",
    tags=["iam"],
    summary="Append subject transition event for a principal",
)
async def principals_subject_events_post(principal_did: str, request: Request) -> Response:
    encoded = httpx.URL(path=f"/{principal_did}").path.lstrip("/")
    path = f"/api/principals/{encoded}/subject/events"
    resp = await _send_to_legacy(request, "POST", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/principals/{principal_did:path}/standing",
    tags=["iam"],
    summary="Get materialized standing view for a principal",
)
async def principals_standing_get(principal_did: str, request: Request) -> Response:
    encoded = httpx.URL(path=f"/{principal_did}").path.lstrip("/")
    path = f"/api/principals/{encoded}/standing"
    resp = await _send_to_legacy(request, "GET", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/principals/{principal_did:path}/standing/events",
    tags=["iam"],
    summary="List standing events for a principal",
)
async def principals_standing_events_get(principal_did: str, request: Request) -> Response:
    encoded = httpx.URL(path=f"/{principal_did}").path.lstrip("/")
    path = f"/api/principals/{encoded}/standing/events"
    resp = await _send_to_legacy(request, "GET", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/principals/{principal_did:path}/standing/events",
    tags=["iam"],
    summary="Append standing event for a principal",
)
async def principals_standing_events_post(principal_did: str, request: Request) -> Response:
    encoded = httpx.URL(path=f"/{principal_did}").path.lstrip("/")
    path = f"/api/principals/{encoded}/standing/events"
    resp = await _send_to_legacy(request, "POST", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/principals/{principal_did:path}/disable",
    tags=["iam"],
    summary="Disable principal",
)
async def principals_disable(principal_did: str, request: Request) -> Response:
    encoded = httpx.URL(path=f"/{principal_did}").path.lstrip("/")
    path = f"/api/principals/{encoded}/disable"
    resp = await _send_to_legacy(request, "POST", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/principals/{principal_did:path}/enable",
    tags=["iam"],
    summary="Enable principal",
)
async def principals_enable(principal_did: str, request: Request) -> Response:
    encoded = httpx.URL(path=f"/{principal_did}").path.lstrip("/")
    path = f"/api/principals/{encoded}/enable"
    resp = await _send_to_legacy(request, "POST", path)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/verified-id/issuance-requests",
    tags=["iam"],
    summary="Create a verified ID issuance request (Entra or walt.id)",
)
async def verified_id_issuance_requests(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/verified-id/issuance-requests")
    if resp.status_code >= 400:
        body = _safe_json_body(resp)
        return JSONResponse(body, status_code=resp.status_code)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


def _safe_json_body(resp: httpx.Response) -> dict[str, Any]:
    text = resp.text or ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"detail": parsed}
    except Exception:
        return {"detail": text[:2000] or "upstream error"}


@app.post(
    "/api/verified-id/presentation-requests",
    tags=["iam"],
    summary="Create a verified ID presentation request",
)
async def verified_id_presentation_requests(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/verified-id/presentation-requests")
    if resp.status_code >= 400:
        body = _safe_json_body(resp)
        return JSONResponse(body, status_code=resp.status_code)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/verified-id/requests/{state}",
    tags=["iam"],
    summary="Get a verified ID request by state",
)
async def verified_id_request(state: str, request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", f"/api/verified-id/requests/{state}")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/webhooks/entra/verified-id",
    tags=["iam"],
    summary="Entra Verified ID callback",
)
async def entra_verified_id_callback(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/api/webhooks/entra/verified-id")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/api/webhooks/walt-id/issuance",
    tags=["iam"],
    summary="walt.id issuance status callback",
)
async def walt_id_issuance_callback(request: Request) -> Response:
    result = await walt_id_callback(request)
    if isinstance(result, Response):
        return result
    return JSONResponse(content=result)


@app.get(
    "/api/trust-anchor/status",
    tags=["iam"],
    summary="Get filtered trust-anchor status for the public identity host",
)
async def trust_anchor_status(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/trust-anchor/status")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/trust-anchor/bundle",
    tags=["iam"],
    summary="Get public trust bundle for verifier discovery",
)
async def trust_anchor_bundle(request: Request) -> Response:
    resp = await _send_to_legacy(request, "GET", "/api/trust-anchor/bundle")
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/ledger/history/{entity_path:path}",
    tags=["ledger"],
    response_model=LedgerHistoryResponse | list[dict[str, Any]],
    summary="Get ordered history for an entity",
)
async def ledger_history(entity_path: str, request: Request, limit: int = 50) -> Response:
    encoded_entity = httpx.URL(path=f"/{entity_path}").path.lstrip("/")
    path = f"/ledger/history/{encoded_entity}"
    resp = await _send_to_legacy(request, "GET", path)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/ledger/all",
    tags=["ledger"],
    summary="Get recent ledger entries across all entities",
)
async def ledger_all(request: Request, limit: int = 100) -> Response:
    path = f"/ledger/all?limit={int(limit)}"
    resp = await _send_to_legacy(request, "GET", path)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/ledger/history_entities",
    tags=["ledger"],
    summary="Get discoverable history entities",
)
async def ledger_history_entities(request: Request, limit: int = 200) -> Response:
    path = f"/ledger/history_entities?limit={int(limit)}"
    resp = await _send_to_legacy(request, "GET", path)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.post(
    "/sync/v0/pull",
    tags=["sync"],
    summary="Sync pull proxy",
)
async def sync_v0_pull(request: Request) -> Response:
    resp = await _send_to_legacy(request, "POST", "/sync/v0/pull")
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return Response(content=resp.content, status_code=resp.status_code, media_type=_content_type(resp))


@app.get(
    "/api/auth/entra/login",
    tags=["auth"],
    summary="Start Entra OIDC login flow",
    include_in_schema=False,
)
async def api_auth_entra_login(request: Request) -> Response:
    return await entra_oidc_login(request)


@app.get(
    "/api/auth/entra/callback",
    tags=["auth"],
    summary="Entra OIDC callback",
    include_in_schema=False,
)
async def api_auth_entra_callback(request: Request) -> Response:
    return await entra_oidc_callback(request)


# Preserve legacy routes without requiring immediate rewrites.
app.mount("/", legacy_app)  # type: ignore[arg-type]
