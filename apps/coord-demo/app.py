"""FastHTML COORD decoder demo with governed-surface SSO.

Provides a single-page UI and a POST /resolve endpoint that forwards a COORD
JSON payload to the middleware resolver scoped to the configured ledger.
Access is gated by the shared control-plane session (ds_backend_session_token).
"""

import json
import os
from urllib.parse import quote

import httpx
from fasthtml.common import Button, Div, Form, H1, P, Pre, Textarea, Titled, fast_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

MIDDLEWARE_URL = (
    os.getenv("MIDDLEWARE_URL")
    or os.getenv("MIDDLEWARE_BASE_URL")
    or os.getenv("API_BASE")
    or "http://middleware:8001"
).rstrip("/")

CONTROL_PLANE_BASE = (
    os.getenv("CONTROL_PLANE_BASE")
    or os.getenv("DUALSUBSTRATE_CONTROL_PLANE_BASE")
    or "https://id.dualsubstrate.com"
).rstrip("/")

DEFAULT_LEDGER_ID = (
    os.getenv("DEFAULT_LEDGER_ID")
    or os.getenv("LEDGER_ID")
    or "loam"
).lower()

COORD_DEMO_BASE_URL = (
    os.getenv("COORD_DEMO_BASE_URL")
    or "https://decode.dualsubstrate.com"
).rstrip("/")

BACKEND_SESSION_TOKEN_COOKIE = "ds_backend_session_token"


app, rt = fast_app(secret_key=os.getenv("FASTHTML_SECRET_KEY", "coord-demo-secret"))


def _login_url(request: Request) -> str:
    callback_url = f"{COORD_DEMO_BASE_URL}/auth/callback"
    return f"{CONTROL_PLANE_BASE}/login/wallet?next={quote(callback_url, safe='/?:&=')}"

def _is_https_request(request: Request) -> bool:
    return str(request.headers.get("x-forwarded-proto") or request.url.scheme).lower() == "https"


async def _verify_session_token(token: str) -> str | None:
    """Ask the control-plane to validate the shared backend session token."""
    if not token:
        return None
    verify_url = f"{CONTROL_PLANE_BASE}/api/auth/session/verify"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                verify_url,
                headers={"x-session-token": token, "accept": "application/json"},
            )
        if resp.status_code >= 400:
            return None
        payload = resp.json()
        if not isinstance(payload, dict):
            return None
        principal_did = str(payload.get("principal_did") or "").strip()
        return principal_did if principal_did else None
    except Exception:
        return None


def _set_session_cookie(response: RedirectResponse, request: Request, token: str) -> None:
    response.set_cookie(
        BACKEND_SESSION_TOKEN_COOKIE,
        token,
        httponly=True,
        secure=_is_https_request(request),
        samesite="lax",
        max_age=3600,
        path="/",
    )


class CoordAuthMiddleware(BaseHTTPMiddleware):
    """Require a valid control-plane session; otherwise redirect to login."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in {"/health", "/favicon.ico", "/auth/callback"} or path.startswith("/static/"):
            return await call_next(request)

        token = str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE) or request.query_params.get("ds_session_token") or "").strip()
        if not token:
            return RedirectResponse(url=_login_url(request), status_code=303)

        principal_did = await _verify_session_token(token)
        if not principal_did:
            return RedirectResponse(url=_login_url(request), status_code=303)

        # If the token arrived in the URL, establish a first-party cookie and
        # strip the query parameter to keep URLs clean.
        if request.query_params.get("ds_session_token"):
            clean_url = str(request.url).split("?")[0]
            response = RedirectResponse(url=clean_url, status_code=303)
            _set_session_cookie(response, request, token)
            return response

        request.state.principal_did = principal_did
        return await call_next(request)


app.add_middleware(CoordAuthMiddleware)


@rt("/")
def index(request: Request):
    principal = getattr(request.state, "principal_did", None)
    header = (
        Div(
            P(f"Authenticated principal: {principal}", cls="muted"),
            style="margin-bottom:1rem;",
        )
        if principal
        else Div()
    )
    return Titled(
        "COORD Demo",
        Div(
            H1("Resolve COORD"),
            header,
            P("Paste a COORD JSON payload and submit it to the middleware resolver."),
            Form(
                Textarea(
                    name="coordinate",
                    placeholder='chat-demo:WX-1',
                    rows=10,
                    style="width:100%;",
                ),
                Button("Resolve", type="submit"),
                action="/resolve",
                method="post",
            ),
            Div(Pre(id="result"), id="result-container"),
        ),
    )


@rt("/resolve", methods=["post"])
def resolve(request: Request, coordinate: str):
    token = str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE) or "").strip()
    payload = {"coordinate": coordinate.strip(), "ledger_id": DEFAULT_LEDGER_ID}
    headers: dict[str, str] = {
        "x-surface-id": "surface:coord-demo",
    }
    if token:
        headers["x-session-token"] = token
    try:
        response = httpx.post(
            f"{MIDDLEWARE_URL}/api/decode_coordinate",
            json=payload,
            headers=headers,
            timeout=30.0,
        )
        if response.status_code >= 400:
            body = response.text
            try:
                body = json.dumps(response.json(), indent=2)
            except Exception:
                pass
            return Pre(
                f"Resolver error: HTTP {response.status_code}\n{body}",
                id="result",
            )
        return Pre(json.dumps(response.json(), indent=2), id="result")
    except httpx.HTTPError as exc:
        return Pre(f"Resolver error: {exc}", id="result")


@rt("/auth/callback")
async def auth_callback(request: Request):
    """Receive a cross-domain session token from the control-plane login flow."""
    token = str(request.query_params.get("ds_session_token") or "").strip()
    if not token:
        return RedirectResponse(url=_login_url(request), status_code=303)
    principal_did = await _verify_session_token(token)
    if not principal_did:
        return RedirectResponse(url=_login_url(request), status_code=303)
    response = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(response, request, token)
    return response


@rt("/health")
def health():
    return {
        "status": "ok",
        "commit_sha": (os.getenv("VERCEL_GIT_COMMIT_SHA") or "").strip() or "unknown",
    }
