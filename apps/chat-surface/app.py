import inspect
import os
import re
import time
import html
import hmac
import hashlib
import json
import httpx
import base64
import asyncio
import binascii
import secrets
from datetime import datetime
from typing import Any, cast
from urllib.parse import quote, urlencode, urlparse
from fasthtml.common import Button, Div, Form, Input, Label, Optgroup, Option, P, Script, Select, Title, fast_app, serve
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.requests import Request
from fastapi import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.datastructures import UploadFile
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.applications import Starlette

from api.llm import PRICING, llm
from api.client import ChatResponse, api, set_request_session_token, reset_request_session_token
from config.settings import DEFAULT_LEDGER_ID, DEFAULT_SESSION_ID, settings
from routes.home import register_home_routes
from routes.wake import register_wake_routes
from routes.agent import register_agent_routes
from utils.session import build_entity_namespace, get_session, update_session
from utils.text_processing import COORD_PATTERN, extract_coords_from_text, truncate_text, normalize_coord_token


def _install_starlette_init_compat() -> None:
    params = inspect.signature(Starlette.__init__).parameters
    missing = {
        name for name in ("on_startup", "on_shutdown", "lifespan")
        if name not in params
    }
    if not missing:
        return

    original_init = Starlette.__init__

    def _compat_init(self, *args, **kwargs):
        for name in missing:
            kwargs.pop(name, None)
        return original_init(self, *args, **kwargs)

    Starlette.__init__ = _compat_init


_install_starlette_init_compat()

MAX_DECODED_COORDS = 18
MAX_SUMMARY_CHARS = 220
MAX_CLAIMS_CHARS = 200
MAX_CONTEXT_CHARS = 1200
MAX_PIPELINE_EVENTS = 64
MANUAL_SYNC_MAX_ROUNDS_DEFAULT = 8
DEMO_NETWORK_PROBE_BYTES = 64 * 1024
DEMO_NETWORK_PROBE_PAYLOAD = "0" * DEMO_NETWORK_PROBE_BYTES
APPRAISAL_GRACE_TIER0 = 0.7
NO_MATCH_FALLBACK_TEXT = "No matching records found."
TIMING_DEBUG = os.getenv("TIMING_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
RESOLVE_SNIPPET_DEBUG = os.getenv("RESOLVE_SNIPPET_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
OPENAI_COMPAT_USE_PIPELINE = os.getenv("OPENAI_COMPAT_USE_PIPELINE", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
OPENAI_COMPAT_PIPELINE_ENGINE = os.getenv("OPENAI_COMPAT_PIPELINE_ENGINE", "middleware").strip().lower()
OPENAI_COMPAT_S_MODE = os.getenv("OPENAI_COMPAT_S_MODE", "s1").strip().lower()
OPENAI_COMPAT_POLICY_ALLOW_CLIENT_OVERRIDES = os.getenv(
    "OPENAI_COMPAT_POLICY_ALLOW_CLIENT_OVERRIDES",
    "0",
).strip().lower() in {"1", "true", "yes", "on"}
CODEX_PRINCIPAL_DID = os.getenv("CODEX_PRINCIPAL_DID", "")
CODEX_PRINCIPAL_KEY_ID = "openai:agent:codex"
CODEX_PRINCIPAL_ID = "openai:codex"
# KIMI_PRINCIPAL_DID must be set in production for the Kimi Code delegated
# agent to surface in the model selector and be available for prompt delegation.
KIMI_PRINCIPAL_DID = os.getenv("KIMI_PRINCIPAL_DID", "")
KIMI_PRINCIPAL_KEY_ID = "moonshot:agent:kimi-code"
KIMI_PRINCIPAL_ID = "moonshot:kimi-code"
OPENAI_COMPAT_INCLUDE_PIPELINE_EVENTS = os.getenv("OPENAI_COMPAT_INCLUDE_PIPELINE_EVENTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LEDGER_INVENTORY_DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("LEDGER_INVENTORY_DISCOVERY_TIMEOUT_SECONDS", "4.0"))
LEDGER_INVENTORY_THREAD_TIMEOUT_SECONDS = float(os.getenv("LEDGER_INVENTORY_THREAD_TIMEOUT_SECONDS", "2.0"))
LEDGER_INVENTORY_MAX_PROBE_ENTITIES = max(int(os.getenv("LEDGER_INVENTORY_MAX_PROBE_ENTITIES", "3")), 1)
HISTORY_DISCOVERY_LIMIT = max(int(os.getenv("HISTORY_DISCOVERY_LIMIT", "100")), 25)
try:
    PIPELINE_WALK_METRIC_STRIDE = max(int(os.getenv("PIPELINE_WALK_METRIC_STRIDE", "2")), 1)
except ValueError:
    PIPELINE_WALK_METRIC_STRIDE = 2
COMPOSE_SYSTEM_PROMPT_FINAL = "You are the Researcher."
COMPOSE_SYSTEM_PROMPT_DRAFT = (
    "You are the Researcher. Provide a concise draft response that reflects how you "
    "intend to answer once all sources are considered. Keep it brief."
)
EMBEDDING_MODEL_MARKERS = (
    "embed",
    "embedding",
    "bge-",
    "bge_",
    "e5-",
    "e5_",
    "nomic-embed",
    "mxbai-embed",
    "snowflake-arctic-embed",
    "gte-",
)
BASIC_AUTH_TRUE_VALUES = {"1", "true", "yes", "on"}
FRONTDOOR_AUTH_ALLOWLIST = {"/health", "/favicon.ico"}
FRONTDOOR_AUTH_ALLOWLIST_PREFIXES = ("/.well-known/", "/_vercel/", "/static/")
FRONTDOOR_AUTH_BOOT_API_ALLOWLIST = {
    "/api/wake",
    "/api/models",
    "/api/ingest/limits",
    "/api/set-agent",
    "/api/deploy-info",
    "/api/auth/session/refresh",
    "/api/onboarding/model-library",
    "/api/onboarding/model-library/select",
    "/api/onboarding/principals",
    "/api/onboarding/principals/agent/bootstrap",
    "/api/onboarding/connections",
    "/api/onboarding/status",
    "/api/onboarding/submit",
    "/api/setup-prompt",
    "/api/setup-prompt/dismiss",
    "/api/provisioning/status",
    "/api/provisioning/run",
    "/api/wallet/credential-offer",
    "/api/wallet/providers",
    "/api/wallet/link/start",
    "/api/wallet/link/complete",
    "/api/account/current",
    "/api/account/subscription",
    "/api/setup-checklist",
    "/api/surfaces",
    "/api/identity",
}
FRONTDOOR_AUTH_COOKIE = "ds_frontdoor_auth"
BACKEND_SESSION_TOKEN_COOKIE = "ds_backend_session_token"
BACKEND_REFRESH_TOKEN_COOKIE = "ds_backend_refresh_token"
FRONTDOOR_AUTH_MODE_VALUES = {"off", "basic", "form"}


def _basic_auth_required() -> bool:
    enabled_raw = str(os.getenv("BASIC_AUTH_ENABLED", "")).strip().lower()
    if enabled_raw:
        return enabled_raw in BASIC_AUTH_TRUE_VALUES

    configured_user = str(os.getenv("BASIC_AUTH_USER", "")).strip()
    configured_password = str(os.getenv("BASIC_AUTH_PASSWORD", "")).strip()
    if configured_user and configured_password:
        return True

    vercel_env = str(os.getenv("VERCEL_ENV", "")).strip().lower()
    is_vercel_runtime = bool(str(os.getenv("VERCEL", "")).strip())
    return vercel_env == "production" or is_vercel_runtime


def _frontdoor_auth_exempt(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    path = request.url.path
    if path in FRONTDOOR_AUTH_ALLOWLIST:
        return True
    if path in FRONTDOOR_AUTH_BOOT_API_ALLOWLIST:
        return True
    if path in {"/login", "/logout"}:
        return True
    if path.startswith("/login/link"):
        return True
    if path.startswith("/login/github"):
        return True
    return any(path.startswith(prefix) for prefix in FRONTDOOR_AUTH_ALLOWLIST_PREFIXES)


def _basic_auth_challenge() -> PlainTextResponse:
    return PlainTextResponse(
        "Authentication Required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Secure Area"'},
    )


async def _shared_backend_session_identity(request: Request) -> tuple[bool, str | None]:
    token = str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE, "") or "").strip()
    if not token:
        return False, None
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
                    return True, principal_did
                return False, None
    except Exception:
        return False, None
    return False, None


def _auth_base_candidates() -> list[str]:
    bases: list[str] = []
    for base in (
        str(os.getenv("DUALSUBSTRATE_AUTH_BASE") or "").strip(),
        str(settings.BACKEND_ADMIN_BASE or "").strip(),
        str(settings.API_BASE or "").strip(),
    ):
        candidate = base.rstrip("/")
        if candidate and candidate not in bases:
            bases.append(candidate)
    return bases


async def _refresh_shared_backend_session(request: Request) -> tuple[int, dict[str, Any]]:
    refresh_token = str(request.cookies.get(BACKEND_REFRESH_TOKEN_COOKIE, "") or "").strip()
    if not refresh_token:
        return 401, {"error": "authentication_required"}
    token = str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE, "") or "").strip()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            bases = _auth_base_candidates()
            for idx, base in enumerate(bases):
                headers = {
                    "x-refresh-token": refresh_token,
                    "accept": "application/json",
                    "content-type": "application/json",
                }
                if token:
                    headers["x-session-token"] = token
                resp = await client.post(
                    f"{base}/auth/session/refresh",
                    json={},
                    headers=headers,
                )
                try:
                    payload = resp.json()
                except Exception:
                    payload = {"error": "upstream_invalid_json", "text": resp.text[:1000]}
                body = payload if isinstance(payload, dict) else {"data": payload}
                if resp.status_code == 404 and idx < len(bases) - 1:
                    continue
                return resp.status_code, body
    except httpx.HTTPError as exc:
        return 503, {"error": "auth_upstream_http_error", "detail": str(exc)}
    except Exception as exc:
        return 503, {"error": "auth_upstream_unavailable", "detail": str(exc)}
    return 503, {"error": "auth_upstream_unavailable"}


class SessionTokenContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token_value = str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE, "") or "").strip()
        ctx_token = set_request_session_token(token_value)
        try:
            return await call_next(request)
        finally:
            reset_request_session_token(ctx_token)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _frontdoor_auth_exempt(request):
            return await call_next(request)

        mode = str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower()
        if mode not in FRONTDOOR_AUTH_MODE_VALUES:
            mode = "basic" if _basic_auth_required() else "off"
        if mode == "off":
            return await call_next(request)
        if mode == "form":
            if _form_auth_cookie_valid(request):
                return await call_next(request)
            shared_session_ok, principal_did = await _shared_backend_session_identity(request)
            if shared_session_ok:
                response = await call_next(request)
                cookie_domain = _cookie_domain(request)
                response.set_cookie(
                    FRONTDOOR_AUTH_COOKIE,
                    _frontdoor_cookie_signature(),
                    httponly=True,
                    samesite="lax",
                    secure=_cookie_secure(request),
                    path="/",
                    max_age=86400,
                    domain=cookie_domain,
                )
                if principal_did:
                    response.set_cookie(
                        "ds_principal_did",
                        principal_did,
                        httponly=False,
                        samesite="lax",
                        secure=_cookie_secure(request),
                        path="/",
                        max_age=86400 * 30,
                        domain=cookie_domain,
                    )
                return response
            return RedirectResponse(url=_control_plane_login_url(request), status_code=303)

        expected_user = str(os.getenv("BASIC_AUTH_USER", "")).strip()
        expected_password = str(os.getenv("BASIC_AUTH_PASSWORD", "")).strip()
        if not expected_user or not expected_password:
            return PlainTextResponse(
                "Basic auth is enabled but not configured",
                status_code=503,
            )

        auth_header = request.headers.get("authorization") or ""
        if not auth_header.lower().startswith("basic "):
            return _basic_auth_challenge()

        encoded = auth_header.split(" ", 1)[1].strip()
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return _basic_auth_challenge()

        if ":" not in decoded:
            return _basic_auth_challenge()

        provided_user, provided_password = decoded.split(":", 1)
        user_ok = hmac.compare_digest(provided_user, expected_user)
        password_ok = hmac.compare_digest(provided_password, expected_password)
        if user_ok and password_ok:
            return await call_next(request)
        return _basic_auth_challenge()


def _frontdoor_cookie_signature() -> str:
    secret = str(os.getenv("FASTHTML_SECRET_KEY", ""))
    user = str(os.getenv("BASIC_AUTH_USER", "")).strip()
    password = str(os.getenv("BASIC_AUTH_PASSWORD", "")).strip()
    payload = f"{user}:{password}:frontdoor".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _form_auth_cookie_valid(request: Request) -> bool:
    provided = str(request.cookies.get(FRONTDOOR_AUTH_COOKIE, "")).strip()
    if not provided:
        return False
    expected = _frontdoor_cookie_signature()
    return hmac.compare_digest(provided, expected)


def _credentials_match(user: str, password: str) -> bool:
    expected_user = str(os.getenv("BASIC_AUTH_USER", "")).strip()
    expected_password = str(os.getenv("BASIC_AUTH_PASSWORD", "")).strip()
    if not expected_user or not expected_password:
        return False
    return hmac.compare_digest(user, expected_user) and hmac.compare_digest(password, expected_password)


def _safe_next_path(value: str) -> str:
    next_path = (value or "/").strip() or "/"
    if not next_path.startswith("/"):
        return "/"
    if next_path.startswith("//"):
        return "/"
    return next_path


def _cookie_secure(request: Request) -> bool:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded_proto == "https" or request.url.scheme == "https"


def _request_host(request: Request) -> str:
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = forwarded_host or str(getattr(request.url, "hostname", "") or "").strip()
    if ":" in host:
        host = host.split(":", 1)[0].strip()
    return host or os.getenv("DEFAULT_HOST", "")


def _cookie_domain(request: Request) -> str | None:
    configured = str(os.getenv("DUALSUBSTRATE_COOKIE_DOMAIN") or "").strip()
    if configured:
        return configured
    host = _request_host(request).lower()
    base_domain = os.getenv("BASE_DOMAIN", "").strip().lower()
    if base_domain and (host == base_domain or host.endswith("." + base_domain)):
        return "." + base_domain
    return ""
    return None


def _request_origin(request: Request) -> str:
    parsed = urlparse(str(request.url))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin


def _surface_return_url(request: Request) -> str:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    scheme = forwarded_proto or request.url.scheme
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = forwarded_host or str(request.headers.get("host") or "").strip()
    if not host:
        host = str(getattr(request.url, "netloc", "") or os.getenv("DEFAULT_CHAT_HOST", "")).strip()
    path = str(request.url.path or "/")
    query = str(request.url.query or "").strip()
    return f"{scheme}://{host}{path}{('?' + query) if query else ''}"


def _control_plane_login_url(request: Request, next_url: str | None = None) -> str:
    control_plane_base = settings.CONTROL_PLANE_BASE.rstrip("/") or os.getenv("CONTROL_PLANE_BASE", "")
    return_target = str(next_url or "").strip() or _surface_return_url(request)
    return f"{control_plane_base}/login?next={quote(return_target, safe='')}"


def _middleware_session_headers(request: Request) -> dict[str, str]:
    token = str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE) or "").strip()
    session_id = str(request.cookies.get("ds_session") or DEFAULT_SESSION_ID).strip() or DEFAULT_SESSION_ID
    headers = {"accept": "application/json", "cookie": f"ds_session={session_id}"}
    if token:
        headers["x-session-token"] = token
        headers["cookie"] = f"{headers['cookie']}; {BACKEND_SESSION_TOKEN_COOKIE}={token}"
    return headers


async def _fetch_middleware_identity_card(request: Request) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{settings.API_BASE.rstrip('/')}/api/auth/identity_card",
                headers=_middleware_session_headers(request),
            )
            if resp.status_code >= 400:
                return None
            body = resp.json()
            return body if isinstance(body, dict) else None
    except Exception:
        return None


async def _post_middleware_passthrough(
    request: Request,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float = 20.0,
) -> tuple[int, dict[str, Any], httpx.Headers]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{settings.API_BASE.rstrip('/')}{path}",
                json=payload,
                headers=_middleware_session_headers(request),
            )
    except httpx.HTTPError as exc:
        return 503, {"error": "middleware_http_error", "detail": str(exc)}, httpx.Headers()
    except Exception as exc:
        return 503, {"error": "middleware_unavailable", "detail": str(exc)}, httpx.Headers()

    try:
        body = resp.json()
        payload_body = body if isinstance(body, dict) else {"data": body}
    except Exception:
        payload_body = {"error": "upstream_invalid_json", "text": resp.text[:1000]}
    return resp.status_code, payload_body, resp.headers


async def _post_middleware_json(
    request: Request,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{settings.API_BASE.rstrip('/')}{path}",
                json=payload,
                headers=_middleware_session_headers(request),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if resp.status_code >= 400:
        detail: Any
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        if resp.status_code == 401:
            merged_detail: dict[str, Any] = {"login_url": _control_plane_login_url(request)}
            if isinstance(detail, dict):
                merged_detail = {**detail, **merged_detail}
            else:
                merged_detail["upstream_detail"] = detail
            raise HTTPException(status_code=resp.status_code, detail=merged_detail)
        raise HTTPException(status_code=resp.status_code, detail=detail)

    try:
        body = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="middleware_invalid_json") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="middleware_invalid_response")
    return body


async def _get_middleware_json(
    request: Request,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{settings.API_BASE.rstrip('/')}{path}",
                params=params,
                headers=_middleware_session_headers(request),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if resp.status_code >= 400:
        detail: Any
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        if resp.status_code == 401:
            merged_detail: dict[str, Any] = {"login_url": _control_plane_login_url(request)}
            if isinstance(detail, dict):
                merged_detail = {**detail, **merged_detail}
            else:
                merged_detail["upstream_detail"] = detail
            raise HTTPException(status_code=resp.status_code, detail=merged_detail)
        raise HTTPException(status_code=resp.status_code, detail=detail)

    try:
        body = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="middleware_invalid_json") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="middleware_invalid_response")
    return body


async def _verified_model_auth_context(request: Request) -> dict[str, Any]:
    middleware_identity_card = await _fetch_middleware_identity_card(request)
    if isinstance(middleware_identity_card, dict):
        identity_raw = middleware_identity_card.get("identity_vc")
        usage_raw = middleware_identity_card.get("usage_stats")
        eq9_raw = middleware_identity_card.get("eq9")
        return {
            "identity_vc": identity_raw if isinstance(identity_raw, dict) else {},
            "usage_stats": usage_raw if isinstance(usage_raw, dict) else {},
            "eq9": eq9_raw if isinstance(eq9_raw, dict) else {},
        }
    return {
        "identity_vc": {},
        "usage_stats": {},
        "eq9": {},
    }


def _runtime_did_web_host(request: Request) -> str:
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
    host = forwarded_host or str(getattr(request.url, "hostname", "") or "").strip().lower()
    return host or os.getenv("DEFAULT_HOST", "")


def _canonical_runtime_subject(*, host: str, entity_type: str, entity_id: str) -> str:
    entity_key = str(entity_type or "").strip().lower() or "resource"
    identifier = re.sub(r"[^a-z0-9]+", "-", str(entity_id or entity_key).strip().lower()).strip("-") or entity_key
    if entity_key in {"ledger", "surface", "provider", "binding", "relationship", "principal"}:
        suffix = "principals" if entity_key == "principal" else (f"{entity_key}s" if entity_key != "binding" else "bindings")
        return f"did:web:{host}:{suffix}:{identifier}"
    return f"did:web:{host}:resources:{identifier}"


def _build_runtime_identity_metadata(request: Request, *, ledger_id: str, entity: str, model_auth_context: dict[str, Any]) -> dict[str, Any]:
    identity_vc_raw = model_auth_context.get("identity_vc")
    identity_vc: dict[str, Any] = identity_vc_raw if isinstance(identity_vc_raw, dict) else {}
    host = _runtime_did_web_host(request)
    principal_did = str(identity_vc.get("principal_did") or "").strip()
    principal_subject = str(identity_vc.get("canonical_subject") or principal_did).strip()
    runtime_identity: dict[str, Any] = {
        "ledger_id": ledger_id,
        "runtime_namespace": entity,
        "ledger_canonical_subject": _canonical_runtime_subject(host=host, entity_type="ledger", entity_id=ledger_id),
    }
    if principal_subject:
        runtime_identity["principal_canonical_subject"] = principal_subject
        runtime_identity["principal_canonical_subject_source"] = str(identity_vc.get("canonical_subject_source") or "principal_did").strip() or "principal_did"
    if principal_did:
        runtime_identity["principal_did"] = principal_did
    vc_refs = {
        "credential_ref": str(identity_vc.get("credential_ref") or "").strip(),
        "standing_envelope_ref": str(identity_vc.get("standing_envelope_ref") or "").strip(),
        "wallet_did": str(identity_vc.get("wallet_did") or "").strip(),
        "wallet_binding_ref": str(identity_vc.get("wallet_binding_ref") or "").strip(),
        "issuer_did": str(identity_vc.get("issuer_did") or "").strip(),
    }
    vc_refs = {key: value for key, value in vc_refs.items() if value}
    if vc_refs:
        runtime_identity["vc_refs"] = vc_refs
    return runtime_identity


def _identity_card_ui_model(identity: dict[str, Any], eq9: dict[str, Any]) -> dict[str, Any]:
    verification_state = str(identity.get("verification_state") or "unverified").strip().lower() or "unverified"
    verification_reason = str(identity.get("reason_code") or "verification_unavailable").strip() or "verification_unavailable"
    auth_method = str(identity.get("auth_method") or "").strip() or "unknown"
    trust_class = str(eq9.get("trust_class") or "").strip()
    posture_class = str(eq9.get("eq9_posture_class") or "").strip()
    posture_reason = str(eq9.get("reason_code") or "").strip() or verification_reason
    failed_eq = str(eq9.get("failed_eq") or "").strip()
    credential_ref = str(identity.get("credential_ref") or "").strip()
    wallet_provider = str(identity.get("wallet_provider") or "").strip()
    wallet_did = str(identity.get("wallet_did") or "").strip()
    activation_state = str(identity.get("activation_state") or "").strip().lower()
    ledger_id = str(identity.get("ledger_id") or "").strip()
    ledger_access_ready = bool(identity.get("ledger_access_ready"))
    repairs_raw = eq9.get("repair_actions")
    repair_actions = [
        str(item).strip()
        for item in repairs_raw
        if isinstance(item, str) and str(item).strip()
    ] if isinstance(repairs_raw, list) else []

    blocked_reasons = {
        "linked_identity_required",
        "oauth_not_configured",
        "oauth_exchange_failed",
        "oauth_identity_fetch_failed",
        "oauth_authorization_denied",
        "principal_link_not_found",
        "principal_link_conflict",
        "policy_blocked",
    }
    posture_state = "unknown"
    panel_state = "unknown"
    headline = "Trust summary unavailable"
    verification_copy = "Source authenticity summary is not available yet."
    posture_copy = "Policy posture has not been evaluated for this session yet."
    repair_copy = "Retry this action or contact operator if the state does not update."
    wallet_copy = "No wallet-backed credential is bound yet."
    provisioning_copy = "Ledger provisioning status is not available yet."

    if verification_state == "verified":
        verification_copy = f"Signed in with {auth_method}. Source authenticity is verified."
        if credential_ref:
            if wallet_provider:
                wallet_copy = f"Wallet-backed authority is present via {wallet_provider} ({credential_ref})."
            else:
                wallet_copy = f"Wallet-backed authority is present ({credential_ref})."
        elif wallet_did:
            wallet_copy = f"Wallet identity is linked at {wallet_did}, but no credential reference is recorded yet."
        elif bool(identity.get("wallet_capable")):
            wallet_copy = "Principal is wallet-capable, but no wallet credential is bound yet."
        if ledger_access_ready:
            provisioning_copy = (
                f"Provisioned ledger access is ready on {ledger_id}."
                if ledger_id
                else "Provisioned ledger access is ready."
            )
        elif activation_state == "pending_provisioning":
            provisioning_copy = "Wallet proof is complete. Ledger provisioning and activation are still pending."
        elif activation_state == "pending_wallet_proof":
            provisioning_copy = "Profile approval is in place, but wallet proof must complete before provisioning can continue."
        elif activation_state == "awaiting_approval":
            provisioning_copy = "Operator approval is still required before ledger provisioning can begin."
        elif activation_state in {"blocked", "disabled"}:
            provisioning_copy = "Ledger provisioning is blocked until operator intervention clears the principal state."
        if posture_reason in blocked_reasons:
            panel_state = "blocked"
            posture_state = "blocked"
            headline = "Verified identity, blocked posture"
            posture_copy = "Identity is verified, but current policy posture blocks privileged use."
        elif failed_eq or repair_actions or trust_class in {"T0", "T1"} or posture_class in {"P0", "P1"}:
            panel_state = "degraded"
            posture_state = "degraded"
            headline = "Verified identity, limited trust posture"
            posture_copy = "Identity is verified, but posture signals indicate limited trust for this session."
        elif trust_class or posture_class or posture_reason == "baseline_satisfied":
            panel_state = "verified"
            posture_state = "verified"
            headline = "Verified identity and posture"
            posture_copy = "Source authenticity and current posture are aligned for normal use."
        else:
            headline = "Verified identity, posture pending"
            verification_copy = f"Signed in with {auth_method}. Source authenticity is verified."
            posture_copy = "Identity is verified, but posture evidence is still loading."
    elif verification_reason in blocked_reasons:
        panel_state = "blocked"
        posture_state = "blocked"
        headline = "Verification blocked"
        verification_copy = "This session cannot establish source authenticity through the configured sign-in path."
        posture_copy = "Policy posture is blocked until identity bootstrap succeeds."
        if bool(identity.get("wallet_capable")):
            wallet_copy = "Wallet-capable principal detected, but binding evidence is unavailable in the blocked state."
    else:
        panel_state = "unverified"
        headline = "Verification required"
        verification_copy = "Identity is not verified. No privileged access is granted."
        posture_copy = "Policy posture should not be treated as authoritative until verification succeeds."
        provisioning_copy = "Ledger access remains unavailable until verification and provisioning both complete."
        if bool(identity.get("wallet_capable")):
            wallet_copy = "Wallet-capable principal detected, but sign-in has not established authenticity yet."

    if panel_state == "blocked":
        repair_copy = "Retry the configured sign-in method or contact operator."
    elif repair_actions:
        repair_copy = "Next steps: " + "; ".join(repair_actions[:3])
    elif panel_state == "degraded":
        repair_copy = "Review posture diagnostics before relying on this session for privileged actions."
    elif panel_state == "verified":
        repair_copy = "No repair action required."

    return {
        "panel_label": "Trust panel",
        "panel_state": panel_state,
        "posture_state": posture_state,
        "headline": headline,
        "verification_copy": verification_copy,
        "posture_copy": posture_copy,
        "repair_copy": repair_copy,
        "wallet_copy": wallet_copy,
        "provisioning_copy": provisioning_copy,
    }


async def _principal_upsert(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    headers = {"content-type": "application/json"}
    base = str(settings.API_BASE or "").rstrip("/")
    if not base:
        return 503, {"error": "principal_registry_unreachable"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{base}/api/principals", json=payload, headers=headers)
        try:
            data = resp.json()
            body = data if isinstance(data, dict) else {"data": data}
        except Exception:
            body = {"error": "upstream_invalid_json", "text": resp.text[:1000]}
    return resp.status_code, body


async def _principal_registry_post(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    headers = {"content-type": "application/json"}
    base = str(settings.API_BASE or "").rstrip("/")
    if not base:
        return 503, {"error": "principal_registry_unreachable"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{base}{path}", json=payload, headers=headers)
        try:
            data = resp.json()
            body = data if isinstance(data, dict) else {"data": data}
        except Exception:
            body = {"error": "upstream_invalid_json", "text": resp.text[:1000]}
    return resp.status_code, body


async def _principal_registry_get(path: str) -> tuple[int, dict[str, Any]]:
    headers = {"accept": "application/json"}
    base = str(settings.API_BASE or "").rstrip("/")
    if not base:
        return 503, {"error": "principal_registry_unreachable"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{base}{path}", headers=headers)
        try:
            data = resp.json()
            body = data if isinstance(data, dict) else {"data": data}
        except Exception:
            body = {"error": "upstream_invalid_json", "text": resp.text[:1000]}
    return resp.status_code, body


def _set_auth_cookies(
    *,
    request: Request,
    response: RedirectResponse | JSONResponse,
    token: str,
    refresh_token: str,
    principal_did: str,
) -> RedirectResponse | JSONResponse:
    cookie_domain = _cookie_domain(request)
    response.set_cookie(
        BACKEND_SESSION_TOKEN_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
        max_age=3600,
        domain=cookie_domain,
    )
    response.set_cookie(
        BACKEND_REFRESH_TOKEN_COOKIE,
        refresh_token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
        max_age=86400,
        domain=cookie_domain,
    )
    response.set_cookie(
        FRONTDOOR_AUTH_COOKIE,
        _frontdoor_cookie_signature(),
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
        max_age=86400,
        domain=cookie_domain,
    )
    response.set_cookie(
        "ds_principal_did",
        principal_did,
        httponly=False,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
        max_age=86400 * 30,
        domain=cookie_domain,
    )
    return response


def _clear_auth_cookies(*, request: Request, response: RedirectResponse | JSONResponse) -> RedirectResponse | JSONResponse:
    cookie_domain = _cookie_domain(request)
    response.delete_cookie(FRONTDOOR_AUTH_COOKIE, path="/", domain=cookie_domain)
    response.delete_cookie(BACKEND_SESSION_TOKEN_COOKIE, path="/", domain=cookie_domain)
    response.delete_cookie(BACKEND_REFRESH_TOKEN_COOKIE, path="/", domain=cookie_domain)
    response.delete_cookie("ds_principal_did", path="/", domain=cookie_domain)
    return response


def _principal_parts(principal_did: str) -> tuple[str, str]:
    value = str(principal_did or "").strip()
    if value.startswith("did:"):
        method_specific = value[len("did:") :].strip()
        if method_specific:
            return "did", method_specific
    return "principal", value or "unknown"


def _principal_slug_from_canonical_subject(canonical_subject: str) -> str:
    subject = str(canonical_subject or "").strip()
    if not subject:
        return ""
    tail = subject.rsplit(":", 1)[-1].strip()
    return tail or ""


def _resolved_human_principal_identity(
    *,
    identity_vc: dict[str, Any] | None,
    fallback_principal_did: str = "",
    fallback_principal_id: str = "",
    fallback_principal_type: str = "user",
    fallback_display_name: str = "",
) -> dict[str, str]:
    identity = identity_vc if isinstance(identity_vc, dict) else {}
    principal_did = str(identity.get("principal_did") or fallback_principal_did or "").strip()
    canonical_subject = str(identity.get("canonical_subject") or "").strip()
    principal_display_name = str(
        identity.get("principal_display_name")
        or identity.get("display_name")
        or fallback_display_name
        or ""
    ).strip()
    principal_type = str(fallback_principal_type or "user").strip() or "user"
    principal_id = str(
        _principal_slug_from_canonical_subject(canonical_subject)
        or fallback_principal_id
        or ""
    ).strip()
    if not principal_id and principal_did:
        _, principal_id = _principal_parts(principal_did)
    if not principal_display_name and principal_id:
        principal_display_name = principal_id.replace(":", "/").replace("-", " ").strip()
    return {
        "principal_did": principal_did,
        "principal_id": principal_id,
        "principal_type": principal_type,
        "principal_display_name": principal_display_name,
        "canonical_subject": canonical_subject,
    }


def _stamp_authenticated_session(
    *,
    request: Request,
    principal_did: str,
    auth_session: dict[str, Any] | None,
    auth_method: str,
) -> None:
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = dict(get_session(session_id))
    auth_session = auth_session if isinstance(auth_session, dict) else {}
    resolved_identity = _resolved_human_principal_identity(
        identity_vc=auth_session,
        fallback_principal_did=principal_did,
        fallback_principal_type="user",
    )
    principal_type = resolved_identity.get("principal_type") or "user"
    principal_id = resolved_identity.get("principal_id") or _principal_parts(principal_did)[1]
    context_id = str(
        session.get("context_id")
        or settings.FRONTEND_CONTEXT_ID
        or "ctx:frontend:local"
    ).strip()

    session["principal_did"] = principal_did
    session["principal_type"] = principal_type
    session["principal_id"] = principal_id
    session["principal_display_name"] = resolved_identity.get("principal_display_name") or ""
    session["principal_canonical_subject"] = resolved_identity.get("canonical_subject") or ""
    session["tenant_id"] = str(session.get("tenant_id") or settings.FRONTEND_TENANT_ID or "").strip()
    session["context_id"] = context_id
    session["auth_method"] = auth_method

    session_jti = str(auth_session.get("jti") or auth_session.get("session_jti") or "").strip()
    if session_jti:
        session["session_jti"] = session_jti

    update_session(session_id, session)


async def _sync_session_to_principal_provisioning(
    *,
    request: Request,
    principal_did: str,
) -> None:
    did = str(principal_did or "").strip()
    if not did:
        return
    encoded_did = quote(did, safe="")
    status_code, body = await _principal_registry_get(f"/api/principals/{encoded_did}/provisioning")
    if status_code >= 400 or not isinstance(body, dict):
        return
    provisioning = body.get("provisioning") if isinstance(body.get("provisioning"), dict) else {}
    if not provisioning:
        return
    if not bool(provisioning.get("ledger_access_ready")):
        return

    ledger_id = str(provisioning.get("ledger_id") or "").strip()
    if not ledger_id:
        return
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = dict(get_session(session_id))
    tenant_id = str(provisioning.get("tenant_id") or "").strip()
    session["ledger_id"] = ledger_id
    session["entity"] = build_entity_namespace(ledger_id, session_id)
    if tenant_id:
        session["tenant_id"] = tenant_id
    update_session(session_id, session)


async def _complete_github_login(
    *,
    request: Request,
    next_path: str,
    principal_did: str,
    principal_key_ref: str,
) -> RedirectResponse:
    cookie_domain = _cookie_domain(request)
    response = RedirectResponse(url=next_path, status_code=303)
    response.set_cookie(
        "ds_principal_did",
        principal_did,
        httponly=False,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
        max_age=86400 * 30,
        domain=cookie_domain,
    )
    try:
        request.session["linked_principal_did"] = principal_did
        request.session["linked_principal_key_ref"] = principal_key_ref
        request.session["linked_auth_source"] = "github"
    except Exception:
        pass
    return response


def _strip_control_protocol(text: str) -> str:
    if not text:
        return ""
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append(raw_line)
            continue
        lowered = line.lower()
        if lowered.startswith("generate & classify -> save"):
            continue
        if lowered.startswith("coord relevant") or lowered.startswith("coord not relevant"):
            continue
        if lowered.startswith("resolve:"):
            continue
        if lowered.startswith("answer:"):
            remainder = line.split(":", 1)[1].strip()
            if remainder:
                cleaned_lines.append(remainder)
            continue
        cleaned_lines.append(raw_line)
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _resolve_s_mode(payload: dict[str, Any]) -> str:
    raw_meta = payload.get("metadata")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    for key in ("s_mode", "pipeline_mode", "latency_mode"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            mode = raw.strip().lower()
            if mode in {"s1", "s2"}:
                return mode
    for key in ("s_mode", "pipeline_mode", "latency_mode"):
        raw = meta.get(key)
        if isinstance(raw, str) and raw.strip():
            mode = raw.strip().lower()
            if mode in {"s1", "s2"}:
                return mode
    return "s2" if OPENAI_COMPAT_S_MODE == "s2" else "s1"


def _requested_s_mode(payload: dict[str, Any]) -> str | None:
    raw_meta = payload.get("metadata")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    for source in (payload, meta):
        for key in ("s_mode", "pipeline_mode", "latency_mode"):
            raw = source.get(key)
            if isinstance(raw, str) and raw.strip():
                mode = raw.strip().lower()
                if mode in {"s1", "s2"}:
                    return mode
    return None


def _claim_from_payload(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    for nested_key in ("auth", "auth_claims", "auth_context", "claims"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            nested_value = nested.get(key)
            if isinstance(nested_value, str) and nested_value.strip():
                return nested_value.strip()
    return ""


def _openai_override_authorized(
    *,
    request: Request,
    payload: dict[str, Any],
) -> tuple[bool, dict[str, str]]:
    claims = {
        key: _claim_from_payload(payload, key)
        for key in ("principal_did", "principal_key_id", "session_jti", "context_id")
    }
    if not claims["principal_did"]:
        header = request.headers.get("x-principal-did") or request.headers.get("x-did")
        claims["principal_did"] = str(header or "").strip()
    if not claims["session_jti"]:
        header = request.headers.get("x-session-jti") or request.headers.get("x-auth-jti")
        claims["session_jti"] = str(header or "").strip()
    if not claims["principal_key_id"]:
        header = request.headers.get("x-principal-key-id") or request.headers.get("x-key-id")
        claims["principal_key_id"] = str(header or "").strip()
    if not claims["context_id"]:
        claims["context_id"] = str(request.headers.get("x-context-id") or "").strip()

    if OPENAI_COMPAT_POLICY_ALLOW_CLIENT_OVERRIDES:
        return True, claims

    auth_header = str(request.headers.get("authorization") or "").strip()
    token_present = auth_header.lower().startswith("bearer ") and bool(auth_header[7:].strip())
    return bool(token_present and claims["principal_did"] and claims["session_jti"]), claims


def _apply_openai_policy_controls(
    *,
    payload: dict[str, Any],
    override_authorized: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    effective = dict(payload)
    policy_rejections: list[str] = []

    requested_enable_ledger_raw = payload.get("enable_ledger")
    requested_enable_ledger = bool(
        requested_enable_ledger_raw if requested_enable_ledger_raw is not None else True
    )
    effective_enable_ledger = requested_enable_ledger
    if requested_enable_ledger_raw is not None and not requested_enable_ledger and not override_authorized:
        effective_enable_ledger = True
        policy_rejections.append("enable_ledger_disabled_by_client")
    effective["enable_ledger"] = effective_enable_ledger

    requested_s_mode = _requested_s_mode(payload)
    effective_s_mode = _resolve_s_mode(payload)
    if requested_s_mode == "s1" and not override_authorized:
        effective_s_mode = "s2"
        policy_rejections.append("s1_mode_requested_by_client")
    effective["s_mode"] = effective_s_mode

    policy_controls = {
        "override_authorized": override_authorized,
        "requested_enable_ledger": requested_enable_ledger,
        "effective_enable_ledger": effective_enable_ledger,
        "requested_s_mode": requested_s_mode,
        "effective_s_mode": effective_s_mode,
        "rejected_overrides": policy_rejections,
    }
    raw_meta = effective.get("metadata")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta["policy_controls"] = policy_controls
    effective["metadata"] = meta
    return effective, policy_controls


def _s_mode_to_dial(mode: str) -> int:
    # S1 prioritizes fast-path caps, S2 allows deeper context/walk behavior.
    return 3 if mode == "s1" else 2


def _compact_pipeline_event(event: dict[str, Any]) -> dict[str, Any]:
    etype = str(event.get("type") or "")
    if etype == "walk_metric_delta":
        payload_raw = event.get("payload")
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        return {
            "type": etype,
            "payload": {
                "hop": payload.get("hop"),
                "coord": payload.get("coord"),
                "law": payload.get("law"),
                "drift": payload.get("drift"),
                "score": payload.get("score"),
            },
        }
    if etype == "meta_patch":
        compacted: dict[str, Any] = {
            "type": etype,
            "kind": event.get("kind"),
            "status": event.get("status"),
            "reason": event.get("reason"),
            "eq9_eval_source": event.get("eq9_eval_source"),
            "eq9_eval_pending": event.get("eq9_eval_pending"),
        }
        if "patch_status" in event:
            compacted["patch_status"] = event["patch_status"]
        if "checksum_336_pass" in event:
            compacted["checksum_336_pass"] = event["checksum_336_pass"]
        return compacted
    if etype in {"context_meta", "decision_trace", "hop_enrich", "grounding_override", "anchor_resolution", "walk_stop", "candidate_trace", "autonomy_decision"}:
        return event
    return {"type": etype}


async def _run_openai_via_middleware_orchestrator(
    *,
    base_payload: dict[str, Any],
    model: str,
    message: str,
    history: list[dict[str, str]],
    session_id: str,
) -> dict[str, Any]:
    s_mode = _resolve_s_mode(base_payload)
    include_pipeline_events = (
        bool(base_payload.get("include_pipeline_events"))
        if isinstance(base_payload.get("include_pipeline_events"), bool)
        else OPENAI_COMPAT_INCLUDE_PIPELINE_EVENTS
    )
    payload: dict[str, Any] = {
        "message": message,
        "history": history[:-1] if history else [],
        "provider": model,
        "agent": model,
        "model": model,
        "s_mode": s_mode,
        "session_id": session_id,
        "enable_ledger": bool(base_payload.get("enable_ledger", True)),
        "backend_stream": False,
        "eq9_control_dial": _s_mode_to_dial(s_mode),
    }
    for key in ("principal_did", "principal_key_id", "session_jti", "context_id"):
        value = base_payload.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value.strip()
    policy_controls = base_payload.get("policy_controls")
    if isinstance(policy_controls, dict):
        payload["policy_controls"] = policy_controls
    for key in ("eligible_for_search", "search_used"):
        value = base_payload.get(key)
        if isinstance(value, bool):
            payload[key] = value
    context_coords = base_payload.get("context_coords")
    if isinstance(context_coords, list):
        payload["context_coords"] = [
            str(coord).strip()
            for coord in context_coords
            if isinstance(coord, str) and str(coord).strip()
        ]
    if include_pipeline_events:
        payload["include_post_introspect_snapshot"] = True

    url = f"{settings.API_BASE.rstrip('/')}/api/chat/smart_stream"
    token_parts: list[str] = []
    meta_event: dict[str, Any] = {}
    policy_envelope_event: dict[str, Any] = {}
    pre_emission_deny_seen = False
    pipeline_events: list[dict[str, Any]] = []
    walk_metric_seen = 0
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, json=payload, headers=api.headers) as resp:
            if resp.status_code >= 400:
                detail = await resp.aread()
                raise RuntimeError(detail.decode("utf-8", errors="ignore") or "Middleware smart_stream failed")
            async for line in resp.aiter_lines():
                row = (line or "").strip()
                if not row:
                    continue
                try:
                    event = json.loads(row)
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue
                etype = event.get("type")
                if etype == "token" and isinstance(event.get("content"), str):
                    token_parts.append(event["content"])
                elif etype == "pre_emission_deny":
                    pre_emission_deny_seen = True
                elif etype == "policy_envelope" and isinstance(event.get("payload"), dict):
                    policy_envelope_event = dict(event.get("payload") or {})
                    if str(policy_envelope_event.get("policy_decision") or "").strip().lower() == "deny":
                        pre_emission_deny_seen = True
                elif etype == "meta":
                    meta_event = event
                elif etype in {
                    "context_meta",
                    "decision_trace",
                    "hop_enrich",
                    "grounding_override",
                    "anchor_resolution",
                    "walk_metric_delta",
                    "walk_stop",
                    "meta_patch",
                    "candidate_trace",
                    "autonomy_decision",
                    "policy_envelope",
                    "pre_emission_deny",
                } and include_pipeline_events:
                    if etype == "walk_metric_delta":
                        walk_metric_seen += 1
                        if (walk_metric_seen - 1) % PIPELINE_WALK_METRIC_STRIDE != 0:
                            continue
                    pipeline_events.append(_compact_pipeline_event(event))
                    if len(pipeline_events) > MAX_PIPELINE_EVENTS:
                        pipeline_events = pipeline_events[-MAX_PIPELINE_EVENTS:]

    assistant_text = _strip_control_protocol("".join(token_parts).strip())
    if not assistant_text and isinstance(meta_event.get("metadata"), dict):
        meta_payload = meta_event["metadata"]
        if isinstance(meta_payload.get("assistant_reply"), str):
            assistant_text = _strip_control_protocol(meta_payload["assistant_reply"])
        elif isinstance(meta_payload.get("content"), str):
            assistant_text = _strip_control_protocol(meta_payload["content"])

    posture_policy_meta = meta_event.get("posture_policy") if isinstance(meta_event.get("posture_policy"), dict) else {}
    posture_policy = dict(policy_envelope_event) if isinstance(policy_envelope_event, dict) and policy_envelope_event else {}
    if isinstance(posture_policy_meta, dict):
        posture_policy = {**posture_policy, **posture_policy_meta}

    deny_detected = pre_emission_deny_seen or str(posture_policy.get("policy_decision") or "").strip().lower() == "deny"
    if deny_detected:
        reason_code = str(posture_policy.get("reason_code") or "policy_blocked").strip() or "policy_blocked"
        trust_class = str(posture_policy.get("trust_class") or "").strip()
        eq9_posture_class = str(posture_policy.get("eq9_posture_class") or "").strip()
        failed_eq = str(posture_policy.get("failed_eq") or "").strip()
        repairs_raw = posture_policy.get("repair_actions")
        repairs: list[str] = []
        if isinstance(repairs_raw, list):
            for item in repairs_raw[:2]:
                if isinstance(item, str) and item.strip():
                    repairs.append(item.strip())
        lines = [
            "Response blocked by policy gate.",
            f"- reason_code={reason_code}",
        ]
        if failed_eq:
            lines.append(f"- failed_eq={failed_eq}")
        if trust_class:
            lines.append(f"- trust_class={trust_class}")
        if eq9_posture_class:
            lines.append(f"- eq9_posture_class={eq9_posture_class}")
        if repairs:
            lines.append("- repair_actions=" + "; ".join(repairs))
        assistant_text = "\n".join(lines)

    usage_raw = meta_event.get("tokens")
    usage = usage_raw if isinstance(usage_raw, dict) else {}
    prompt_tokens = int(usage.get("prompt") or usage.get("input") or 0)
    completion_tokens = int(usage.get("completion") or usage.get("output") or max(len(assistant_text.split()), 0))
    total_tokens = int(usage.get("total") or (prompt_tokens + completion_tokens))

    result = {
        "assistant_text": assistant_text,
        "response_model": str(meta_event.get("model") or model),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "governance": {
            "policy_controls": meta_event.get("policy_controls")
            if isinstance(meta_event.get("policy_controls"), dict)
            else {},
            "governance_path": meta_event.get("governance_path")
            if isinstance(meta_event.get("governance_path"), dict)
            else {},
            "latency_policy": (
                (meta_event.get("latency_diagnostics") or {}).get("policy")
                if isinstance(meta_event.get("latency_diagnostics"), dict)
                else {}
            ),
            "posture_policy": posture_policy if isinstance(posture_policy, dict) else {},
        },
    }
    if include_pipeline_events:
        patch_summary = next(
            (
                e
                for e in reversed(pipeline_events)
                if e.get("type") == "meta_patch" and "patch_status" in e
            ),
            None,
        )
        governance_event = {
            "type": "governance_summary",
            "policy_controls": result["governance"].get("policy_controls"),
            "governance_path": result["governance"].get("governance_path"),
            "latency_policy": result["governance"].get("latency_policy"),
        }
        if patch_summary:
            governance_event["patch_status"] = patch_summary.get("patch_status")
            governance_event["checksum_336_pass"] = patch_summary.get("checksum_336_pass")
        pipeline_events.append(governance_event)
        if len(pipeline_events) > MAX_PIPELINE_EVENTS:
            pipeline_events = pipeline_events[-MAX_PIPELINE_EVENTS:]
        result["pipeline_events"] = pipeline_events
    return result


async def _prepare_middleware_chat_proxy(
    request: Request,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    session_id = str(
        payload.get("session_id")
        or request.cookies.get("ds_session")
        or DEFAULT_SESSION_ID
    ).strip() or DEFAULT_SESSION_ID
    session = get_session(session_id)
    if _demo_offline_blocks_provider(session, payload):
        raise HTTPException(status_code=503, detail="demo_offline")
    ledger_id, entity, session = _resolve_session_scope(session_id, session)
    api.set_ledger(ledger_id)

    forwarded = dict(payload or {})
    forwarded["session_id"] = session_id
    forwarded["entity"] = entity
    forwarded["ledger_id"] = ledger_id
    forwarded.setdefault("enable_ledger", True)
    forwarded.setdefault("history", [])

    payload_meta_raw = forwarded.get("metadata")
    payload_meta: dict[str, Any] = payload_meta_raw if isinstance(payload_meta_raw, dict) else {}
    model_auth_context_raw = await _verified_model_auth_context(request)
    model_auth_context: dict[str, Any] = model_auth_context_raw if isinstance(model_auth_context_raw, dict) else {}
    payload_meta["model_auth_context"] = model_auth_context
    resolved_identity = _resolved_human_principal_identity(
        identity_vc=model_auth_context.get("identity_vc") if isinstance(model_auth_context.get("identity_vc"), dict) else None,
        fallback_principal_did=str(
            request.session.get("principal_did")
            or session.get("principal_did")
            or ""
        ).strip(),
        fallback_principal_id=str(
            request.session.get("principal_id")
            or session.get("principal_id")
            or settings.FRONTEND_PRINCIPAL_ID
            or ""
        ).strip(),
        fallback_principal_type=str(
            request.session.get("principal_type")
            or session.get("principal_type")
            or settings.FRONTEND_PRINCIPAL_TYPE
            or "user"
        ).strip(),
        fallback_display_name=str(
            request.session.get("principal_display_name")
            or session.get("principal_display_name")
            or ""
        ).strip(),
    )
    principal_display_name = str(resolved_identity.get("principal_display_name") or "").strip()
    if principal_display_name:
        payload_meta["principal_display_name"] = principal_display_name
    principal_id = str(resolved_identity.get("principal_id") or "").strip()
    if principal_id and "principal_display_name" not in payload_meta:
        payload_meta["principal_display_name"] = principal_id
    forwarded["metadata"] = payload_meta

    identity_vc_raw = model_auth_context.get("identity_vc")
    identity_vc: dict[str, Any] = identity_vc_raw if isinstance(identity_vc_raw, dict) else {}
    for key in ("principal_did", "session_jti", "auth_method"):
        value = identity_vc.get(key)
        if isinstance(value, str) and value.strip():
            forwarded[key] = value.strip()

    prompt_principal_mode = str(
        forwarded.pop("prompt_principal_mode", "")
        or forwarded.pop("prompt_as_principal", "")
        or ""
    ).strip().lower()
    if prompt_principal_mode in {"codex", "kimi"} and not isinstance(forwarded.get("delegated_principal"), dict):
        resolved_identity = _resolved_human_principal_identity(
            identity_vc=identity_vc,
            fallback_principal_did=str(
                forwarded.get("principal_did")
                or request.session.get("principal_did")
                or ""
            ).strip(),
            fallback_principal_id=str(
                request.session.get("principal_id")
                or session.get("principal_id")
                or settings.FRONTEND_PRINCIPAL_ID
                or ""
            ).strip(),
            fallback_principal_type="user",
            fallback_display_name=str(
                request.session.get("principal_display_name")
                or session.get("principal_display_name")
                or ""
            ).strip(),
        )
        operator_principal_did = str(
            resolved_identity.get("principal_did")
            or ""
        ).strip()
        session_jti = str(identity_vc.get("session_jti") or forwarded.get("session_jti") or "").strip()
        session_token = str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE) or "").strip()
        if not operator_principal_did or not session_jti or not session_token:
            raise HTTPException(
                status_code=400,
                detail=f"{prompt_principal_mode}_prompt_requires_authenticated_delegation_context",
            )
        operator_principal_id = str(
            resolved_identity.get("principal_id")
            or settings.FRONTEND_PRINCIPAL_ID
            or "demo-user"
        ).strip()
        if prompt_principal_mode == "codex":
            forwarded["delegated_principal"] = {
                "principal_did": CODEX_PRINCIPAL_DID,
                "principal_key_id": CODEX_PRINCIPAL_KEY_ID,
                "principal_id": CODEX_PRINCIPAL_ID,
                "principal_display_name": "openai/codex",
                "prompt_principal_display_name": "openai/codex",
                "principal_type": "agent",
                # Reuse the existing delegated middleware path and fail closed if it cannot apply.
                "explicit_cli_request": True,
                "delegation_mode": "delegated_only",
                "delegated_by_principal_did": operator_principal_did,
                "delegated_by_principal_id": operator_principal_id,
                "ledger_scope": [ledger_id],
                "surface_scope": [settings.CHAT_SURFACE_ID],
                "surface_id": settings.CHAT_SURFACE_ID,
            }
        elif prompt_principal_mode == "kimi":
            forwarded["delegated_principal"] = {
                "principal_did": KIMI_PRINCIPAL_DID,
                "principal_key_id": KIMI_PRINCIPAL_KEY_ID,
                "principal_id": KIMI_PRINCIPAL_ID,
                "principal_display_name": "Moonshot: Kimi-code",
                "prompt_principal_display_name": "Moonshot: Kimi-code",
                "principal_type": "agent",
                "explicit_cli_request": True,
                "delegation_mode": "delegated_only",
                "delegated_by_principal_did": operator_principal_did,
                "delegated_by_principal_id": operator_principal_id,
                "ledger_scope": [ledger_id],
                "surface_scope": [settings.CHAT_SURFACE_ID],
                "surface_id": settings.CHAT_SURFACE_ID,
            }

    outbound_headers = dict(api.headers)
    session_token = str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE) or "").strip()
    if session_token:
        outbound_headers["authorization"] = f"Bearer {session_token}"
    principal_did = str(forwarded.get("principal_did") or "").strip()
    session_jti = str(forwarded.get("session_jti") or "").strip()
    auth_method = str(forwarded.get("auth_method") or "").strip()
    resolved_identity = _resolved_human_principal_identity(
        identity_vc=identity_vc,
        fallback_principal_did=principal_did,
        fallback_principal_id=str(
            request.session.get("principal_id")
            or session.get("principal_id")
            or settings.FRONTEND_PRINCIPAL_ID
            or ""
        ).strip(),
        fallback_principal_type=str(
            request.session.get("principal_type")
            or session.get("principal_type")
            or settings.FRONTEND_PRINCIPAL_TYPE
            or "user"
        ).strip(),
        fallback_display_name=str(
            request.session.get("principal_display_name")
            or session.get("principal_display_name")
            or ""
        ).strip(),
    )
    if resolved_identity.get("principal_did"):
        principal_did = str(resolved_identity.get("principal_did") or "").strip()
    if principal_did:
        outbound_headers["x-principal-did"] = principal_did
    principal_id = str(resolved_identity.get("principal_id") or "").strip()
    principal_type = str(resolved_identity.get("principal_type") or "user").strip() or "user"
    if principal_id:
        outbound_headers["x-principal-id"] = principal_id
    if principal_type:
        outbound_headers["x-principal-type"] = principal_type
    if session_jti:
        outbound_headers["x-session-jti"] = session_jti
    if auth_method:
        outbound_headers["x-auth-method"] = auth_method
    if principal_did:
        request.session["principal_did"] = principal_did
        session["principal_did"] = principal_did
    if principal_id:
        request.session["principal_id"] = principal_id
        session["principal_id"] = principal_id
    request.session["principal_type"] = principal_type
    session["principal_type"] = principal_type
    principal_display_name = str(resolved_identity.get("principal_display_name") or "").strip()
    if principal_display_name:
        request.session["principal_display_name"] = principal_display_name
        session["principal_display_name"] = principal_display_name
    canonical_subject = str(resolved_identity.get("canonical_subject") or "").strip()
    if canonical_subject:
        request.session["principal_canonical_subject"] = canonical_subject
        session["principal_canonical_subject"] = canonical_subject
    update_session(session_id, session)

    return forwarded, outbound_headers


def _openai_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return ""


def _normalize_provider_model(model: str) -> str:
    if not model:
        return settings.LLM_MODEL
    for prefix in ("ollama/", "openrouter/"):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _is_embedding_like_model(model_id: str, model_name: str = "") -> bool:
    haystack = f"{model_id} {model_name}".lower()
    return any(marker in haystack for marker in EMBEDDING_MODEL_MARKERS)


def _pick_preferred_local_model(
    models: list[dict[str, str]],
    preferred: str | None = None,
) -> str:
    if not models:
        return ""
    model_ids = [str(item.get("id") or "").strip() for item in models if item.get("id")]
    if preferred and preferred in model_ids:
        return preferred
    for candidate in model_ids:
        lowered = candidate.lower()
        if "llama" in lowered:
            return candidate
    return model_ids[0]


def _normalize_local_model_id(model_id: str) -> str:
    raw = str(model_id or "").strip()
    if not raw:
        return ""
    return raw if raw.lower().startswith("ollama/") else f"ollama/{raw}"


async def _fetch_local_models(timeout: float) -> list[dict[str, str]]:
    local_base = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    if not local_base:
        return []
    ollama_root = local_base[:-3] if local_base.endswith("/v1") else local_base
    ollama_tags_url = f"{ollama_root}/api/tags"
    fetched_models: list[dict[str, str]] = []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(ollama_tags_url)
            if response.status_code != 200:
                return []
            payload = response.json()
            for item in payload.get("models", []):
                raw_mid = str(item.get("model") or item.get("name") or "").strip()
                mname = str(item.get("name") or raw_mid).strip()
                if not raw_mid:
                    continue
                if _is_embedding_like_model(raw_mid, mname):
                    continue
                mid = _normalize_local_model_id(raw_mid)
                fetched_models.append({"id": mid, "name": mname})
    except Exception:
        return []
    return fetched_models


async def _fetch_local_models_debug(timeout: float) -> dict[str, Any]:
    local_base = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    result: dict[str, Any] = {
        "local_base": local_base,
        "ollama_root": "",
        "models_raw": [],
        "models_filtered_in": [],
        "models_filtered_out": [],
        "error": None,
    }
    if not local_base:
        result["error"] = "LLM_BASE_URL is not set"
        return result

    ollama_root = local_base[:-3] if local_base.endswith("/v1") else local_base
    result["ollama_root"] = ollama_root
    ollama_tags_url = f"{ollama_root}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(ollama_tags_url)
            if response.status_code != 200:
                result["error"] = f"HTTP {response.status_code} from {ollama_tags_url}"
                return result
            payload = response.json()
    except Exception as exc:
        result["error"] = str(exc)
        return result

    models = payload.get("models") if isinstance(payload, dict) else []
    if not isinstance(models, list):
        result["error"] = "Unexpected /api/tags payload"
        return result

    for item in models:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("model") or item.get("name") or "").strip()
        mname = str(item.get("name") or mid).strip()
        if not mid:
            continue
        result["models_raw"].append({"id": mid, "name": mname})
        if _is_embedding_like_model(mid, mname):
            result["models_filtered_out"].append(
                {"id": mid, "name": mname, "reason": "embedding-like"}
            )
        else:
            result["models_filtered_in"].append({"id": mid, "name": mname})

    preferred = _pick_preferred_local_model(result["models_filtered_in"], settings.LLM_MODEL)
    result["preferred_default"] = preferred
    return result


def _form_str(value: Any, default: str = "") -> str:
    if value is None or isinstance(value, UploadFile):
        return default
    return str(value)


def _extract_keywords(value: str | None) -> list[str]:
    if not value:
        return []
    tokens = re.findall(r"[A-Za-z0-9]{4,}", value.lower())
    seen: set[str] = set()
    for token in tokens:
        if token:
            seen.add(token)
    return list(seen)


def _normalize_knowledge_tree_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    coordinate = ""
    if item.get("coordinate"):
        coordinate = str(item["coordinate"])
    elif item.get("namespace") and item.get("identifier"):
        coordinate = f"{item['namespace']}:{item['identifier']}"
    key = item.get("key")
    if not coordinate and isinstance(key, dict):
        namespace = key.get("namespace")
        identifier = key.get("identifier")
        if namespace and identifier:
            coordinate = f"{namespace}:{identifier}"
    if not coordinate:
        return None
    score = item.get("relevance_score") or item.get("score") or 0.0
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    tier_rank = item.get("tier_rank") or item.get("tierRank") or 0
    try:
        tier_rank = int(tier_rank)
    except (TypeError, ValueError):
        tier_rank = 0
    return {"coordinate": coordinate, "score": score, "tier_rank": tier_rank}


def _select_coords(
    knowledge_tree: list[dict],
    appraisal: dict | None,
) -> list[str]:
    normalized = [
        item for item in (_normalize_knowledge_tree_item(item) for item in knowledge_tree) if item
    ]
    normalized.sort(key=lambda item: item["score"], reverse=True)
    caps = {3: 3, 2: 2, 1: 1, 0: 1}
    grace_score = None
    if isinstance(appraisal, dict):
        raw_grace_score = appraisal.get("grace_score") or appraisal.get("graceScore")
        if isinstance(raw_grace_score, (int, float, str)):
            try:
                grace_score = float(raw_grace_score)
            except (TypeError, ValueError):
                grace_score = None
    allow_tier_zero = grace_score is None or grace_score >= APPRAISAL_GRACE_TIER0
    selected: list[str] = []
    seen: set[str] = set()
    for tier in (3, 2, 1, 0):
        if tier == 0 and not allow_tier_zero:
            continue
        cap = caps.get(tier, 0)
        if cap <= 0:
            continue
        tier_items = [item for item in normalized if item["tier_rank"] == tier]
        for item in tier_items[:cap]:
            coord = item["coordinate"]
            if coord in seen:
                continue
            seen.add(coord)
            selected.append(coord)
    if not selected:
        for item in normalized[:3]:
            coord = item["coordinate"]
            if coord in seen:
                continue
            seen.add(coord)
            selected.append(coord)
    return selected


def _ndjson_event(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode() + b"\n"


def _normalize_attachment_coord(coord: str) -> str:
    if not coord:
        return coord
    text = str(coord)
    if "-P" in text:
        return text.split("-P", 1)[0]
    return text


def _entity_from_coord(coord: str | None) -> str:
    raw = str(coord or "").strip()
    if not raw:
        return ""
    parts = [part for part in raw.split(":") if part]
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return ""


def _extract_entity_from_entry(item: Any) -> str:
    if not isinstance(item, dict):
        return ""

    key = item.get("key")
    if isinstance(key, dict):
        namespace = str(key.get("namespace") or "").strip()
        if namespace:
            return namespace
    elif isinstance(key, str):
        derived = _entity_from_coord(key)
        if derived:
            return derived

    for field in ("entry_id", "coordinate", "coord"):
        value = item.get(field)
        if isinstance(value, str):
            derived = _entity_from_coord(value)
            if derived:
                return derived

    state_raw = item.get("state")
    state: dict[str, Any] = state_raw if isinstance(state_raw, dict) else {}
    metadata_raw = state.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    metadata_entity = str(metadata.get("entity") or "").strip()
    if metadata_entity:
        return metadata_entity

    for field in ("coordinate", "related_coord"):
        value = metadata.get(field)
        if isinstance(value, str):
            derived = _entity_from_coord(value)
            if derived:
                return derived

    nested_entry_raw = item.get("entry")
    nested_entry: dict[str, Any] = nested_entry_raw if isinstance(nested_entry_raw, dict) else {}
    nested_key_raw = nested_entry.get("key")
    nested_key: dict[str, Any] = nested_key_raw if isinstance(nested_key_raw, dict) else {}
    namespace = str(nested_key.get("namespace") or "").strip()
    if namespace:
        return namespace

    return ""


_CHAT_ENTITY_PATTERN = re.compile(r"^[0-9a-f]{8}:[0-9a-f]{8}$", re.IGNORECASE)


def _is_chat_entity(entity: str) -> bool:
    text = str(entity or "").strip()
    if not text:
        return False

    mode = str(os.getenv("FRONTEND_ENTITY_MODE", "ledger") or "ledger").strip().lower()
    if mode == "session_hash":
        if text.startswith("chat-"):
            return True
        return bool(_CHAT_ENTITY_PATTERN.match(text))

    # Ledger mode: keep canonical ledger-style entities only.
    return text.startswith("chat-")


def _resolve_session_scope(session_id: str, session: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Normalize session ledger/entity to canonical demo foundation in ledger mode."""

    desired_ledger = str(session.get("ledger_id") or settings.DEFAULT_LEDGER_ID or "").strip() or settings.DEFAULT_LEDGER_ID
    demo_ledger = str(os.getenv("DEMO_LEDGER_ID") or "").strip()
    if desired_ledger == "default" and demo_ledger:
        desired_ledger = demo_ledger

    desired_entity = build_entity_namespace(desired_ledger, session_id)
    if session.get("ledger_id") != desired_ledger or session.get("entity") != desired_entity:
        session = dict(session)
        session["ledger_id"] = desired_ledger
        session["entity"] = desired_entity
        update_session(session_id, session)

    return desired_ledger, desired_entity, session


def _iter_entry_records(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        rows.extend([item for item in payload if isinstance(item, dict)])
    elif isinstance(payload, dict):
        for key in ("entries", "items", "recent", "results", "history", "messages"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([item for item in value if isinstance(item, dict)])
    return rows


def _query_mentions_attachment(query: str) -> bool:
    if not query:
        return False
    lowered = query.lower()
    return any(term in lowered for term in ("attachment", "document", "file", "upload"))


def _log_timing(label: str, started_at: float, extra: dict | None = None) -> None:
    if not TIMING_DEBUG:
        return
    elapsed_ms = int((time.time() - started_at) * 1000)
    payload = f"[timing] {label} {elapsed_ms}ms"
    if extra:
        payload += f" {extra}"
    print(payload)


def _build_attachment_part_coords(
    meta: dict,
    parent_coord: str,
    keywords: list[str],
    payload_parts: list[dict] | None = None,
    limit: int = 3,
) -> list[str]:
    parts = payload_parts if isinstance(payload_parts, list) else meta.get("attachment_parts")
    if not isinstance(parts, list) or not parent_coord:
        return []
    namespace = None
    identifier = None
    if ":" in parent_coord:
        namespace, identifier = parent_coord.rsplit(":", 1)
    base_identifier = meta.get("attachment_group") or identifier
    if not base_identifier:
        return []
    scored: list[tuple[int, str]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        topics_raw = part.get("topics")
        tags_raw = part.get("tags")
        topics = topics_raw if isinstance(topics_raw, list) else []
        tags = tags_raw if isinstance(tags_raw, list) else []
        label_parts: list[str] = []
        for entry in topics + tags:
            if entry is None:
                continue
            label_parts.append(str(entry))
        label = " ".join(label_parts).lower()
        hits = sum(1 for keyword in keywords if keyword in label)
        if hits <= 0:
            continue
        part_coord = part.get("coord") if isinstance(part.get("coord"), str) else None
        if part_coord:
            scored.append((hits, part_coord))
            continue
        suffix = part.get("part_suffix")
        if not suffix and isinstance(part.get("index"), int):
            suffix = f"P{part['index']:03d}"
        if not suffix:
            continue
        if isinstance(suffix, str) and suffix.startswith("P"):
            suffix = f"T{suffix[1:]}"
        scored.append((hits, suffix))
    scored.sort(key=lambda item: item[0], reverse=True)
    coords: list[str] = []
    for _, suffix in scored[:limit]:
        if isinstance(suffix, str) and ":" in suffix:
            coords.append(suffix)
            continue
        part_id = f"{base_identifier}-{suffix}"
        coords.append(f"{namespace}:{part_id}" if namespace else part_id)
    return coords


def _dedupe_snippets(snippets: list[str]) -> list[str]:
    output: list[str] = []
    seen: list[str] = []
    for snippet in snippets:
        normalized = " ".join(snippet.lower().split())
        if not normalized:
            continue
        duplicate = False
        for existing in seen:
            if normalized in existing or existing in normalized:
                duplicate = True
                break
        if duplicate:
            continue
        seen.append(normalized)
        output.append(snippet)
    return output


def _render_snippets_html(snippets: list[dict]) -> str:
    if not snippets:
        return "<p>No matching records found.</p>"
    lengths = [len(item["text"]) for item in snippets if item.get("text")]
    avg_len = sum(lengths) / len(lengths) if lengths else 0
    if avg_len < 80:
        items = []
        for item in snippets:
            text = html.escape(item["text"])
            coord = html.escape(item.get("coordinate", ""))
            coord_span = f" <span class=\"coord-ref\" data-coord=\"{coord}\">[{coord}]</span>" if coord else ""
            items.append(f"<li>{text}{coord_span}</li>")
        return f"<ul>{''.join(items)}</ul>"
    blocks = []
    for item in snippets:
        text = html.escape(item["text"])
        coord = html.escape(item.get("coordinate", ""))
        coord_span = f"<span class=\"coord-ref\" data-coord=\"{coord}\">[{coord}]</span>" if coord else ""
        blocks.append(f"<p>{text} {coord_span}</p>")
    return "".join(blocks)


def _extract_content_text(decoded: dict) -> str | None:
    if not isinstance(decoded, dict):
        return None
    payload = decoded.get("payload") or {}
    if isinstance(payload, dict):
        blobs = payload.get("blobs")
        segments = payload.get("segments")
        if isinstance(blobs, dict) and isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                blob_ref = segment.get("blob_ref")
                if blob_ref and isinstance(blobs.get(blob_ref), str):
                    return blobs[blob_ref].strip()
    content = decoded.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _normalize_decoded_payload(decoded: dict) -> str | None:
    if not isinstance(decoded, dict):
        return None

    content_text = _extract_content_text(decoded)
    if content_text:
        return content_text

    skim = decoded.get("skim") if isinstance(decoded.get("skim"), dict) else None
    if skim:
        one_line = skim.get("one_line")
        if isinstance(one_line, str) and one_line.strip():
            return one_line.strip()
    return None

# IMPORTANT: pass secret_key so FastHTML does NOT try to write .sesskey
_fast_app_result = fast_app(
    secret_key=os.environ.get("FASTHTML_SECRET_KEY", "")
)
app = _fast_app_result[0]
rt = _fast_app_result[1]
handler = app
app.add_middleware(SessionTokenContextMiddleware)
app.add_middleware(BasicAuthMiddleware)

# Mount static files
app.mount("/static",
          StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
          name="static")


def _is_demo_offline(session: dict[str, Any] | None) -> bool:
    if not isinstance(session, dict):
        return False
    return bool(session.get("demo_offline", False))


def _is_ollama_provider(value: Any) -> bool:
    provider = str(value or "").strip().lower()
    return provider.startswith("ollama/")


def _demo_offline_blocks_provider(session: dict[str, Any] | None, payload: dict[str, Any] | None) -> bool:
    if not _is_demo_offline(session):
        return False
    body = payload if isinstance(payload, dict) else {}
    provider = body.get("provider") or body.get("agent") or body.get("model") or ""
    return not _is_ollama_provider(provider)


@rt("/health")
def health_check():
    status = {
        "status": "ok",
        "llm_configured": llm is not None,
        "backend_url": settings.API_BASE,
        "commit_sha": (os.getenv("VERCEL_GIT_COMMIT_SHA") or "").strip() or "unknown",
    }
    if llm is not None:
        status["llm_model"] = settings.LLM_PROVIDER 
    return status


@rt("/login", methods=["GET", "HEAD"])
def login_page(request: Request):
    mode = str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower()
    if mode not in FRONTDOOR_AUTH_MODE_VALUES:
        mode = "basic" if _basic_auth_required() else "off"
    if mode != "form":
        return RedirectResponse(url="/", status_code=303)
    if _form_auth_cookie_valid(request):
        next_path = _safe_next_path(str(request.query_params.get("next") or "/"))
        return RedirectResponse(url=next_path, status_code=303)

    next_path = _safe_next_path(str(request.query_params.get("next") or "/"))
    github_error = str(request.query_params.get("github_error") or "").strip()
    github_error_messages = {
        "oauth_provider_error": "GitHub sign-in failed at the provider. Please try again.",
        "oauth_state_invalid": "GitHub sign-in could not be verified. Please restart the sign-in flow.",
        "oauth_code_missing": "GitHub did not return an authorization code. Please try again.",
        "oauth_token_exchange_failed": "GitHub token exchange failed. Please try again.",
        "oauth_token_missing": "GitHub did not return an access token. Please try again.",
        "oauth_user_fetch_failed": "GitHub user lookup failed. Please try again.",
        "github_user_missing": "GitHub did not return a usable user identity.",
        "github_session_token_failed": "GitHub sign-in succeeded, but DSS could not establish an authenticated session.",
        "github_session_token_missing": "GitHub sign-in succeeded, but DSS did not receive a usable session token.",
    }
    github_error_block = (
        Div(
            github_error_messages.get(github_error) or "GitHub sign-in failed. Please try again.",
            style=(
                "margin:0 0 1rem 0;padding:0.75rem 0.9rem;border-radius:8px;"
                "background:#fee2e2;color:#991b1b;font-size:0.9rem;"
            ),
        )
        if github_error
        else ""
    )
    return (
        Title("Login | Dual-Substrate"),
        Div(
            Div(
                Div(
                    P(
                        "Intelligence is continuous.",
                        style="margin:0; font-weight:400; color:#444; font-size:1.2rem;",
                    ),
                    P(
                        "Memory is exact.",
                        style="margin:0; font-weight:600; color:#111; font-size:1.2rem;",
                    ),
                    style="text-align:center; margin-bottom:1.5rem; line-height:1.4;",
                ),
                github_error_block,
                Button(
                    "Continue with GitHub",
                    type="button",
                    onclick=f"window.location='/login/github?next={quote(next_path, safe='/?=&')}'",
                    style=(
                        "width:100%;padding:0.75rem;background:#111;color:#fff;border:none;"
                        "border-radius:6px;font-weight:600;font-size:1rem;cursor:pointer;"
                    ),
                ),
                P(
                    "Use GitHub to continue into the Dual-Substrate control plane and linked identity flow.",
                    style="margin-top:0.9rem;font-size:0.8rem;color:#6b7280;line-height:1.4;",
                ),
                style=(
                    "background:#fff;padding:2rem;border-radius:12px;"
                    "box-shadow:0 10px 25px rgba(0,0,0,0.05);border:1px solid #f3f4f6;"
                    "width:100%;max-width:400px;"
                ),
            ),
            style=(
                "display:flex;align-items:center;justify-content:center;min-height:100vh;"
                "background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
                "padding:1rem;"
            ),
        ),
    )


@rt("/.well-known/vercel/jwe", methods=["GET", "HEAD"])
def vercel_feedback_jwe_probe(_: Request):
    return PlainTextResponse("", status_code=204)


@rt("/login", methods=["POST"])
async def login_submit(request: Request):
    mode = str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower()
    if mode not in FRONTDOOR_AUTH_MODE_VALUES:
        mode = "basic" if _basic_auth_required() else "off"
    if mode != "form":
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    next_path = _safe_next_path(str(form.get("next") or "/"))
    return RedirectResponse(
        url=f"/login/github?next={quote(next_path, safe='/?=&')}",
        status_code=303,
    )


@rt("/login/github", methods=["GET", "HEAD"])
def login_github_page(request: Request):
    mode = str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower()
    if mode not in FRONTDOOR_AUTH_MODE_VALUES:
        mode = "basic" if _basic_auth_required() else "off"
    if mode != "form":
        return RedirectResponse(url="/", status_code=303)

    next_path = _safe_next_path(str(request.query_params.get("next") or "/"))
    client_id = str(os.getenv("GITHUB_OAUTH_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("GITHUB_OAUTH_CLIENT_SECRET") or "").strip()
    github_flow_ready = bool(client_id and client_secret)
    cta_label = "Continue with GitHub"
    cta_onclick = f"window.location='/login/github/start?next={quote(next_path, safe='/?=&')}'"
    cta_disabled = None
    status_copy = "Choose a GitHub account to authorize DSS."
    status_style = "background:#d1fae5;color:#065f46;"

    if not github_flow_ready:
        cta_label = "GitHub unavailable"
        cta_onclick = None
        cta_disabled = "disabled"
        status_copy = "GitHub OAuth provider flow is not configured in this deployment. Contact operator."
        status_style = "background:#fee2e2;color:#991b1b;"
    return (
        Title("Authorize DSS | GitHub"),
        Div(
            Div(
                P("Authorize DSS", style="font-size:1.2rem;font-weight:600;margin:0 0 0.7rem 0;"),
                P(
                    "From the options below, choose which account you would like to use to authorize this app.",
                    style="margin:0 0 1rem 0;color:#374151;line-height:1.45;",
                ),
                P(
                    status_copy,
                    style=(
                        "margin:0 0 1rem 0;padding:0.65rem 0.8rem;border-radius:8px;font-size:0.85rem;"
                        + status_style
                    ),
                ),
                Button(
                    cta_label,
                    type="button",
                    onclick=cta_onclick,
                    disabled=cta_disabled,
                    style=(
                        "width:100%;padding:0.75rem;background:#111;color:#fff;border:none;"
                        f"border-radius:6px;font-weight:600;font-size:1rem;{'opacity:0.55;cursor:not-allowed;' if cta_disabled else 'cursor:pointer;'}"
                    ),
                ),
                Button(
                    "Back",
                    type="button",
                    onclick="window.location='/login'",
                    style="width:100%;margin-top:0.7rem;padding:0.7rem;background:#fff;border:1px solid #d1d5db;border-radius:6px;",
                ),
                style=(
                    "background:#fff;padding:1.6rem;border-radius:12px;border:1px solid #f3f4f6;"
                    "box-shadow:0 10px 25px rgba(0,0,0,0.05);width:100%;max-width:520px;"
                ),
            ),
            style=(
                "display:flex;align-items:center;justify-content:center;min-height:100vh;"
                "background:#f9fafb;padding:1rem;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
            ),
        ),
    )


@rt("/login/github/start", methods=["GET"])
def login_github_start(request: Request):
    mode = str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower()
    if mode not in FRONTDOOR_AUTH_MODE_VALUES:
        mode = "basic" if _basic_auth_required() else "off"
    if mode != "form":
        return RedirectResponse(url="/", status_code=303)

    client_id = str(os.getenv("GITHUB_OAUTH_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("GITHUB_OAUTH_CLIENT_SECRET") or "").strip()
    next_path = _safe_next_path(str(request.query_params.get("next") or "/"))
    if not client_id or not client_secret:
        return RedirectResponse(
            url=f"/login/github?next={quote(next_path, safe='/?=&')}&error=oauth_not_configured",
            status_code=303,
        )

    state = secrets.token_urlsafe(24)
    request.session["github_oauth_state"] = state
    request.session["github_oauth_next"] = next_path

    redirect_uri = str(os.getenv("GITHUB_OAUTH_REDIRECT_URI") or "").strip()
    if not redirect_uri:
        redirect_uri = f"{_request_origin(request)}/login/github/callback"

    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return RedirectResponse(url=f"https://github.com/login/oauth/authorize?{query}", status_code=303)


@rt("/login/github/callback", methods=["GET"])
async def login_github_callback(request: Request):
    mode = str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower()
    if mode not in FRONTDOOR_AUTH_MODE_VALUES:
        mode = "basic" if _basic_auth_required() else "off"
    if mode != "form":
        return RedirectResponse(url="/", status_code=303)

    expected_state = str(request.session.get("github_oauth_state") or "").strip()
    next_path = _safe_next_path(str(request.session.get("github_oauth_next") or "/"))
    state = str(request.query_params.get("state") or "").strip()
    code = str(request.query_params.get("code") or "").strip()
    err = str(request.query_params.get("error") or "").strip()
    request.session.pop("github_oauth_state", None)
    request.session.pop("github_oauth_next", None)

    if err:
        return RedirectResponse(
            url=f"/login?github_error=oauth_provider_error&next={quote(next_path, safe='/?=&')}",
            status_code=303,
        )
    if not expected_state or not state or not hmac.compare_digest(expected_state, state):
        return RedirectResponse(
            url=f"/login?github_error=oauth_state_invalid&next={quote(next_path, safe='/?=&')}",
            status_code=303,
        )
    if not code:
        return RedirectResponse(
            url=f"/login?github_error=oauth_code_missing&next={quote(next_path, safe='/?=&')}",
            status_code=303,
        )

    client_id = str(os.getenv("GITHUB_OAUTH_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("GITHUB_OAUTH_CLIENT_SECRET") or "").strip()
    redirect_uri = str(os.getenv("GITHUB_OAUTH_REDIRECT_URI") or "").strip()
    if not redirect_uri:
        redirect_uri = f"{_request_origin(request)}/login/github/callback"

    async with httpx.AsyncClient(timeout=20.0) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if token_resp.status_code >= 400:
            return RedirectResponse(
                url=f"/login?github_error=oauth_token_exchange_failed&next={quote(next_path, safe='/?=&')}",
                status_code=303,
            )
        token_payload = token_resp.json()
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            return RedirectResponse(
                url=f"/login?github_error=oauth_token_missing&next={quote(next_path, safe='/?=&')}",
                status_code=303,
            )

        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"authorization": f"Bearer {access_token}", "accept": "application/json"},
        )
        if user_resp.status_code >= 400:
            return RedirectResponse(
                url=f"/login?github_error=oauth_user_fetch_failed&next={quote(next_path, safe='/?=&')}",
                status_code=303,
            )
        user_payload = user_resp.json()
        emails_resp = await client.get(
            "https://api.github.com/user/emails",
            headers={"authorization": f"Bearer {access_token}", "accept": "application/json"},
        )
        emails_payload = emails_resp.json() if emails_resp.status_code < 400 else []

    github_user_id = str(user_payload.get("id") or "").strip()
    github_login = str(user_payload.get("login") or "").strip()
    if not github_user_id:
        return RedirectResponse(
            url=f"/login?github_error=github_user_missing&next={quote(next_path, safe='/?=&')}",
            status_code=303,
        )

    principal_key_ref = f"github:user:{github_user_id}"
    github_email = ""
    if isinstance(emails_payload, list):
        for item in emails_payload:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email") or "").strip().lower()
            if not email:
                continue
            if bool(item.get("verified")) and bool(item.get("primary")):
                github_email = email
                break
            if bool(item.get("verified")) and not github_email:
                github_email = email

    link_status, link_body = await _principal_registry_post(
        "/api/principals/link/github/start",
        {
            "github_user_id": github_user_id,
            "github_login": github_login,
            "github_email": github_email or None,
            "tenant_id": settings.FRONTEND_TENANT_ID,
        },
    )
    if link_status < 400 and isinstance(link_body, dict):
        link_state = str(link_body.get("link_state") or "").strip()
        principal_raw = link_body.get("principal")
        principal: dict[str, Any] = principal_raw if isinstance(principal_raw, dict) else {}
        principal_did = str(principal.get("principal_did") or link_body.get("principal_did") or "").strip()
        if link_state == "linked" and principal_did:
            return await _complete_github_login(
                request=request,
                next_path=next_path,
                principal_did=principal_did,
                principal_key_ref=principal_key_ref,
            )
        if link_state == "verification_required":
            request.session["github_link_pending"] = {
                "github_user_id": github_user_id,
                "github_login": github_login,
                "github_email": github_email or None,
                "next_path": next_path,
                "challenge_id": str(link_body.get("challenge_id") or "").strip(),
                "masked_destination": str(link_body.get("masked_destination") or "").strip(),
                "delivery_channel": str(link_body.get("delivery_channel") or "").strip(),
                "debug_code": str(link_body.get("debug_code") or "").strip(),
            }
            return RedirectResponse(url=f"/login/link?next={quote(next_path, safe='/?=&')}&challenge=1", status_code=303)

    detail = link_body.get("detail") if isinstance(link_body, dict) else {}
    error_code = ""
    if isinstance(detail, dict):
        error_code = str(detail.get("error") or "").strip()
    elif isinstance(link_body, dict):
        error_code = str(link_body.get("error") or "").strip()

    if link_status == 409 or error_code == "principal_link_conflict":
        request.session["github_link_pending"] = {
            "github_user_id": github_user_id,
            "github_login": github_login,
            "github_email": github_email or None,
            "next_path": next_path,
            "error": "principal_link_conflict",
        }
        return RedirectResponse(url=f"/login/link?next={quote(next_path, safe='/?=&')}&error=principal_link_conflict", status_code=303)

    if link_status == 404 and error_code not in {"principal_link_conflict"}:
        request.session["github_link_pending"] = {
            "github_user_id": github_user_id,
            "github_login": github_login,
            "github_email": github_email or None,
            "next_path": next_path,
        }
        if github_email:
            return RedirectResponse(url=f"/login/link?next={quote(next_path, safe='/?=&')}", status_code=303)

    principal_did = f"did:github:{github_user_id}"
    upsert_status, _ = await _principal_upsert(
        {
            "principal_did": principal_did,
            "principal_key_refs": [principal_key_ref],
            "tenant_id": settings.FRONTEND_TENANT_ID,
            "display_name": github_login or principal_did,
            "metadata": {
                "auth_provider": "github",
                "github_login": github_login,
                "github_user_id": github_user_id,
                "github_email": github_email or None,
            },
            "status": "active",
        }
    )
    if upsert_status >= 400:
        return RedirectResponse(
            url=f"/login?github_error=github_principal_upsert_failed&next={quote(next_path, safe='/?=&')}",
            status_code=303,
        )
    return await _complete_github_login(
        request=request,
        next_path=next_path,
        principal_did=principal_did,
        principal_key_ref=principal_key_ref,
    )


@rt("/login/link", methods=["GET"])
def login_link_page(request: Request):
    mode = str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower()
    if mode not in FRONTDOOR_AUTH_MODE_VALUES:
        mode = "basic" if _basic_auth_required() else "off"
    if mode != "form":
        return RedirectResponse(url="/", status_code=303)
    pending = request.session.get("github_link_pending")
    if not isinstance(pending, dict):
        return RedirectResponse(url="/login", status_code=303)
    next_path = _safe_next_path(str(pending.get("next_path") or request.query_params.get("next") or "/"))
    github_email = str(pending.get("github_email") or "").strip()
    challenge_id = str(pending.get("challenge_id") or "").strip()
    error_code = str(request.query_params.get("error") or pending.get("error") or "").strip()
    masked_destination = str(pending.get("masked_destination") or "").strip()
    delivery_channel = str(pending.get("delivery_channel") or "").strip()
    debug_code = str(pending.get("debug_code") or "").strip()
    challenge_active = bool(challenge_id)
    status_copy = (
        f"Verification code sent via {delivery_channel} to {masked_destination}."
        if challenge_active
        else "Confirm your existing DSS identity with email or phone before linking GitHub."
    )
    if error_code == "principal_link_conflict":
        status_copy = "Multiple existing identities match this GitHub account. Contact operator to complete linking."
    elif error_code == "email_delivery_not_configured":
        status_copy = "Email delivery is not configured in the current deployment. Configure Resend before retrying."
    elif error_code == "email_sender_not_configured":
        status_copy = "Email sender identity is not configured in the current deployment. Set a verified PRINCIPAL_LINK_EMAIL_FROM value before retrying."
    elif error_code == "email_delivery_failed":
        status_copy = "The verification email could not be delivered. Check the Resend API key, sender address, and delivery logs, then retry."
    return (
        Title("Link Existing Identity | Dual-Substrate"),
        Div(
            Div(
                P("Link Existing DSS Identity", style="font-size:1.2rem;font-weight:600;margin:0 0 1rem 0;"),
                P(status_copy, style="margin:0 0 1rem 0;color:#374151;line-height:1.45;"),
                (
                    Form(
                        Input(type="hidden", name="next", value=next_path),
                        Label("Email or phone", for_="contact_value"),
                        Input(id="contact_value", name="contact_value", type="text", value=github_email, style="width:100%;padding:0.7rem;border:1px solid #d1d5db;border-radius:6px;margin:0.35rem 0 0.75rem;"),
                        Label("Channel", for_="contact_channel"),
                        Select(
                            Option("Email", value="email", selected="selected"),
                            Option("Phone", value="phone"),
                            id="contact_channel",
                            name="contact_channel",
                            style="width:100%;padding:0.7rem;border:1px solid #d1d5db;border-radius:6px;margin:0.35rem 0 0.75rem;",
                        ),
                        Button("Send verification code", type="submit", style="width:100%;padding:0.75rem;background:#111;color:#fff;border:none;border-radius:6px;font-weight:600;"),
                        action="/login/link/start",
                        method="post",
                    )
                    if not challenge_active and error_code != "principal_link_conflict"
                    else ""
                ),
                (
                    P(
                        f"Developer code: {debug_code}",
                        style="margin:0.75rem 0 0;color:#92400e;font-size:0.85rem;"
                    )
                    if challenge_active and debug_code
                    else ""
                ),
                (
                    Form(
                        Input(type="hidden", name="next", value=next_path),
                        Label("Verification code", for_="link_code"),
                        Input(id="link_code", name="code", type="text", style="width:100%;padding:0.7rem;border:1px solid #d1d5db;border-radius:6px;margin:0.35rem 0 0.75rem;"),
                        Button("Verify and continue", type="submit", style="width:100%;padding:0.75rem;background:#111;color:#fff;border:none;border-radius:6px;font-weight:600;"),
                        action="/login/link/verify",
                        method="post",
                    )
                    if challenge_active
                    else ""
                ),
                Button(
                    "Back",
                    type="button",
                    onclick="window.location='/login'",
                    style="width:100%;margin-top:0.7rem;padding:0.7rem;background:#fff;border:1px solid #d1d5db;border-radius:6px;",
                ),
                style="background:#fff;padding:1.6rem;border-radius:12px;border:1px solid #f3f4f6;box-shadow:0 10px 25px rgba(0,0,0,0.05);width:100%;max-width:520px;",
            ),
            style="display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f9fafb;padding:1rem;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;",
        ),
    )


@rt("/login/link/start", methods=["POST"])
async def login_link_start(request: Request):
    pending = request.session.get("github_link_pending")
    if not isinstance(pending, dict):
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    next_path = _safe_next_path(str(form.get("next") or pending.get("next_path") or "/"))
    contact_value = str(form.get("contact_value") or "").strip()
    channel = str(form.get("contact_channel") or "").strip().lower()
    link_status, link_body = await _principal_registry_post(
        "/api/principals/link/github/start",
        {
            "github_user_id": pending.get("github_user_id"),
            "github_login": pending.get("github_login"),
            "github_email": pending.get("github_email"),
            "tenant_id": settings.FRONTEND_TENANT_ID,
            "contact_channel": channel,
            "contact_value": contact_value,
        },
    )
    if link_status < 400 and isinstance(link_body, dict) and str(link_body.get("link_state") or "").strip() == "verification_required":
        pending["challenge_id"] = str(link_body.get("challenge_id") or "").strip()
        pending["masked_destination"] = str(link_body.get("masked_destination") or "").strip()
        pending["delivery_channel"] = str(link_body.get("delivery_channel") or "").strip()
        pending["debug_code"] = str(link_body.get("debug_code") or "").strip()
        request.session["github_link_pending"] = pending
        return RedirectResponse(url=f"/login/link?next={quote(next_path, safe='/?=&')}&challenge=1", status_code=303)
    error_code = "principal_link_not_found"
    detail = link_body.get("detail") if isinstance(link_body, dict) else None
    if isinstance(detail, str) and detail.strip():
        error_code = detail.strip()
    elif isinstance(detail, dict):
        candidate = str(detail.get("error") or "").strip()
        if candidate:
            error_code = candidate
    elif isinstance(link_body, dict):
        candidate = str(link_body.get("error") or "").strip()
        if candidate:
            error_code = candidate
    pending["error"] = error_code
    request.session["github_link_pending"] = pending
    return RedirectResponse(url=f"/login/link?next={quote(next_path, safe='/?=&')}&error={quote(error_code, safe='_-')}", status_code=303)


@rt("/login/link/verify", methods=["POST"])
async def login_link_verify(request: Request):
    pending = request.session.get("github_link_pending")
    if not isinstance(pending, dict):
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    next_path = _safe_next_path(str(form.get("next") or pending.get("next_path") or "/"))
    code_value = str(form.get("code") or "").strip()
    verify_status, verify_body = await _principal_registry_post(
        "/api/principals/link/github/verify",
        {
            "challenge_id": pending.get("challenge_id"),
            "code": code_value,
        },
    )
    if verify_status >= 400 or not isinstance(verify_body, dict):
        return RedirectResponse(url=f"/login/link?next={quote(next_path, safe='/?=&')}&challenge=1&error=link_code_invalid", status_code=303)
    principal_raw = verify_body.get("principal")
    principal: dict[str, Any] = principal_raw if isinstance(principal_raw, dict) else {}
    principal_did = str(principal.get("principal_did") or "").strip()
    if not principal_did:
        return RedirectResponse(url=f"/login/link?next={quote(next_path, safe='/?=&')}&challenge=1&error=principal_link_not_found", status_code=303)
    request.session.pop("github_link_pending", None)
    return await _complete_github_login(
        request=request,
        next_path=next_path,
        principal_did=principal_did,
        principal_key_ref=f"github:user:{str(pending.get('github_user_id') or '').strip()}",
    )


@rt("/api/auth/identity_card", methods=["GET"])
async def api_auth_identity_card(request: Request):
    middleware_identity_card = await _fetch_middleware_identity_card(request)
    if not isinstance(middleware_identity_card, dict):
        return JSONResponse(
            {
                "status": "unavailable",
                "error": "middleware_identity_card_unavailable",
                "detail": "Identity card is owned by middleware and is currently unavailable.",
            },
            status_code=503,
        )
    identity_raw = middleware_identity_card.get("identity_vc")
    identity: dict[str, Any] = identity_raw if isinstance(identity_raw, dict) else {}
    usage_raw = middleware_identity_card.get("usage_stats")
    usage: dict[str, Any] = usage_raw if isinstance(usage_raw, dict) else {}
    eq9_raw = middleware_identity_card.get("eq9")
    eq9: dict[str, Any] = eq9_raw if isinstance(eq9_raw, dict) else {}
    ui = _identity_card_ui_model(identity, eq9)
    return {
        "status": "ok",
        "identity_vc": identity,
        "usage_stats": usage,
        "eq9": eq9,
        "ui": ui,
    }


@rt("/api/auth/session/refresh", methods=["POST"])
async def api_auth_session_refresh(request: Request):
    status_code, body = await _refresh_shared_backend_session(request)
    payload = body if isinstance(body, dict) else {"error": "auth_refresh_failed"}
    if status_code == 401:
        payload = {
            **payload,
            "login_url": _control_plane_login_url(request),
        }
    response = JSONResponse(payload, status_code=status_code)
    session_raw = payload.get("session")
    session = session_raw if isinstance(session_raw, dict) else {}
    refresh_session_raw = payload.get("refresh_session")
    refresh_session = refresh_session_raw if isinstance(refresh_session_raw, dict) else {}
    refreshed_token = str(session.get("token") or "").strip()
    refreshed_refresh_token = str(refresh_session.get("token") or "").strip()
    principal_did = str(payload.get("principal_did") or "").strip()
    if status_code < 400 and refreshed_token and refreshed_refresh_token and principal_did:
        _set_auth_cookies(
            request=request,
            response=response,
            token=refreshed_token,
            refresh_token=refreshed_refresh_token,
            principal_did=principal_did,
        )
    elif status_code == 401:
        _clear_auth_cookies(request=request, response=response)
    return response


@rt("/logout", methods=["POST"])
def logout_submit(request: Request):
    response = RedirectResponse(url="/login", status_code=303)
    return _clear_auth_cookies(request=request, response=response)


@rt("/api/chat/smart_stream", methods=["POST"])
async def proxy_smart_stream(request: Request):
    client = httpx.AsyncClient(timeout=None)
    upstream_resp: httpx.Response | None = None
    upstream_url: str | None = None
    try:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"detail": "Invalid JSON"}, status_code=400)

        try:
            payload, outbound_headers = await _prepare_middleware_chat_proxy(request, dict(payload or {}))
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

        base = settings.API_BASE.rstrip("/")
        candidate_urls = [
            f"{base}/api/chat/smart_stream",
            f"{base}/chat/stream",
        ]
        seen: set[str] = set()
        urls = [url for url in candidate_urls if not (url in seen or seen.add(url))]

        for idx, url in enumerate(urls):
            resp = await client.send(
                client.build_request("POST", url, json=payload, headers=outbound_headers),
                stream=True,
            )
            if resp.status_code == 404 and idx < len(urls) - 1:
                await resp.aclose()
                continue
            upstream_resp = resp
            upstream_url = url
            break

        if upstream_resp is None:
            return JSONResponse(
                {"detail": "No upstream endpoint available for smart stream"},
                status_code=502,
            )

        if upstream_resp.status_code >= 400:
            detail = await upstream_resp.aread()
            payload = {
                "detail": detail.decode("utf-8", errors="ignore") or "Upstream request failed",
                "upstream_url": upstream_url,
            }
            if upstream_resp.status_code == 401:
                payload["login_url"] = _control_plane_login_url(request)
            response = JSONResponse(payload, status_code=upstream_resp.status_code)
            if upstream_resp.status_code == 401:
                _clear_auth_cookies(request=request, response=response)
            return response

        async def _stream():
            assert upstream_resp is not None
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await upstream_resp.aclose()
                await client.aclose()

        upstream_label = ""
        if upstream_url:
            try:
                parsed_upstream = httpx.URL(upstream_url)
                upstream_label = f"{parsed_upstream.host}{parsed_upstream.path}"
            except Exception:
                upstream_label = str(upstream_url)

        response = StreamingResponse(_stream(), media_type="application/x-ndjson")
        if upstream_label:
            response.headers["x-ds-upstream-url"] = upstream_label
            response.headers["x-ds-upstream-fallback"] = "true" if upstream_label.endswith("/chat/stream") else "false"
        return response
    except httpx.HTTPError as exc:
        if upstream_resp is not None:
            try:
                await upstream_resp.aclose()
            except Exception:
                pass
        await client.aclose()
        return JSONResponse(
            {
                "detail": f"Smart stream upstream unavailable: {exc}",
                "error_class": "httpx_error",
                "upstream_url": upstream_url,
            },
            status_code=502,
        )
    except Exception as exc:
        if upstream_resp is not None:
            try:
                await upstream_resp.aclose()
            except Exception:
                pass
        await client.aclose()
        return JSONResponse(
            {
                "detail": f"Smart stream proxy failed: {exc}",
                "error_class": "proxy_error",
                "upstream_url": upstream_url,
            },
            status_code=500,
        )



@rt("/api/thinking_trace/emit", methods=["POST"])
async def proxy_thinking_trace_emit(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    if not str(payload.get("session_id") or "").strip():
        payload["session_id"] = session_id

    url = f"{settings.API_BASE.rstrip('/')}/api/thinking_trace/emit"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload, headers=api.headers)
    except httpx.HTTPError as exc:
        return JSONResponse({"detail": f"Thinking trace emit upstream unavailable: {exc}"}, status_code=502)
    except Exception as exc:
        return JSONResponse({"detail": f"Thinking trace emit failed: {exc}"}, status_code=502)
    if resp.status_code >= 400:
        detail = resp.text.strip() or "Upstream thinking trace emit failed"
        payload: dict[str, Any] = {"detail": detail}
        if resp.status_code == 401:
            payload["login_url"] = _control_plane_login_url(request)
        return JSONResponse(payload, status_code=resp.status_code)
    try:
        body = resp.json()
    except Exception:
        body = {}
    return JSONResponse(body)


@rt("/api/thinking_trace/stream", methods=["GET"])
async def proxy_thinking_trace_stream(request: Request):
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    params = dict(request.query_params or {})
    if not str(params.get("session_id") or "").strip():
        params["session_id"] = session_id

    url = f"{settings.API_BASE.rstrip('/')}/api/thinking_trace/stream"
    client = httpx.AsyncClient(timeout=None)
    upstream_resp: httpx.Response | None = None

    try:
        upstream_resp = await client.send(
            client.build_request("GET", url, params=params, headers=api.headers),
            stream=True,
        )
        if upstream_resp.status_code >= 400:
            detail = await upstream_resp.aread()
            await upstream_resp.aclose()
            await client.aclose()
            payload: dict[str, Any] = {
                "detail": detail.decode("utf-8", errors="ignore") or "Upstream thinking trace stream failed",
                "upstream_url": url,
            }
            if upstream_resp.status_code == 401:
                payload["login_url"] = _control_plane_login_url(request)
            return JSONResponse(payload, status_code=upstream_resp.status_code)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(
            {
                "detail": f"Thinking trace stream upstream unavailable: {exc}",
                "upstream_url": url,
            },
            status_code=502,
        )
    except Exception as exc:
        await client.aclose()
        return JSONResponse(
            {
                "detail": f"Thinking trace stream failed: {exc}",
                "upstream_url": url,
            },
            status_code=502,
        )

    async def _stream():
        assert upstream_resp is not None
        try:
            async for chunk in upstream_resp.aiter_bytes():
                if chunk:
                    yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(_stream(), media_type="application/x-ndjson")

# Register your routes
register_home_routes(rt)
register_wake_routes(rt)
register_agent_routes(rt)


@rt("/api/render_history")
async def render_history(request: Request):
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id, entity, session = _resolve_session_scope(session_id, session)

    try:
        payload = await _get_middleware_json(
            request,
            f"/ledger/history/{quote(str(entity), safe='')}",
            params={"limit": 50},
        )
        history = payload.get("history") if isinstance(payload.get("history"), list) else []
    except Exception:
        history = []

    if not isinstance(history, list):
        history = []

    def _is_smoke_message(message: dict[str, Any]) -> bool:
        metadata_raw = message.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        provider = str(metadata.get("provider") or "").strip().lower()
        model = str(metadata.get("model") or "").strip().lower()
        source = str(metadata.get("source") or message.get("source") or "").strip().lower()
        reason = str(metadata.get("reason") or "").strip().lower()
        text = str(message.get("content") or message.get("text") or "").strip().lower()
        return (
            source == "acceptance"
            or provider == "smoke"
            or model == "smoke"
            or reason == "post-reset-check"
            or text == "post reset known ledger write"
        )

    rendered = []
    for idx, message in enumerate(history):
        if isinstance(message, dict) and _is_smoke_message(message):
            continue
        role = (
            message.get("role")
            or message.get("speaker")
            or message.get("source")
            or ("user" if idx % 2 == 0 else "assistant")
        ).lower()

        content = (
            message.get("content")
            or message.get("text")
            or message.get("message")
            or message.get("body")
            or message.get("value")
            or ""
        )

        if role == "assistant":
            rendered.append(
                Div(
                    content,
                    cls="font-sans text-base text-gray-700 leading-relaxed mb-8",
                )
            )
        else:
            rendered.append(
                Div(
                    content,
                    cls="font-serif text-xl text-gray-900 leading-relaxed mb-4",
                )
            )

    return tuple(rendered)


@rt("/v1/chat/completions", methods=["POST"])
async def openai_chat_completions(request: Request):
    """OpenAI-compatible endpoint for OpenClaw custom provider integration."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")

    override_authorized, auth_claims = _openai_override_authorized(
        request=request,
        payload=payload,
    )
    payload, policy_controls = _apply_openai_policy_controls(
        payload=payload,
        override_authorized=override_authorized,
    )
    for key in ("principal_did", "principal_key_id", "session_jti", "context_id"):
        value = auth_claims.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value.strip()

    model_raw = str(payload.get("model") or settings.LLM_MODEL).strip()
    model = _normalize_provider_model(model_raw)
    stream = bool(payload.get("stream", False))
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=422, detail="messages are required")

    history: list[dict[str, str]] = []
    latest_user_message = ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        content = _openai_content_text(msg.get("content"))
        if not content:
            continue
        if role == "user":
            latest_user_message = content
        if role in {"user", "assistant"}:
            history.append({"role": role, "content": content})

    if not latest_user_message:
        raise HTTPException(status_code=422, detail="at least one user message is required")

    created = int(time.time())
    assistant_text = ""
    response_model = model
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    if OPENAI_COMPAT_USE_PIPELINE:
        raw_payload_meta = payload.get("metadata")
        payload_meta: dict[str, Any] = raw_payload_meta if isinstance(raw_payload_meta, dict) else {}
        session_id = str(
            payload.get("user")
            or payload_meta.get("session_id")
            or payload.get("session_id")
            or "openclaw-dashboard"
        ).strip() or "openclaw-dashboard"
        try:
            orchestrated = await _run_openai_via_middleware_orchestrator(
                base_payload=payload,
                model=model,
                message=latest_user_message,
                history=history,
                session_id=session_id,
            )
            assistant_text = str(orchestrated.get("assistant_text") or "")
            response_model = str(orchestrated.get("response_model") or model)
            prompt_tokens = int(orchestrated.get("prompt_tokens") or 0)
            completion_tokens = int(orchestrated.get("completion_tokens") or 0)
            total_tokens = int(orchestrated.get("total_tokens") or (prompt_tokens + completion_tokens))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Middleware orchestrator failed: {exc}")
    else:
        llm_response = await llm.generate_response(
            message=latest_user_message,
            history=history[:-1] if history else [],
            agent=model,
        )
        assistant_text = llm_response.get("text") if isinstance(llm_response, dict) else ""
        assistant_text = _strip_control_protocol(str(assistant_text or ""))
        response_model = (
            llm_response.get("model")
            if isinstance(llm_response, dict)
            else model
        ) or model
        usage = llm_response.get("tokens") if isinstance(llm_response, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        prompt_tokens = int(usage.get("prompt") or usage.get("input") or 0)
        completion_tokens = int(usage.get("completion") or usage.get("output") or 0)
        total_tokens = int(usage.get("total") or (prompt_tokens + completion_tokens))
    completion_id = f"chatcmpl-{created}"

    if not stream:
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": response_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": assistant_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
            }
        )

    async def _stream():
        first_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": response_model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(first_chunk, ensure_ascii=True)}\n\n"
        for token in assistant_text.split(" "):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": response_model,
                "choices": [{"index": 0, "delta": {"content": f"{token} "}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=True)}\n\n"
            await asyncio.sleep(0)
        final_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": response_model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(final_chunk, ensure_ascii=True)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")

# In app.py

async def api_chat(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")
    prepared_payload, outbound_headers = await _prepare_middleware_chat_proxy(request, payload)
    url = f"{settings.API_BASE.rstrip('/')}/api/chat"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=prepared_payload, headers=outbound_headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Middleware chat failed: {exc}")

    try:
        body = resp.json()
    except Exception:
        body = {"detail": resp.text[:1000] if hasattr(resp, "text") else "invalid upstream response"}

    if resp.status_code >= 400:
        return JSONResponse(
            body if isinstance(body, dict) else {"detail": "Middleware chat failed"},
            status_code=resp.status_code,
        )
    return JSONResponse(
        body if isinstance(body, dict) else {"detail": "invalid upstream response"},
        status_code=resp.status_code,
    )


@rt("/api/decode_coordinate")
async def decode_coordinate(request: Request):
    """Resolve a ledger coordinate via middleware."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    coordinate = (payload.get("coordinate") or "").strip()
    if not coordinate:
        raise HTTPException(status_code=422, detail="coordinate is required")

    resolved = await _post_middleware_json(
        request,
        "/api/decode_coordinate",
        {"coordinate": coordinate},
    )
    return JSONResponse(resolved)


@rt("/api/ingest/file")
async def ingest_file(request: Request):
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=422, detail="file is required")

    kind = _form_str(form.get("kind"), "attachment").strip() or "attachment"

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ingest_provider = _form_str(form.get("provider") or form.get("model") or form.get("agent"))
    if _demo_offline_blocks_provider(session, {"provider": ingest_provider}):
        raise HTTPException(status_code=503, detail="demo_offline")
    ledger_id, entity, session = _resolve_session_scope(session_id, session)
    content = await upload.read()
    metadata = {
        "filename": upload.filename,
        "content_type": upload.content_type or "application/octet-stream",
        "size_bytes": len(content),
    }

    data = {
        "entity": entity,
        "kind": kind,
        "ledger_id": ledger_id,
        "session_id": str(session_id),
        "context_id": settings.FRONTEND_CONTEXT_ID,
        "metadata": json.dumps(metadata),
    }
    files = {
        "file": (
            upload.filename or "attachment",
            content,
            upload.content_type or "application/octet-stream",
        )
    }

    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.API_BASE.rstrip('/')}/api/ingest/file",
                data=data,
                files=files,
                headers=_middleware_session_headers(request),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    result = resp.json() if resp.content else {}

    if isinstance(result, dict):
        coord = result.get("coordinate") or result.get("entry_id") or result.get("web4_key")
        if coord:
            coord = _normalize_attachment_coord(str(coord))
            session.setdefault("attachment_coords", [])
            coords = session.get("attachment_coords")
            if isinstance(coords, list) and coord not in coords:
                coords.append(coord)
                session["attachment_coords"] = coords[-5:]
                update_session(session_id, session)

    return JSONResponse(result)


@rt("/api/ingest/stream-file")
async def ingest_stream_file(request: Request):
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=422, detail="file is required")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ingest_provider = _form_str(form.get("provider") or form.get("model") or form.get("agent"))
    if _demo_offline_blocks_provider(session, {"provider": ingest_provider}):
        raise HTTPException(status_code=503, detail="demo_offline")
    ledger_id, canonical_entity, session = _resolve_session_scope(session_id, session)
    entity = (_form_str(form.get("entity")) or canonical_entity).strip() or canonical_entity
    api.set_ledger(ledger_id)

    data = {}
    for key, value in form.multi_items():
        if key == "file":
            continue
        data[key] = _form_str(value, "")
    data.setdefault("entity", entity)
    data.setdefault("kind", "attachment")
    # Enforce canonical scope at proxy boundary to avoid payload/header drift.
    data["ledger_id"] = ledger_id
    if "context_id" not in data or not str(data.get("context_id") or "").strip():
        data["context_id"] = settings.FRONTEND_CONTEXT_ID

    files = {
        "file": (
            upload.filename or "attachment",
            upload.file,
            upload.content_type or "application/octet-stream",
        )
    }

    upstream_base = settings.API_BASE.rstrip('/')
    if _is_loopback_api_base(request):
        fallback_base = str(settings.BACKEND_ADMIN_BASE or "").strip().rstrip('/')
        if fallback_base:
            upstream_base = fallback_base
    url = f"{upstream_base}/api/ingest/file"
    headers = {
        key: value
        for key, value in api.headers.items()
        if key.lower() != "content-type"
    }

    async def _stream():
        async with httpx.AsyncClient(timeout=None) as client:
            yield json.dumps({"type": "status", "message": "Processing upload..."}) + "\n"
            resp = await client.post(
                url,
                data=data,
                files=files,
                headers=headers,
            )
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

            payload = resp.json() if resp.content else {}
            coord = payload.get("coordinate") or payload.get("entry_id") or payload.get("web4_key")
            if coord:
                coord = _normalize_attachment_coord(str(coord))
                session.setdefault("attachment_coords", [])
                coords = session.get("attachment_coords")
                if isinstance(coords, list) and coord not in coords:
                    coords.append(coord)
                    session["attachment_coords"] = coords[-5:]
                    update_session(session_id, session)
            yield json.dumps(
                {
                    "type": "meta",
                    "coordinate": coord,
                    "entity": data.get("entity"),
                }
            ) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@rt("/api/ingest/limits")
async def ingest_limits(request: Request):
    url = f"{settings.API_BASE.rstrip('/')}/api/ingest/limits"
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers=api.headers)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return JSONResponse(resp.json())
    except httpx.HTTPError as exc:
        fallback = {"attachment_max_bytes": settings.ATTACHMENT_MAX_BYTES}
        return JSONResponse(fallback)


@rt("/api/ledger/{ledger_id}/purpose")
async def ledger_purpose(request: Request, ledger_id: str):
    safe_ledger_id = (ledger_id or "").strip()
    if not safe_ledger_id:
        raise HTTPException(status_code=404, detail="ledger_id is required")
    url = f"{settings.API_BASE.rstrip('/')}/api/ledger/{safe_ledger_id}/purpose"
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers=api.headers)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return JSONResponse(resp.json())
    except httpx.HTTPError as exc:
        return JSONResponse({"ledger_id": safe_ledger_id, "purpose": None, "error": "upstream_failed"})


@rt("/api/chat/web4/decode")
async def decode_web4(request: Request):
    """Resolve a Web4 key using namespace + identifier via middleware."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    namespace = (payload.get("namespace") or "").strip()
    identifier = (payload.get("identifier") or "").strip()
    if not namespace or not identifier:
        raise HTTPException(status_code=422, detail="namespace and identifier are required")

    resolved = await _post_middleware_json(
        request,
        "/api/chat/web4/decode",
        {"namespace": namespace, "identifier": identifier},
    )
    return JSONResponse(resolved)

@rt("/api/chat/resolve-references")
async def resolve_references(request: Request):
    started_at = time.time()
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    debug_param = request.query_params.get("debug") or ""
    debug_enabled = str(debug_param).strip().lower() in {"1", "true", "yes", "on"}

    knowledge_tree = payload.get("knowledge_tree") or []
    appraisal = payload.get("appraisal") if isinstance(payload, dict) else None
    query = (payload.get("query") or "").strip()

    if not isinstance(knowledge_tree, list):
        raise HTTPException(status_code=422, detail="knowledge_tree must be a list")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id, entity, session = _resolve_session_scope(session_id, session)
    api.set_ledger(ledger_id)

    keywords = _extract_keywords(query)
    if not keywords:
        keywords = _extract_keywords(query.lower())
    selected_coords = _select_coords(knowledge_tree, appraisal if isinstance(appraisal, dict) else None)
    query_coords = extract_coords_from_text(query)
    merged_coords: list[str] = []
    seen_coords: set[str] = set()
    for coord in [*query_coords, *selected_coords]:
        if coord in seen_coords:
            continue
        seen_coords.add(coord)
        merged_coords.append(coord)
    selected_coords = merged_coords
    if not selected_coords and knowledge_tree:
        selected_coords = [
            item["coordinate"]
            for item in (_normalize_knowledge_tree_item(item) for item in knowledge_tree)
            if item
        ]

    attachment_coords = session.get("attachment_coords") if isinstance(session.get("attachment_coords"), list) else []
    if attachment_coords:
        normalized_attachment_coords = [
            _normalize_attachment_coord(str(coord))
            for coord in attachment_coords
            if coord
        ]
        # Keep only attachment coordinates that belong to the active entity to
        # avoid stale cross-session/cross-ledger references.
        attachment_coords = [
            coord for coord in normalized_attachment_coords
            if _extract_entity_from_entry({"coord": coord}) == entity
        ]
        if attachment_coords and (_query_mentions_attachment(query) or not selected_coords):
            for coord in attachment_coords:
                if coord not in selected_coords:
                    selected_coords.insert(0, coord)

    if not selected_coords:
        try:
            history_payload = await _get_middleware_json(
                request,
                f"/ledger/history/{quote(str(entity), safe='')}",
                params={"limit": 8},
            )
            entries_raw = history_payload.get("history")
            entries: list[Any] = entries_raw if isinstance(entries_raw, list) else []
        except Exception:
            entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            coord = entry.get("coordinate") or entry.get("key")
            if coord:
                selected_coords.append(str(coord))

    tier_map: dict[str, int] = {}
    for item in knowledge_tree:
        normalized = _normalize_knowledge_tree_item(item) if isinstance(item, dict) else None
        if not normalized:
            continue
        tier_map[str(normalized["coordinate"])] = int(normalized.get("tier_rank") or 0)

    resolved_snippets: list[dict[str, str | int]] = []
    decode_failures: list[dict[str, str]] = []

    async def _resolve_coord_via_middleware(coord: str) -> dict[str, Any]:
        return await _post_middleware_json(
            request,
            "/api/decode_coordinate",
            {"coordinate": coord},
        )

    for coord in selected_coords:
        try:
            resolved = await _resolve_coord_via_middleware(coord)
        except Exception:
            decode_failures.append({"coordinate": coord, "error": "decode_exception"})
            continue
        if not isinstance(resolved, dict) or not (resolved.get("coord") or resolved.get("canonical_coord")):
            detail = None
            if isinstance(resolved, dict):
                detail = resolved.get("detail")
            error = str(detail) if detail is not None else "decode_failed"
            decode_failures.append(
                {
                    "coordinate": coord,
                    "error": error,
                }
            )
            continue
        meta_raw = resolved.get("meta")
        payload_raw = resolved.get("payload")
        meta: dict[str, Any] = (
            cast(dict[str, Any], meta_raw) if isinstance(meta_raw, dict) else {}
        )
        payload: dict[str, Any] = (
            cast(dict[str, Any], payload_raw) if isinstance(payload_raw, dict) else {}
        )
        payload_parts = payload.get("parts") if isinstance(payload.get("parts"), list) else None

        added_part = False
        part_coords = _build_attachment_part_coords(
            meta,
            coord,
            keywords,
            payload_parts=payload_parts,
        )
        for part_coord in part_coords:
            try:
                part_resolved = await _resolve_coord_via_middleware(part_coord)
            except Exception:
                continue
            if not isinstance(part_resolved, dict) or not (
                part_resolved.get("coord") or part_resolved.get("canonical_coord")
            ):
                continue
            part_text = _normalize_decoded_payload(part_resolved)
            if part_text:
                resolved_snippets.append(
                    {
                        "text": str(part_text),
                        "coordinate": str(part_coord),
                        "tier_rank": tier_map.get(coord, 0),
                    }
                )
                added_part = True

        if not added_part:
            summary = _normalize_decoded_payload(resolved)
            if summary:
                resolved_snippets.append(
                    {
                        "text": str(summary),
                        "coordinate": str(coord),
                        "tier_rank": tier_map.get(coord, 0),
                    }
                )

    deduped_texts = _dedupe_snippets(
        [str(item["text"]) for item in resolved_snippets if item.get("text")]
    )
    deduped_snippets: list[dict[str, str | int]] = []
    used_texts: set[str] = set()
    empty_snippet_count = 0
    filtered_snippet_count = 0
    for item in resolved_snippets:
        text = item.get("text")
        if not text:
            empty_snippet_count += 1
            continue
        normalized = " ".join(str(text).lower().split())
        if normalized in used_texts or str(text) not in deduped_texts:
            filtered_snippet_count += 1
            continue
        used_texts.add(normalized)
        deduped_snippets.append(
            {
                "text": str(text),
                "coordinate": str(item.get("coordinate", "")),
                "tier_rank": int(item.get("tier_rank") or 0),
            }
        )

    if not deduped_snippets and query:
        try:
            search_payload = await api.search_any(
                query=query,
                limit=5,
                namespace_filter=[entity],
                namespace_mode="any",
            )
        except Exception:
            search_payload = {}

        results = search_payload.get("results") if isinstance(search_payload, dict) else None
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                entry_id = result.get("entry_id")
                if not entry_id:
                    entry_raw = result.get("entry")
                    entry: dict[str, Any] = (
                        cast(dict[str, Any], entry_raw)
                        if isinstance(entry_raw, dict)
                        else {}
                    )
                    entry_key_raw = entry.get("key")
                    entry_key: dict[str, Any] = (
                        cast(dict[str, Any], entry_key_raw)
                        if isinstance(entry_key_raw, dict)
                        else {}
                    )
                    namespace = entry_key.get("namespace")
                    identifier = entry_key.get("identifier")
                    if namespace and identifier:
                        entry_id = f"{namespace}:{identifier}"
                if not entry_id or not str(entry_id).startswith(entity):
                    continue
                coord = str(entry_id)
                try:
                    resolved = await _resolve_coord_via_middleware(coord)
                except Exception:
                    resolved = None
                if isinstance(resolved, dict):
                    text = _normalize_decoded_payload(resolved)
                    if text:
                        deduped_snippets.append({"text": str(text), "coordinate": coord})
                        continue
                snippet = result.get("snippet")
                if snippet:
                    deduped_snippets.append({"text": str(snippet), "coordinate": coord})

    if TIMING_DEBUG or RESOLVE_SNIPPET_DEBUG:
        for snippet in deduped_snippets:
            text = snippet.get("text") or ""
            coord = snippet.get("coordinate") or ""
            excerpt = str(text).replace("\n", " ").replace("\r", " ")[:200]
            print(
                "[resolve_references] snippet "
                f"coord={coord} len={len(str(text))} text={excerpt!r}"
            )
        print(
            "[resolve_references] snippet_summary "
            f"total={len(deduped_snippets)} empty={empty_snippet_count} filtered={filtered_snippet_count}"
        )

    html_payload = None if debug_enabled else _render_snippets_html(deduped_snippets)
    debug_stats = {}
    if isinstance(appraisal, dict):
        for key in ("score", "law_score", "grace_score", "drift"):
            value = appraisal.get(key) if key in appraisal else appraisal.get(key.replace("_", ""))
            if isinstance(value, (int, float)):
                debug_stats[key] = float(value)

    _log_timing(
        "resolve_references",
        started_at,
        {
            "selected": len(selected_coords),
            "snippets": len(deduped_snippets),
            "failures": len(decode_failures),
        },
    )
    response_payload = {
        "snippets": deduped_snippets,
        "debug_stats": debug_stats,
        "debug_selected": selected_coords,
        "debug_failures": decode_failures,
        "debug_tiers": tier_map,
        "resolve_summary": {
            "supports_coord_resolution": True,
            "requested_count": len(selected_coords),
            "resolved_count": len(deduped_snippets),
            "unresolved_count": len(decode_failures),
            "requested_coords": selected_coords,
            "unresolved_coords": [item.get("coordinate", "") for item in decode_failures],
        },
    }
    if html_payload is not None:
        response_payload["html"] = html_payload
    return JSONResponse(response_payload)


@rt("/api/chat/compose")
async def compose_answer(request: Request):
    started_at = time.time()
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = (payload.get("message") or "").strip()
    provider = (payload.get("provider") or settings.LLM_MODEL).strip()
    mode = (payload.get("mode") or "final").strip().lower()
    snippets = payload.get("snippets") if isinstance(payload, dict) else None

    if not message:
        raise HTTPException(status_code=422, detail="message is required")

    context_items: list[dict[str, str]] = []
    if isinstance(snippets, list):
        for item in snippets:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not text:
                continue
            coord = item.get("coordinate")
            if coord:
                context_items.append({"text": f"[{coord}] {text}"})
            else:
                context_items.append({"text": str(text)})
    if "snippets" in payload and snippets is not None and not context_items:
        raise HTTPException(status_code=422, detail="snippets empty after filtering")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    raw_history = session.get("messages")
    if not isinstance(raw_history, list):
        raw_history = []
    history = [
        item
        for item in raw_history
        if isinstance(item, dict)
        and item.get("content") != NO_MATCH_FALLBACK_TEXT
    ]

    system_prompt = (
        COMPOSE_SYSTEM_PROMPT_DRAFT
        if mode == "draft"
        else COMPOSE_SYSTEM_PROMPT_FINAL
    )
    response = await llm.generate_response(
        message=message,
        context=context_items if context_items else None,
        history=history,
        agent=provider or settings.LLM_MODEL,
        system_prompt=system_prompt,
    )
    _log_timing(
        "compose_answer",
        started_at,
        {
            "mode": mode,
            "model": response.get("model"),
            "snippets": len(context_items),
        },
    )

    cost = response.get("cost")
    if isinstance(cost, (int, float)):
        session["total_cost"] = session.get("total_cost", 0.0) + float(cost)
        update_session(session_id, session)

    return JSONResponse(response)


@rt("/api/chat/stream")
async def chat_stream(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id, entity, session = _resolve_session_scope(session_id, session)
    api.set_ledger(ledger_id)

    payload = dict(payload or {})
    payload.setdefault("session_id", session_id)
    payload.setdefault("entity", entity)
    payload.setdefault("enable_ledger", True)
    payload.setdefault("history", [])
    payload.setdefault("provider", settings.LLM_MODEL)

    url = f"{settings.API_BASE.rstrip('/')}/chat/stream"

    async def _stream():
        buffer = ""
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, json=payload, headers=api.headers) as resp:
                if resp.status_code >= 400:
                    detail = await resp.aread()
                    raise HTTPException(status_code=resp.status_code, detail=detail.decode())
                try:
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        try:
                            buffer += chunk.decode()
                            lines = buffer.split("\n")
                            buffer = lines.pop() or ""
                            for line in lines:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    data = json.loads(line)
                                except Exception:
                                    continue
                                if data.get("type") == "meta":
                                    latency_ms = data.get("latency_ms")
                                    if isinstance(latency_ms, (int, float)):
                                        session["last_latency_ms"] = int(latency_ms)
                                    appraisal = data.get("appraisal")
                                    if isinstance(appraisal, dict):
                                        session["last_appraisal"] = appraisal
                                    update_session(session_id, session)
                        except Exception:
                            pass
                        yield chunk
                except httpx.RemoteProtocolError:
                    yield json.dumps(
                        {"type": "error", "message": "Upstream stream closed early."}
                    ).encode() + b"\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@rt("/api/chat/stream/confirm")
async def confirm_chat_stream(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    coordinate = (payload.get("coordinate") or "").strip()
    if not coordinate:
        raise HTTPException(status_code=422, detail="coordinate is required")

    url = f"{settings.API_BASE.rstrip('/')}/chat/stream/confirm"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params={"coordinate": coordinate}, headers=api.headers)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return JSONResponse(resp.json())


@rt("/api/chat/commit-answer")
async def commit_answer(request: Request):
    started_at = time.time()
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = (payload.get("message") or "").strip()
    reply = (payload.get("reply") or "").strip()
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    precomputed_appraisal = payload.get("precomputed_appraisal") if isinstance(payload, dict) else None

    if not message or not reply:
        raise HTTPException(status_code=422, detail="message and reply are required")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id, entity, session = _resolve_session_scope(session_id, session)

    if "messages" not in session:
        session["messages"] = []
    session["messages"].append({"role": "user", "content": message})
    session["messages"].append({"role": "assistant", "content": reply})
    update_session(session_id, session)

    safe_meta = metadata if isinstance(metadata, dict) else {}
    safe_meta.setdefault("user_message", message)
    model_auth_context = await _verified_model_auth_context(request)
    safe_meta.setdefault(
        "runtime_identity",
        _build_runtime_identity_metadata(request, ledger_id=ledger_id, entity=entity, model_auth_context=model_auth_context),
    )
    if precomputed_appraisal is not None:
        safe_meta.setdefault("precomputed_appraisal", precomputed_appraisal)
    await _post_middleware_json(
        request,
        "/api/chat/commit-answer",
        {
            "entity": entity,
            "ledger_id": ledger_id,
            "message": message,
            "reply": reply,
            "precomputed_appraisal": precomputed_appraisal,
            "metadata": safe_meta,
        },
    )

    _log_timing(
        "commit_answer",
        started_at,
        {"entity": entity, "reply_chars": len(reply)},
    )
    return JSONResponse({"status": "ok"})


@rt("/api/chat/stream/commit")
async def commit_chat_stream(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = (payload.get("message") or "").strip()
    reply = (payload.get("reply") or "").strip()
    latency_ms = payload.get("latency_ms")
    metadata = payload.get("metadata") if isinstance(payload, dict) else None

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id, entity, session = _resolve_session_scope(session_id, session)

    if not isinstance(latency_ms, (int, float)):
        latency_ms = None

    def _estimate_turn_cost(text: str) -> float:
        p_tok = 50
        c_tok = len(text.split()) * 1.3
        return (p_tok * 5.0 + c_tok * 15.0) / 1_000_000

    turn_cost = _estimate_turn_cost(reply)
    session["last_latency_ms"] = latency_ms or session.get("last_latency_ms", 0)
    session["total_cost"] = session.get("total_cost", 0.0) + turn_cost

    if "messages" not in session:
        session["messages"] = []
    if message:
        session["messages"].append({"role": "user", "content": message})
    if reply:
        session["messages"].append({"role": "assistant", "content": reply})

    update_session(session_id, session)

    safe_meta = metadata if isinstance(metadata, dict) else {}
    safe_meta.setdefault("user_message", message)
    model_auth_context = await _verified_model_auth_context(request)
    safe_meta.setdefault(
        "runtime_identity",
        _build_runtime_identity_metadata(
            request, ledger_id=ledger_id, entity=entity, model_auth_context=model_auth_context
        ),
    )
    precomputed_appraisal = payload.get("precomputed_appraisal")
    if precomputed_appraisal is not None:
        safe_meta.setdefault("precomputed_appraisal", precomputed_appraisal)

    await _post_middleware_json(
        request,
        "/api/chat/commit-answer",
        {
            "entity": entity,
            "ledger_id": ledger_id,
            "message": message,
            "reply": reply,
            "precomputed_appraisal": precomputed_appraisal,
            "metadata": safe_meta,
        },
    )

    return JSONResponse(
        {
            "status": "ok",
            "total_cost": session.get("total_cost", 0.0),
            "last_latency": session.get("last_latency_ms", 0),
        }
    )


@rt("/api/log")
async def log_client_event(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")

    level = str(payload.get("level") or "info").lower()
    message = str(payload.get("message") or "")
    data = payload.get("data")
    print(f"[client-log] {level} {message} {data}")
    return JSONResponse({"status": "ok"})


@rt("/api/preferences/backend_stream", methods=["POST"])
async def set_backend_stream_preference(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")

    raw_enabled = payload.get("enabled")
    if isinstance(raw_enabled, bool):
        enabled = raw_enabled
    elif isinstance(raw_enabled, (int, float)):
        enabled = bool(raw_enabled)
    elif isinstance(raw_enabled, str):
        enabled = raw_enabled.strip().lower() in {"1", "true", "yes", "on"}
    else:
        raise HTTPException(status_code=422, detail="enabled must be a boolean")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    session["backend_stream_enabled"] = enabled
    update_session(session_id, session)

    return JSONResponse({"status": "ok", "enabled": enabled})


@rt("/api/all_entries")
async def all_entries(request: Request):
    """Fetch recent ledger entries across all namespaces."""
    try:
        limit = int(request.query_params.get("limit", 100))
    except ValueError:
        limit = 100
    limit = max(1, min(limit, HISTORY_DISCOVERY_LIMIT))

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id, _, session = _resolve_session_scope(session_id, session)

    try:
        entries = await _get_middleware_json(
            request,
            "/ledger/all",
            params={"limit": limit},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    rows = _iter_entry_records(entries)
    if rows:
        return JSONResponse(entries)

    # Fallback: synthesize entry-like rows from visible per-entity history.
    discovered: list[str] = []
    seen_entities: set[str] = set()
    try:
        payload = await _get_middleware_json(
            request,
            "/ledger/history_entities",
            params={"limit": max(limit * 4, HISTORY_DISCOVERY_LIMIT), "include_counts": "true"},
        )
    except Exception:
        payload = {}

    if isinstance(payload, dict):
        entities = payload.get("entities")
        if isinstance(entities, list):
            for value in entities:
                text = str(value or "").strip()
                if text and text not in seen_entities:
                    seen_entities.add(text)
                    discovered.append(text)

    synthesized: list[dict[str, Any]] = []
    per_entity_limit = max(5, min(50, max(1, limit // max(1, len(discovered) or 1))))
    for entity in discovered[:50]:
        try:
            history_payload = await _get_middleware_json(
                request,
                f"/ledger/history/{quote(str(entity), safe='')}",
                params={"limit": per_entity_limit},
            )
            history_rows = history_payload.get("history") if isinstance(history_payload.get("history"), list) else []
        except Exception:
            continue
        if not isinstance(history_rows, list):
            continue
        for row in history_rows:
            if not isinstance(row, dict):
                continue
            coordinate = str(row.get("coordinate") or "").strip()
            namespace = ""
            identifier = ""
            if ":" in coordinate:
                namespace, identifier = coordinate.rsplit(":", 1)
            metadata_raw = row.get("metadata")
            metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
            role = str(row.get("role") or metadata.get("role") or "assistant")
            content = str(row.get("content") or metadata.get("content") or "").strip()
            if not metadata:
                metadata = {"role": role, "content": content}
            if namespace and not metadata.get("entity"):
                metadata = dict(metadata)
                metadata["entity"] = namespace
            synthesized.append(
                {
                    "key": {"namespace": namespace, "identifier": identifier} if namespace and identifier else {},
                    "state": {"metadata": metadata},
                    "created_at": row.get("timestamp"),
                    "coordinate": coordinate or None,
                    "entry_id": row.get("entry_id"),
                }
            )

    synthesized.sort(
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    if len(synthesized) > limit:
        synthesized = synthesized[:limit]

    return JSONResponse({"entries": synthesized, "count": len(synthesized), "source": "history_fallback"})


async def _local_llm_chat(**kwargs) -> ChatResponse:
    """Local fallback ChatResponse placeholder."""
    return ChatResponse.from_json({"reply": "Local fallback not active", "stats": {}})


def _is_loopback_api_base(request: Request | None = None) -> bool:
    base = str(settings.API_BASE or "").strip()
    if not base:
        return False
    try:
        parsed = urlparse(base)
    except Exception:
        return False

    host = (parsed.hostname or "").lower()
    port = parsed.port
    if request is not None:
        req_host = str(getattr(request.url, "hostname", "") or "").lower()
        req_port = getattr(request.url, "port", None)
        if req_host and host == req_host:
            if port is None or req_port is None or port == req_port:
                return True
    # Without an incoming request context we cannot safely infer loopback.
    return False


def _empty_model_payload(reason: str) -> dict[str, Any]:
    return {
        "models": [],
        "local_models": [],
        "online_models": [],
        "fallback": False,
        "unavailable": True,
        "reason": reason,
    }


async def _fetch_middleware_models(mode: str, timeout: float, request: Request | None = None) -> dict[str, Any]:
    """Fetch model options from middleware; frontend does not synthesize provider catalogs."""
    mode_value = (mode or "default").strip().lower() or "default"

    # Guard against local self-loop misconfiguration where API_BASE points to this frontend app.
    if _is_loopback_api_base(request):
        return _empty_model_payload("middleware_loopback")

    url = f"{api.base_url.rstrip('/')}/api/models"
    params = {"mode": mode_value, "surface_id": settings.CHAT_SURFACE_ID}
    try:
        headers = dict(api.headers)
        headers["Accept"] = "application/json"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict):
                models = payload.get("models")
                if isinstance(models, list):
                    return payload
    except Exception:
        return _empty_model_payload("middleware_unavailable")

    return _empty_model_payload("middleware_invalid_response")


def _dedupe_models(models: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in models:
        mid = str(item.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        name = str(item.get("name") or "").strip()
        # The binding id may live in the id field or in the name field.
        binding_id = mid if mid.startswith("binding:") else (name if name.startswith("binding:") else "")
        if not name or name == mid or name.startswith("binding:"):
            if binding_id:
                generated = _display_name_from_binding_id(binding_id)
                if generated:
                    name = generated
            name = name or mid
        deduped.append({"id": mid, "name": name})
    return deduped


_DEPRECATED_MODEL_IDS = {
    "x-ai/grok-4-fast": "x-ai/grok-4.3",
}

_MODEL_DISPLAY_NAMES = {
    "x-ai/grok-4.3": "xAI: Grok 4.3",
}


def _display_name_for_model_id(model_id: str) -> str:
    return _MODEL_DISPLAY_NAMES.get(model_id, model_id)


def _display_name_from_binding_id(model_id: str) -> str | None:
    """Convert a binding id such as 'binding:chat:anthropic-claude-fable-5'
    into an OpenRouter-style label like 'Anthropic: Claude Fable 5'.
    """
    if not str(model_id or "").strip().startswith("binding:"):
        return None
    tail = model_id.split(":")[-1]
    if "-" not in tail:
        return None
    provider_slug, _, model_slug = tail.partition("-")
    provider = provider_slug.replace("_", " ").strip().title()
    model_name = model_slug.replace("-", " ").strip().title()
    if provider and model_name:
        return f"{provider}: {model_name}"
    return None


def _migrate_model_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Replace deprecated model ids with their successors and remove stale entries."""
    migrated: list[dict[str, str]] = []
    for row in rows:
        mid = str(row.get("id") or "").strip()
        name = str(row.get("name") or mid).strip()
        if mid in _DEPRECATED_MODEL_IDS:
            replacement = _DEPRECATED_MODEL_IDS[mid]
            name = name.replace(mid, replacement) if mid in name else _display_name_for_model_id(replacement)
            migrated.append({"id": replacement, "name": name})
        else:
            migrated.append({"id": mid, "name": name})
    return migrated


def _split_models_from_middleware(payload: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    models_raw = payload.get("models") if isinstance(payload, dict) else []
    local_raw = payload.get("local_models") if isinstance(payload, dict) else []
    online_raw = payload.get("online_models") if isinstance(payload, dict) else []

    models: list[dict[str, str]] = []
    if isinstance(models_raw, list):
        models = _dedupe_models([item for item in models_raw if isinstance(item, dict)])

    local_models: list[dict[str, str]] = []
    if isinstance(local_raw, list):
        local_models = _dedupe_models([item for item in local_raw if isinstance(item, dict)])

    online_models: list[dict[str, str]] = []
    if isinstance(online_raw, list):
        online_models = _migrate_model_rows(
            _dedupe_models([item for item in online_raw if isinstance(item, dict)])
        )

    if not local_models and not online_models and models:
        for item in models:
            mid = str(item.get("id") or "").strip()
            if "/" in mid and not mid.startswith("ollama/"):
                online_models.append(item)
            else:
                local_models.append({
                    "id": _normalize_local_model_id(mid),
                    "name": str(item.get("name") or mid).strip(),
                })

    local_models = [
        {
            "id": _normalize_local_model_id(item.get("id") or ""),
            "name": str(item.get("name") or item.get("id") or "").strip(),
        }
        for item in local_models
        if str(item.get("id") or "").strip()
    ]
    local_models = _migrate_model_rows(_dedupe_models(local_models))

    models = _dedupe_models([
        *local_models,
        *[item for item in online_models if str(item.get("id") or "").strip()],
    ])

    return models, local_models, online_models


async def set_agent(request: Request):
    """Persist the selected model; middleware remains model source of truth."""
    form = await request.form()
    agent = form.get("agent")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    selected_agent = str(agent or "").strip()
    selected_agent = _DEPRECATED_MODEL_IDS.get(selected_agent, selected_agent)

    timeout = settings.HTTP_TIMEOUT if settings.HTTP_TIMEOUT > 30 else 60.0
    middleware_payload = await _fetch_middleware_models("full", timeout, request)
    models_data, _, _ = _split_models_from_middleware(middleware_payload)
    available_ids = {item.get("id") for item in models_data if item.get("id")}
    if KIMI_PRINCIPAL_DID:
        available_ids.add("delegated:kimi")

    if not selected_agent:
        preferred = str(settings.LLM_MODEL or "").strip()
        if preferred and preferred in available_ids:
            selected_agent = preferred
        elif models_data:
            selected_agent = str(models_data[0].get("id") or "").strip()
        else:
            selected_agent = preferred

    if not selected_agent:
        selected_agent = str(settings.LLM_MODEL or "").strip()

    session["agent"] = selected_agent
    update_session(session_id, session)
    return JSONResponse({"status": "ok", "agent": selected_agent, "requested_agent": agent})


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _hash64_text(value: str) -> str:
    return hashlib.blake2b(str(value or "").encode("utf-8"), digest_size=8).hexdigest()


def _configured_sync_ledgers_h64() -> list[str]:
    raw_multi = str(os.getenv("SYNC_LEDGER_IDS_H64", "")).strip()
    raw_single = str(os.getenv("SYNC_LEDGER_ID_H64", "")).strip()
    candidates: list[str] = []
    if raw_multi:
        candidates.extend(part.strip() for part in raw_multi.replace(";", ",").split(","))
    if raw_single:
        candidates.append(raw_single)
    if not candidates:
        candidates.append(_hash64_text(DEFAULT_LEDGER_ID or settings.DEFAULT_LEDGER_ID or "default"))

    seen: set[str] = set()
    normalized: list[str] = []
    for item in candidates:
        text = str(item or "").strip().lower()
        if text.startswith("0x"):
            text = text[2:]
        if len(text) != 16:
            continue
        try:
            int(text, 16)
        except Exception:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


async def _sync_pull_v0(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    ledger_id_h64: str,
    peer_id: str,
    cursors: dict[str, int],
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    resp = await client.post(
        f"{base_url.rstrip('/')}/sync/v0/pull",
        json={
            "peer_id": peer_id,
            "ledger_id_h64": ledger_id_h64,
            "cursors": cursors,
            "limit": limit,
        },
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"pull failed ({base_url}): {resp.status_code} {resp.text}")
    body = resp.json() if resp.content else {}
    items_raw = body.get("items") if isinstance(body, dict) else []
    items = [item for item in items_raw if isinstance(item, dict)] if isinstance(items_raw, list) else []
    next_raw = body.get("next_cursors") if isinstance(body, dict) else {}
    next_cursors: dict[str, int] = {}
    if isinstance(next_raw, dict):
        for key, value in next_raw.items():
            try:
                next_cursors[str(key)] = int(value)
            except Exception:
                continue
    return items, next_cursors


async def _sync_push_v0(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    ledger_id_h64: str,
    peer_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    push_items: list[dict[str, Any]] = []
    for item in items:
        envelope_hex = str(item.get("envelope_hex") or "").strip()
        if envelope_hex:
            push_items.append({"envelope_hex": envelope_hex, "allow_backfill": False})

    if not push_items:
        return {"accepted": 0, "duplicate": 0, "quarantine": 0}

    resp = await client.post(
        f"{base_url.rstrip('/')}/sync/v0/push",
        json={
            "peer_id": peer_id,
            "ledger_id_h64": ledger_id_h64,
            "items": push_items,
        },
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"push failed ({base_url}): {resp.status_code} {resp.text}")
    body = resp.json() if resp.content else {}
    return body if isinstance(body, dict) else {"accepted": 0, "duplicate": 0, "quarantine": 0}


async def _run_manual_sync_direction(
    *,
    client: httpx.AsyncClient,
    source_api: str,
    target_api: str,
    ledger_id_h64: str,
    peer_id: str,
    limit: int,
    max_rounds: int,
) -> dict[str, int]:
    cursors: dict[str, int] = {}
    totals = {"rounds": 0, "pulled": 0, "accepted": 0, "duplicate": 0, "quarantine": 0}
    for _ in range(max_rounds):
        items, next_cursors = await _sync_pull_v0(
            client=client,
            base_url=source_api,
            ledger_id_h64=ledger_id_h64,
            peer_id=peer_id,
            cursors=cursors,
            limit=limit,
        )
        if not items:
            break
        result = await _sync_push_v0(
            client=client,
            base_url=target_api,
            ledger_id_h64=ledger_id_h64,
            peer_id=peer_id,
            items=items,
        )
        totals["rounds"] += 1
        totals["pulled"] += len(items)
        totals["accepted"] += _as_int(result.get("accepted"), 0)
        totals["duplicate"] += _as_int(result.get("duplicate"), 0)
        totals["quarantine"] += _as_int(result.get("quarantine"), 0)
        if _as_int(result.get("quarantine"), 0) > 0:
            break
        cursors = next_cursors
    return totals


async def manual_sync_all_ledgers(_: Request):
    local_api = str(os.getenv("LOCAL_API", "")).strip()
    cloud_api = str(os.getenv("CLOUD_API", "")).strip()
    ledgers = _configured_sync_ledgers_h64()
    if not ledgers:
        return JSONResponse(
            {"status": "error", "message": "No valid sync ledger scopes configured."},
            status_code=400,
        )

    limit = max(1, min(_as_int(os.getenv("SYNC_BATCH_LIMIT", "200"), 200), 500))
    max_rounds = max(1, min(_as_int(os.getenv("MANUAL_SYNC_MAX_ROUNDS", str(MANUAL_SYNC_MAX_ROUNDS_DEFAULT)), MANUAL_SYNC_MAX_ROUNDS_DEFAULT), 20))
    peer_id = str(os.getenv("SYNC_PEER_ID", "frontend-manual-sync")).strip() or "frontend-manual-sync"

    summary: list[dict[str, Any]] = []
    total_accepted = 0
    total_quarantine = 0
    total_pulled = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            for ledger_h64 in ledgers:
                local_to_cloud = await _run_manual_sync_direction(
                    client=client,
                    source_api=local_api,
                    target_api=cloud_api,
                    ledger_id_h64=ledger_h64,
                    peer_id=peer_id,
                    limit=limit,
                    max_rounds=max_rounds,
                )
                cloud_to_local = await _run_manual_sync_direction(
                    client=client,
                    source_api=cloud_api,
                    target_api=local_api,
                    ledger_id_h64=ledger_h64,
                    peer_id=peer_id,
                    limit=limit,
                    max_rounds=max_rounds,
                )
                summary.append(
                    {
                        "ledger_id_h64": ledger_h64,
                        "local_to_cloud": local_to_cloud,
                        "cloud_to_local": cloud_to_local,
                    }
                )
                total_accepted += local_to_cloud["accepted"] + cloud_to_local["accepted"]
                total_quarantine += local_to_cloud["quarantine"] + cloud_to_local["quarantine"]
                total_pulled += local_to_cloud["pulled"] + cloud_to_local["pulled"]
    except Exception as exc:
        return JSONResponse(
            {
                "status": "error",
                "message": f"Manual sync failed: {exc}",
            },
            status_code=502,
        )

    message = (
        f"Manual sync completed: pulled={total_pulled}, accepted={total_accepted}, quarantine={total_quarantine}."
    )
    return JSONResponse(
        {
            "status": "ok",
            "message": message,
            "ledgers": summary,
            "local_api": local_api,
            "cloud_api": cloud_api,
        }
    )



@rt("/api/export")
async def export_conversation(request: Request):
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id, entity, session = _resolve_session_scope(session_id, session)

    try:
        payload = await _get_middleware_json(
            request,
            f"/ledger/history/{quote(str(entity), safe='')}",
            params={"limit": 500},
        )
        history = payload.get("history") if isinstance(payload.get("history"), list) else payload.get("messages") or []
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if isinstance(history, dict):
        history = history.get("history") or history.get("messages") or []
    if not isinstance(history, list):
        history = []

    return {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "entity": entity,
        "ledger_id": ledger_id,
        "messages": history,
    }

async def list_models(request: Request):
    """Expose middleware-provided model options; frontend only persists selection."""

    mode = (request.query_params.get("mode") or "default").lower()
    timeout = settings.HTTP_TIMEOUT if settings.HTTP_TIMEOUT > 30 else 60.0

    payload = await _fetch_middleware_models(mode, timeout, request)
    models, local_models, online_models = _split_models_from_middleware(payload)
    fallback_used = bool(payload.get("fallback")) if isinstance(payload, dict) else False

    if not local_models and not online_models and models:
        for item in models:
            mid = str(item.get("id") or "").strip()
            if mid.startswith("ollama/"):
                local_models.append(item)
            elif mid.startswith("binding:") or "/" in mid:
                online_models.append(item)
            else:
                local_models.append(item)

    # Surface the Kimi Code delegated agent as a selectable option when configured.
    delegated_models: list[dict[str, str]] = []
    if KIMI_PRINCIPAL_DID:
        delegated_models.append({"id": "delegated:kimi", "name": "Moonshot: Kimi-code"})

    accept_header = (request.headers.get("accept") or "").lower()
    is_htmx = (request.headers.get("hx-request") or "").lower() == "true"
    wants_json = "application/json" in accept_header and not is_htmx

    if not wants_json:
        session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
        session = get_session(session_id)
        current_model = str(session.get("agent") or "").strip()
        current_model = _DEPRECATED_MODEL_IDS.get(current_model, current_model)
        if current_model != str(session.get("agent") or "").strip():
            session["agent"] = current_model
            update_session(session_id, session)

        # Only rescue a saved model when the middleware itself could not be reached.
        # When the middleware responds, the control-plane relationship gate is authoritative.
        if current_model and payload.get("unavailable") and all(item.get("id") != current_model for item in models):
            models = [{"id": current_model, "name": f"{current_model} (Saved)"}, *models]
        if not current_model and models:
            current_model = str(models[0].get("id") or "").strip()

        local_options = tuple(
            Option(model["name"], value=model["id"], selected=(model["id"] == current_model))
            for model in local_models
        ) or (Option("No local models found", value="", disabled=True),)

        online_options = tuple(
            Option(model["name"], value=model["id"], selected=(model["id"] == current_model))
            for model in online_models
        ) or (Option("No online models configured", value="", disabled=True),)

        delegated_options = tuple(
            Option(model["name"], value=model["id"], selected=(model["id"] == current_model))
            for model in delegated_models
        ) or (Option("No delegated agents configured", value="", disabled=True),)

        groups = [
            Optgroup(*local_options, label="Ollama (local)"),
            Optgroup(*online_options, label="OpenRouter (online)"),
        ]
        if delegated_models:
            groups.append(Optgroup(*delegated_options, label="Delegated agents"))
        return tuple(groups)

    return {
        "models": models,
        "local_models": local_models,
        "online_models": online_models,
        "delegated_models": delegated_models,
        "fallback": fallback_used,
        "unavailable": bool(payload.get("unavailable")) if isinstance(payload, dict) else False,
        "reason": str(payload.get("reason") or "").strip() or None if isinstance(payload, dict) else None,
    }


async def models_debug(request: Request):
    timeout = settings.HTTP_TIMEOUT if settings.HTTP_TIMEOUT > 30 else 60.0
    local = await _fetch_local_models_debug(timeout)
    return {
        "llm_base_url": os.getenv("LLM_BASE_URL"),
        "settings_llm_model": settings.LLM_MODEL,
        "settings_llm_provider": settings.LLM_PROVIDER,
        "local": local,
    }


async def list_ledgers(request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Ledger management disabled")

    session_id = (
        request.cookies.get("ds_session")
        or request.query_params.get("session_id")
        or DEFAULT_SESSION_ID
    )
    session = get_session(session_id)
    active_ledger, _, session = _resolve_session_scope(session_id, session)

    try:
        body = await _get_middleware_json(request, "/admin/ledgers")
        ledgers_raw = body.get("ledgers")
        ledgers = (
            [str(item).strip() for item in ledgers_raw if str(item).strip()]
            if isinstance(ledgers_raw, list)
            else []
        )
    except Exception:
        # Fall back to at least returning the active ledger if the backend call fails
        ledgers = []

    ledgers = sorted({*ledgers, active_ledger})
    # Hide legacy/default from the picker once a provisioned demo ledger is active.
    if active_ledger and active_ledger != "default":
        ledgers = [item for item in ledgers if str(item).strip() != "default"]
    return {"active_ledger": active_ledger, "ledgers": ledgers}


async def create_or_switch_ledger(request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Ledger management disabled")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    ledger_id = (payload.get("ledger_id") or payload.get("name") or "").strip()
    session_id = (
        request.cookies.get("ds_session")
        or payload.get("session_id")
        or DEFAULT_SESSION_ID
    )

    if not ledger_id:
        raise HTTPException(status_code=422, detail="ledger_id is required")

    try:
        upstream = await api.create_or_switch_ledger(ledger_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    resolved_ledger = (
        (upstream.get("ledger_id") if isinstance(upstream, dict) else None)
        or (upstream.get("active_ledger") if isinstance(upstream, dict) else None)
        or ledger_id
    )

    session = get_session(session_id)
    session["ledger_id"] = resolved_ledger
    session["entity"] = build_entity_namespace(str(resolved_ledger), session_id)
    session["attachment_coords"] = []
    update_session(session_id, session)
    api.set_ledger(resolved_ledger)

    response = {"ledger_id": resolved_ledger}
    if resolved_ledger != ledger_id:
        response["requested_ledger_id"] = ledger_id
    return response


async def ledgers_inventory(request: Request):
    session_id = (
        request.cookies.get("ds_session")
        or request.query_params.get("session_id")
        or DEFAULT_SESSION_ID
    )
    session = get_session(session_id)
    active_ledger, current_entity, session = _resolve_session_scope(session_id, session)
    active_ledger = str(active_ledger)
    current_entity = str(current_entity)
    hostname = str(getattr(request.url, "hostname", "") or "").lower()
    default_context_id = "ctx:frontend:vercel" if "vercel" in hostname else "ctx:frontend:local"
    context_id = str(
        session.get("context_id")
        or request.headers.get("x-context-id")
        or settings.FRONTEND_CONTEXT_ID
        or default_context_id
    )
    principal_id = str(session.get("principal_id") or settings.FRONTEND_PRINCIPAL_ID or "demo-user")
    principal_type = str(session.get("principal_type") or settings.FRONTEND_PRINCIPAL_TYPE or "user")
    contributor_id = f"{principal_type}:{principal_id}"
    api.set_ledger(active_ledger)

    inventory: dict[str, Any] = {
        "session": {
            "session_id": session_id,
            "active_ledger": active_ledger,
            "current_entity": current_entity,
            "demo_offline": bool(session.get("demo_offline", False)),
            "context_id": context_id,
            "principal_id": principal_id,
            "principal_type": principal_type,
            "principal_did": str(session.get("principal_did") or "").strip(),
            "contributor_id": contributor_id,
            "tenant_id": str(session.get("tenant_id") or settings.FRONTEND_TENANT_ID or ""),
        },
        "middleware": {
            "base": settings.API_BASE,
            "ledgers": [],
            "active_ledger": active_ledger,
        },
        "backend_admin": {
            "enabled": bool((settings.BACKEND_ADMIN_TOKEN or "").strip()),
            "base": settings.BACKEND_ADMIN_BASE,
            "ledgers": [],
            "ledger_records": [],
        },
        "history_entities": {
            "current_entity": current_entity,
            "entities": [current_entity],
        },
        "probes": {
            "history_counts": {},
            "middleware_namespaces": [],
        },
    }

    try:
        middleware_ledgers_body = await _get_middleware_json(request, "/admin/ledgers")
        middleware_ledgers = middleware_ledgers_body.get("ledgers")
        if isinstance(middleware_ledgers, list):
            inventory["middleware"]["ledgers"] = sorted(
                {str(item).strip() for item in middleware_ledgers if str(item).strip()}
            )
            if inventory["middleware"]["ledgers"]:
                inventory["middleware"]["active_ledger"] = str(
                    middleware_ledgers_body.get("active_ledger")
                    or inventory["middleware"]["ledgers"][0]
                )
    except Exception as exc:
        inventory["middleware"]["error"] = str(exc)

    try:
        discovered = await asyncio.wait_for(
            _get_middleware_json(
                request,
                "/ledger/history_entities",
                params={"limit": HISTORY_DISCOVERY_LIMIT, "include_counts": "true"},
                timeout=LEDGER_INVENTORY_DISCOVERY_TIMEOUT_SECONDS,
            ),
            timeout=LEDGER_INVENTORY_DISCOVERY_TIMEOUT_SECONDS,
        )
        if isinstance(discovered, dict):
            current = str(discovered.get("current_entity") or current_entity)
            entities_raw = discovered.get("entities")
            entities = (
                [str(item).strip() for item in entities_raw if str(item).strip()]
                if isinstance(entities_raw, list)
                else [current]
            )
            if current and current not in entities:
                entities.append(current)
            inventory["history_entities"] = {
                "current_entity": current,
                "entities": sorted(set(entities)),
            }
    except Exception as exc:
        inventory["history_entities"]["error"] = str(exc)

    entities_for_probe = inventory["history_entities"].get("entities") or []
    for entity in entities_for_probe[:LEDGER_INVENTORY_MAX_PROBE_ENTITIES]:
        try:
            history_payload = await asyncio.wait_for(
                _get_middleware_json(
                    request,
                    f"/ledger/history/{quote(str(entity), safe='')}",
                    params={"limit": 20},
                    timeout=LEDGER_INVENTORY_THREAD_TIMEOUT_SECONDS,
                ),
                timeout=LEDGER_INVENTORY_THREAD_TIMEOUT_SECONDS,
            )
            history = history_payload.get("history") if isinstance(history_payload.get("history"), list) else []
            inventory["probes"]["history_counts"][entity] = {
                "status": "ok",
                "count": len(history) if isinstance(history, list) else 0,
            }
        except Exception as exc:
            inventory["probes"]["history_counts"][entity] = {
                "status": "error",
                "error": str(exc),
            }

    try:
        payload = await _get_middleware_json(
            request,
            "/ledger/all",
            params={"limit": 400},
            timeout=settings.HTTP_TIMEOUT,
        )
        entries: list[dict[str, Any]] = []
        if isinstance(payload, list):
            entries = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            for key in ("entries", "items", "recent", "results", "history", "messages"):
                value = payload.get(key)
                if isinstance(value, list):
                    entries.extend(item for item in value if isinstance(item, dict))
        namespaces: set[str] = set()
        for item in entries:
            key_obj = item.get("key")
            if isinstance(key_obj, dict):
                namespace = str(key_obj.get("namespace") or "").strip()
                if namespace:
                    namespaces.add(namespace)
        inventory["probes"]["middleware_namespaces"] = sorted(namespaces)
    except Exception as exc:
        inventory["probes"]["middleware_namespaces_error"] = str(exc)

    backend_admin_token = (settings.BACKEND_ADMIN_TOKEN or "").strip()
    if backend_admin_token:
        try:
            async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
                resp = await client.get(
                    f"{settings.BACKEND_ADMIN_BASE.rstrip('/')}/admin/ledgers",
                    headers={"x-admin-token": backend_admin_token},
                )
                if resp.status_code >= 400:
                    inventory["backend_admin"]["status"] = resp.status_code
                    inventory["backend_admin"]["error"] = resp.text
                else:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        ledgers = payload.get("ledgers")
                        ledger_records = payload.get("ledger_records")
                        if isinstance(ledgers, list):
                            inventory["backend_admin"]["ledgers"] = ledgers
                        if isinstance(ledger_records, list):
                            inventory["backend_admin"]["ledger_records"] = [
                                row for row in ledger_records if isinstance(row, dict)
                            ]
                        inventory["backend_admin"]["status"] = 200
                    elif isinstance(payload, list):
                        inventory["backend_admin"]["ledgers"] = payload
                        inventory["backend_admin"]["status"] = 200
        except Exception as exc:
            inventory["backend_admin"]["error"] = str(exc)

    ledger_records = inventory["backend_admin"].get("ledger_records")
    if isinstance(ledger_records, list):
        for record in ledger_records:
            if not isinstance(record, dict):
                continue
            if str(record.get("ledger_id") or "").strip() != active_ledger:
                continue
            tenant_id = str(record.get("tenant_id") or "").strip()
            owner_principal_id = str(record.get("owner_principal_id") or "").strip()
            owner_principal_type = str(record.get("owner_principal_type") or "").strip()
            if tenant_id and tenant_id not in {"tenant:unknown", "unknown"}:
                inventory["session"]["tenant_id"] = tenant_id
            if owner_principal_id and owner_principal_id not in {"unknown", "anonymous"}:
                inventory["session"]["principal_id"] = owner_principal_id
            if owner_principal_type and owner_principal_type not in {"unknown", "anonymous"}:
                inventory["session"]["principal_type"] = owner_principal_type
            inventory["session"]["contributor_id"] = (
                f"{inventory['session']['principal_type']}:{inventory['session']['principal_id']}"
            )
            metadata = record.get("metadata")
            if isinstance(metadata, dict):
                allowed_context_ids = metadata.get("allowed_context_ids")
                if isinstance(allowed_context_ids, list):
                    inventory["session"]["allowed_context_ids"] = [
                        str(item).strip() for item in allowed_context_ids if str(item).strip()
                    ]
            break

    return JSONResponse(inventory)


@rt("/api/demo/offline-toggle", methods=["POST"])
async def demo_offline_toggle(request: Request):
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    next_state = not bool(session.get("demo_offline", False))
    try:
        payload = await request.json()
    except Exception:
        payload = None
    if isinstance(payload, dict) and "offline" in payload:
        next_state = bool(payload.get("offline"))
    session["demo_offline"] = next_state
    update_session(session_id, session)
    return JSONResponse(
        {
            "offline": next_state,
            "label": "Go Online" if next_state else "Go Offline",
            "mode": "offline" if next_state else "online",
        }
    )


@rt("/api/demo/network-probe", methods=["GET"])
async def demo_network_probe(_: Request):
    response = PlainTextResponse(DEMO_NETWORK_PROBE_PAYLOAD)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@rt("/api/deploy-info", methods=["GET"])
async def deploy_info(_: Request):
    return JSONResponse(
        {
            "status": "ok",
            "vercel_commit_sha": (os.getenv("VERCEL_GIT_COMMIT_SHA") or "").strip() or "unknown",
            "api_base": settings.API_BASE.rstrip("/"),
            "backend_admin_base": settings.BACKEND_ADMIN_BASE.rstrip("/"),
        }
    )


app.route("/api/chat", methods=["POST"])(api_chat)
app.route("/api/set-agent", methods=["POST"])(set_agent)
app.route("/api/sync/all", methods=["POST"])(manual_sync_all_ledgers)
app.route("/api/models")(list_models)
app.route("/api/models/debug")(models_debug)
app.route("/api/ledgers", methods=["GET"])(list_ledgers)
app.route("/api/ledgers", methods=["POST"])(create_or_switch_ledger)
app.route("/api/ledgers/inventory", methods=["GET"])(ledgers_inventory)


@rt("/api/onboarding/model-library", methods=["GET"])
async def onboarding_model_library(_: Request):
    """Proxy to middleware /account/current/model-library."""
    try:
        data = await api.get_model_library()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/onboarding/model-library/select", methods=["POST"])
async def onboarding_model_library_select(request: Request):
    """Proxy to middleware /account/current/model-library/select."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    provider = str(payload.get("provider") or "").strip()
    model_id = str(payload.get("model_id") or "").strip()
    if not provider or not model_id:
        return JSONResponse({"error": "provider_and_model_id_required"}, status_code=422)
    try:
        data = await api.select_model(
            provider=provider,
            model_id=model_id,
            api_key=payload.get("api_key"),
            base_url=payload.get("base_url"),
        )
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/onboarding/principals", methods=["GET"])
async def onboarding_principals(_: Request):
    """Proxy to middleware /account/current/principals."""
    try:
        data = await api.get_account_principals()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/onboarding/principals/agent/bootstrap", methods=["POST"])
async def onboarding_agent_bootstrap(_: Request):
    """Proxy to middleware /account/current/principals/agent/bootstrap."""
    try:
        data = await api.bootstrap_agent_principal()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/onboarding/connections", methods=["GET"])
async def onboarding_connections(_: Request):
    """Proxy to middleware /account/current/connections."""
    try:
        data = await api.get_connections()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/onboarding/status", methods=["GET"])
async def onboarding_status(_: Request):
    """Proxy to middleware /account/current/onboarding."""
    try:
        data = await api.get_onboarding_status()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/setup-prompt", methods=["GET"])
async def setup_prompt(_: Request):
    """Proxy to middleware /account/current/setup-prompt."""
    try:
        data = await api.get_setup_prompt()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/setup-prompt/dismiss", methods=["POST"])
async def setup_prompt_dismiss(request: Request):
    """Proxy to middleware /account/current/setup-prompt/dismiss."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    mode = str(payload.get("mode") or "").strip()
    if not mode:
        return JSONResponse({"error": "mode_required"}, status_code=422)
    try:
        data = await api.dismiss_setup_prompt(mode=mode, snoozed_until=payload.get("snoozed_until"))
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/wallet/credential-offer", methods=["GET"])
async def wallet_credential_offer(request: Request):
    """Proxy to middleware /wallet/credential-offer."""
    session_id = str(request.query_params.get("session_id") or "").strip()
    wallet_provider = str(request.query_params.get("wallet_provider") or "microsoft_authenticator").strip()
    if not session_id:
        return JSONResponse({"error": "session_id_required"}, status_code=422)
    try:
        data = await api.get_wallet_credential_offer(session_id=session_id, wallet_provider=wallet_provider)
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/wallet/providers", methods=["GET"])
async def wallet_providers(_: Request):
    """Proxy to middleware /wallet/providers."""
    try:
        data = await api.get_wallet_providers()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/onboarding/submit", methods=["POST"])
async def onboarding_submit(request: Request):
    """Proxy to middleware /account/current/onboarding."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    required = ["owner_display_name", "workspace_or_dss_space_label", "primary_contact", "pilot_use_case", "free_trial_scope_acknowledgement", "idempotency_key"]
    missing = [f for f in required if not payload.get(f)]
    if missing:
        return JSONResponse({"error": "missing_required_fields", "fields": missing}, status_code=422)
    try:
        data = await api.submit_onboarding(payload=payload)
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/provisioning/status", methods=["GET"])
async def provisioning_status(_: Request):
    """Proxy to middleware /account/current/provisioning."""
    try:
        data = await api.get_provisioning_status()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/provisioning/run", methods=["POST"])
async def provisioning_run(_: Request):
    """Proxy to middleware /account/current/provisioning/run."""
    try:
        data = await api.run_provisioning()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/wallet/link/start", methods=["POST"])
async def wallet_link_start(request: Request):
    """Proxy to middleware /account/current/identity/wallet-link/start."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    provider = str(payload.get("provider") or "").strip()
    try:
        data = await api.start_wallet_link(provider=provider or None)
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/wallet/link/complete", methods=["POST"])
async def wallet_link_complete(request: Request):
    """Proxy to middleware /account/current/identity/wallet-link/complete."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    provider = str(payload.get("provider") or "").strip() or None
    wallet_did = str(payload.get("wallet_did") or "").strip() or None
    try:
        data = await api.complete_wallet_link(provider=provider, wallet_did=wallet_did)
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/account/current", methods=["GET"])
async def account_current(_: Request):
    """Proxy to middleware /account/current."""
    try:
        data = await api.get_account_summary()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/account/subscription", methods=["GET"])
async def account_subscription(_: Request):
    """Proxy to middleware /account/current/subscription."""
    try:
        data = await api.get_account_subscription()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/setup-checklist", methods=["GET"])
async def setup_checklist(_: Request):
    """Proxy to middleware /account/current/setup-checklist."""
    try:
        data = await api.get_setup_checklist()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/surfaces", methods=["GET"])
async def surfaces(_: Request):
    """Proxy to middleware /account/current/surfaces."""
    try:
        data = await api.get_surfaces()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/identity", methods=["GET"])
async def identity(_: Request):
    """Proxy to middleware /account/current/identity."""
    try:
        data = await api.get_identity()
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/admin/provisioning/jobs/{job_id}", methods=["GET"])
async def admin_provisioning_job(request: Request):
    """Proxy to middleware /admin/provisioning/jobs/{job_id}."""
    job_id = request.path_params.get("job_id", "")
    if not job_id:
        return JSONResponse({"error": "job_id_required"}, status_code=422)
    try:
        data = await api.get_admin_provisioning_job(job_id=job_id)
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


@rt("/api/admin/provisioning/jobs/{job_id}/steps", methods=["GET"])
async def admin_provisioning_job_steps(request: Request):
    """Proxy to middleware /admin/provisioning/jobs/{job_id}/steps."""
    job_id = request.path_params.get("job_id", "")
    if not job_id:
        return JSONResponse({"error": "job_id_required"}, status_code=422)
    try:
        data = await api.get_admin_provisioning_job_steps(job_id=job_id)
        return JSONResponse(data)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        return JSONResponse({"error": "upstream_error", "detail": detail}, status_code=status)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": "upstream_unavailable", "detail": str(exc)}, status_code=502)


if __name__ == "__main__":
    serve()
