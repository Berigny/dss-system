"""Document surface v0.1 FastHTML UI.

Reuses layout and hamburger menu patterns from the DSS chat surface.
Authentication is handled by sharing the backend session token cookie with the
DSS control plane login flow.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import httpx
from fasthtml.common import (
    A,
    Button,
    Div,
    Form,
    H2,
    H3,
    Input,
    Label,
    Option,
    P,
    Pre,
    Select,
    Textarea,
    fast_app,
    serve,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from components.layout import page_shell
from config.settings import settings


COOKIE = settings.BACKEND_SESSION_TOKEN_COOKIE
BYPASS_AUTH = os.getenv("DOCUMENT_SURFACE_BYPASS_AUTH", "").lower() in {"1", "true", "yes", "on"}


def _cookie_secure(request: Request) -> bool:
    return request.url.scheme == "https"


def _control_plane_login_url(request: Request) -> str:
    base = (settings.CONTROL_PLANE_BASE or "").rstrip("/")
    if not base:
        return ""
    next_url = str(request.url)
    return f"{base}/login?next={urllib.parse.quote(next_url, safe='')}&surface=document"


async def _verify_session_token(token: str) -> str | None:
    """Return principal_did if token is valid, otherwise None."""
    if not token:
        return None
    bases: list[str] = []
    for base in (
        str(os.getenv("DUALSUBSTRATE_AUTH_BASE") or "").strip(),
        str(settings.BACKEND_ADMIN_BASE or "").strip(),
        str(settings.API_BASE or "").strip(),
    ):
        candidate = base.rstrip("/")
        if candidate and candidate not in bases:
            bases.append(candidate)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for idx, base in enumerate(bases):
                resp = await client.get(
                    f"{base}/auth/session/verify",
                    headers={"x-session-token": token, "accept": "application/json"},
                )
                try:
                    payload = resp.json()
                except Exception:
                    payload = {}
                principal_did = str(payload.get("principal_did") or "").strip() if isinstance(payload, dict) else ""
                if resp.status_code == 404 and idx < len(bases) - 1:
                    continue
                if resp.status_code < 400 and principal_did:
                    return principal_did
                return None
    except Exception:
        return None
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated requests to the control-plane login."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in {"/auth/callback", "/logout", "/healthz"}:
            return await call_next(request)
        if BYPASS_AUTH:
            request.state.principal_did = os.getenv("DOCUMENT_SURFACE_DEMO_PRINCIPAL", "did:web:demo")
            return await call_next(request)
        token = str(request.cookies.get(COOKIE) or "").strip()
        principal_did = await _verify_session_token(token)
        if not principal_did:
            login_url = _control_plane_login_url(request)
            if login_url:
                return RedirectResponse(url=login_url, status_code=303)
            return PlainTextResponse("Authentication required", status_code=401)
        request.state.principal_did = principal_did
        return await call_next(request)


app, rt = fast_app()
app.add_middleware(AuthMiddleware)


def _backend_headers(request: Request) -> dict[str, str]:
    token = str(request.cookies.get(COOKIE) or "").strip()
    headers: dict[str, str] = {
        "accept": "application/json",
        "content-type": "application/json",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers


async def _backend_request(
    request: Request,
    method: str,
    path: str,
    json_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the DSS backend API and return JSON."""
    base = (settings.API_BASE or "").rstrip("/")
    if not base:
        raise RuntimeError("API_BASE is not configured")
    url = f"{base}{path}"
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        if method.upper() == "GET":
            resp = await client.get(url, headers=_backend_headers(request))
        else:
            resp = await client.request(
                method.upper(),
                url,
                headers=_backend_headers(request),
                json=json_payload,
            )
    resp.raise_for_status()
    return resp.json()


def _error_div(message: str) -> Div:
    return Div(P(message), cls="error-message")


def _doc_card(doc: dict[str, Any]) -> Div:
    doc_id = doc.get("doc_id", "")
    title = doc.get("title", "Untitled")
    return Div(
        A(H3(title), href=f"/doc/{doc_id}"),
        P(f"ID: {doc_id}", cls="doc-id"),
        cls="doc-card",
    )


def _version_select(chunk_coord: str, versions: list[str], active: str) -> Select:
    return Select(
        *[
            Option(v, value=v, selected="selected" if v == active else None)
            for v in versions
        ],
        name="active_version",
        hx_post=f"/api/chunks/{chunk_coord}/version",
        hx_target=f"#chunk-{chunk_coord}",
        hx_swap="outerHTML",
        cls="version-select",
    )


def _chunk_card(chunk: dict[str, Any], versions: list[str]) -> Div:
    coord = chunk["chunk_coord"]
    active = chunk.get("active_version", "")
    text = chunk.get("full_text", "")
    sel_start = chunk.get("sel_start", 0)
    sel_end = chunk.get("sel_end", len(text))
    visible = chunk.get("visible", True)
    return Div(
        Div(
            Span(f"Chunk {coord}", cls="chunk-title"),
            Span("hidden" if not visible else "visible", cls="chunk-status"),
            cls="chunk-header",
        ),
        Div(
            Label("Version", for_=f"version-{coord}"),
            _version_select(coord, versions, active),
            cls="chunk-control",
        ),
        Div(
            Label("Selection", for_=f"sel-{coord}"),
            Input(
                type="number",
                name="sel_start",
                value=str(sel_start),
                min="0",
                max=str(len(text)),
                cls="sel-input",
            ),
            Input(
                type="number",
                name="sel_end",
                value=str(sel_end),
                min="0",
                max=str(len(text)),
                cls="sel-input",
            ),
            Button(
                "Apply",
                hx_post=f"/api/chunks/{coord}/selection",
                hx_target=f"#chunk-{coord}",
                hx_swap="outerHTML",
                hx_include=f"#chunk-{coord} input",
                cls="btn secondary",
            ),
            cls="chunk-control",
        ),
        Div(
            Textarea(text, name="text", readonly=True, cls="chunk-text"),
            cls="chunk-body",
        ),
        Div(
            Button(
                "↑",
                hx_post=f"/api/chunks/{coord}/move?delta=-1",
                hx_target="#chunks",
                hx_swap="innerHTML",
                cls="btn icon",
            ),
            Button(
                "↓",
                hx_post=f"/api/chunks/{coord}/move?delta=1",
                hx_target="#chunks",
                hx_swap="innerHTML",
                cls="btn icon",
            ),
            Button(
                "Reprompt",
                hx_get=f"/api/chunks/{coord}/reprompt-form",
                hx_target=f"#chunk-{coord}-reprompt",
                cls="btn secondary",
            ),
            Button(
                "Hide" if visible else "Restore",
                hx_post=f"/api/chunks/{coord}/toggle",
                hx_target=f"#chunk-{coord}",
                hx_swap="outerHTML",
                cls="btn secondary",
            ),
            cls="chunk-actions",
        ),
        Div(id=f"chunk-{coord}-reprompt", cls="reprompt-slot"),
        id=f"chunk-{coord}",
        cls="chunk-card",
    )


def _chunks_partial(doc: dict[str, Any]) -> Div:
    chunks = doc.get("chunks", [])
    if not chunks:
        return Div(P("No chunks yet."), id="chunks")
    cards = []
    for chunk in chunks:
        versions = [chunk.get("active_version", "")]
        cards.append(_chunk_card(chunk, versions))
    return Div(*cards, id="chunks")


@rt("/healthz")
async def healthz():
    return {"status": "ok"}


@rt("/auth/callback")
async def auth_callback(request: Request):
    """Receive a backend session token from the control plane and store it."""
    token = ""
    next_url = "/"
    if request.method == "GET":
        token = str(request.query_params.get("token") or "").strip()
        next_url = str(request.query_params.get("next") or "/").strip()
    else:
        form = await request.form()
        token = str(form.get("token") or "").strip()
        next_url = str(form.get("next") or "/").strip()
    if not token:
        return PlainTextResponse("Missing token", status_code=400)
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
        max_age=86400,
    )
    return response


@rt("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return response


@rt("/")
async def index(request: Request):
    docs: list[dict[str, Any]] = []
    error = ""
    try:
        docs = await _backend_request(request, "GET", "/v1/documents")
        if isinstance(docs, dict):
            docs = docs.get("documents", [])
    except Exception as exc:
        error = f"Could not load documents: {exc}"
    content = Div(
        Div(
            P(f"Principal: {request.state.principal_did}", cls="principal"),
            A("Logout", href="/logout", cls="logout-link"),
            cls="top-bar",
        ),
        Div(
            H2("New document"),
            Form(
                Input(name="title", placeholder="Untitled", cls="text-input"),
                Button("Create", type="submit", cls="btn primary"),
                hx_post="/api/documents",
                hx_target="#doc-list",
                hx_swap="beforeend",
            ),
            cls="composer",
        ),
        Div(
            H2("Documents"),
            Div(
                *[_doc_card(d) for d in docs],
                id="doc-list",
            ),
            _error_div(error) if error else "",
            cls="doc-list-container",
        ),
    )
    return page_shell(content, title="docs.DSS")


@rt("/doc/{doc_id}")
async def doc_editor(request: Request, doc_id: str):
    error = ""
    doc: dict[str, Any] = {}
    try:
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
    except Exception as exc:
        error = f"Could not load document: {exc}"
    title = doc.get("title", "Untitled")
    content = Div(
        Div(
            P(f"Principal: {request.state.principal_did}", cls="principal"),
            A("← All documents", href="/", cls="back-link"),
            cls="top-bar",
        ),
        Div(
            H2(title),
            P(f"ID: {doc_id}", cls="doc-id"),
            cls="doc-header",
        ),
        Div(
            H3("Add chunk"),
            Form(
                Textarea(name="prompt", placeholder="Prompt for this chunk...", cls="text-input"),
                Button("Generate chunk", type="submit", cls="btn primary"),
                hx_post=f"/api/documents/{doc_id}/chunks",
                hx_target="#chunks",
                hx_swap="beforeend",
            ),
            cls="composer",
        ),
        _chunks_partial(doc),
        _error_div(error) if error else "",
        Div(
            H3("Export"),
            Button(
                "Render export",
                hx_get=f"/api/documents/{doc_id}/export",
                hx_target="#export-view",
                cls="btn primary",
            ),
            Pre(id="export-view", cls="export-view"),
            cls="export-container",
        ),
    )
    return page_shell(content, title=f"{title} | docs.DSS")


@rt("/api/documents", methods=["POST"])
async def api_create_document(request: Request):
    form = await request.form()
    title = str(form.get("title") or "").strip() or "Untitled"
    try:
        payload = await _backend_request(request, "POST", "/v1/documents", {"title": title})
    except Exception as exc:
        return _error_div(f"Create failed: {exc}")
    return _doc_card(payload)


@rt("/api/documents/{doc_id}/chunks", methods=["POST"])
async def api_create_chunk(request: Request, doc_id: str):
    form = await request.form()
    prompt = str(form.get("prompt") or "").strip()
    try:
        await _backend_request(request, "POST", f"/v1/documents/{doc_id}/chunks", {"prompt": prompt})
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
    except Exception as exc:
        return _error_div(f"Chunk failed: {exc}")
    return _chunks_partial(doc)


@rt("/api/chunks/{chunk_coord}/reprompt-form", methods=["GET"])
async def api_reprompt_form(chunk_coord: str):
    return Div(
        Form(
            Textarea(name="prompt", placeholder="New prompt...", cls="text-input"),
            Button("Generate", type="submit", cls="btn primary"),
            hx_post=f"/api/chunks/{chunk_coord}/reprompt",
            hx_target=f"#chunk-{chunk_coord}",
            hx_swap="outerHTML",
        ),
        id=f"chunk-{chunk_coord}-reprompt",
    )


@rt("/api/chunks/{chunk_coord}/reprompt", methods=["POST"])
async def api_reprompt(request: Request, chunk_coord: str):
    form = await request.form()
    prompt = str(form.get("prompt") or "").strip()
    doc_id = _doc_id_from_chunk(chunk_coord)
    try:
        await _backend_request(request, "POST", f"/v1/documents/chunks/{chunk_coord}/reprompt", {"prompt": prompt})
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
    except Exception as exc:
        return _error_div(f"Reprompt failed: {exc}")
    return _chunks_partial(doc)


@rt("/api/chunks/{chunk_coord}/version", methods=["POST"])
async def api_set_version(request: Request, chunk_coord: str):
    form = await request.form()
    version = str(form.get("active_version") or "").strip()
    doc_id = _doc_id_from_chunk(chunk_coord)
    try:
        await _backend_request(request, "PATCH", f"/v1/documents/chunks/{chunk_coord}", {"active_version": version})
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
    except Exception as exc:
        return _error_div(f"Version change failed: {exc}")
    return _chunks_partial(doc)


@rt("/api/chunks/{chunk_coord}/selection", methods=["POST"])
async def api_set_selection(request: Request, chunk_coord: str):
    form = await request.form()
    try:
        sel_start = int(str(form.get("sel_start") or "0").strip())
        sel_end = int(str(form.get("sel_end") or "0").strip())
    except ValueError:
        return _error_div("Selection values must be integers")
    doc_id = _doc_id_from_chunk(chunk_coord)
    try:
        await _backend_request(request, "PATCH", f"/v1/documents/chunks/{chunk_coord}", {"sel_start": sel_start, "sel_end": sel_end})
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
    except Exception as exc:
        return _error_div(f"Selection update failed: {exc}")
    return _chunks_partial(doc)


@rt("/api/chunks/{chunk_coord}/toggle", methods=["POST"])
async def api_toggle_visible(request: Request, chunk_coord: str):
    doc_id = _doc_id_from_chunk(chunk_coord)
    try:
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
        chunk = next((c for c in doc.get("chunks", []) if c["chunk_coord"] == chunk_coord), {})
        visible = not chunk.get("visible", True)
        await _backend_request(request, "PATCH", f"/v1/documents/chunks/{chunk_coord}", {"visible": visible})
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
    except Exception as exc:
        return _error_div(f"Toggle failed: {exc}")
    return _chunks_partial(doc)


@rt("/api/chunks/{chunk_coord}/move", methods=["POST"])
async def api_move_chunk(request: Request, chunk_coord: str):
    delta = int(request.query_params.get("delta") or "0")
    doc_id = _doc_id_from_chunk(chunk_coord)
    try:
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
        chunks = sorted(doc.get("chunks", []), key=lambda c: c.get("position", 0))
        positions = {c["chunk_coord"]: c.get("position", 0) for c in chunks}
        current = positions.get(chunk_coord, 0)
        target = current + delta
        # Find neighbour at target position and swap.
        neighbour = next((c for c in chunks if c.get("position", 0) == target), None)
        if neighbour:
            await _backend_request(
                request,
                "PATCH",
                f"/v1/documents/chunks/{neighbour['chunk_coord']}",
                {"position": current},
            )
        await _backend_request(request, "PATCH", f"/v1/documents/chunks/{chunk_coord}", {"position": target})
        doc = await _backend_request(request, "GET", f"/v1/documents/{doc_id}")
    except Exception as exc:
        return _error_div(f"Move failed: {exc}")
    return _chunks_partial(doc)


@rt("/api/documents/{doc_id}/export", methods=["GET"])
async def api_export(request: Request, doc_id: str):
    try:
        payload = await _backend_request(request, "GET", f"/v1/documents/{doc_id}/export")
    except Exception as exc:
        return _error_div(f"Export failed: {exc}")
    text = payload.get("text", "")
    return Pre(text, cls="export-view")


def _doc_id_from_chunk(chunk_coord: str) -> str:
    """Extract doc_id from a DOC-<doc>-C<n> coord."""
    # chunk_coord format: DOC-{doc_id}-C{n}
    prefix = "DOC-"
    suffix_start = chunk_coord.rfind("-C")
    if not chunk_coord.startswith(prefix) or suffix_start <= len(prefix):
        raise ValueError("Invalid chunk coord")
    return chunk_coord[len(prefix):suffix_start]


if __name__ == "__main__":
    serve()
