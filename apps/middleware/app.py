import os
import re
import hashlib
import hmac
import secrets
import logging
import time
import html
import json
import base64
import httpx
import prometheus_client
import asyncio
from datetime import datetime
from urllib.parse import unquote, parse_qs, urlparse, urlencode, quote
from typing import Any, cast
from fasthtml.common import Button, Div, Input, Option, P, Script, Title, fast_app, serve
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, StreamingResponse, RedirectResponse, Response
from starlette.requests import Request
from starlette.exceptions import HTTPException
from starlette.datastructures import UploadFile

from api.llm import PRICING, llm, set_openrouter_api_key
from api.client import BackendDecodeError, ChatResponse, api
from config.settings import DEFAULT_LEDGER_ID, DEFAULT_SESSION_ID, settings
from routes.orchestrator import register_orchestrator_routes, _synthesize_field_state
from routes.wake import register_wake_routes
from routes.agent import register_agent_routes
from utils.session import build_entity_namespace, get_session, update_session
from utils.qp_pure_metrics import qp_pure_metrics
from utils.stats import build_stats_payload
from utils.text_processing import COORD_PATTERN, extract_coords_from_text, truncate_text, normalize_coord_token
from utils.execution_governor import ExecutionGovernor
from utils.auth_envelope import build_backend_auth_envelope
from utils.control_plane_registry import ControlPlaneRegistry
from utils.principal_registry import PrincipalRegistry
from utils.principal_link_challenges import PrincipalLinkChallenges
from utils.mcp_server import DSMCPServer
from utils.mcp_oauth_dev import DevOAuthProvider
from utils.openrouter_config import get_api_key as _get_openrouter_override, set_api_key as _set_openrouter_override
from utils.verified_id_requests import VerifiedIDRequests

MAX_DECODED_COORDS = 18
MAX_SUMMARY_CHARS = 220
MAX_CLAIMS_CHARS = 200
MAX_CONTEXT_CHARS = 1200
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
_ADAPTIVE_PROVIDER_MARKERS = tuple(
    marker.strip().lower()
    for marker in settings.ADAPTIVE_EXECUTION_LOCAL_PROVIDER_MARKERS.split(",")
    if marker and marker.strip()
)
EXECUTION_GOVERNOR = ExecutionGovernor(
    enabled=settings.ADAPTIVE_EXECUTION_ENABLED,
    force_profile=settings.ADAPTIVE_EXECUTION_FORCE_PROFILE,
    local_provider_markers=_ADAPTIVE_PROVIDER_MARKERS or ("ollama", "llama", "local"),
)
MCP_SERVER = DSMCPServer(backend_base=settings.API_BASE, timeout_s=settings.HTTP_TIMEOUT)
MCP_OAUTH = DevOAuthProvider()
PRINCIPAL_REGISTRY = PrincipalRegistry(
    os.getenv("PRINCIPAL_REGISTRY_PATH", "./data/principal_registry.json").strip()
    or "./data/principal_registry.json"
)
PRINCIPAL_LINK_CHALLENGES = PrincipalLinkChallenges(
    os.getenv("PRINCIPAL_LINK_CHALLENGES_PATH", "./data/principal_link_challenges.json").strip()
    or "./data/principal_link_challenges.json"
)
PRINCIPAL_LINK_EMAIL_FROM = (
    os.getenv("PRINCIPAL_LINK_EMAIL_FROM", "").strip()
)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
VERIFIED_ID_REQUESTS = VerifiedIDRequests(
    os.getenv("VERIFIED_ID_REQUESTS_PATH", "./data/verified_id_requests.json").strip()
    or "./data/verified_id_requests.json"
)
CONTROL_PLANE_REGISTRY = ControlPlaneRegistry(
    os.getenv("CONTROL_PLANE_REGISTRY_PATH", "./data/control_plane_registry.json").strip()
    or "./data/control_plane_registry.json"
)
VERIFIED_ID_REQUEST_SERVICE_SCOPE = "3db474b9-6a0c-4840-96ac-1fceb342124f/.default"

WALT_ID_ISSUER_URL = (
    os.getenv("WALT_ID_ISSUER_URL", "").strip().rstrip("/")
)
WALT_ID_ISSUER_DID = (
    os.getenv("WALT_ID_ISSUER_DID", "").strip()
)
WALT_ID_ISSUER_KEY_JWK = os.getenv("WALT_ID_ISSUER_KEY_JWK", "").strip()
WALT_ID_CALLBACK_API_KEY = os.getenv("WALT_ID_CALLBACK_API_KEY", "").strip()

ENTRA_OIDC_CLIENT_ID = os.getenv("ENTRA_OIDC_CLIENT_ID", "").strip()
ENTRA_OIDC_CLIENT_SECRET = os.getenv("ENTRA_OIDC_CLIENT_SECRET", "").strip()
ENTRA_OIDC_TENANT_ID = os.getenv("ENTRA_OIDC_TENANT_ID", "2f013f08-f893-436f-becc-9f82d02ca76d").strip()
ENTRA_OIDC_REDIRECT_URI = os.getenv("ENTRA_OIDC_REDIRECT_URI", "").strip()
ENTRA_OIDC_AUTH_COOKIE = "ds_entra_oidc"
ENTRA_OIDC_AUTH_MAX_AGE = 3600  # 1 hour

logger = logging.getLogger(__name__)
FRONTDOOR_AUTH_COOKIE = "ds_frontdoor_auth"
BACKEND_SESSION_TOKEN_COOKIE = "ds_backend_session_token"


def _entra_oidc_redirect_uri(request: Request) -> str:
    configured = ENTRA_OIDC_REDIRECT_URI
    if configured:
        return configured
    base = _public_base_url(request)
    return f"{base}/api/auth/entra/callback"


def _entra_oidc_login_url(*, request: Request, state: str, nonce: str) -> str:
    tenant = ENTRA_OIDC_TENANT_ID or "common"
    authorize_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    redirect_uri = _entra_oidc_redirect_uri(request)
    params = {
        "client_id": ENTRA_OIDC_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
    }
    return f"{authorize_url}?{urlencode(params)}"


def _entra_auth_cookie_secret() -> str:
    return str(os.getenv("FASTHTML_SECRET_KEY", ""))


def _sign_entra_auth_payload(payload: dict[str, Any]) -> str:
    secret = _entra_auth_cookie_secret().encode("utf-8")
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(secret, data, hashlib.sha256).hexdigest()
    import base64
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=") + "." + sig


def _verify_entra_auth_token(token_value: str | None) -> dict[str, Any] | None:
    if not token_value:
        return None
    try:
        parts = token_value.split(".")
        if len(parts) != 2:
            return None
        data_b64 = parts[0] + "=" * (4 - len(parts[0]) % 4)
        data = base64.urlsafe_b64decode(data_b64)
        sig_expected = hmac.new(
            _entra_auth_cookie_secret().encode("utf-8"), data, hashlib.sha256
        ).hexdigest()
        if not secrets.compare_digest(parts[1], sig_expected):
            return None
        payload = json.loads(data)
        if not isinstance(payload, dict):
            return None
        issued_at = int(payload.get("iat") or 0)
        if issued_at and (time.time() - issued_at) > ENTRA_OIDC_AUTH_MAX_AGE:
            return None
        return payload
    except Exception:
        return None


async def _entra_oidc_exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    tenant = ENTRA_OIDC_TENANT_ID or "common"
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": ENTRA_OIDC_CLIENT_ID,
                "client_secret": ENTRA_OIDC_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
                "scope": "openid profile email",
            },
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()


def _entra_auth_cookie_attrs(request: Request, max_age: int = ENTRA_OIDC_AUTH_MAX_AGE) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "httponly": True,
        "secure": _cookie_secure(request),
        "samesite": "lax",
        "max_age": max_age,
    }
    domain = _cookie_domain(request)
    if domain:
        attrs["domain"] = domain
    return attrs


def _mcp_static_token_configured() -> bool:
    return bool(settings.MCP_AUTH_TOKEN)


def _mcp_check_static_token(authorization_header: str | None) -> bool:
    if not _mcp_static_token_configured():
        return False
    if not authorization_header:
        return False
    if not authorization_header.lower().startswith("bearer "):
        return False
    token = authorization_header.split(" ", 1)[1].strip()
    return bool(token) and token == settings.MCP_AUTH_TOKEN


def _mcp_unauthorized(*, base_url: str) -> JSONResponse:
    challenge = f'Bearer realm="ds-mcp", resource="{base_url}/mcp"'
    return JSONResponse(
        {
            "error": "unauthorized",
            "detail": "Bearer token required for MCP access.",
        },
        status_code=401,
        headers={"WWW-Authenticate": challenge},
    )
OPENAI_COMPAT_S_MODE = os.getenv("OPENAI_COMPAT_S_MODE", "s1").strip().lower()
OPENAI_COMPAT_POLICY_ALLOW_CLIENT_OVERRIDES = (
    os.getenv("OPENAI_COMPAT_POLICY_ALLOW_CLIENT_OVERRIDES", "0").strip().lower()
    in {"1", "true", "yes", "on"}
)
MIDDLEWARE_ENABLE_UI = os.getenv("MIDDLEWARE_ENABLE_UI", "0").strip().lower() in {"1", "true", "yes", "on"}
_cors_origins_raw = os.getenv(
    "MIDDLEWARE_CORS_ORIGINS",
    os.getenv("CORS_ALLOWED_ORIGINS", ""),
).strip()
MIDDLEWARE_CORS_ORIGINS = [
    origin.strip()
    for origin in _cors_origins_raw.split(",")
    if origin and origin.strip()
]

FRONTEND_CONTEXT_ID = (
    os.getenv("FRONTEND_CONTEXT_ID", "ctx:frontend:vercel").strip() or "ctx:frontend:vercel"
)

MANUAL_SYNC_MAX_ROUNDS_DEFAULT = 8
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
    meta_raw = payload.get("metadata")
    meta: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
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
    meta_raw = payload.get("metadata")
    meta: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
    for source in (payload, meta):
        for key in ("s_mode", "pipeline_mode", "latency_mode"):
            raw = source.get(key)
            if isinstance(raw, str) and raw.strip():
                mode = raw.strip().lower()
                if mode in {"s1", "s2"}:
                    return mode
    return None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
    return f"{parsed.scheme}://{parsed.netloc}"


def _request_rp_id(request: Request) -> str:
    configured = str(os.getenv("AUTH_WEBAUTHN_RP_ID") or "").strip()
    if configured:
        return configured
    host = _request_host(request)
    lowered = host.lower()
    base_domain = os.getenv("BASE_DOMAIN", "").strip().lower()
    if base_domain and (lowered == base_domain or lowered.endswith("." + base_domain)):
        return base_domain
    return ""
    return host or os.getenv("DEFAULT_HOST", "")


def _frontdoor_cookie_signature() -> str:
    secret = str(os.getenv("FASTHTML_SECRET_KEY", ""))
    user = str(os.getenv("BASIC_AUTH_USER", "")).strip()
    password = str(os.getenv("BASIC_AUTH_PASSWORD", "")).strip()
    payload = f"{user}:{password}:frontdoor".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


async def _auth_backend_get(path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    url = f"{settings.API_BASE.rstrip('/')}{path}"
    request_headers = dict(headers or {})
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            response = await client.get(url, headers=request_headers)
            try:
                payload = response.json()
                body = payload if isinstance(payload, dict) else {"data": payload}
            except Exception:
                body = {"error": "upstream_invalid_json", "text": response.text[:1000]}
            return response.status_code, body
    except httpx.HTTPError as exc:
        return 503, {"error": "auth_upstream_http_error", "detail": str(exc)}
    except Exception as exc:
        return 503, {"error": "auth_upstream_unavailable", "detail": str(exc)}


async def _auth_backend_post(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    url = f"{settings.API_BASE.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers={"content-type": "application/json"})
            try:
                data = response.json()
                body = data if isinstance(data, dict) else {"data": data}
            except Exception:
                body = {"error": "upstream_invalid_json", "text": response.text[:1000]}
            return response.status_code, body
    except httpx.HTTPError as exc:
        return 503, {"error": "auth_upstream_http_error", "detail": str(exc)}
    except Exception as exc:
        return 503, {"error": "auth_upstream_unavailable", "detail": str(exc)}


def _openai_override_authorized(
    *,
    request: Request,
    payload: dict[str, Any],
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    auth_envelope = build_backend_auth_envelope(request=request, payload=payload)
    claims_raw = auth_envelope.get("claims")
    claims: dict[str, Any] = claims_raw if isinstance(claims_raw, dict) else {}
    if OPENAI_COMPAT_POLICY_ALLOW_CLIENT_OVERRIDES:
        return True, auth_envelope, claims
    token_present = bool(auth_envelope.get("token_present"))
    principal_did = str(claims.get("principal_did") or "").strip()
    session_jti = str(claims.get("session_jti") or "").strip()
    return bool(token_present and principal_did and session_jti), auth_envelope, claims


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
    meta_raw = effective.get("metadata")
    meta = dict(meta_raw) if isinstance(meta_raw, dict) else {}
    meta["policy_controls"] = policy_controls
    effective["metadata"] = meta
    return effective, policy_controls


def _s_mode_to_dial(mode: str) -> int:
    # S1 prioritizes fast-path caps, S2 allows deeper context/walk behavior.
    return 3 if mode == "s1" else 2


async def _run_openai_via_middleware_orchestrator(
    *,
    base_payload: dict[str, Any],
    model: str,
    message: str,
    history: list[dict[str, str]],
    session_id: str,
) -> dict[str, Any]:
    if not isinstance(orchestrator_handlers, dict):
        raise RuntimeError("orchestrator handlers unavailable")
    orchestrate = orchestrator_handlers.get("smart_stream") or orchestrator_handlers.get("orchestrate")
    if orchestrate is None:
        raise RuntimeError("middleware orchestrator route unavailable")

    s_mode = _resolve_s_mode(base_payload)
    payload_meta = base_payload.get("metadata") if isinstance(base_payload.get("metadata"), dict) else {}
    context_coords: list[str] = []
    seen_coords: set[str] = set()

    def _add_coord(value: Any) -> None:
        if not isinstance(value, str):
            return
        raw = value.strip()
        if not raw:
            return
        for candidate in (raw, normalize_coord_token(raw) or raw):
            cleaned = candidate.strip()
            if not cleaned or cleaned in seen_coords:
                continue
            seen_coords.add(cleaned)
            context_coords.append(cleaned)

    for source in (base_payload, payload_meta):
        if not isinstance(source, dict):
            continue
        _add_coord(source.get("coordinate"))
        _add_coord(source.get("coord"))
        for list_key in ("context_coords", "coordinates", "coords"):
            values = source.get(list_key)
            if not isinstance(values, list):
                continue
            for value in values:
                _add_coord(value)
    for coord in extract_coords_from_text(message):
        _add_coord(coord)

    orchestrator_payload: dict[str, Any] = {
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
            orchestrator_payload[key] = value.strip()
    policy_controls = base_payload.get("policy_controls")
    if isinstance(policy_controls, dict):
        orchestrator_payload["policy_controls"] = policy_controls
    if context_coords:
        orchestrator_payload["context_coords"] = context_coords

    class _RequestShim:
        def __init__(self, json_payload: dict[str, Any], session: str):
            self._json_payload = json_payload
            self.cookies = {"ds_session": session}

        async def json(self) -> dict[str, Any]:
            return self._json_payload

    shim_request = cast(Any, _RequestShim(orchestrator_payload, session_id))
    stream_response = await orchestrate(shim_request)
    if getattr(stream_response, "status_code", 200) >= 400:
        detail = getattr(stream_response, "body", b"").decode("utf-8", errors="ignore")
        raise RuntimeError(detail or "Middleware orchestrator failed")

    token_parts: list[str] = []
    meta_event: dict[str, Any] = {}
    line_buffer = ""
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8", errors="ignore") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        if not text:
            continue
        line_buffer += text
        lines = line_buffer.split("\n")
        line_buffer = lines.pop() if lines else ""
        for line in lines:
            row = line.strip()
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
            elif etype == "meta":
                meta_event = event

    assistant_text = _strip_control_protocol("".join(token_parts).strip())
    if not assistant_text and isinstance(meta_event.get("metadata"), dict):
        meta_payload = meta_event["metadata"]
        if isinstance(meta_payload.get("assistant_reply"), str):
            assistant_text = _strip_control_protocol(meta_payload["assistant_reply"])
        elif isinstance(meta_payload.get("content"), str):
            assistant_text = _strip_control_protocol(meta_payload["content"])

    usage_raw = meta_event.get("tokens")
    usage = usage_raw if isinstance(usage_raw, dict) else {}
    prompt_tokens = int(usage.get("prompt") or usage.get("input") or 0)
    completion_tokens = int(usage.get("completion") or usage.get("output") or max(len(assistant_text.split()), 0))
    total_tokens = int(usage.get("total") or (prompt_tokens + completion_tokens))

    return {
        "assistant_text": assistant_text,
        "response_model": str(meta_event.get("model") or model),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


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
                mid = str(item.get("model") or item.get("name") or "").strip()
                mname = str(item.get("name") or mid).strip()
                if not mid:
                    continue
                if _is_embedding_like_model(mid, mname):
                    continue
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


def _extract_skim_line(decoded: dict) -> str | None:
    if not isinstance(decoded, dict):
        return None
    skim = decoded.get("skim")
    if isinstance(skim, dict):
        one_line = skim.get("one_line")
        if isinstance(one_line, str) and one_line.strip():
            return one_line.strip()
    return None


def _decode_attempt_specs(coordinate: str, entity: str, session_id: str) -> list[dict[str, str] | None]:
    attempts: list[dict[str, str] | None] = [{"entity": str(entity), "session_id": str(session_id)}]
    if ":" in coordinate:
        inferred_entity = coordinate.rsplit(":", 1)[0]
        if inferred_entity and inferred_entity != str(entity):
            attempts.append({"entity": inferred_entity, "session_id": str(session_id)})
    attempts.append(None)
    return attempts


async def _decode_with_fallback_attempts(
    coordinate: str,
    *,
    entity: str,
    session_id: str,
    auth_headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    resolved: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] = []
    for idx, attempt in enumerate(_decode_attempt_specs(coordinate, entity, session_id), start=1):
        label = (
            "session_scope"
            if idx == 1
            else "coord_namespace_scope"
            if idx == 2 and attempt is not None
            else "unscoped"
        )
        attempt_start = time.perf_counter()
        try:
            if attempt is None:
                candidate = await api.decode_coordinate(
                    coordinate,
                    auth_headers=auth_headers,
                )
            else:
                candidate = await api.decode_coordinate(
                    coordinate,
                    entity=attempt.get("entity"),
                    session_id=attempt.get("session_id"),
                    auth_headers=auth_headers,
                )
        except BackendDecodeError as exc:
            body = exc.body
            error_code = body.get("error_code") if isinstance(body, dict) else None
            diagnostics.append(
                {
                    "attempt": idx,
                    "scope": label,
                    "entity": attempt.get("entity") if attempt else None,
                    "session_id": attempt.get("session_id") if attempt else None,
                    "ok": False,
                    "error_kind": "backend_http",
                    "status": exc.status_code,
                    "error_code": error_code,
                    "detail": body,
                    "latency_ms": round((time.perf_counter() - attempt_start) * 1000, 2),
                }
            )
            continue
        except httpx.HTTPError as exc:
            diagnostics.append(
                {
                    "attempt": idx,
                    "scope": label,
                    "entity": attempt.get("entity") if attempt else None,
                    "session_id": attempt.get("session_id") if attempt else None,
                    "ok": False,
                    "error_kind": "transport",
                    "error": str(exc),
                    "latency_ms": round((time.perf_counter() - attempt_start) * 1000, 2),
                }
            )
            continue
        except Exception as exc:
            diagnostics.append(
                {
                    "attempt": idx,
                    "scope": label,
                    "entity": attempt.get("entity") if attempt else None,
                    "session_id": attempt.get("session_id") if attempt else None,
                    "ok": False,
                    "error_kind": "unknown",
                    "error": str(exc),
                    "latency_ms": round((time.perf_counter() - attempt_start) * 1000, 2),
                }
            )
            continue
        if not isinstance(candidate, dict):
            diagnostics.append(
                {
                    "attempt": idx,
                    "scope": label,
                    "entity": attempt.get("entity") if attempt else None,
                    "session_id": attempt.get("session_id") if attempt else None,
                    "ok": False,
                    "error_kind": "invalid_payload",
                    "status": "invalid_payload",
                    "latency_ms": round((time.perf_counter() - attempt_start) * 1000, 2),
                }
            )
            continue
        status = str(candidate.get("status") or "ok")
        detail = candidate.get("detail")
        ok = status != "error"
        diagnostics.append(
            {
                "attempt": idx,
                "scope": label,
                "entity": attempt.get("entity") if attempt else None,
                "session_id": attempt.get("session_id") if attempt else None,
                "ok": ok,
                "error_kind": "backend_error" if not ok else None,
                "status": status,
                "detail": detail,
                "latency_ms": round((time.perf_counter() - attempt_start) * 1000, 2),
            }
        )
        if ok and resolved is None:
            resolved = candidate
    return resolved, diagnostics

# IMPORTANT: pass secret_key so FastHTML does NOT try to write .sesskey
app, rt = fast_app(
    secret_key=os.environ.get("FASTHTML_SECRET_KEY", "")
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=MIDDLEWARE_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional UI mount; middleware defaults to API-only for decoupled frontend.
if MIDDLEWARE_ENABLE_UI:
    from starlette.staticfiles import StaticFiles
    app.mount(
        "/static",
        StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
        name="static",
    )


def _public_base_url(request: Request) -> str:
    configured = os.getenv("MCP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = forwarded_host or (request.headers.get("host") or "").split(",")[0].strip()
    if host:
        scheme = forwarded_proto or request.url.scheme or "https"
        observed = f"{scheme}://{host}"
        if configured:
            try:
                configured_host = (urlparse(configured).netloc or "").lower()
            except Exception:
                configured_host = ""
            if configured_host and configured_host != host.lower():
                return observed
        return configured or observed
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _control_plane_canonical_subject(request: Request, *, entity_type: str, entity_id: str) -> str:
    base_url = _public_base_url(request)
    public_host = (urlparse(base_url).hostname or "").strip().lower() or os.getenv("PUBLIC_HOST", "")
    entity_key = str(entity_type or "").strip().lower()
    identifier = re.sub(r"[^a-z0-9]+", "-", str(entity_id or entity_key).strip().lower()).strip("-") or entity_key
    if entity_key in {"ledger", "surface", "provider", "binding", "relationship"}:
        suffix = f"{entity_key}s" if entity_key != "binding" else "bindings"
        return f"did:web:{public_host}:{suffix}:{identifier}"
    return f"{base_url}/entities/{entity_key}/{identifier}"


def _print_mcp_boot_banner() -> None:
    local_base = os.getenv("LOCAL_API", "")
    public_base = settings.MCP_PUBLIC_BASE_URL
    effective_base = public_base or local_base
    oauth_meta = MCP_OAUTH.authorization_server_metadata(effective_base)

    print("[ds-middleware] MCP ready")
    print(f"[ds-middleware] mcp_local={local_base}/mcp")
    if public_base:
        print(f"[ds-middleware] mcp_public={public_base}/mcp")
    if _mcp_static_token_configured():
        print("[ds-middleware] mcp_auth=bearer_token")
    elif MCP_OAUTH.enabled:
        print("[ds-middleware] mcp_auth=oauth")
    else:
        print("[ds-middleware] mcp_auth=none")
    if settings.DEMO_OVERRIDE_MODE:
        print(f"[ds-middleware] demo_override_mode=on default_ledger={settings.DEMO_OVERRIDE_DEFAULT_LEDGER}")
    print(
        "[ds-middleware] oauth_metadata="
        f"{oauth_meta.get('issuer')} | "
        f"auth={oauth_meta.get('authorization_endpoint')} | "
        f"token={oauth_meta.get('token_endpoint')} | "
        f"register={oauth_meta.get('registration_endpoint')}"
    )
    print(
        "[ds-middleware] well_known="
        f"{effective_base}/.well-known/oauth-authorization-server "
        f"{effective_base}/.well-known/oauth-protected-resource"
    )


@rt("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request: Request):
    base = _public_base_url(request)
    resource_url = f"{base}/mcp"
    return JSONResponse(MCP_OAUTH.protected_resource_metadata(base, resource_url))


@rt("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
async def oauth_protected_resource_mcp_alias(request: Request):
    # Some clients probe this path variant during MCP resource discovery.
    base = _public_base_url(request)
    resource_url = f"{base}/mcp"
    return JSONResponse(MCP_OAUTH.protected_resource_metadata(base, resource_url))


@rt("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_authorization_server(request: Request):
    base = _public_base_url(request)
    return JSONResponse(MCP_OAUTH.authorization_server_metadata(base))


@rt("/.well-known/openid-configuration", methods=["GET"])
async def oauth_openid_configuration(request: Request):
    # Compatibility alias for OAuth/OIDC clients that probe this metadata path.
    base = _public_base_url(request)
    return JSONResponse(MCP_OAUTH.authorization_server_metadata(base))


async def _oauth_register_impl(request: Request):
    if not MCP_OAUTH.enabled:
        raise HTTPException(status_code=404, detail="OAuth disabled")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")
    try:
        result = MCP_OAUTH.register_client(body)
    except ValueError as exc:
        return JSONResponse({"error": "invalid_client_metadata", "error_description": str(exc)}, status_code=400)
    return JSONResponse(result, status_code=201)


@rt("/oauth/register", methods=["POST"])
async def oauth_register(request: Request):
    return await _oauth_register_impl(request)


@rt("/register", methods=["POST"])
async def oauth_register_alias(request: Request):
    return await _oauth_register_impl(request)


@rt("/oauth/authorize", methods=["GET"])
async def oauth_authorize(request: Request):
    if not MCP_OAUTH.enabled:
        raise HTTPException(status_code=404, detail="OAuth disabled")
    params = {k: v for k, v in request.query_params.items()}
    try:
        redirect_to = MCP_OAUTH.authorize(params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(url=redirect_to, status_code=302)


@rt("/oauth/token", methods=["POST"])
async def oauth_token(request: Request):
    if not MCP_OAUTH.enabled:
        raise HTTPException(status_code=404, detail="OAuth disabled")
    form: dict[str, str] = {}
    # Some ASGI stacks may pre-consume the stream before this handler runs.
    # Prefer Starlette form parsing and gracefully degrade to raw body parsing.
    try:
        parsed_form = await request.form()
        for key, value in parsed_form.multi_items():
            if key not in form:
                form[str(key)] = str(value)
    except Exception:
        raw = ""
        try:
            raw = (await request.body()).decode("utf-8", errors="ignore")
        except Exception:
            raw = ""
        if raw:
            parsed = parse_qs(raw, keep_blank_values=True)
            form = {k: (v[0] if isinstance(v, list) and v else "") for k, v in parsed.items()}
    auth_header = request.headers.get("authorization")
    try:
        token = MCP_OAUTH.exchange_token(form, auth_header=auth_header)
    except ValueError as exc:
        msg = str(exc)
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse(token)


@rt("/mcp", methods=["GET"])
async def mcp_info(request: Request):
    base = _public_base_url(request)
    oauth_urls = MCP_OAUTH.authorization_server_metadata(base)
    auth_mode = "none"
    auth_required = bool(settings.MCP_AUTH_REQUIRED)
    if _mcp_static_token_configured():
        auth_mode = "bearer_token"
    elif MCP_OAUTH.enabled:
        auth_mode = "oauth"
    return {
        "service": "ds-mcp",
        "status": "ok",
        "endpoint": "/mcp",
        "transport": "http-jsonrpc",
        "public_url": f"{base}/mcp",
        "auth_mode": auth_mode,
        "auth_required": auth_required,
        "demo_override_mode": bool(settings.DEMO_OVERRIDE_MODE),
        "demo_override_default_ledger": settings.DEMO_OVERRIDE_DEFAULT_LEDGER,
        "oauth_enabled": MCP_OAUTH.enabled,
        "oauth": oauth_urls if MCP_OAUTH.enabled else None,
        "tools": [
            "ds.handshake",
            "ds.append_event",
            "ds.sync_status",
            "ds.sync_flush",
            "ds.verify",
            "ds.introspect",
        ],
    }


@rt("/mcp", methods=["POST"])
async def mcp_rpc(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")

    required_scopes = MCP_SERVER.required_scopes_for_rpc(payload)
    auth_header = request.headers.get("authorization")
    base_url = _public_base_url(request)
    if settings.MCP_AUTH_REQUIRED:
        if _mcp_static_token_configured():
            if not _mcp_check_static_token(auth_header):
                return _mcp_unauthorized(base_url=base_url)
        elif MCP_OAUTH.enabled and required_scopes:
            validation = MCP_OAUTH.validate_bearer(
                auth_header,
                required_scopes,
            )
            if not validation.ok:
                return MCP_OAUTH.unauthorized_response(
                    base_url=base_url,
                    required_scopes=required_scopes,
                    error=str(validation.error or "invalid_token"),
                )

    response = await MCP_SERVER.handle_rpc(payload)
    return JSONResponse(response)


@rt("/health")
def health_check():
    status = {
        "status": "ok",
        "llm_configured": llm is not None,
        "backend_url": settings.API_BASE,
        "git_sha": (os.getenv("GIT_SHA", "").strip() or "unknown"),
        "qp_pure_metrics": qp_pure_metrics.snapshot(),
    }
    if llm is not None:
        status["llm_model"] = settings.LLM_PROVIDER
    return status


@rt("/metrics")
def metrics_check():
    return {
        "status": "ok",
        "qp_pure_metrics": qp_pure_metrics.snapshot(),
    }


@rt("/prometheus")
def prometheus_metrics(request: Request):
    return Response(
        content=prometheus_client.generate_latest(prometheus_client.REGISTRY),
        media_type=prometheus_client.CONTENT_TYPE_LATEST,
    )


@rt("/version")
def version_info():
    return {
        "git_sha": (os.getenv("GIT_SHA", "").strip() or "unknown"),
        "backend_url": settings.API_BASE,
    }


@rt("/")
def root_info():
    return {
        "service": "ds-middleware",
        "status": "ok",
        "message": "Middleware is API-only. Frontend UI is served from port 5000.",
        "ui_url": os.getenv("LOCAL_UI_URL", ""),
        "health_url": "/health",
    }


def _backend_headers_from_request(request: Request) -> dict[str, str]:
    """Build upstream headers from the caller's request context.

    The global API client headers are used as a base, but any ledger/auth
    context is taken from the incoming request. If the caller supplies a
    ledger_id via query string instead of header, we promote it to the
    x-ledger-id header so the backend sees the correct scope.
    """
    headers = dict(api.headers)
    caller_headers = {key.lower(): value for key, value in request.headers.items()}
    context_keys = (
        "x-ledger-id",
        "x-ledger-id-h64",
        "x-context-id",
        "x-principal-id",
        "x-principal-type",
        "x-tenant-id",
        "authorization",
        "x-session-token",
    )
    for key in context_keys:
        if key in caller_headers:
            headers[key] = caller_headers[key]
        else:
            headers.pop(key, None)

    if "x-ledger-id" not in caller_headers:
        ledger_id = request.query_params.get("ledger_id") or request.query_params.get("ledger")
        if ledger_id:
            headers["x-ledger-id"] = ledger_id
    return headers


async def _backend_fetch_json(
    *,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Any:
    url = f"{settings.API_BASE.rstrip('/')}{path}"
    request_headers = headers or api.headers
    request_method = method.upper()
    try:
        async with httpx.AsyncClient(timeout=timeout or settings.HTTP_TIMEOUT) as client:
            if request_method == "GET":
                response = await client.get(
                    url,
                    params=params,
                    headers=request_headers,
                )
            elif request_method == "POST":
                response = await client.post(
                    url,
                    params=params,
                    json=payload,
                    headers=request_headers,
                )
            else:
                response = await client.request(
                    request_method,
                    url,
                    params=params,
                    json=payload if request_method != "GET" else None,
                    headers=request_headers,
                )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"Upstream timeout: {url}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request error: {url}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    if not response.content:
        return {}
    return response.json()


@rt("/ledger/history/{entity_path:path}")
async def proxy_ledger_history(request: Request, entity_path: str):
    entity = unquote(entity_path or "").strip("/")
    if not entity:
        raise HTTPException(status_code=422, detail="entity is required")
    limit_raw = request.query_params.get("limit", "50")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 50
    params: dict[str, Any] = {"limit": limit}
    for passthrough_key in ("ledger_id", "context_id"):
        passthrough_value = request.query_params.get(passthrough_key)
        if passthrough_value:
            params[passthrough_key] = passthrough_value
    body = await _backend_fetch_json(
        method="GET",
        path=f"/ledger/history/{entity}",
        params=params,
        headers=_backend_headers_from_request(request),
    )
    return JSONResponse(body)


@rt("/ledger/all")
async def proxy_ledger_all(request: Request):
    limit_raw = request.query_params.get("limit", "100")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 100
    params: dict[str, Any] = {"limit": limit}
    namespace = str(request.query_params.get("namespace") or "").strip()
    if namespace:
        params["namespace"] = namespace
    body = await _backend_fetch_json(
        method="GET",
        path="/ledger/all",
        params=params,
    )
    return JSONResponse(body)


@rt("/ledger/history_entities")
async def proxy_ledger_history_entities(request: Request):
    limit_raw = request.query_params.get("limit", "200")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 200
    include_counts_raw = str(request.query_params.get("include_counts", "true")).strip().lower()
    include_counts = include_counts_raw not in {"0", "false", "no", "off"}
    body = await _backend_fetch_json(
        method="GET",
        path="/ledger/history_entities",
        params={"limit": limit, "include_counts": str(include_counts).lower()},
    )
    return JSONResponse(body)


@rt("/sync/v0/pull", methods=["POST"])
async def proxy_sync_v0_pull(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    body = await _backend_fetch_json(
        method="POST",
        path="/sync/v0/pull",
        payload=payload,
    )
    return JSONResponse(body)


@rt("/ledger/summary/{entity_path:path}")
async def proxy_ledger_summary(request: Request, entity_path: str):
    entity = unquote(entity_path or "").strip("/")
    if not entity:
        raise HTTPException(status_code=422, detail="entity is required")
    body = await _backend_fetch_json(
        method="GET",
        path=f"/ledger/summary/{entity}",
    )
    return JSONResponse(body)


@rt("/api/auth/identity_card", methods=["GET"])
async def api_auth_identity_card(request: Request):
    token = str(
        request.headers.get("x-session-token")
        or request.cookies.get("ds_backend_session_token")
        or ""
    ).strip()
    verify_headers = {"x-session-token": token} if token else {}
    verify_status, verify_body = await _auth_backend_get("/auth/session/verify", headers=verify_headers)

    session_id = str(request.cookies.get("ds_session") or DEFAULT_SESSION_ID).strip() or DEFAULT_SESSION_ID
    session = get_session(session_id)
    stats = await build_stats_payload(request)
    posture_policy = _dict_or_empty(session.get("last_posture_policy"))
    appraisal = _dict_or_empty(session.get("last_appraisal"))

    identity: dict[str, Any] = {
        "verified": bool(isinstance(verify_body, dict) and verify_body.get("verified") is True),
        "verification_state": "verified" if isinstance(verify_body, dict) and verify_body.get("verified") is True else "unverified",
        "reason_code": str((verify_body or {}).get("reason") or "verification_unavailable"),
        "principal_did": str((verify_body or {}).get("principal_did") or "").strip() or None,
        "auth_method": str((verify_body or {}).get("auth_method") or "").strip() or None,
        "session_jti": str((verify_body or {}).get("session_jti") or "").strip() or None,
        "status_code": verify_status,
    }
    principal_did = str(identity.get("principal_did") or "").strip()
    if principal_did:
        principal_record = _dict_or_empty(PRINCIPAL_REGISTRY.get(principal_did))
        identity["principal_lookup_status"] = 200 if principal_record else 404
        if not principal_record:
            identity["principal_lookup_error"] = "principal_lookup_failed"
        metadata = _dict_or_empty(principal_record.get("metadata"))
        standing_view = _dict_or_empty(principal_record.get("standing_view"))
        identity["principal_display_name"] = str(principal_record.get("display_name") or "").strip() or None
        identity["canonical_subject"] = str(principal_record.get("canonical_subject") or principal_did).strip() or None
        identity["canonical_subject_source"] = str(principal_record.get("canonical_subject_source") or "principal_did").strip() or None
        identity["wallet_capable"] = bool(metadata.get("wallet_capable"))
        identity["wallet_provider"] = str(
            metadata.get("wallet_provider")
            or metadata.get("wallet_binding_provider")
            or ""
        ).strip() or None
        identity["wallet_did"] = str(
            metadata.get("wallet_did")
            or metadata.get("external_did")
            or ""
        ).strip() or None
        identity["wallet_binding_ref"] = str(
            metadata.get("wallet_binding_ref")
            or metadata.get("vc_id")
            or ""
        ).strip() or None
        identity["credential_ref"] = str(
            standing_view.get("credential_ref")
            or metadata.get("credential_ref")
            or ""
        ).strip() or None
        identity["standing_envelope_ref"] = str(standing_view.get("standing_envelope_ref") or "").strip() or None
        identity["issuer_did"] = str(metadata.get("issuer_did") or "").strip() or None
        identity["trust_anchor_role"] = str(metadata.get("trust_anchor_role") or "").strip() or None
        identity["tenant_id"] = str(principal_record.get("tenant_id") or "").strip() or None

        provisioning = _principal_provisioning_summary(principal_record) if principal_record else {}
        identity["provisioning_lookup_status"] = 200 if provisioning else 404
        if not provisioning:
            identity["provisioning_lookup_error"] = "provisioning_lookup_failed"
        else:
            identity["activation_state"] = str(provisioning.get("activation_state") or "").strip() or None
            identity["provisioning_state"] = str(provisioning.get("provisioning_state") or "").strip() or None
            identity["ledger_access_ready"] = bool(provisioning.get("ledger_access_ready"))
            identity["ledger_id"] = str(provisioning.get("ledger_id") or "").strip() or None
            if str(provisioning.get("tenant_id") or "").strip():
                identity["tenant_id"] = str(provisioning.get("tenant_id") or "").strip()
            identity["provisioning_reason_code"] = str(provisioning.get("reason_code") or "").strip() or None

    usage: dict[str, Any] = {
        "chat_unit_cost": stats.get("chat_unit_cost"),
        "memory_unit_cost": stats.get("memory_unit_cost"),
        "retrieval_rate": stats.get("retrieval_rate"),
        "resolved_per_turn": stats.get("resolved_per_turn"),
        "accuracy_numerator": stats.get("accuracy_numerator"),
        "accuracy_denominator": stats.get("accuracy_denominator"),
        "totals": stats.get("totals") if isinstance(stats.get("totals"), dict) else {},
    }
    eq9: dict[str, Any] = {
        "eq9_posture_class": str(posture_policy.get("eq9_posture_class") or "").strip() or None,
        "trust_class": str(posture_policy.get("trust_class") or "").strip() or None,
        "reason_code": str(posture_policy.get("reason_code") or "").strip() or None,
        "failed_eq": str(posture_policy.get("failed_eq") or "").strip() or None,
        "repair_actions": posture_policy.get("repair_actions") if isinstance(posture_policy.get("repair_actions"), list) else [],
        "appraisal": appraisal,
    }
    return {
        "status": "ok",
        "identity_vc": identity,
        "usage_stats": usage,
        "eq9": eq9,
    }


@rt("/api/auth/passkey/register/start", methods=["POST"])
async def api_auth_passkey_register_start(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    principal_did = str((payload or {}).get("principal_did") or "").strip()
    if not principal_did:
        principal_did = str(request.cookies.get("ds_principal_did") or "").strip()
    if not principal_did:
        return JSONResponse({"error": "linked_identity_required"}, status_code=400)
    status_code, body = await _auth_backend_post(
        "/auth/register/challenge",
        {
            "principal_did": principal_did,
            "origin": _request_origin(request),
            "rp_id": _request_rp_id(request),
        },
    )
    return JSONResponse(body, status_code=status_code)


@rt("/api/auth/passkey/register/finish", methods=["POST"])
async def api_auth_passkey_register_finish(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status_code, body = await _auth_backend_post("/auth/register/verify", dict(payload or {}))
    return JSONResponse(body, status_code=status_code)


@rt("/api/auth/passkey/login/start", methods=["POST"])
async def api_auth_passkey_login_start(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    principal_did = str((payload or {}).get("principal_did") or "").strip()
    if not principal_did:
        principal_did = str(request.cookies.get("ds_principal_did") or "").strip()
    if not principal_did:
        return JSONResponse({"error": "linked_identity_required"}, status_code=400)
    status_code, body = await _auth_backend_post(
        "/auth/challenge",
        {
            "principal_did": principal_did,
            "origin": _request_origin(request),
            "rp_id": _request_rp_id(request),
        },
    )
    return JSONResponse(body, status_code=status_code)


@rt("/api/auth/passkey/login/finish", methods=["POST"])
async def api_auth_passkey_login_finish(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status_code, body = await _auth_backend_post("/auth/verify", dict(payload or {}))
    response = JSONResponse(body, status_code=status_code)
    if status_code == 200 and isinstance(body, dict):
        cookie_domain = _cookie_domain(request)
        session_raw = body.get("session")
        session_obj = session_raw if isinstance(session_raw, dict) else {}
        token = str(session_obj.get("token") or "").strip()
        principal_did = str(session_obj.get("principal_did") or body.get("principal_did") or "").strip()
        if token:
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
            if str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower() == "form":
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


@rt("/api/auth/pilot/signup", methods=["POST"])
async def api_auth_pilot_signup(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status_code, body = await _auth_backend_post("/auth/pilot/signup", dict(payload or {}))
    return JSONResponse(body, status_code=status_code)


@rt("/api/auth/pilot/signup/verify", methods=["POST"])
async def api_auth_pilot_signup_verify(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status_code, body = await _auth_backend_post("/auth/pilot/signup/verify", dict(payload or {}))
    return JSONResponse(body, status_code=status_code)


@rt("/api/auth/signin", methods=["POST"])
async def api_auth_signin(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status_code, body = await _auth_backend_post("/auth/signin", dict(payload or {}))
    response = JSONResponse(body, status_code=status_code)
    if status_code == 200 and isinstance(body, dict):
        cookie_domain = _cookie_domain(request)
        session_raw = body.get("session")
        session_obj = session_raw if isinstance(session_raw, dict) else {}
        token = str(session_obj.get("token") or "").strip()
        principal_did = str(session_obj.get("principal_did") or body.get("principal_did") or "").strip()
        if token:
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
            if str(os.getenv("FRONTDOOR_AUTH_MODE", "")).strip().lower() == "form":
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


@rt("/api/chat/commit-answer", methods=["POST"])
async def api_chat_commit_answer(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")

    message = str(payload.get("message") or "").strip()
    reply = str(payload.get("reply") or "").strip()
    if not message or not reply:
        raise HTTPException(status_code=422, detail="message and reply are required")

    session_id = str(request.cookies.get("ds_session") or DEFAULT_SESSION_ID).strip() or DEFAULT_SESSION_ID
    session = get_session(session_id)
    ledger_id = str(payload.get("ledger_id") or session.get("ledger_id") or settings.DEFAULT_LEDGER_ID).strip() or settings.DEFAULT_LEDGER_ID
    entity = str(payload.get("entity") or session.get("entity") or build_entity_namespace(ledger_id, session_id)).strip()
    api.set_ledger(ledger_id)

    precomputed_appraisal = payload.get("precomputed_appraisal")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

    # Ensure chat commits carry the field_state envelope required by the backend
    # genesis-ladder gate.
    if "field_state" not in metadata:
        metadata["field_state"] = _synthesize_field_state(
            f"{message}\n{reply}", grid_size=32
        )

    auth_envelope = build_backend_auth_envelope(request=request, payload=payload)
    auth_headers = auth_envelope.get("headers") if isinstance(auth_envelope, dict) else {}
    auth_claims = auth_envelope.get("claims") if isinstance(auth_envelope, dict) else {}
    context_id = str(auth_claims.get("context_id") or "").strip()
    if context_id:
        api.set_context(context_id)

    async def _try_commit(
        *,
        headers: dict[str, str] | None,
        claims: dict[str, str] | None,
    ) -> dict[str, Any]:
        return await api.commit_answer(
            entity=entity,
            message=message,
            reply=reply,
            precomputed_appraisal=precomputed_appraisal,
            metadata=metadata,
            auth_headers=headers if headers else None,
            auth_claims=claims if claims else None,
        )

    commit_result: dict[str, Any] | None = None
    try:
        commit_result = await _try_commit(headers=auth_headers, claims=auth_claims)
    except httpx.HTTPStatusError as exc:
        # If the user's authenticated principal is blocked from writing (e.g.
        # incomplete onboarding / paused trial), retry as an anonymous session
        # commit so chat turns can still be persisted. Provenance metadata is
        # still preserved in the payload's runtime_identity block.
        if exc.response.status_code == 403:
            try:
                commit_result = await _try_commit(headers=None, claims=None)
            except httpx.HTTPStatusError as anonymous_exc:
                detail: Any
                try:
                    detail = anonymous_exc.response.json()
                except Exception:
                    detail = anonymous_exc.response.text or "upstream commit failed"
                return JSONResponse(
                    {"status": "error", "detail": detail},
                    status_code=anonymous_exc.response.status_code,
                )
        else:
            detail = {}
            try:
                detail = exc.response.json()
            except Exception:
                detail = exc.response.text or "upstream commit failed"
            return JSONResponse(
                {"status": "error", "detail": detail},
                status_code=exc.response.status_code,
            )

    if commit_result is None:
        return JSONResponse({"status": "ok"})
    return JSONResponse(
        {
            "status": commit_result.get("status", "ok"),
            "coordinate": commit_result.get("coordinate"),
            "metadata": commit_result.get("metadata"),
        }
    )


@rt("/stats/global")
async def compat_stats_global(request: Request):
    # Compatibility alias for legacy UI probes.
    return await get_global_stats(request)


@rt("/admin/ledgers")
async def compat_admin_ledgers(request: Request):
    # Compatibility alias for legacy UI probes.
    return await list_ledgers(request)


@rt("/billing/openrouter")
async def compat_billing_openrouter(request: Request):
    # Compatibility alias for legacy UI probes.
    return await get_costs(request)


# Register middleware routes
orchestrator_handlers = register_orchestrator_routes(rt)
register_wake_routes(rt)
register_agent_routes(rt)

# Ensure POST routes are registered on the Starlette app (Vercel runtime)
if isinstance(orchestrator_handlers, dict):
    orchestrate_handler = orchestrator_handlers.get("orchestrate")
    smart_stream_handler = orchestrator_handlers.get("smart_stream")
    if orchestrate_handler:
        app.route("/api/orchestrator", methods=["POST"])(orchestrate_handler)
    if smart_stream_handler:
        app.route("/api/chat/smart_stream", methods=["POST"])(smart_stream_handler)


@rt("/v1/chat/completions", methods=["POST"])
async def openai_chat_completions(request: Request):
    """OpenAI-compatible endpoint for OpenClaw custom provider integration."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")

    override_authorized, _auth_envelope, auth_claims = _openai_override_authorized(
        request=request,
        payload=payload,
    )
    payload, policy_controls = _apply_openai_policy_controls(
        payload=payload,
        override_authorized=override_authorized,
    )
    if isinstance(auth_claims, dict):
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
        payload_meta_raw = payload.get("metadata")
        payload_meta: dict[str, Any] = payload_meta_raw if isinstance(payload_meta_raw, dict) else {}
        session_id = str(
            payload.get("user")
            or payload_meta.get("session_id")
            or payload.get("session_id")
            or "openclaw-dashboard"
        ).strip() or "openclaw-dashboard"
        if OPENAI_COMPAT_PIPELINE_ENGINE == "middleware":
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
            enable_ledger = bool(payload.get("enable_ledger", True))
            backend_enable_ledger = enable_ledger
            pipeline_payload = {
                "message": latest_user_message,
                "history": history[:-1] if history else [],
                "provider": model,
                "session_id": session_id,
                "enable_ledger": backend_enable_ledger,
                "policy_controls": policy_controls,
            }
            if isinstance(auth_claims, dict):
                for key in ("principal_did", "principal_key_id", "session_jti", "context_id"):
                    value = auth_claims.get(key)
                    if isinstance(value, str) and value.strip():
                        pipeline_payload[key] = value.strip()

            class _RequestShim:
                def __init__(self, json_payload: dict[str, Any], session: str):
                    self._json_payload = json_payload
                    self.cookies = {"ds_session": session}

                async def json(self) -> dict[str, Any]:
                    return self._json_payload

            pipeline_request = cast(Any, _RequestShim(pipeline_payload, session_id))
            pipeline_response = await api_chat(pipeline_request)
            if getattr(pipeline_response, "status_code", 200) >= 400:
                detail = getattr(pipeline_response, "body", b"").decode("utf-8", errors="ignore")
                raise HTTPException(status_code=502, detail=detail or "Middleware pipeline failed")

            body_raw = getattr(pipeline_response, "body", b"{}")
            body = json.loads(body_raw.decode("utf-8"))
            assistant_text = _strip_control_protocol(str(body.get("reply") or ""))
            stats = body.get("stats") if isinstance(body.get("stats"), dict) else {}
            response_model = str(stats.get("model") or model)
            completion_tokens = max(len(assistant_text.split()), 0)
            prompt_tokens = max(
                len(" ".join(msg.get("content", "") for msg in history if isinstance(msg, dict)).split()),
                0,
            )
            total_tokens = prompt_tokens + completion_tokens
    else:
        llm_response = await llm.generate_response(
            message=latest_user_message,
            history=history[:-1] if history else [],
            agent=model,
        )
        if isinstance(llm_response, dict) and llm_response.get("error") == "provider_billing":
            return JSONResponse(
                {
                    "error": "provider_billing",
                    "message": str(llm_response.get("text") or "OpenRouter billing error."),
                    "detail": str(llm_response.get("detail") or "https://openrouter.ai/settings/billing"),
                },
                status_code=402,
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


async def _deferred_guardian_enrich(
    *,
    entity: str,
    user_message: str,
    assistant_reply: str,
    profile: str,
    pressure: float,
) -> None:
    """Persist deferred guardian enrichment after response streaming."""
    try:
        appraisal_payload: dict[str, Any] | None = None
        try:
            assessed = await api.assess_chat(
                user_message=user_message,
                assistant_reply=assistant_reply,
                entity=entity,
            )
            if isinstance(assessed, dict) and isinstance(assessed.get("appraisal"), dict):
                appraisal_payload = assessed.get("appraisal")
        except Exception:
            appraisal_payload = None

        metadata = {
            "adaptive_execution": {
                "deferred_guardian": True,
                "profile": profile,
                "pressure": round(float(pressure), 2),
            }
        }
        await api.commit_answer(
            entity=entity,
            message=user_message,
            reply=assistant_reply,
            precomputed_appraisal=appraisal_payload,
            metadata=metadata,
        )
    except Exception:
        return



# In app.py

async def api_chat(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    auth_envelope = build_backend_auth_envelope(request=request, payload=payload)
    auth_headers = auth_envelope.get("headers") if isinstance(auth_envelope, dict) else {}
    auth_claims = auth_envelope.get("claims") if isinstance(auth_envelope, dict) else {}

    # ... [Keep parsing logic] ...
    message = (payload.get("message") or "").strip()
    session_id = payload.get("session_id") or DEFAULT_SESSION_ID
    session = get_session(session_id)
    ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
    api.set_ledger(ledger_id)
    provider = payload.get("provider") or settings.LLM_PROVIDER
    # Default to current session entity or demo
    entity = session.get("entity") or build_entity_namespace(ledger_id, session_id)
    history = payload.get("history") or []
    enable_ledger = bool(payload.get("enable_ledger", True))

    payload_meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    network_pressure_hint = payload_meta.get("network_pressure")
    if not isinstance(network_pressure_hint, (int, float)):
        network_pressure_hint = None

    governor_decision = EXECUTION_GOVERNOR.decide(
        provider=str(provider),
        enable_ledger=enable_ledger,
        network_pressure_hint=network_pressure_hint,
    )
    backend_enable_ledger = bool(governor_decision.backend_enable_ledger)

    start = time.time()
    debug_log: dict[str, Any] = {
        "execution": {
            "profile": governor_decision.profile,
            "pressure": round(float(governor_decision.pressure), 2),
            "allow_assemble": governor_decision.allow_assemble,
            "max_decoded_coords": governor_decision.max_decoded_coords,
            "backend_enable_ledger": backend_enable_ledger,
            "defer_guardian": governor_decision.defer_guardian,
            "reason": governor_decision.reason,
        }
    }

    async def _decode_coordinate_with_fallback(coord: str) -> dict[str, Any] | None:
        resolved, _ = await _decode_with_fallback_attempts(
            coord,
            entity=str(entity),
            session_id=str(session_id),
        )
        return resolved

    decoded_context = []
    if message:
        coord_limit = max(0, min(MAX_DECODED_COORDS, int(governor_decision.max_decoded_coords)))
        extracted = extract_coords_from_text(message)[:coord_limit] if coord_limit > 0 else []
        coordinates: list[str] = []
        seen_coords: set[str] = set()
        for coord in extracted:
            for candidate in (coord, normalize_coord_token(coord) or coord):
                if candidate in seen_coords:
                    continue
                seen_coords.add(candidate)
                coordinates.append(candidate)

        for coord in coordinates:
            try:
                decoded = await _decode_coordinate_with_fallback(coord)
            except Exception:
                continue
            if not isinstance(decoded, dict):
                continue
            normalized = _normalize_decoded_payload(decoded)
            if normalized:
                decoded_context.append(f"[{len(decoded_context) + 1}] {coord} — {normalized}")

    if decoded_context:
        if not isinstance(history, list):
            history = []
        context_text = "Decoded coordinate context:\n" + "\n".join(decoded_context)
        if len(context_text) > MAX_CONTEXT_CHARS:
            context_text = truncate_text(context_text, MAX_CONTEXT_CHARS)
        history = [
            *history,
            {
                "role": "system",
                "content": context_text,
            },
        ]

    backend_payload = {
        "session_id": session_id,
        "entity": entity,
        "message": message,
        "history": history,
        "provider": provider,
        "enable_ledger": backend_enable_ledger,
    }
    if isinstance(auth_claims, dict):
        for key in ("principal_did", "principal_key_id", "session_jti", "context_id"):
            value = auth_claims.get(key)
            if isinstance(value, str) and value.strip():
                backend_payload[key] = value.strip()

    # --- 1. ASSEMBLE ---
    t_assemble_start = time.time()
    assemble_result = None
    context_log = [] 
    
    if enable_ledger and governor_decision.allow_assemble:
        try:
            # We inject the full context request to the backend
            assemble_result = await api.assemble(
                **backend_payload,
                auth_headers=auth_headers if isinstance(auth_headers, dict) else None,
                auth_claims=auth_claims if isinstance(auth_claims, dict) else None,
            )
            
            # Extract readable "thoughts" for the UI
            if assemble_result and not assemble_result.get("error"):
                s2 = assemble_result.get("s2", {})
                
                # Check for Retrieved Memories (from Neuro-Symbolic Search)
                if retrieved := assemble_result.get("retrieved", []):
                    for item in retrieved:
                        if snippet := item.get("snippet"):
                            # Truncate for UI cleanliness
                            preview = (snippet[:75] + '...') if len(snippet) > 75 else snippet
                            context_log.append(f"Recalled: {preview}")

                # Check for Claims (Beliefs)
                elif "19" in s2 and s2["19"].get("claims"):
                    for claim in s2["19"]["claims"][:2]: 
                        context_log.append(f"Context: {claim}")

        except Exception as exc:
            assemble_result = {"error": str(exc)}
            
    if enable_ledger and not governor_decision.allow_assemble:
        context_log.append("Adaptive fast path: assembly skipped under pressure")
    debug_log["assemble_ms"] = int((time.time() - t_assemble_start) * 1000)

    # --- 2. CHAT ---
    backend_response: ChatResponse | None = None
    used_fallback = False
    
    try:
        # Call Backend
        backend_response = await api.chat(
            **backend_payload,
            auth_headers=auth_headers if isinstance(auth_headers, dict) else None,
            auth_claims=auth_claims if isinstance(auth_claims, dict) else None,
        )
    except Exception as exc:
        # FALLBACK DETECTED
        used_fallback = True
        debug_log["backend_error"] = str(exc)
        
        if settings.ENABLE_LOCAL_LLM or not settings.API_KEY:
            backend_response = await _local_llm_chat(
                message=message,
                history=history,
                provider=provider,
                enable_ledger=backend_enable_ledger,
                session=session,
                entity=entity,
                start=start,
                ledger_id=ledger_id,
            )
        else:
            raise HTTPException(status_code=502, detail=f"Backend failed: {str(exc)}")

    if backend_response is None:
        raise HTTPException(status_code=502, detail="Backend failed: No response")

    if backend_response.error:
        raise HTTPException(status_code=502, detail=backend_response.error)

    # --- 3. PROCESS ---
    assistant_text = backend_response.primary_text or "Sorry, I had a problem responding."
    assistant_text = _strip_control_protocol(assistant_text)

    if governor_decision.defer_guardian and enable_ledger and message and assistant_text:
        debug_log["guardian_mode"] = "deferred_post_output"
        asyncio.create_task(
            _deferred_guardian_enrich(
                entity=str(entity),
                user_message=message,
                assistant_reply=assistant_text,
                profile=governor_decision.profile,
                pressure=float(governor_decision.pressure),
            )
        )
    else:
        debug_log["guardian_mode"] = "inline_or_backend"

    stats = backend_response.stats or {}
    if not isinstance(stats, dict):
        stats = {}
        
    # --- NEW EXTRACTION ---
    knowledge_tree = backend_response.knowledge_tree or []
    coordinate = backend_response.coordinate
    web4_key = backend_response.web4_key
    unverified = backend_response.unverified
    # ----------------------

    tokens = backend_response.tokens or stats.get("tokens")
    
    # Cost Estimation Logic
    def _estimate_turn_cost() -> float:
        """Best-effort turn cost using backend stats or token counts."""
        if val := stats.get("cost_usd"): return float(val)
        if val := backend_response.cost_usd: return float(val)

        p_tok = 50
        c_tok = len(assistant_text.split()) * 1.3
        
        if isinstance(tokens, dict):
            p_tok = tokens.get("prompt", p_tok)
            c_tok = tokens.get("completion", c_tok)
            
        return (p_tok * 5.0 + c_tok * 15.0) / 1_000_000

    turn_cost = _estimate_turn_cost()
    model_id = backend_response.model

    # Latency
    latency_ms = stats.get("last_latency") or stats.get("latency_ms")
    if not latency_ms:
        latency_ms = int((time.time() - start) * 1000)

    # --- 4. PERSIST STATS TO SESSION ---
    session["last_latency_ms"] = latency_ms
    session["total_cost"] = session.get("total_cost", 0.0) + turn_cost
    
    if "messages" not in session:
        session["messages"] = []
    session["messages"].append({"role": "user", "content": message})
    session["messages"].append({"role": "assistant", "content": assistant_text})
    
    update_session(session_id, session)

    stats["model"] = model_id or provider

    return JSONResponse(
        content={
            "reply": assistant_text,
            "stats": {
                "memory_count": session.get("memory_count", 0),
                "total_cost": session.get("total_cost", 0.0),
                "last_latency": latency_ms,
                "model": model_id or provider,
                "debug": {
                    "used_fallback": used_fallback,
                    "timings": debug_log
                }
            },
            "context_log": context_log,
            # --- PASS THROUGH TO FRONTEND ---
            "knowledge_tree": knowledge_tree,
            "coordinate": coordinate,
            "web4_key": web4_key,
            "unverified": unverified
            # --------------------------------
        }
    )


def _compact_decode_diagnostics(diagnostics: list[dict[str, Any]]) -> str:
    """Return a compact, secret-safe JSON summary for the X-Decode-Diagnostics header."""
    summary = {
        "attempts": len(diagnostics),
        "scopes": [
            item.get("scope")
            for item in diagnostics
            if isinstance(item, dict) and item.get("scope")
        ],
        "final_error_code": next(
            (
                item.get("error_code")
                for item in reversed(diagnostics)
                if isinstance(item, dict) and item.get("error_code")
            ),
            None,
        ),
        "final_status": next(
            (
                item.get("status")
                for item in reversed(diagnostics)
                if isinstance(item, dict) and "status" in item
            ),
            None,
        ),
        "final_error_kind": next(
            (
                item.get("error_kind")
                for item in reversed(diagnostics)
                if isinstance(item, dict) and item.get("error_kind")
            ),
            None,
        ),
    }
    try:
        return json.dumps(summary, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return "{\"error\":\"header_encoding_failed\"}"


@rt("/api/decode_coordinate")
async def decode_coordinate(request: Request):
    """Resolve a ledger coordinate via the backend /web4/decode endpoint."""
    request_start = time.perf_counter()
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    coordinate = (payload.get("coordinate") or "").strip()
    if not coordinate:
        raise HTTPException(status_code=422, detail="coordinate is required")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id = str(payload.get("ledger_id") or session.get("ledger_id") or settings.DEFAULT_LEDGER_ID or "").strip().lower()
    entity = session.get("entity") or build_entity_namespace(ledger_id, session_id)
    api.set_ledger(ledger_id)

    surface_id = str(
        request.headers.get("x-surface-id")
        or payload.get("surface_id")
        or ""
    ).strip()
    auth_envelope = build_backend_auth_envelope(request=request, payload=payload)
    auth_headers: dict[str, str] = dict(auth_envelope.get("headers") or {})
    if surface_id:
        auth_headers["x-surface-id"] = surface_id

    logger.info(
        "decode_coordinate start: coordinate=%s ledger_id=%s surface_id=%s session_id=%s",
        coordinate,
        ledger_id,
        surface_id,
        session_id,
    )

    resolved, diagnostics = await _decode_with_fallback_attempts(
        coordinate,
        entity=str(entity),
        session_id=str(session_id),
        auth_headers=auth_headers,
    )

    total_latency_ms = round((time.perf_counter() - request_start) * 1000, 2)
    diag_header = _compact_decode_diagnostics(diagnostics)

    if resolved is None:
        _AUTHORITY_ERROR_CODES = {
            "surface_inactive",
            "surface_not_bound_to_ledger",
            "decode_requires_authenticated_principal",
            "principal_not_authorized_for_surface",
        }
        _CLIENT_ERROR_CODES = {
            "invalid_coordinate",
            "invalid_web4_coordinate",
            "ledger_scope_mismatch",
            "missing_namespace",
            "coordinate_not_found",
        }
        authority_detail = next(
            (
                item.get("detail")
                for item in reversed(diagnostics)
                if isinstance(item, dict)
                and isinstance(item.get("detail"), dict)
                and str(item["detail"].get("error") or "").strip() in _AUTHORITY_ERROR_CODES
            ),
            None,
        )
        if authority_detail is not None:
            logger.info(
                "decode_coordinate authority failure: coordinate=%s error=%s latency_ms=%s",
                coordinate,
                authority_detail.get("error"),
                total_latency_ms,
            )
            return JSONResponse(
                authority_detail,
                status_code=403,
                headers={"X-Decode-Diagnostics": diag_header},
            )

        last_backend = next(
            (
                item
                for item in reversed(diagnostics)
                if isinstance(item, dict) and item.get("error_kind") == "backend_http"
            ),
            None,
        )
        if last_backend is not None:
            status = last_backend.get("status")
            error_code = last_backend.get("error_code")
            detail = last_backend.get("detail")
            if status == 503:
                logger.info(
                    "decode_coordinate backend unavailable: coordinate=%s error_code=%s latency_ms=%s",
                    coordinate,
                    error_code,
                    total_latency_ms,
                )
                return JSONResponse(
                    {"status": "error", "error_code": error_code or "backend_unavailable", "detail": detail},
                    status_code=503,
                    headers={"X-Decode-Diagnostics": diag_header},
                )
            if status in (400, 404, 422) or error_code in _CLIENT_ERROR_CODES:
                logger.info(
                    "decode_coordinate client error: coordinate=%s status=%s error_code=%s latency_ms=%s",
                    coordinate,
                    status,
                    error_code,
                    total_latency_ms,
                )
                return JSONResponse(
                    {"status": "error", "error_code": error_code or "decode_client_error", "detail": detail},
                    status_code=int(status) if isinstance(status, int) else 400,
                    headers={"X-Decode-Diagnostics": diag_header},
                )

        last_transport = next(
            (
                item
                for item in reversed(diagnostics)
                if isinstance(item, dict) and item.get("error_kind") == "transport"
            ),
            None,
        )
        logger.warning(
            "decode_coordinate gateway failure: coordinate=%s error_kind=%s latency_ms=%s",
            coordinate,
            last_transport.get("error_kind") if last_transport else "unknown",
            total_latency_ms,
        )
        return JSONResponse(
            {
                "status": "error",
                "error_code": "upstream_unavailable",
                "detail": last_transport.get("error") if last_transport else f"Unable to decode coordinate: {coordinate}",
                "diagnostics": diagnostics,
            },
            status_code=502,
            headers={"X-Decode-Diagnostics": diag_header},
        )

    logger.info(
        "decode_coordinate success: coordinate=%s latency_ms=%s",
        coordinate,
        total_latency_ms,
    )
    return JSONResponse(resolved, headers={"X-Decode-Diagnostics": diag_header})


@rt("/api/resolve_tiered", methods=["POST"])
async def resolve_tiered(request: Request):
    """Resolve a ledger coordinate via the backend tiered resolver contract."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    coordinate = str(payload.get("coordinate") or "").strip()
    namespace = str(payload.get("namespace") or "").strip()
    identifier = str(payload.get("identifier") or "").strip()
    read_tier = str(payload.get("read_tier") or "public_skim").strip().lower() or "public_skim"
    if coordinate:
        if ":" in coordinate:
            namespace, identifier = coordinate.rsplit(":", 1)
        elif not identifier:
            identifier = coordinate
    if not namespace or not identifier:
        raise HTTPException(status_code=422, detail="coordinate or namespace/identifier is required")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
    api.set_ledger(ledger_id)

    body = await _backend_fetch_json(
        method="POST",
        path="/resolve/tiered",
        payload={
            "namespace": namespace,
            "identifier": identifier,
            "read_tier": read_tier,
        },
        headers={
            **api.headers,
            "x-ledger-id": namespace,
        },
    )
    return JSONResponse(body)


@rt("/api/coord/diagnose", methods=["POST"])
async def diagnose_coordinate(request: Request):
    """Diagnose coordinate resolution attempts/scopes for local debugging."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    coordinate = str(payload.get("coordinate") or "").strip()
    if not coordinate:
        raise HTTPException(status_code=422, detail="coordinate is required")

    session_id = str(payload.get("session_id") or request.cookies.get("ds_session", DEFAULT_SESSION_ID)).strip() or DEFAULT_SESSION_ID
    session = get_session(session_id)
    ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
    entity = str(payload.get("entity") or session.get("entity") or build_entity_namespace(ledger_id, session_id))
    api.set_ledger(ledger_id)

    resolved, diagnostics = await _decode_with_fallback_attempts(
        coordinate,
        entity=entity,
        session_id=session_id,
    )

    normalized = _normalize_decoded_payload(resolved) if isinstance(resolved, dict) else None
    skim = _extract_skim_line(resolved) if isinstance(resolved, dict) else None
    quote_candidate = _extract_content_text(resolved) if isinstance(resolved, dict) else None

    return JSONResponse(
        {
            "coordinate": coordinate,
            "resolved": isinstance(resolved, dict),
            "entity": entity,
            "session_id": session_id,
            "ledger_id": ledger_id,
            "api_base": settings.API_BASE,
            "attempts": diagnostics,
            "summary": normalized or skim,
            "exact_quote_candidate": quote_candidate,
            "resolved_payload": resolved if isinstance(resolved, dict) else None,
        }
    )


@rt("/api/ingest/file")
async def ingest_file(request: Request):
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=422, detail="file is required")

    kind = _form_str(form.get("kind"), "attachment").strip() or "attachment"

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    requested_ledger = _form_str(form.get("ledger_id")).strip()
    ledger_id = requested_ledger or session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
    entity = (
        _form_str(form.get("entity")).strip()
        or session.get("entity")
        or build_entity_namespace(ledger_id, session_id)
    )
    context_id = _form_str(form.get("context_id")).strip() or getattr(settings, "FRONTEND_CONTEXT_ID", FRONTEND_CONTEXT_ID)
    if session.get("ledger_id") != ledger_id or session.get("entity") != entity:
        session = dict(session)
        session["ledger_id"] = ledger_id
        session["entity"] = entity
        update_session(session_id, session)
    api.set_ledger(ledger_id)
    if context_id:
        api.set_context(context_id)

    content = await upload.read()
    metadata = {
        "filename": upload.filename,
        "content_type": upload.content_type or "application/octet-stream",
        "size_bytes": len(content),
    }
    metadata_raw = _form_str(form.get("metadata")).strip()
    if metadata_raw:
        try:
            user_meta = json.loads(metadata_raw)
            if isinstance(user_meta, dict):
                metadata.update(user_meta)
        except Exception:
            pass
    try:
        result = await api.ingest_file(
            entity=entity,
            filename=upload.filename or "attachment",
            content=content,
            content_type=upload.content_type or "application/octet-stream",
            kind=kind,
            metadata=metadata,
            ledger_id=ledger_id,
            context_id=context_id,
            session_id=_form_str(form.get("session_id")).strip() or session_id,
            turn_id=_form_str(form.get("turn_id")).strip() or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

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
    requested_ledger = _form_str(form.get("ledger_id")).strip()
    ledger_id = requested_ledger or session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
    entity = (
        _form_str(form.get("entity"))
        or session.get("entity")
        or build_entity_namespace(ledger_id, session_id)
    ).strip()
    context_id = _form_str(form.get("context_id")).strip() or getattr(settings, "FRONTEND_CONTEXT_ID", FRONTEND_CONTEXT_ID)
    if session.get("ledger_id") != ledger_id or session.get("entity") != entity:
        session = dict(session)
        session["ledger_id"] = ledger_id
        session["entity"] = entity
        update_session(session_id, session)
    api.set_ledger(ledger_id)
    if context_id:
        api.set_context(context_id)

    data = {}
    for key, value in form.multi_items():
        if key == "file":
            continue
        data[key] = _form_str(value, "")
    data.setdefault("entity", entity)
    data.setdefault("kind", "attachment")
    data["ledger_id"] = ledger_id
    if "context_id" not in data or not str(data.get("context_id") or "").strip():
        data["context_id"] = context_id

    files = {
        "file": (
            upload.filename or "attachment",
            upload.file,
            upload.content_type or "application/octet-stream",
        )
    }

    url = f"{settings.API_BASE.rstrip('/')}/api/ingest/stream-file"
    headers = {
        key: value
        for key, value in api.headers.items()
        if key.lower() != "content-type"
    }

    async def _stream():
        async with httpx.AsyncClient(timeout=None) as client:
            yield json.dumps({"type": "status", "message": "Processing upload..."}) + "\n"
            try:
                async with client.stream(
                    "POST",
                    url,
                    data=data,
                    files=files,
                    headers=headers,
                    timeout=None,
                ) as resp:
                    if resp.status_code >= 400:
                        detail = ""
                        try:
                            detail = await resp.aread()
                            detail = detail.decode("utf-8", errors="ignore")
                        except Exception:
                            detail = ""
                        yield json.dumps(
                            {
                                "type": "error",
                                "status_code": resp.status_code,
                                "detail": detail or f"Upload failed ({resp.status_code})",
                            }
                        ) + "\n"
                        return

                    buffered = ""
                    async for chunk in resp.aiter_text():
                        if not chunk:
                            continue
                        buffered += chunk
                        while "\n" in buffered:
                            line, buffered = buffered.split("\n", 1)
                            stripped = line.strip()
                            if stripped:
                                try:
                                    event = json.loads(stripped)
                                    if isinstance(event, dict) and event.get("type") == "meta":
                                        coord = event.get("coordinate")
                                        if coord:
                                            coord = _normalize_attachment_coord(str(coord))
                                            session.setdefault("attachment_coords", [])
                                            coords = session.get("attachment_coords")
                                            if isinstance(coords, list) and coord not in coords:
                                                coords.append(coord)
                                                session["attachment_coords"] = coords[-5:]
                                                update_session(session_id, session)
                                except Exception:
                                    pass
                            yield line + "\n"
                    if buffered.strip():
                        stripped = buffered.strip()
                        try:
                            event = json.loads(stripped)
                            if isinstance(event, dict) and event.get("type") == "meta":
                                coord = event.get("coordinate")
                                if coord:
                                    coord = _normalize_attachment_coord(str(coord))
                                    session.setdefault("attachment_coords", [])
                                    coords = session.get("attachment_coords")
                                    if isinstance(coords, list) and coord not in coords:
                                        coords.append(coord)
                                        session["attachment_coords"] = coords[-5:]
                                        update_session(session_id, session)
                        except Exception:
                            pass
                        yield buffered + "\n"
            except Exception as exc:
                yield json.dumps(
                    {
                        "type": "error",
                        "status_code": 502,
                        "detail": f"Upload proxy error: {exc}",
                    }
                ) + "\n"
                return

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@rt("/api/ingest/limits")
async def ingest_limits(request: Request):
    try:
        body = await _backend_fetch_json(
            method="GET",
            path="/api/ingest/limits",
        )
        return JSONResponse(body)
    except HTTPException:
        fallback = {"attachment_max_bytes": settings.ATTACHMENT_MAX_BYTES}
        return JSONResponse(fallback)


@rt("/api/chat/web4/decode")
async def decode_web4(request: Request):
    """Resolve a Web4 key using namespace + identifier via backend /chat/web4/decode."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    namespace = (payload.get("namespace") or "").strip()
    identifier = (payload.get("identifier") or "").strip()
    if not namespace or not identifier:
        raise HTTPException(status_code=422, detail="namespace and identifier are required")

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
    api.set_ledger(ledger_id)

    try:
        resolved = await api.decode_web4(namespace=namespace, identifier=identifier)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return JSONResponse(resolved)

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

    try:
        entries = await api.get_all_entries(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return JSONResponse(entries)

async def _local_llm_chat(**kwargs) -> ChatResponse:
    """Local fallback ChatResponse placeholder."""
    return ChatResponse.from_json({"reply": "Local fallback not active", "stats": {}})

async def set_agent(request: Request):
    """Set the active LLM agent OR trigger full load."""
    form = await request.form()
    agent = form.get("agent")
    
    if agent == "add_new":
        scope = request.scope
        scope["query_string"] = b"mode=full"
        new_req = Request(scope)
        return await list_models(new_req)

    session_id = request.cookies.get("ds_session", "demo-session")
    session = get_session(session_id)
    selected_agent = str(agent or "").strip()
    timeout = settings.HTTP_TIMEOUT if settings.HTTP_TIMEOUT > 30 else 60.0
    local_models = await _fetch_local_models(timeout)
    has_openrouter = bool(settings.OPENROUTER_API_KEY)
    if local_models:
        local_model_ids = {item["id"] for item in local_models}
        if selected_agent and selected_agent not in local_model_ids:
            if not (has_openrouter and "/" in selected_agent):
                selected_agent = _pick_preferred_local_model(local_models, settings.LLM_MODEL)
    if not selected_agent:
        selected_agent = settings.LLM_MODEL

    session['agent'] = selected_agent
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
    ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
    entity = session.get("entity") or build_entity_namespace(ledger_id, session_id)
    api.set_ledger(ledger_id)

    try:
        history = await api.thread(entity=entity, limit=500)
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

async def get_stats(request: Request):
    """Fetch persistent stats from the backend Ledger."""
    return await build_stats_payload(request)

async def get_global_stats(request: Request):
    """Fetch global stats from the backend."""
    try:
        stats_data = await api.global_stats()
    except Exception:
        stats_data = {}
    return stats_data

async def get_costs(request: Request):
    """Fetch account-wide credits via backend billing proxy and current session spend."""

    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    session_cost = float(session.get("total_cost", 0.0) or 0.0)

    try:
        billing = await api.billing_openrouter()
    except Exception as exc:  # pragma: no cover - passthrough proxy
        print(f"Billing fetch failed: {exc}")
        billing = {}

    if not isinstance(billing, dict):
        billing = {}

    credits = billing.get("credits") if isinstance(billing.get("credits"), dict) else {}

    total_cost = None
    remaining_cost = None

    for key in ("total", "total_usd", "credits_total", "usd_total", "balance_total", "balance_usd"):
        value = credits.get(key) if isinstance(credits, dict) else None
        if value is None:
            value = billing.get(key)
        if isinstance(value, (int, float)):
            total_cost = float(value)
            break

    for key in (
        "remaining",
        "available",
        "balance",
        "usd",
        "balance_usd",
        "usd_cents",
        "usd_cents_total",
        "credits_remaining",
    ):
        value = credits.get(key) if isinstance(credits, dict) else None
        if value is None:
            value = billing.get(key)
        if isinstance(value, (int, float)):
            remaining_cost = float(value)
            break

    if remaining_cost is None and isinstance(total_cost, (int, float)):
        remaining_cost = max(total_cost - session_cost, 0)

    return {
        "total_cost": total_cost,
        "session_cost": session_cost,
        "remaining_cost": remaining_cost,
        "billing": billing,
    }

DEFAULT_MODEL_FALLBACK = [
    {"id": "openai/gpt-4o", "name": "OpenAI: GPT-4o"},
    {"id": "anthropic/claude-3.5-sonnet", "name": "Anthropic: Claude 3.5 Sonnet"},
]

CURATED_MODELS = [
    {"id": "moonshotai/kimi-k2.5", "name": "MoonshotAI: Kimi K2.5"},
    {"id": "google/gemini-2.5-flash", "name": "Google: Gemini 2.5 Flash"},
    {"id": "x-ai/grok-4.3", "name": "xAI: Grok 4.3"},
    {"id": "openai/gpt-5.1-chat", "name": "OpenAI: GPT-5.1 Chat"},
    {"id": "anthropic/claude-haiku-4.5", "name": "Anthropic: Claude Haiku 4.5"},
]

_DEPRECATED_MODEL_IDS = {
    "x-ai/grok-4-fast": "x-ai/grok-4.3",
}

_MODEL_DISPLAY_NAMES = {
    "x-ai/grok-4.3": "xAI: Grok 4.3",
}


def _display_name_for_model_id(model_id: str) -> str:
    return _MODEL_DISPLAY_NAMES.get(model_id, model_id)


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


def _control_plane_binding_model_rows(payload: dict[str, Any] | None, *, surface_id: str = "surface:chat:primary") -> list[dict[str, str]]:
    body = payload if isinstance(payload, dict) else {}
    binding_rows: list[Any] = _list_or_empty(body.get("model_bindings"))
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    allowed_statuses = {"active", "derived", "available", "planned"}
    for row in binding_rows:
        if not isinstance(row, dict):
            continue
        provider_type = str(row.get("provider_type") or "").strip().lower()
        model_id = str(row.get("model_id") or "").strip()
        status = str(row.get("status") or "").strip().lower()
        row_surfaces = _list_or_empty(row.get("app_surfaces"))
        app_surfaces = {
            str(item).strip()
            for item in row_surfaces
            if str(item).strip()
        }
        binding_id = str(row.get("binding_id") or "").strip().lower()
        if not model_id or model_id in seen:
            continue
        if provider_type != "openrouter":
            continue
        if status and status not in allowed_statuses:
            continue
        if app_surfaces and surface_id not in app_surfaces:
            continue
        if not app_surfaces and not binding_id.startswith("binding:chat:"):
            continue
        seen.add(model_id)
        selected.append({"id": model_id, "name": str(row.get("name") or model_id).strip() or model_id})
    return selected

async def list_models(request: Request):
    """List available models from Ollama (local) or OpenRouter."""

    mode = (request.query_params.get("mode") or "default").lower()
    surface_id = str(request.query_params.get("surface_id") or settings.CHAT_SURFACE_ID or "surface:chat:primary").strip()
    timeout = settings.HTTP_TIMEOUT if settings.HTTP_TIMEOUT > 30 else 60.0

    headers = {
        "Accept": "application/json",
        "HTTP-Referer": settings.API_BASE,
        "X-Title": "ourIP.AI",
    }

    if settings.OPENROUTER_API_KEY:
        headers["Authorization"] = f"Bearer {settings.OPENROUTER_API_KEY}"

    models_data: list[dict[str, str]] = []
    fallback_used = False
    local_base = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    has_openrouter = bool(settings.OPENROUTER_API_KEY)
    local_models: list[dict[str, str]] = []
    online_models: list[dict[str, str]] = []

    def _dedupe_models(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            mid = str(row.get("id") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            deduped.append({"id": mid, "name": str(row.get("name") or mid).strip()})
        return deduped

    if local_base:
        local_models = _migrate_model_rows(
            sorted(await _fetch_local_models(timeout), key=lambda x: x["name"])
        )

    control_plane_available = False
    if has_openrouter:
        try:
            control_plane_path = f"/api/control-plane/model-bindings?{urlencode({'surface_id': surface_id})}"
            control_plane_models = _migrate_model_rows(
                _control_plane_binding_model_rows(
                    await _control_plane_backend_fetch(
                        request,
                        method="GET",
                        path=control_plane_path,
                    ),
                    surface_id=surface_id,
                )
            )
            control_plane_available = True
        except HTTPException:
            control_plane_models = []
        online_models = control_plane_models if control_plane_available else _migrate_model_rows(list(CURATED_MODELS))

    if mode == "full":
        if has_openrouter:
            fetched_online: list[dict[str, str]] = []
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(
                        "https://openrouter.ai/api/v1/models", headers=headers
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        for item in payload.get("data", []):
                            mid = item.get("id")
                            mname = item.get("name") or mid
                            if mid:
                                fetched_online.append({"id": mid, "name": mname})
            except Exception:
                pass
            fetched_online = _migrate_model_rows(fetched_online)
            if fetched_online:
                online_models = sorted(fetched_online, key=lambda x: x["name"])
            else:
                fallback_used = True
                online_models = _migrate_model_rows(list(DEFAULT_MODEL_FALLBACK))

        if not local_models and not online_models:
            fallback_used = True
    elif not local_models and not online_models:
        fallback_used = True

    default_model = (settings.LLM_MODEL or "").strip()
    default_model = _DEPRECATED_MODEL_IDS.get(default_model, default_model)
    if default_model and has_openrouter and all(item.get("id") != default_model for item in online_models):
        # Only inject the configured default when the control-plane filter is not
        # authoritative; otherwise the relationship gate decides which models are shown.
        if not control_plane_available:
            online_models.append({"id": default_model, "name": default_model})

    local_models = _dedupe_models(local_models)
    online_models = _dedupe_models(online_models)
    models_data = _dedupe_models([*local_models, *online_models])

    accept_header = (request.headers.get("accept") or "").lower()
    is_htmx = (request.headers.get("hx-request") or "").lower() == "true"
    wants_json = "application/json" in accept_header and not is_htmx

    if not wants_json:
        session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
        session = get_session(session_id)
        current_model = str(session.get("agent") or settings.LLM_MODEL).strip()
        # Migrate deprecated model ids to their replacements.
        current_model = _DEPRECATED_MODEL_IDS.get(current_model, current_model)
        if current_model and current_model != str(session.get("agent") or "").strip():
            session["agent"] = current_model
            update_session(session_id, session)
        if current_model and all(item.get("id") != current_model for item in models_data):
            models_data = [{"id": current_model, "name": f"{current_model} (Saved)"}, *models_data]
        if not current_model and models_data:
            current_model = _pick_preferred_local_model(models_data, settings.LLM_MODEL)
        options = []
        
        for model in models_data:
            is_selected = (model["id"] == current_model)
            options.append(
                Option(model["name"], value=model["id"], selected=is_selected)
            )

        if mode != "full":
            options.append(Option("Add new", value="add_new"))
            
        return tuple(options)

    return {
        "models": models_data,
        "local_models": local_models,
        "online_models": online_models,
        "fallback": fallback_used if mode == "full" else False,
    }


async def models_debug(request: Request):
    timeout = settings.HTTP_TIMEOUT if settings.HTTP_TIMEOUT > 30 else 60.0
    local = await _fetch_local_models_debug(timeout)
    return {
        "llm_base_url": os.getenv("LLM_BASE_URL"),
        "settings_llm_model": _DEPRECATED_MODEL_IDS.get(settings.LLM_MODEL, settings.LLM_MODEL),
        "settings_llm_provider": settings.LLM_PROVIDER,
        "local": local,
    }


def _middleware_admin_token() -> str:
    return (
        os.getenv("ADMIN_TOKEN")
        or os.getenv("TRUST_ANCHOR_ADMIN_TOKEN")
        or ""
    ).strip()


def _require_middleware_admin(request: Request) -> bool:
    """Verify the caller supplied the configured middleware admin token.

    If no admin token is configured locally, the route is still available to
    keep local development unblocked; the dashboard's own auth gate remains the
    primary authorization layer.
    """
    expected = _middleware_admin_token()
    if not expected:
        return True
    provided = str(request.headers.get("x-admin-token") or "").strip()
    return provided == expected


def _mask_api_key(api_key: str) -> str | None:
    key = str(api_key or "").strip()
    if not key:
        return None
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


async def get_openrouter_key_status(request: Request):
    if not _require_middleware_admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    override = _get_openrouter_override()
    env_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    source = "override" if override else "env"
    effective = override or env_key
    masked = _mask_api_key(effective)
    return {
        "configured": bool(effective),
        "masked": masked,
        "source": source,
    }


async def get_openrouter_key_status_only(request: Request):
    """Return only configuration status without the masked key value."""
    if not _require_middleware_admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    override = _get_openrouter_override()
    env_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    source = "override" if override else "env"
    effective = override or env_key
    return {
        "configured": bool(effective),
        "source": source,
    }


async def set_openrouter_key_endpoint(request: Request):
    if not _require_middleware_admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    api_key = str(body.get("api_key") or "").strip()
    if not api_key:
        return JSONResponse({"error": "api_key is required"}, status_code=400)
    set_openrouter_api_key(api_key)
    _set_openrouter_override(api_key)
    return {
        "configured": True,
        "masked": _mask_api_key(api_key),
        "source": "override",
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
    active_ledger = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)

    try:
        ledgers = await api.list_ledgers()
    except Exception:
        # Fall back to at least returning the active ledger if the backend call fails
        ledgers = []

    ledgers = sorted({*ledgers, active_ledger})
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
        await api.create_or_switch_ledger(ledger_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    session = get_session(session_id)
    session["ledger_id"] = ledger_id
    update_session(session_id, session)
    api.set_ledger(ledger_id)

    return {"ledger_id": ledger_id}


def _control_plane_actor(request: Request, payload: dict[str, Any]) -> str:
    envelope = build_backend_auth_envelope(request=request, payload=payload)
    claims_raw = envelope.get("claims")
    claims: dict[str, Any] = claims_raw if isinstance(claims_raw, dict) else {}
    return str(
        claims.get("principal_did")
        or request.headers.get("x-principal-did")
        or request.headers.get("x-did")
        or ""
    ).strip()


def _control_plane_backend_headers(request: Request, payload: dict[str, Any] | None = None) -> dict[str, str]:
    payload = payload if isinstance(payload, dict) else {}
    actor = _control_plane_actor(request, payload)
    tenant_id = str(payload.get("tenant_id") or request.headers.get("x-tenant-id") or "").strip()
    context_id = str(request.headers.get("x-context-id") or FRONTEND_CONTEXT_ID or "ctx:middleware:control-plane").strip()
    admin_token = (os.getenv("ADMIN_TOKEN") or os.getenv("TRUST_ANCHOR_ADMIN_TOKEN") or "").strip()
    headers = {
        "Content-Type": "application/json",
        "x-principal-id": actor or "ops-admin",
        "x-principal-type": "admin",
        "x-context-id": context_id,
    }
    if actor:
        headers["x-principal-did"] = actor
    if tenant_id:
        headers["x-tenant-id"] = tenant_id
    if admin_token:
        headers["x-admin-token"] = admin_token
    return headers


def _account_backend_headers(request: Request) -> dict[str, str]:
    """Extract user session auth from request for backend account endpoint proxying."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    auth_header = str(request.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        headers["x-session-token"] = token
    elif request.headers.get("x-session-token"):
        headers["x-session-token"] = str(request.headers.get("x-session-token")).strip()
    cookie_token = request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE)
    if cookie_token:
        headers["x-session-token"] = cookie_token
    admin_token = request.headers.get("x-admin-token")
    if admin_token:
        headers["x-admin-token"] = str(admin_token).strip()
    else:
        # Fallback to middleware-configured admin token so control plane
        # approvals that proxy through us can reach admin-gated backends.
        middleware_admin = (os.getenv("ADMIN_TOKEN") or "").strip()
        if not middleware_admin:
            middleware_admin = (os.getenv("TRUST_ANCHOR_ADMIN_TOKEN") or "").strip()
        if middleware_admin:
            headers["x-admin-token"] = middleware_admin
    return headers


async def _account_backend_fetch(
    request: Request,
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{settings.API_BASE.rstrip('/')}{path}"
    request_headers = _account_backend_headers(request)
    request_method = method.upper()
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            if request_method == "GET":
                response = await client.get(url, headers=request_headers)
            elif request_method == "POST":
                response = await client.post(url, json=payload, headers=request_headers)
            else:
                response = await client.request(request_method, url, json=payload, headers=request_headers)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"Upstream timeout: {url}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request error: {url}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    try:
        body = response.json()
    except Exception:
        body = {"error": "upstream_invalid_json", "text": response.text[:1000]}
    if not isinstance(body, dict):
        body = {"data": body}
    return body


async def proxy_account_model_library(request: Request):
    """Proxy GET /account/current/model-library to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/model-library")


async def proxy_account_model_library_select(request: Request):
    """Proxy POST /account/current/model-library/select to backend."""
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    return await _account_backend_fetch(request, method="POST", path="/account/current/model-library/select", payload=payload)


async def proxy_account_principals(request: Request):
    """Proxy GET /account/current/principals to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/principals")


async def proxy_account_agent_principal_bootstrap(request: Request):
    """Proxy POST /account/current/principals/agent/bootstrap to backend."""
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    return await _account_backend_fetch(
        request,
        method="POST",
        path="/account/current/principals/agent/bootstrap",
        payload=payload,
    )


async def proxy_account_connections(request: Request):
    """Proxy GET /account/current/connections to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/connections")


async def proxy_account_onboarding(request: Request):
    """Proxy GET /account/current/onboarding to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/onboarding")


async def proxy_account_setup_prompt(request: Request):
    """Proxy GET /account/current/setup-prompt to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/setup-prompt")


async def proxy_account_setup_prompt_dismiss(request: Request):
    """Proxy POST /account/current/setup-prompt/dismiss to backend."""
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    return await _account_backend_fetch(request, method="POST", path="/account/current/setup-prompt/dismiss", payload=payload)


async def proxy_wallet_credential_offer(request: Request):
    """Proxy GET /wallet/credential-offer to backend."""
    query = str(request.url.query or "").strip()
    path = "/wallet/credential-offer"
    if query:
        path = f"{path}?{query}"
    return await _account_backend_fetch(request, method="GET", path=path)


async def proxy_wallet_did_document(request: Request):
    """Proxy GET /wallet/{wallet_id}/did.json to backend."""
    wallet_id = request.path_params.get("wallet_id", "")
    path = f"/wallet/{wallet_id}/did.json"
    return await _account_backend_fetch(request, method="GET", path=path)


async def proxy_wallet_providers(request: Request):
    """Proxy GET /wallet/providers to backend."""
    return await _account_backend_fetch(request, method="GET", path="/wallet/providers")


async def proxy_admin_provisioning_job(request: Request):
    """Proxy GET /admin/provisioning/jobs/{job_id} to backend."""
    job_id = request.path_params.get("job_id", "")
    path = f"/admin/provisioning/jobs/{job_id}"
    return await _account_backend_fetch(request, method="GET", path=path)


async def proxy_admin_provisioning_job_steps(request: Request):
    """Proxy GET /admin/provisioning/jobs/{job_id}/steps to backend."""
    job_id = request.path_params.get("job_id", "")
    path = f"/admin/provisioning/jobs/{job_id}/steps"
    return await _account_backend_fetch(request, method="GET", path=path)


async def proxy_account_onboarding_post(request: Request):
    """Proxy POST /account/current/onboarding to backend."""
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    return await _account_backend_fetch(request, method="POST", path="/account/current/onboarding", payload=payload)


async def proxy_account_provisioning(request: Request):
    """Proxy GET /account/current/provisioning to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/provisioning")


async def proxy_account_provisioning_run(request: Request):
    """Proxy POST /account/current/provisioning/run to backend."""
    return await _account_backend_fetch(request, method="POST", path="/account/current/provisioning/run")


async def proxy_account_identity_wallet_link_start(request: Request):
    """Proxy POST /account/current/identity/wallet-link/start to backend."""
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    return await _account_backend_fetch(request, method="POST", path="/account/current/identity/wallet-link/start", payload=payload)


async def proxy_account_identity_wallet_link_complete(request: Request):
    """Proxy POST /account/current/identity/wallet-link/complete to backend."""
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    return await _account_backend_fetch(request, method="POST", path="/account/current/identity/wallet-link/complete", payload=payload)


async def proxy_account_current(request: Request):
    """Proxy GET /account/current to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current")


async def proxy_account_subscription(request: Request):
    """Proxy GET /account/current/subscription to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/subscription")


async def proxy_account_setup_checklist(request: Request):
    """Proxy GET /account/current/setup-checklist to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/setup-checklist")


async def proxy_account_surfaces(request: Request):
    """Proxy GET /account/current/surfaces to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/surfaces")


async def proxy_account_identity(request: Request):
    """Proxy GET /account/current/identity to backend."""
    return await _account_backend_fetch(request, method="GET", path="/account/current/identity")


def _control_plane_idempotency_key(payload: dict[str, Any]) -> str:
    raw = str(payload.get("idempotency_key") or "").strip()
    if raw:
        return raw
    return f"cpm:{secrets.token_hex(12)}"


def _control_plane_mutation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded = dict(payload)
    forwarded["idempotency_key"] = _control_plane_idempotency_key(forwarded)
    return forwarded


def _control_plane_governance_mode(payload: dict[str, Any]) -> str:
    return str(payload.get("governance_mode") or "").strip().lower()


def _control_plane_guarded_path(path: str) -> bool:
    return path in {
        "/api/control-plane/relationships",
        "/api/control-plane/entities/activate",
        "/api/control-plane/entities/remove",
    }


def _control_plane_break_glass(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("break_glass")
    if not isinstance(raw, dict):
        return None
    actor = str(raw.get("actor") or payload.get("break_glass_actor") or "").strip()
    reason_code = str(raw.get("reason_code") or payload.get("break_glass_reason_code") or "").strip()
    scope = str(raw.get("scope") or payload.get("break_glass_scope") or "").strip()
    expires_at = str(raw.get("expires_at") or "").strip() or None
    if not actor or not reason_code or not scope:
        raise HTTPException(status_code=422, detail="break_glass requires actor, reason_code, and scope")
    return {
        "actor": actor,
        "reason_code": reason_code,
        "scope": scope,
        "expires_at": expires_at,
    }


def _control_plane_policy_trace(*, path: str, payload: dict[str, Any], execution_mode: str) -> dict[str, Any]:
    guarded = _control_plane_guarded_path(path)
    break_glass = _control_plane_break_glass(payload) if (guarded and execution_mode == "direct_write") else None
    reason_code = "break_glass_override" if break_glass else ("strict_default_submission" if guarded else "direct_write_allowed")
    policy_decision = "override" if break_glass else ("submit_for_approval" if guarded else "allow")
    return {
        "policy_decision": policy_decision,
        "reason_code": reason_code,
        "guarded_path": guarded,
        "requested_governance_mode": _control_plane_governance_mode(payload) or None,
        "execution_mode": execution_mode,
        "break_glass_active": bool(break_glass),
    }


def _control_plane_forward_payload(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded = dict(payload)
    forwarded.pop("governance_mode", None)
    forwarded.pop("break_glass", None)
    forwarded.pop("break_glass_actor", None)
    forwarded.pop("break_glass_reason_code", None)
    forwarded.pop("break_glass_scope", None)
    return forwarded


def _control_plane_guarded_override_error(*, path: str, payload: dict[str, Any]) -> JSONResponse | None:
    governance_mode = _control_plane_governance_mode(payload)
    if governance_mode != "direct_write" or not _control_plane_guarded_path(path):
        return None
    if _control_plane_break_glass(payload) is not None:
        return None
    return JSONResponse({"detail": {"error": "break_glass_required", "path": path}}, status_code=403)


def _should_submit_control_plane_mutation(*, path: str, payload: dict[str, Any]) -> bool:
    governance_mode = _control_plane_governance_mode(payload)
    guarded = _control_plane_guarded_path(path)
    if governance_mode == "submitted_for_approval":
        return True
    if governance_mode == "direct_write":
        return False
    return guarded


def _control_plane_submission_payload(*, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    entity_type = str(payload.get("entity_type") or "").strip().lower() or None
    entity_id = str(payload.get("entity_id") or "").strip() or None
    if path == "/api/control-plane/relationships":
        entity_type = "relationship"
        entity_id = str(payload.get("relationship_id") or "").strip() or "::".join(
            [
                str(payload.get("subject_entity_type") or "").strip().lower(),
                str(payload.get("subject_entity_id") or "").strip(),
                str(payload.get("object_entity_type") or "").strip().lower(),
                str(payload.get("object_entity_id") or "").strip(),
            ]
        )
    return {
        "mutation_kind": path.rsplit("/", 1)[-1] or "mutation",
        "target_path": path,
        "target_entity_type": entity_type,
        "target_entity_id": entity_id,
        "payload": _control_plane_forward_payload(payload),
        "reason": str(payload.get("reason") or "governed_control_plane_mutation").strip() or "governed_control_plane_mutation",
        "submitted_by": str(payload.get("submitted_by") or "").strip() or None,
        "evidence_refs": list(payload.get("evidence_refs") or []),
        "idempotency_key": str(payload.get("idempotency_key") or "").strip() or None,
        "submission_ref": str(payload.get("submission_ref") or "").strip() or None,
        "policy_trace": _control_plane_policy_trace(path=path, payload=payload, execution_mode="submitted_for_approval"),
    }


async def _submit_control_plane_mutation(
    request: Request,
    *,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    submission_payload = _control_plane_submission_payload(path=path, payload={**payload, "submitted_by": _control_plane_actor(request, payload)})
    body = await _control_plane_backend_fetch(
        request,
        method="POST",
        path="/api/control-plane/submissions",
        payload=submission_payload,
    )
    return _normalize_control_plane_mutation_response(
        body,
        idempotency_key=str(payload.get("idempotency_key") or "").strip() or None,
        policy_trace=submission_payload.get("policy_trace") if isinstance(submission_payload.get("policy_trace"), dict) else None,
    )


def _normalize_control_plane_mutation_response(body: dict[str, Any], *, idempotency_key: str | None = None, policy_trace: dict[str, Any] | None = None, break_glass_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = dict(body)
    normalized["execution_mode"] = str(body.get("execution_mode") or "direct_write").strip() or "direct_write"
    normalized["submission_status"] = str(body.get("submission_status") or "applied").strip() or "applied"
    normalized["mutation_ref"] = str(body.get("mutation_ref") or f"cpm:{secrets.token_hex(12)}").strip()
    status = normalized["submission_status"]
    if str(body.get("submitted_at") or "").strip():
        normalized["submitted_at"] = str(body.get("submitted_at")).strip()
    elif status == "submitted":
        normalized["submitted_at"] = datetime.utcnow().isoformat()
    if str(body.get("approved_at") or "").strip():
        normalized["approved_at"] = str(body.get("approved_at")).strip()
    if str(body.get("rejected_at") or "").strip():
        normalized["rejected_at"] = str(body.get("rejected_at")).strip()
    if str(body.get("failed_at") or "").strip():
        normalized["failed_at"] = str(body.get("failed_at")).strip()
    if str(body.get("applied_at") or "").strip():
        normalized["applied_at"] = str(body.get("applied_at")).strip()
    elif status == "applied":
        normalized["applied_at"] = datetime.utcnow().isoformat()
    normalized["idempotency_key"] = str(body.get("idempotency_key") or idempotency_key or "").strip() or None
    if "submission_ref" not in normalized:
        normalized["submission_ref"] = None
    if isinstance(policy_trace, dict):
        normalized["policy_trace"] = policy_trace
    if isinstance(break_glass_audit, dict):
        normalized["break_glass_audit"] = break_glass_audit
    return normalized


async def _control_plane_backend_fetch(
    request: Request,
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = await _backend_fetch_json(
        method=method,
        path=path,
        payload=payload,
        headers=_control_plane_backend_headers(request, payload),
    )
    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="Invalid upstream control-plane response")
    return body


async def list_control_plane_ledgers(request: Request):
    return await _control_plane_backend_fetch(request, method="GET", path="/api/control-plane/ledgers")


async def list_control_plane_principals(request: Request):
    query = str(request.url.query or "").strip()
    path = "/api/control-plane/principals"
    if query:
        path = f"{path}?{query}"
    body = await _control_plane_backend_fetch(request, method="GET", path=path)
    principals = body.get("principals") if isinstance(body.get("principals"), list) else []
    body["principals"] = principals
    return body


async def list_control_plane_submissions(request: Request):
    return await _control_plane_backend_fetch(request, method="GET", path="/api/control-plane/submissions")


async def review_control_plane_submission(request: Request):
    payload = await request.json()
    submission_ref = str(request.path_params.get("submission_ref") or "").strip()
    if not submission_ref:
        raise HTTPException(status_code=422, detail="submission_ref is required")
    body = await _control_plane_backend_fetch(
        request,
        method="POST",
        path=f"/api/control-plane/submissions/{submission_ref}/review",
        payload=payload,
    )
    return _normalize_control_plane_mutation_response(body)


async def list_control_plane_providers(request: Request):
    return await _control_plane_backend_fetch(request, method="GET", path="/api/control-plane/providers")


async def upsert_control_plane_provider(request: Request):
    payload = await request.json()
    provider_id = str(payload.get("provider_id") or "").strip()
    provider_type = str(payload.get("provider_type") or "").strip()
    if not provider_id or not provider_type:
        raise HTTPException(status_code=422, detail="provider_id and provider_type are required")
    forwarded = _control_plane_mutation_payload(payload)
    forwarded["provider_id"] = provider_id
    forwarded["provider_type"] = provider_type
    body = await _control_plane_backend_fetch(request, method="POST", path="/api/control-plane/providers", payload=forwarded)
    return _normalize_control_plane_mutation_response(body, idempotency_key=forwarded.get("idempotency_key"), policy_trace=_control_plane_policy_trace(path="/api/control-plane/providers", payload=forwarded, execution_mode="direct_write"))


async def list_control_plane_model_bindings(request: Request):
    return await _control_plane_backend_fetch(request, method="GET", path="/api/control-plane/model-bindings")


async def upsert_control_plane_model_binding(request: Request):
    payload = await request.json()
    binding_id = str(payload.get("binding_id") or "").strip()
    provider_type = str(payload.get("provider_type") or "").strip()
    model_id = str(payload.get("model_id") or "").strip()
    if not binding_id or not provider_type or not model_id:
        raise HTTPException(status_code=422, detail="binding_id, provider_type, and model_id are required")
    forwarded = _control_plane_mutation_payload(payload)
    forwarded["binding_id"] = binding_id
    forwarded["provider_type"] = provider_type
    forwarded["model_id"] = model_id
    body = await _control_plane_backend_fetch(request, method="POST", path="/api/control-plane/model-bindings", payload=forwarded)
    return _normalize_control_plane_mutation_response(body, idempotency_key=forwarded.get("idempotency_key"), policy_trace=_control_plane_policy_trace(path="/api/control-plane/model-bindings", payload=forwarded, execution_mode="direct_write"))


async def upsert_control_plane_ledger(request: Request):
    payload = await request.json()
    ledger_id = str(payload.get("ledger_id") or payload.get("name") or "").strip()
    if not ledger_id:
        raise HTTPException(status_code=422, detail="ledger_id is required")
    forwarded = _control_plane_mutation_payload(payload)
    forwarded.setdefault("namespace", ledger_id)
    forwarded.setdefault("name", str(payload.get("display_name") or payload.get("name") or ledger_id).strip() or ledger_id)
    forwarded.setdefault("status", str(payload.get("status") or "pending").strip().lower() or "pending")
    forwarded.setdefault("provisioning_source", str(payload.get("provisioning_source") or "control_plane").strip() or "control_plane")
    forwarded.setdefault("canonical_subject", _control_plane_canonical_subject(request, entity_type="ledger", entity_id=ledger_id))
    forwarded.setdefault("canonical_subject_source", "did:web:ledger")
    body = await _control_plane_backend_fetch(request, method="POST", path="/api/control-plane/ledgers", payload=forwarded)
    return _normalize_control_plane_mutation_response(body, idempotency_key=forwarded.get("idempotency_key"), policy_trace=_control_plane_policy_trace(path="/api/control-plane/ledgers", payload=forwarded, execution_mode="direct_write"))


async def list_control_plane_surfaces(request: Request):
    return await _control_plane_backend_fetch(request, method="GET", path="/api/control-plane/surfaces")


async def upsert_control_plane_surface(request: Request):
    payload = await request.json()
    surface_id = str(payload.get("surface_id") or payload.get("name") or "").strip()
    if not surface_id:
        raise HTTPException(status_code=422, detail="surface_id is required")
    forwarded = _control_plane_mutation_payload(payload)
    forwarded["surface_id"] = surface_id
    forwarded.setdefault("display_name", str(payload.get("display_name") or payload.get("name") or surface_id).strip() or surface_id)
    forwarded.setdefault("status", str(payload.get("status") or "pending").strip().lower() or "pending")
    forwarded.setdefault("provisioning_source", str(payload.get("provisioning_source") or "control_plane").strip() or "control_plane")
    forwarded.setdefault("canonical_subject", _control_plane_canonical_subject(request, entity_type="surface", entity_id=surface_id))
    forwarded.setdefault("canonical_subject_source", "did:web:surface")
    body = await _control_plane_backend_fetch(request, method="POST", path="/api/control-plane/surfaces", payload=forwarded)
    return _normalize_control_plane_mutation_response(body, idempotency_key=forwarded.get("idempotency_key"), policy_trace=_control_plane_policy_trace(path="/api/control-plane/surfaces", payload=forwarded, execution_mode="direct_write"))


async def list_control_plane_relationships(request: Request):
    return await _control_plane_backend_fetch(request, method="GET", path="/api/control-plane/relationships")


async def upsert_control_plane_relationship(request: Request):
    payload = await request.json()
    subject_entity_type = str(payload.get("subject_entity_type") or "").strip().lower()
    subject_entity_id = str(payload.get("subject_entity_id") or "").strip()
    object_entity_type = str(payload.get("object_entity_type") or "").strip().lower()
    object_entity_id = str(payload.get("object_entity_id") or "").strip()
    relationship_id = str(payload.get("relationship_id") or "").strip()
    if not subject_entity_type or not subject_entity_id:
        raise HTTPException(status_code=422, detail="subject_entity_type and subject_entity_id are required")
    if not object_entity_type or not object_entity_id:
        raise HTTPException(status_code=422, detail="object_entity_type and object_entity_id are required")
    if not relationship_id:
        relationship_id = "::".join([subject_entity_type, subject_entity_id, object_entity_type, object_entity_id])
    forwarded = _control_plane_mutation_payload(payload)
    forwarded["relationship_id"] = relationship_id
    forwarded["subject_entity_type"] = subject_entity_type
    forwarded["subject_entity_id"] = subject_entity_id
    forwarded["object_entity_type"] = object_entity_type
    forwarded["object_entity_id"] = object_entity_id
    forwarded.setdefault("status", str(payload.get("status") or "active").strip().lower() or "active")
    forwarded.setdefault("canonical_subject", _control_plane_canonical_subject(request, entity_type="relationship", entity_id=relationship_id))
    forwarded.setdefault("canonical_subject_source", "did:web:relationship")
    override_error = _control_plane_guarded_override_error(path="/api/control-plane/relationships", payload=forwarded)
    if override_error is not None:
        return override_error
    if _should_submit_control_plane_mutation(path="/api/control-plane/relationships", payload=forwarded):
        return await _submit_control_plane_mutation(request, path="/api/control-plane/relationships", payload=forwarded)
    body = await _control_plane_backend_fetch(
        request,
        method="POST",
        path="/api/control-plane/relationships",
        payload=_control_plane_forward_payload(forwarded),
    )
    return _normalize_control_plane_mutation_response(body, idempotency_key=forwarded.get("idempotency_key"), policy_trace=_control_plane_policy_trace(path="/api/control-plane/relationships", payload=forwarded, execution_mode="direct_write"), break_glass_audit=_control_plane_break_glass(forwarded))


async def upsert_control_plane_principal(request: Request):
    payload = await request.json()
    principal_did = str(payload.get("principal_did") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    forwarded = _control_plane_mutation_payload(payload)
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    metadata["actor_type"] = str(payload.get("principal_type") or metadata.get("actor_type") or "human").strip().lower() or "human"
    ledger_id = str(payload.get("ledger_id") or "").strip()
    if ledger_id:
        metadata["ledger_id"] = ledger_id
    forwarded["metadata"] = metadata
    forwarded.setdefault("status", str(payload.get("status") or "pending").strip().lower() or "pending")
    forwarded.setdefault("provisioning_source", str(payload.get("provisioning_source") or "control_plane").strip() or "control_plane")
    body = await _control_plane_backend_fetch(request, method="POST", path="/api/control-plane/principals", payload=forwarded)
    body = _normalize_control_plane_mutation_response(body, idempotency_key=forwarded.get("idempotency_key"), policy_trace=_control_plane_policy_trace(path="/api/control-plane/entities/activate", payload=forwarded, execution_mode="direct_write"), break_glass_audit=_control_plane_break_glass(forwarded))
    principal = body.get("principal") if isinstance(body.get("principal"), dict) else None
    _sync_control_plane_principal_locally(principal)
    provisioning = _principal_provisioning_summary(principal or {})
    body["provisioning"] = provisioning
    return body


async def provision_control_plane_codex_principal(request: Request):
    payload = await request.json()
    forwarded = _control_plane_mutation_payload(payload)
    ledger_id = str(payload.get("ledger_id") or "").strip()
    if ledger_id:
        forwarded["ledger_id"] = ledger_id
    surface_ids = payload.get("surface_ids")
    if isinstance(surface_ids, list):
        forwarded["surface_ids"] = [str(item).strip() for item in surface_ids if str(item).strip()]
    delegated_by_principal_did = str(payload.get("delegated_by_principal_did") or "").strip()
    if delegated_by_principal_did:
        forwarded["delegated_by_principal_did"] = delegated_by_principal_did
    body = await _control_plane_backend_fetch(
        request,
        method="POST",
        path="/api/control-plane/principals/codex/provision",
        payload=forwarded,
    )
    normalized = _normalize_control_plane_mutation_response(
        body,
        idempotency_key=forwarded.get("idempotency_key"),
        policy_trace=_control_plane_policy_trace(
            path="/api/control-plane/principals/codex/provision",
            payload=forwarded,
            execution_mode="direct_write",
        ),
    )
    principal = normalized.get("principal") if isinstance(normalized.get("principal"), dict) else None
    _sync_control_plane_principal_locally(principal)
    return normalized


async def update_control_plane_principal_status(request: Request):
    principal_did = str(request.path_params.get("principal_did") or "").strip()
    if not principal_did:
        return JSONResponse({"detail": "principal_did is required"}, status_code=400)
    payload = await request.json()
    forwarded = _control_plane_mutation_payload(payload)
    body = await _control_plane_backend_fetch(
        request,
        method="POST",
        path=f"/api/control-plane/principals/{principal_did}/status",
        payload=forwarded,
    )
    normalized = _normalize_control_plane_mutation_response(
        body,
        idempotency_key=forwarded.get("idempotency_key"),
        policy_trace=_control_plane_policy_trace(
            path=f"/api/control-plane/principals/{principal_did}/status",
            payload=forwarded,
            execution_mode="direct_write",
        ),
    )
    principal = normalized.get("principal") if isinstance(normalized.get("principal"), dict) else None
    _sync_control_plane_principal_locally(principal)
    return normalized


async def activate_control_plane_entity(request: Request):
    payload = await request.json()
    entity_type = str(payload.get("entity_type") or "").strip().lower()
    entity_id = str(payload.get("entity_id") or "").strip()
    target_status = str(payload.get("status") or "active").strip().lower() or "active"
    if entity_type not in {"ledger", "principal", "surface"}:
        raise HTTPException(status_code=422, detail="entity_type must be ledger, principal, or surface")
    if not entity_id:
        raise HTTPException(status_code=422, detail="entity_id is required")
    forwarded = _control_plane_mutation_payload(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "status": target_status,
            "tenant_id": payload.get("tenant_id"),
            "ledger_id": payload.get("ledger_id"),
            "submission_ref": payload.get("submission_ref"),
            "idempotency_key": payload.get("idempotency_key"),
            "governance_mode": payload.get("governance_mode"),
            "break_glass": payload.get("break_glass"),
        }
    )
    override_error = _control_plane_guarded_override_error(path="/api/control-plane/entities/activate", payload=forwarded)
    if override_error is not None:
        return override_error
    if _should_submit_control_plane_mutation(path="/api/control-plane/entities/activate", payload=forwarded):
        return await _submit_control_plane_mutation(request, path="/api/control-plane/entities/activate", payload=forwarded)
    body = await _control_plane_backend_fetch(
        request,
        method="POST",
        path="/api/control-plane/entities/activate",
        payload=_control_plane_forward_payload(forwarded),
    )
    body = _normalize_control_plane_mutation_response(body, idempotency_key=forwarded.get("idempotency_key"), policy_trace=_control_plane_policy_trace(path="/api/control-plane/entities/activate", payload=forwarded, execution_mode="direct_write"), break_glass_audit=_control_plane_break_glass(forwarded))
    principal = body.get("principal") if isinstance(body.get("principal"), dict) else None
    if principal is not None:
        body["provisioning"] = _principal_provisioning_summary(principal)
    return body


async def remove_control_plane_entity(request: Request):
    payload = await request.json()
    entity_type = str(payload.get("entity_type") or "").strip().lower()
    entity_id = str(payload.get("entity_id") or "").strip()
    if entity_type not in {"ledger", "principal", "surface"}:
        raise HTTPException(status_code=422, detail="entity_type must be ledger, principal, or surface")
    if not entity_id:
        raise HTTPException(status_code=422, detail="entity_id is required")
    forwarded = _control_plane_mutation_payload(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "tenant_id": payload.get("tenant_id"),
            "ledger_id": payload.get("ledger_id"),
            "submission_ref": payload.get("submission_ref"),
            "idempotency_key": payload.get("idempotency_key"),
            "governance_mode": payload.get("governance_mode"),
            "break_glass": payload.get("break_glass"),
        }
    )
    override_error = _control_plane_guarded_override_error(path="/api/control-plane/entities/remove", payload=forwarded)
    if override_error is not None:
        return override_error
    if _should_submit_control_plane_mutation(path="/api/control-plane/entities/remove", payload=forwarded):
        return await _submit_control_plane_mutation(request, path="/api/control-plane/entities/remove", payload=forwarded)
    try:
        body = await _control_plane_backend_fetch(
            request,
            method="POST",
            path="/api/control-plane/entities/remove",
            payload=_control_plane_forward_payload(forwarded),
        )
    except HTTPException as exc:
        # Backend may return 404 for entities that were never persisted (e.g.
        # derived model principals or template surfaces). Treat removal as
        # idempotent success so the dashboard UI doesn't show a generic 404.
        if exc.status_code == 404:
            return JSONResponse(
                {
                    "status": "ok",
                    "removed": True,
                    "not_found": True,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                },
                status_code=200,
            )
        return JSONResponse(
            {"error": "upstream_error", "status_code": exc.status_code, "detail": str(exc.detail)},
            status_code=exc.status_code,
        )
    return _normalize_control_plane_mutation_response(body, idempotency_key=forwarded.get("idempotency_key"), policy_trace=_control_plane_policy_trace(path="/api/control-plane/entities/remove", payload=forwarded, execution_mode="direct_write"), break_glass_audit=_control_plane_break_glass(forwarded))

app.route("/api/chat", methods=["POST"])(api_chat)
app.route("/api/set-agent", methods=["POST"])(set_agent)
app.route("/api/sync/all", methods=["POST"])(manual_sync_all_ledgers)
app.route("/api/stats")(get_stats)
app.route("/api/stats/global")(get_global_stats)
app.route("/api/costs")(get_costs)
app.route("/api/costs")(get_costs)
app.route("/api/models")(list_models)
app.route("/api/models/debug")(models_debug)
app.route("/api/control-plane/providers/openrouter/key", methods=["GET"])(get_openrouter_key_status)
app.route("/api/control-plane/providers/openrouter/status", methods=["GET"])(get_openrouter_key_status_only)
app.route("/api/control-plane/providers/openrouter/key", methods=["POST"])(set_openrouter_key_endpoint)


async def list_principals(request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")

    status = (request.query_params.get("status") or "").strip() or None
    tenant_id = (request.query_params.get("tenant_id") or "").strip() or None
    limit_raw = (request.query_params.get("limit") or "200").strip()
    offset_raw = (request.query_params.get("offset") or "0").strip()
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = 200
    try:
        offset = int(offset_raw)
    except ValueError:
        offset = 0

    rows = PRINCIPAL_REGISTRY.list(status=status, tenant_id=tenant_id, limit=limit, offset=offset)
    return {"principals": rows, "count": len(rows)}


async def get_principal(principal_did: str, _: Request):
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    record = PRINCIPAL_REGISTRY.get(did)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")
    return record


def _tenant_ready(value: str | None) -> bool:
    tenant = str(value or "").strip()
    return bool(tenant and tenant.lower() not in {"tenant:unknown", "unknown"})


def _principal_provisioning_summary(record: dict[str, Any]) -> dict[str, Any]:
    metadata_raw = record.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    standing_view_raw = record.get("standing_view")
    standing_view: dict[str, Any] = standing_view_raw if isinstance(standing_view_raw, dict) else {}

    principal_status = str(record.get("status") or "active").strip().lower() or "active"
    tenant_id = (
        str(metadata.get("provisioned_tenant_id") or "").strip()
        or str(record.get("tenant_id") or "").strip()
    )
    ledger_id = (
        str(metadata.get("provisioned_ledger_id") or "").strip()
        or str(metadata.get("ledger_id") or "").strip()
        or str(metadata.get("default_ledger_id") or "").strip()
    )
    wallet_proof_state = str(metadata.get("wallet_proof_state") or "").strip().lower()
    wallet_issuance_state = str(metadata.get("wallet_issuance_state") or "").strip().lower()
    profile_approval_state = str(metadata.get("profile_approval_state") or "").strip().lower()
    provisioning_state_raw = str(
        metadata.get("provisioning_state")
        or metadata.get("provisioning_status")
        or metadata.get("activation_state")
        or ""
    ).strip().lower()
    tenant_ready = _tenant_ready(tenant_id)
    ledger_ready = bool(ledger_id)
    authority_ready = bool(
        str(standing_view.get("credential_ref") or metadata.get("credential_ref") or "").strip()
        and str(metadata.get("wallet_binding_ref") or "").strip()
    )
    wallet_ready = wallet_proof_state == "verified" and profile_approval_state == "approved_wallet_verified"

    activation_state = "awaiting_approval"
    reason_code = "approval_required"

    if principal_status != "active":
        activation_state = "disabled"
        reason_code = "principal_disabled"
    elif provisioning_state_raw in {"failed", "error", "blocked"}:
        activation_state = "blocked"
        reason_code = provisioning_state_raw
    elif provisioning_state_raw in {"active", "provisioned", "ready", "completed"} and tenant_ready and ledger_ready:
        activation_state = "active"
        reason_code = "ledger_ready"
    elif wallet_ready and tenant_ready and ledger_ready:
        activation_state = "active"
        reason_code = "ledger_ready"
    elif wallet_ready:
        activation_state = "pending_provisioning"
        reason_code = "ledger_assignment_pending"
    elif wallet_issuance_state == "issued_in_wallet" or profile_approval_state in {
        "approved_pending_wallet_proof",
        "approved_wallet_verified",
    }:
        activation_state = "pending_wallet_proof"
        reason_code = "wallet_proof_pending"

    if not provisioning_state_raw:
        provisioning_state = activation_state
    elif provisioning_state_raw in {"active", "provisioned", "ready", "completed"}:
        provisioning_state = "active"
    elif provisioning_state_raw in {"pending", "queued", "provisioning"}:
        provisioning_state = "pending_provisioning"
    else:
        provisioning_state = provisioning_state_raw

    return {
        "principal_did": str(record.get("principal_did") or "").strip() or None,
        "principal_status": principal_status,
        "activation_state": activation_state,
        "provisioning_state": provisioning_state,
        "reason_code": reason_code,
        "tenant_id": tenant_id or None,
        "ledger_id": ledger_id or None,
        "ledger_access_ready": activation_state == "active",
        "tenant_ready": tenant_ready,
        "ledger_ready": ledger_ready,
        "wallet_ready": wallet_ready,
        "authority_ready": authority_ready,
        "wallet_proof_state": wallet_proof_state or None,
        "wallet_issuance_state": wallet_issuance_state or None,
        "profile_approval_state": profile_approval_state or None,
        "credential_ref": str(standing_view.get("credential_ref") or metadata.get("credential_ref") or "").strip() or None,
        "wallet_binding_ref": str(metadata.get("wallet_binding_ref") or "").strip() or None,
        "wallet_did": str(metadata.get("wallet_did") or "").strip() or None,
        "provisioned_at": str(metadata.get("provisioned_at") or "").strip() or None,
        "authority_evidence_ref": str(
            metadata.get("authority_evidence_ref")
            or metadata.get("dia_ref")
            or standing_view.get("credential_ref")
            or ""
        ).strip() or None,
        "notification_state": str(metadata.get("notification_state") or "").strip() or None,
        "next_action": (
            "complete_wallet_proof"
            if activation_state == "pending_wallet_proof"
            else "assign_ledger_and_activate"
            if activation_state == "pending_provisioning"
            else "contact_operator"
            if activation_state in {"blocked", "disabled"}
            else "none"
            if activation_state == "active"
            else "await_operator_approval"
        ),
    }


def _sync_control_plane_principal_locally(principal: dict[str, Any] | None) -> None:
    if not isinstance(principal, dict):
        return
    principal_did = str(principal.get("principal_did") or "").strip()
    if not principal_did:
        return
    principal_key_refs_raw = principal.get("principal_key_refs")
    if isinstance(principal_key_refs_raw, list):
        principal_key_refs = principal_key_refs_raw
    else:
        key_references_raw = principal.get("key_references")
        principal_key_refs = key_references_raw if isinstance(key_references_raw, list) else []
    PRINCIPAL_REGISTRY.upsert(
        principal_did=principal_did,
        principal_key_refs=[str(item).strip() for item in principal_key_refs if str(item).strip()],
        tenant_id=str(principal.get("tenant_id") or "").strip() or None,
        display_name=str(principal.get("display_name") or "").strip() or None,
        metadata=dict(principal.get("metadata") or {}) if isinstance(principal.get("metadata"), dict) else {},
        status=str(principal.get("status") or "").strip().lower() or None,
    )


async def get_principal_provisioning(principal_did: str, _: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    record = PRINCIPAL_REGISTRY.get(did)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")
    return {"status": "ok", "provisioning": _principal_provisioning_summary(record)}


async def update_principal_provisioning(principal_did: str, request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    record = PRINCIPAL_REGISTRY.get(did)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    metadata_raw = record.get("metadata")
    metadata_seed: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    metadata: dict[str, Any] = dict(metadata_seed)
    current_summary = _principal_provisioning_summary(record)

    tenant_id = str(payload.get("tenant_id") or "").strip() or str(record.get("tenant_id") or "").strip() or None
    ledger_id = str(payload.get("ledger_id") or "").strip()
    requested_state = str(payload.get("provisioning_state") or "").strip().lower()
    notification_state = str(payload.get("notification_state") or "").strip()
    authority_evidence_ref = str(payload.get("authority_evidence_ref") or payload.get("dia_ref") or "").strip()

    if requested_state in {"active", "provisioned", "ready", "completed"} and not ledger_id and not current_summary.get("ledger_id"):
        raise HTTPException(status_code=422, detail="ledger_id is required for active provisioning")

    if ledger_id:
        metadata["provisioned_ledger_id"] = ledger_id
        metadata["ledger_id"] = ledger_id
    if tenant_id:
        metadata["provisioned_tenant_id"] = tenant_id
    if notification_state:
        metadata["notification_state"] = notification_state
    if authority_evidence_ref:
        metadata["authority_evidence_ref"] = authority_evidence_ref
    if requested_state:
        metadata["provisioning_state"] = requested_state
    elif ledger_id:
        metadata["provisioning_state"] = "active"
    else:
        metadata["provisioning_state"] = "pending_provisioning"

    if metadata.get("provisioning_state") in {"active", "provisioned", "ready", "completed"}:
        metadata["provisioned_at"] = str(metadata.get("provisioned_at") or datetime.utcnow().isoformat() + "Z")

    updated = PRINCIPAL_REGISTRY.upsert(
        principal_did=did,
        principal_key_refs=record.get("principal_key_refs") if isinstance(record.get("principal_key_refs"), list) else [],
        tenant_id=tenant_id,
        display_name=str(record.get("display_name") or "").strip() or None,
        metadata=metadata,
        status=str(record.get("status") or "").strip() or None,
    )
    summary = _principal_provisioning_summary(updated)
    idempotent = (
        current_summary.get("activation_state") == summary.get("activation_state")
        and current_summary.get("tenant_id") == summary.get("tenant_id")
        and current_summary.get("ledger_id") == summary.get("ledger_id")
        and current_summary.get("notification_state") == summary.get("notification_state")
        and current_summary.get("authority_evidence_ref") == summary.get("authority_evidence_ref")
    )
    return {"status": "ok", "idempotent": idempotent, "principal": updated, "provisioning": summary}


async def resolve_principal(request: Request):
    key_ref = str(request.query_params.get("key_ref") or "").strip()
    tenant_id = str(request.query_params.get("tenant_id") or "").strip() or None
    if not key_ref:
        raise HTTPException(status_code=422, detail="key_ref is required")
    resolution = PRINCIPAL_REGISTRY.resolve_key_ref(key_ref, tenant_id=tenant_id)
    outcome = str(resolution.get("outcome") or "not_found")
    if outcome == "not_found":
        return JSONResponse({"detail": resolution}, status_code=404)
    if outcome == "conflict":
        return JSONResponse({"detail": resolution}, status_code=409)
    return {"status": "ok", **resolution}


async def upsert_principal(request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    principal_did = str(payload.get("principal_did") or "").strip()
    principal_key_refs_raw = payload.get("principal_key_refs")
    principal_key_refs = principal_key_refs_raw if isinstance(principal_key_refs_raw, list) else []
    tenant_id = payload.get("tenant_id") if isinstance(payload.get("tenant_id"), str) else None
    display_name = payload.get("display_name") if isinstance(payload.get("display_name"), str) else None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

    try:
        record = PRINCIPAL_REGISTRY.upsert(
            principal_did=principal_did,
            principal_key_refs=[str(item) for item in principal_key_refs],
            tenant_id=tenant_id,
            display_name=display_name,
            metadata=metadata,
            status=payload.get("status") if isinstance(payload.get("status"), str) else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {"status": "ok", "principal": record}


async def bind_principal_identity(principal_did: str, request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")

    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    binding_ref = str(payload.get("binding_ref") or "").strip()
    binding_type = str(payload.get("binding_type") or "").strip().lower()
    binding_subject = str(payload.get("binding_subject") or "").strip()
    if not binding_ref:
        if not binding_type or not binding_subject:
            raise HTTPException(status_code=422, detail="binding_ref or binding_type + binding_subject is required")
        binding_ref = f"{binding_type}:{binding_subject}"
    tenant_id = payload.get("tenant_id") if isinstance(payload.get("tenant_id"), str) else None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    issuer = str(payload.get("issuer") or "").strip() or None
    reason = str(payload.get("reason") or "").strip() or None
    idempotency_key = str(payload.get("idempotency_key") or "").strip() or None
    evidence_refs_raw = payload.get("evidence_refs")
    evidence_refs = [str(item) for item in evidence_refs_raw] if isinstance(evidence_refs_raw, list) else None

    try:
        record, binding_event = PRINCIPAL_REGISTRY.bind_key_ref(
            principal_did=did,
            principal_key_ref=binding_ref,
            tenant_id=tenant_id,
            binding_metadata=metadata,
            issuer=issuer,
            evidence_refs=evidence_refs,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="principal not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "ok", "principal": record, "binding_event": binding_event}


async def get_principal_binding_events(principal_did: str, request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    principal = PRINCIPAL_REGISTRY.get(did)
    if not isinstance(principal, dict):
        raise HTTPException(status_code=404, detail="principal not found")
    limit = int(request.query_params.get("limit") or 50)
    events = PRINCIPAL_REGISTRY.list_binding_events(did, limit=limit)
    return {
        "status": "ok",
        "principal": principal,
        "bindings": principal.get("principal_key_refs") if isinstance(principal.get("principal_key_refs"), list) else [],
        "binding_events": events,
        "count": len(events),
    }


async def set_principal_status(principal_did: str, request: Request, *, status: str):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")

    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")

    reason = ""
    try:
        payload = await request.json()
        if isinstance(payload, dict) and isinstance(payload.get("reason"), str):
            reason = str(payload.get("reason") or "").strip()
    except Exception:
        reason = ""

    try:
        record = PRINCIPAL_REGISTRY.set_status(did, status=status, reason=reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="principal not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {"status": "ok", "principal": record}


async def get_principal_subject_events(principal_did: str, request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    if not isinstance(PRINCIPAL_REGISTRY.get(did), dict):
        raise HTTPException(status_code=404, detail="principal not found")
    limit = int(request.query_params.get("limit") or 50)
    events = PRINCIPAL_REGISTRY.list_subject_events(did, limit=limit)
    return {"status": "ok", "events": events, "count": len(events)}


async def append_principal_subject_event(principal_did: str, request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = str(payload.get("event_type") or "").strip()
    reason = str(payload.get("reason") or "").strip() or None
    issuer = str(payload.get("issuer") or "").strip() or None
    evidence_refs_raw = payload.get("evidence_refs")
    evidence_refs = [str(item) for item in evidence_refs_raw] if isinstance(evidence_refs_raw, list) else None
    standing_carryover = str(payload.get("standing_carryover") or "").strip() or None
    credential_carryover = str(payload.get("credential_carryover") or "").strip() or None

    try:
        record, event = PRINCIPAL_REGISTRY.append_subject_event(
            principal_did=did,
            event_type=event_type,
            reason=reason,
            issuer=issuer,
            evidence_refs=evidence_refs,
            standing_carryover=standing_carryover,
            credential_carryover=credential_carryover,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="principal not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {"status": "ok", "principal": record, "event": event}


async def get_principal_standing_events(principal_did: str, request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    if not isinstance(PRINCIPAL_REGISTRY.get(did), dict):
        raise HTTPException(status_code=404, detail="principal not found")
    limit = int(request.query_params.get("limit") or 50)
    events = PRINCIPAL_REGISTRY.list_standing_events(did, limit=limit)
    return {"status": "ok", "events": events, "count": len(events)}


async def get_principal_standing_view(principal_did: str, _: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    standing_view = PRINCIPAL_REGISTRY.get_standing_view(did)
    if not isinstance(standing_view, dict):
        raise HTTPException(status_code=404, detail="principal not found")
    return {"status": "ok", "standing": standing_view}


async def get_principal_authority_history(principal_did: str, request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    record = PRINCIPAL_REGISTRY.get(did)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")
    limit = int(request.query_params.get("limit") or 50)
    subject_events = PRINCIPAL_REGISTRY.list_subject_events(did, limit=limit)
    standing_events = PRINCIPAL_REGISTRY.list_standing_events(did, limit=limit)
    timeline = []
    for row in subject_events:
        if not isinstance(row, dict):
            continue
        timeline.append({
            "family": "subject",
            "event_id": row.get("event_id"),
            "event_type": row.get("event_type"),
            "created_at": row.get("created_at"),
            "issuer": row.get("issuer"),
            "evidence_refs": row.get("evidence_refs") or [],
            "prior_authority_subject_id": row.get("prior_authority_subject_id"),
            "resulting_authority_subject_id": row.get("resulting_authority_subject_id"),
        })
    for row in standing_events:
        if not isinstance(row, dict):
            continue
        timeline.append({
            "family": "authority",
            "event_id": row.get("event_id"),
            "event_type": row.get("event_type"),
            "created_at": row.get("created_at"),
            "issuer": row.get("issuer"),
            "reason_code": row.get("reason_code"),
            "evidence_refs": row.get("evidence_refs") or [],
            "credential_ref": row.get("credential_ref"),
            "standing_envelope_ref": row.get("standing_envelope_ref"),
        })
    timeline.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("event_id") or "")))
    standing_view_raw = record.get("standing_view")
    standing_view: dict[str, Any] = (
        cast(dict[str, Any], standing_view_raw) if isinstance(standing_view_raw, dict) else {}
    )
    latest_subject_event_raw = subject_events[-1] if subject_events else None
    latest_subject_event: dict[str, Any] = (
        cast(dict[str, Any], latest_subject_event_raw) if isinstance(latest_subject_event_raw, dict) else {}
    )
    authority_subject_id = standing_view.get("authority_subject_id")
    current_validation_status = standing_view.get("current_validation_status")
    subject_transition_event_ref = latest_subject_event.get("event_id") or standing_view.get("subject_transition_event_ref")
    canonical_subject = record.get("canonical_subject")
    diagnostics = {
        "principal_did": did,
        "authority_subject_id": authority_subject_id,
        "subject_event_count": len(subject_events),
        "authority_event_count": len(standing_events),
        "timeline_count": len(timeline),
        "current_validation_status": current_validation_status,
        "materialized_from_principal_registry": True,
    }
    return {
        "status": "ok",
        "principal": record,
        "current_subject": {
            "principal_did": did,
            "canonical_subject": canonical_subject,
            "authority_subject_id": authority_subject_id,
            "subject_transition_event_ref": subject_transition_event_ref,
        },
        "current_standing": standing_view,
        "timeline": timeline,
        "diagnostics": diagnostics,
    }


async def get_principal_authority(principal_did: str, _: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")

    record = PRINCIPAL_REGISTRY.get(did)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")

    metadata_raw = record.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    standing_view_raw = record.get("standing_view")
    standing_view: dict[str, Any] = standing_view_raw if isinstance(standing_view_raw, dict) else {}
    authority_refs = [
        ref for ref in [
            str(standing_view.get("credential_ref") or "").strip() or None,
            str(metadata.get("wallet_binding_ref") or "").strip() or None,
            str(metadata.get("issuer_did") or "").strip() or None,
        ]
        if ref
    ]

    credential_ref = str(standing_view.get("credential_ref") or metadata.get("credential_ref") or "").strip()
    wallet_binding_ref = str(metadata.get("wallet_binding_ref") or "").strip()
    trust_anchor_role = str(metadata.get("trust_anchor_role") or "").strip()
    actor_type = str(metadata.get("actor_type") or "").strip().lower()

    authority_type = "principal"
    if actor_type == "human" and credential_ref and wallet_binding_ref and trust_anchor_role:
        authority_type = "wallet_bound_operator"
    elif actor_type == "human" and credential_ref and wallet_binding_ref:
        authority_type = "wallet_bound_human"
    elif credential_ref:
        authority_type = "credential_bound_principal"
    elif bool(metadata.get("wallet_capable")):
        authority_type = "wallet_capable_principal"

    return {
        "status": "ok",
        "authority": {
            "principal_did": record.get("principal_did"),
            "canonical_subject": record.get("canonical_subject"),
            "canonical_subject_source": record.get("canonical_subject_source"),
            "display_name": record.get("display_name"),
            "tenant_id": record.get("tenant_id"),
            "actor_type": metadata.get("actor_type"),
            "authority_type": authority_type,
            "wallet_capable": bool(metadata.get("wallet_capable")),
            "wallet_provider": str(metadata.get("wallet_provider") or "").strip() or None,
            "wallet_did": str(metadata.get("wallet_did") or "").strip() or None,
            "wallet_binding_ref": wallet_binding_ref or None,
            "credential_ref": credential_ref or None,
            "issuer_did": str(metadata.get("issuer_did") or "").strip() or None,
            "trust_anchor_role": trust_anchor_role or None,
            "trust_class": standing_view.get("trust_class"),
            "posture_class": standing_view.get("posture_class"),
            "operator_profile": standing_view.get("operator_profile"),
            "probation_status": standing_view.get("probation_status"),
            "standing_envelope_ref": standing_view.get("standing_envelope_ref"),
            "authority_refs": authority_refs,
            "verifier_summary": {
                "authority_active": str(record.get("status") or "").strip().lower() == "active",
                "wallet_bound": bool(str(metadata.get("wallet_binding_ref") or "").strip()),
                "credential_bound": bool(str(standing_view.get("credential_ref") or metadata.get("credential_ref") or "").strip()),
                "issuer_linked": bool(str(metadata.get("issuer_did") or "").strip()),
            },
        },
    }


async def append_principal_standing_event(principal_did: str, request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    did = (principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    try:
        record, event = PRINCIPAL_REGISTRY.append_standing_event(
            principal_did=did,
            event_type=str(payload.get("event_type") or "").strip(),
            issuer=str(payload.get("issuer") or "").strip(),
            reason_code=str(payload.get("reason_code") or "").strip(),
            delta=payload.get("delta") if isinstance(payload.get("delta"), dict) else None,
            evidence_refs=([str(item) for item in _list_or_empty(payload.get("evidence_refs"))] or None),
            idempotency_key=str(payload.get("idempotency_key") or "").strip(),
            credential_ref=str(payload.get("credential_ref") or "").strip() or None,
            standing_envelope_ref=str(payload.get("standing_envelope_ref") or "").strip() or None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="principal not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {"status": "ok", "principal": record, "event": event}


async def disable_principal(principal_did: str, request: Request):
    return await set_principal_status(principal_did, request, status="disabled")


async def enable_principal(principal_did: str, request: Request):
    return await set_principal_status(principal_did, request, status="active")


def _mask_contact(value: str, *, channel: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    if channel == "email":
        if "@" not in text:
            return "***"
        local, domain = text.split("@", 1)
        local_masked = (local[:1] + "***") if local else "***"
        return f"{local_masked}@{domain}"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 4:
        return f"***{digits[-4:]}"
    return "***"


async def _send_principal_link_email(
    *,
    to_email: str,
    code: str,
    github_login: str,
    expires_at: str | None,
) -> None:
    if not RESEND_API_KEY:
        raise HTTPException(status_code=503, detail="email_delivery_not_configured")
    if not PRINCIPAL_LINK_EMAIL_FROM:
        raise HTTPException(status_code=503, detail="email_sender_not_configured")

    login_text = str(github_login or "").strip() or "your GitHub account"
    expiry_text = str(expires_at or "").strip()
    expiry_line = f"This code expires at {expiry_text}." if expiry_text else "This code expires in 10 minutes."
    text_body = (
        "Your DSS verification code is "
        f"{code}.\n\nUse it to link {login_text} to your existing DSS identity.\n{expiry_line}"
    )
    html_body = (
        "<p>Your DSS verification code is "
        f"<strong>{html.escape(code)}</strong>.</p>"
        f"<p>Use it to link <strong>{html.escape(login_text)}</strong> to your existing DSS identity.</p>"
        f"<p>{html.escape(expiry_line)}</p>"
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": PRINCIPAL_LINK_EMAIL_FROM,
                "to": [to_email],
                "subject": "Your DSS verification code",
                "text": text_body,
                "html": html_body,
            },
        )
    if response.status_code not in {200, 201, 202}:
        response_text = response.text.strip()
        logger.error(
            "resend email delivery failed status=%s from=%r to=%r body=%r",
            response.status_code,
            PRINCIPAL_LINK_EMAIL_FROM,
            _mask_contact(to_email, channel="email"),
            response_text[:1000],
        )
        raise HTTPException(status_code=502, detail="email_delivery_failed")




def _verified_id_callback_base_url() -> str:
    return (
        os.getenv("VERIFIED_ID_CALLBACK_BASE_URL")
        or os.getenv("MIDDLEWARE_PUBLIC_BASE_URL")
        or os.getenv("MIDDLEWARE_PUBLIC_URL", "")
    ).strip().rstrip("/")


def _verified_id_callback_url() -> str:
    return f"{_verified_id_callback_base_url()}/api/webhooks/entra/verified-id"


def _verified_id_callback_api_key() -> str:
    return os.getenv("VERIFIED_ID_CALLBACK_API_KEY", "").strip()


def _verified_id_default_accepted_issuers() -> list[str]:
    raw = os.getenv("VERIFIED_ID_ACCEPTED_ISSUERS", "").strip()
    return [item.strip() for item in raw.split(",") if item and item.strip()]


def _verified_id_config() -> dict[str, Any]:
    return {
        "tenant_id": os.getenv("VERIFIED_ID_TENANT_ID", "").strip(),
        "client_id": os.getenv("VERIFIED_ID_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("VERIFIED_ID_CLIENT_SECRET", "").strip(),
        "authority": os.getenv("VERIFIED_ID_AUTHORITY", "").strip(),
        "credential_type": os.getenv("VERIFIED_ID_CREDENTIAL_TYPE", "").strip(),
        "manifest_url": os.getenv("VERIFIED_ID_MANIFEST_URL", "").strip(),
        "issuance_purpose": os.getenv("VERIFIED_ID_ISSUANCE_PURPOSE", "Issue your DSS identity credential into Microsoft Authenticator before wallet authority is activated.").strip() or "Issue your DSS identity credential into Microsoft Authenticator before wallet authority is activated.",
        "issuance_pin_length": int((os.getenv("VERIFIED_ID_ISSUANCE_PIN_LENGTH", "0") or "0").strip() or "0"),
        "client_name": os.getenv("VERIFIED_ID_CLIENT_NAME", "Dual Substrate Identity").strip() or "Dual Substrate Identity",
        "purpose": os.getenv("VERIFIED_ID_PURPOSE", "Verify your identity before wallet authority is activated.").strip() or "Verify your identity before wallet authority is activated.",
        "accepted_issuers": _verified_id_default_accepted_issuers(),
        "callback_url": _verified_id_callback_url(),
        "callback_api_key": _verified_id_callback_api_key(),
    }


def _verified_id_missing_config(config: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in ("tenant_id", "client_id", "client_secret", "authority", "credential_type"):
        if not str(config.get(key) or "").strip():
            missing.append(key)
    return missing


def _verified_id_missing_issuance_config(config: dict[str, Any]) -> list[str]:
    missing = _verified_id_missing_config(config)
    if not str(config.get("manifest_url") or "").strip():
        missing.append("manifest_url")
    return missing


def _verified_id_public_record(record: dict[str, Any]) -> dict[str, Any]:
    response_payload_raw = record.get("response_payload")
    response_payload: dict[str, Any] = response_payload_raw if isinstance(response_payload_raw, dict) else {}
    request_payload_raw = record.get("request_payload")
    request_payload: dict[str, Any] = request_payload_raw if isinstance(request_payload_raw, dict) else {}
    requested_credentials: list[Any] = _list_or_empty(request_payload.get("requestedCredentials"))
    requested_credential = requested_credentials[0] if requested_credentials and isinstance(requested_credentials[0], dict) else {}
    registration_raw = request_payload.get("registration")
    registration: dict[str, Any] = registration_raw if isinstance(registration_raw, dict) else {}
    finalization = record.get("finalization") if isinstance(record.get("finalization"), dict) else None
    return {
        "state": record.get("state"),
        "request_id": record.get("request_id"),
        "principal_did": record.get("principal_did"),
        "mode": record.get("mode"),
        "status": record.get("status"),
        "request_url": record.get("request_url") or response_payload.get("url"),
        "request_qr_code": record.get("request_qr_code") or response_payload.get("qrCode"),
        "expiry": record.get("expiry") or response_payload.get("expiry"),
        "subject": record.get("subject"),
        "verified_credentials_data": record.get("verified_credentials_data"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "request_debug": {
            "authority": request_payload.get("authority"),
            "client_name": registration.get("clientName"),
            "purpose": registration.get("purpose"),
            "credential_type": request_payload.get("type") or requested_credential.get("type"),
            "manifest": request_payload.get("manifest"),
            "accepted_issuers": requested_credential.get("acceptedIssuers"),
        },
        "finalization": finalization,
    }


def _walt_id_config() -> dict[str, str]:
    return {
        "issuer_url": WALT_ID_ISSUER_URL,
        "issuer_did": WALT_ID_ISSUER_DID,
        "issuer_key_jwk": WALT_ID_ISSUER_KEY_JWK,
        "callback_api_key": WALT_ID_CALLBACK_API_KEY,
        "credential_configuration_id": str(os.getenv("WALT_ID_CREDENTIAL_CONFIGURATION_ID", "DssIdentity_jwt_vc_json")).strip(),
    }


def _walt_id_issuer_key(config: dict[str, str]) -> dict[str, Any] | None:
    raw = str(config.get("issuer_key_jwk") or "").strip()
    if not raw:
        return None
    try:
        jwk = json.loads(raw)
        if isinstance(jwk, dict):
            return jwk
    except Exception:
        pass
    return None


async def _walt_id_create_issuance_request(
    config: dict[str, str],
    *,
    state: str,
    claims: dict[str, Any],
    subject_did: str,
) -> dict[str, Any]:
    issuer_key = _walt_id_issuer_key(config)
    if not issuer_key:
        raise RuntimeError("walt_id_issuer_key_not_configured")
    issuer_did = str(config.get("issuer_did") or "").strip()
    credential_configuration_id = str(config.get("credential_configuration_id") or "").strip()
    if not credential_configuration_id:
        raise RuntimeError("walt_id_credential_configuration_id_not_configured")

    # walt.id v0.20.1 constructs credential JWT kid as issuerDid + "#" + jwk.kid.
    # If jwk.kid is already a full DID URL (e.g. "did:web:...#v2"), the result is
    # malformed ("did:web:...#did:web:...#v2"). Strip to fragment-only so the
    # credential header kid matches the verificationMethod.id in the DID document.
    walt_id_issuer_key = dict(issuer_key)
    kid = str(walt_id_issuer_key.get("kid") or "").strip()
    if kid and issuer_did and kid.startswith(issuer_did + "#"):
        walt_id_issuer_key["kid"] = kid.split("#", 1)[1]

    credential_data: dict[str, Any] = {
        "@context": ["https://www.w3.org/2018/credentials/v1"],
        "type": ["VerifiableCredential", "DssIdentity"],
        "issuer": {"id": issuer_did},
        "credentialSubject": {
            "id": subject_did,
            **claims,
        },
    }
    request_payload: dict[str, Any] = {
        "issuerKey": {"type": "jwk", "jwk": walt_id_issuer_key},
        "issuerDid": issuer_did,
        "credentialConfigurationId": credential_configuration_id,
        "credentialData": credential_data,
        "mapping": {
            "id": "<uuid>",
            "issuer": {"id": "<issuerDid>"},
            "credentialSubject": {"id": "<subjectDid>"},
            "issuanceDate": "<timestamp>",
            "expirationDate": "<timestamp-in:365d>",
        },
        "authenticationMethod": "PRE_AUTHORIZED",
        "standardVersion": "DRAFT13",
    }

    callback_url = os.getenv("TRUST_ANCHOR_PUBLIC_BASE_URL", "").strip().rstrip("/")
    callback_url = f"{callback_url}/api/webhooks/walt-id/issuance?state={state}"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if str(config.get("callback_api_key") or "").strip():
        headers["statusCallbackApiKey"] = str(config.get("callback_api_key") or "").strip()

    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        response = await client.post(
            f"{WALT_ID_ISSUER_URL}/openid4vc/jwt/issue",
            headers=headers,
            params={"statusCallbackUri": callback_url},
            json=request_payload,
        )
        response.raise_for_status()
        credential_offer = response.text
    return {
        "credentialOfferUri": credential_offer.strip(),
        "url": credential_offer.strip(),
    }


async def _finalize_walt_id_issuance(*, state: str, callback_payload: dict[str, Any]) -> dict[str, Any]:
    record = VERIFIED_ID_REQUESTS.get(state)
    if not isinstance(record, dict):
        raise KeyError("verified_id_request_not_found")
    principal_did = str(record.get("principal_did") or "").strip()
    principal = PRINCIPAL_REGISTRY.get(principal_did)
    if not isinstance(principal, dict):
        raise KeyError("principal not found")

    metadata_raw = principal.get("metadata")
    metadata_seed: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    metadata: dict[str, Any] = dict(metadata_seed)
    request_id = str(record.get("request_id") or "").strip()

    if str(metadata.get("wallet_issuance_state") or "").strip().lower() == "issued_in_wallet":
        return {
            "principal_did": principal_did,
            "wallet_issuance_state": "issued_in_wallet",
            "request_id": request_id,
            "idempotent": True,
        }

    metadata["wallet_provider"] = "openid4vci"
    metadata["wallet_issuance_state"] = "issued_in_wallet"
    metadata["wallet_issued_at"] = datetime.utcnow().isoformat() + "Z"
    metadata["wallet_issued_request_id"] = request_id
    if not str(metadata.get("provisioning_state") or "").strip():
        metadata["provisioning_state"] = "pending_wallet_proof"
    if not str(metadata.get("wallet_proof_state") or "").strip():
        metadata["wallet_proof_state"] = "pending_presentation_verification"
    elif str(metadata.get("wallet_proof_state") or "").strip() == "pending_verified_id":
        metadata["wallet_proof_state"] = "pending_presentation_verification"

    # Store walt.id session details if available
    walt_id_session_id = str(callback_payload.get("id") or "").strip()
    walt_id_event_type = str(callback_payload.get("type") or "").strip()
    if walt_id_session_id:
        metadata["walt_id_session_id"] = walt_id_session_id
    if walt_id_event_type:
        metadata["walt_id_event_type"] = walt_id_event_type
    walt_id_data = callback_payload.get("data")
    if isinstance(walt_id_data, dict):
        jwt_issued = walt_id_data.get("jwt") or walt_id_data.get("sdjwt")
        if jwt_issued:
            metadata["walt_id_issued_jwt"] = jwt_issued

    PRINCIPAL_REGISTRY.upsert(
        principal_did=principal_did,
        principal_key_refs=principal.get("principal_key_refs") if isinstance(principal.get("principal_key_refs"), list) else [],
        tenant_id=str(principal.get("tenant_id") or "").strip() or None,
        display_name=str(principal.get("display_name") or "").strip() or None,
        metadata=metadata,
        status=str(principal.get("status") or "").strip() or None,
    )

    finalization = {
        "principal_did": principal_did,
        "wallet_issuance_state": "issued_in_wallet",
        "request_id": request_id,
        "status": "issued",
    }
    VERIFIED_ID_REQUESTS.mark_finalization(state=state, finalization=finalization)
    return finalization


def _generate_qr_data_uri(url: str) -> str | None:
    if not url:
        return None
    try:
        import io
        import base64
        import qrcode
        img = qrcode.make(url, box_size=6, border=2)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


async def _verified_id_access_token(config: dict[str, Any]) -> str:
    token_url = f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "scope": VERIFIED_ID_REQUEST_SERVICE_SCOPE,
            },
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("verified_id_access_token_missing")
    return token


async def _verified_id_create_presentation_request(config: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
    token = await _verified_id_access_token(config)
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        response = await client.post(
            "https://verifiedid.did.msidentity.com/v1.0/verifiableCredentials/createPresentationRequest",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=request_payload,
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("verified_id_invalid_response")
    return payload


async def _verified_id_create_issuance_request(config: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
    token = await _verified_id_access_token(config)
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        primary_payload = dict(request_payload)
        if "manifest" in primary_payload and "manifestUrl" not in primary_payload:
            primary_payload["manifestUrl"] = primary_payload.pop("manifest")
        response = await client.post(
            "https://verifiedid.did.msidentity.com/v1.0/verifiableCredentials/createIssuanceRequest",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=primary_payload,
        )
        if response.status_code >= 400 and "manifestUrl" in primary_payload and "manifest" not in primary_payload:
            retry_payload = dict(request_payload)
            response = await client.post(
                "https://verifiedid.did.msidentity.com/v1.0/verifiableCredentials/createIssuanceRequest",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=retry_payload,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("verified_id_invalid_response")
            return payload
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("verified_id_invalid_response")
    return payload


def _verified_id_claims_for_principal(principal: dict[str, Any], principal_did: str) -> dict[str, Any]:
    metadata_raw = principal.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    display_name = str(principal.get("display_name") or "").strip()
    email = str(metadata.get("email") or metadata.get("email_normalized") or "").strip()
    company = str(
        metadata.get("company")
        or metadata.get("organisation")
        or metadata.get("organization")
        or os.getenv("VERIFIED_ID_DEFAULT_COMPANY")
        or "Dual Substrate"
    ).strip()
    claims: dict[str, Any] = {"principal_did": principal_did}
    if display_name:
        claims["display_name"] = display_name
        parts = [part for part in display_name.split() if part]
        if parts:
            claims["given_name"] = parts[0]
            claims["family_name"] = " ".join(parts[1:]) if len(parts) > 1 else parts[0]
    if email:
        claims["email"] = email
    if company:
        claims["company"] = company
    return claims


async def _finalize_verified_id_issuance(*, state: str, callback_payload: dict[str, Any]) -> dict[str, Any]:
    record = VERIFIED_ID_REQUESTS.get(state)
    if not isinstance(record, dict):
        raise KeyError("verified_id_request_not_found")
    principal_did = str(record.get("principal_did") or "").strip()
    principal = PRINCIPAL_REGISTRY.get(principal_did)
    if not isinstance(principal, dict):
        raise KeyError("principal not found")

    metadata_raw = principal.get("metadata")
    metadata_seed: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    metadata: dict[str, Any] = dict(metadata_seed)
    request_id = str(record.get("request_id") or "").strip()

    callback_subject = str(callback_payload.get("subject") or "").strip() or None
    if str(metadata.get("wallet_issuance_state") or "").strip().lower() == "issued_in_wallet":
        if callback_subject and str(metadata.get("wallet_issued_subject") or "").strip() != callback_subject:
            metadata["wallet_issued_subject"] = callback_subject
            PRINCIPAL_REGISTRY.upsert(
                principal_did=principal_did,
                principal_key_refs=principal.get("principal_key_refs") if isinstance(principal.get("principal_key_refs"), list) else [],
                tenant_id=str(principal.get("tenant_id") or "").strip() or None,
                display_name=str(principal.get("display_name") or "").strip() or None,
                metadata=metadata,
                status=str(principal.get("status") or "").strip() or None,
            )
        return {
            "principal_did": principal_did,
            "wallet_issuance_state": "issued_in_wallet",
            "request_id": request_id,
            "idempotent": True,
        }

    metadata["wallet_provider"] = str(metadata.get("wallet_provider") or "microsoft_authenticator").strip() or "microsoft_authenticator"
    metadata["wallet_issuance_state"] = "issued_in_wallet"
    metadata["wallet_issued_at"] = datetime.utcnow().isoformat() + "Z"
    metadata["wallet_issued_request_id"] = request_id
    metadata["wallet_issued_subject"] = callback_subject
    if not str(metadata.get("provisioning_state") or "").strip():
        metadata["provisioning_state"] = "pending_wallet_proof"
    if not str(metadata.get("wallet_proof_state") or "").strip():
        metadata["wallet_proof_state"] = "pending_presentation_verification"
    elif str(metadata.get("wallet_proof_state") or "").strip() == "pending_verified_id":
        metadata["wallet_proof_state"] = "pending_presentation_verification"

    PRINCIPAL_REGISTRY.upsert(
        principal_did=principal_did,
        principal_key_refs=principal.get("principal_key_refs") if isinstance(principal.get("principal_key_refs"), list) else [],
        tenant_id=str(principal.get("tenant_id") or "").strip() or None,
        display_name=str(principal.get("display_name") or "").strip() or None,
        metadata=metadata,
        status=str(principal.get("status") or "").strip() or None,
    )

    finalization = {
        "principal_did": principal_did,
        "wallet_issuance_state": "issued_in_wallet",
        "request_id": request_id,
        "status": "issued",
    }
    VERIFIED_ID_REQUESTS.mark_finalization(state=state, finalization=finalization)
    return finalization


async def _finalize_verified_id_presentation(*, state: str, callback_payload: dict[str, Any]) -> dict[str, Any]:
    record = VERIFIED_ID_REQUESTS.get(state)
    if not isinstance(record, dict):
        raise KeyError("verified_id_request_not_found")
    principal_did = str(record.get("principal_did") or "").strip()
    principal = PRINCIPAL_REGISTRY.get(principal_did)
    if not isinstance(principal, dict):
        subject = str(callback_payload.get("subject") or "").strip()
        if subject:
            for row in PRINCIPAL_REGISTRY.list(limit=1000):
                if not isinstance(row, dict):
                    continue
                metadata_raw = row.get("metadata")
                metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
                if (
                    str(metadata.get("wallet_did") or "").strip() == subject
                    or str(metadata.get("pending_wallet_did") or "").strip() == subject
                    or str(metadata.get("wallet_issued_subject") or "").strip() == subject
                    or str(metadata.get("wallet_verified_subject") or "").strip() == subject
                ):
                    principal = row
                    principal_did = str(row.get("principal_did") or "").strip()
                    break
    if not isinstance(principal, dict):
        verified_credentials = callback_payload.get("verifiedCredentialsData")
        presented = verified_credentials[0] if isinstance(verified_credentials, list) and verified_credentials and isinstance(verified_credentials[0], dict) else {}
        claims_raw = presented.get("claims")
        claims: dict[str, Any] = claims_raw if isinstance(claims_raw, dict) else {}
        first_name = str(claims.get("firstName") or claims.get("given_name") or "").strip()
        last_name = str(claims.get("lastName") or claims.get("family_name") or "").strip()
        company_name = str(claims.get("companyName") or claims.get("company") or "").strip()
        presented_name = " ".join(part for part in [first_name, last_name] if part).strip()
        for row in PRINCIPAL_REGISTRY.list(limit=1000):
            if not isinstance(row, dict):
                continue
            metadata_raw = row.get("metadata")
            metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
            row_name = str(row.get("display_name") or "").strip()
            row_company = str(
                metadata.get("company")
                or metadata.get("organisation")
                or metadata.get("organization")
                or ""
            ).strip()
            if (
                str(metadata.get("wallet_issuance_state") or "").strip() == "issued_in_wallet"
                and str(metadata.get("profile_approval_state") or "").strip() in {"approved_pending_wallet_proof", "approved_wallet_verified"}
                and presented_name
                and row_name == presented_name
                and (not company_name or not row_company or row_company == company_name)
            ):
                principal = row
                principal_did = str(row.get("principal_did") or "").strip()
                break
    # Fallback: match any active principal by display name (supports passkey-registered
    # principals that never went through wallet-issuance but have a verified ID credential).
    if not isinstance(principal, dict):
        for row in PRINCIPAL_REGISTRY.list(limit=1000):
            if not isinstance(row, dict):
                continue
            metadata_raw = row.get("metadata")
            metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
            row_name = str(row.get("display_name") or "").strip()
            row_company = str(
                metadata.get("company")
                or metadata.get("organisation")
                or metadata.get("organization")
                or ""
            ).strip()
            is_active = (
                str(row.get("status") or "").strip().lower() == "active"
                or str(metadata.get("provisioning_state") or "").strip().lower() == "active"
            )
            if (
                is_active
                and presented_name
                and row_name == presented_name
                and (not company_name or not row_company or row_company == company_name)
            ):
                principal = row
                principal_did = str(row.get("principal_did") or "").strip()
                break
    if not isinstance(principal, dict):
        raise KeyError("principal not found")

    metadata_raw = principal.get("metadata")
    metadata_seed: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    metadata: dict[str, Any] = dict(metadata_seed)
    standing_view_raw = principal.get("standing_view")
    standing_view: dict[str, Any] = standing_view_raw if isinstance(standing_view_raw, dict) else {}
    request_id = str(record.get("request_id") or "").strip()
    credential_ref = str(metadata.get("pending_credential_ref") or metadata.get("credential_ref") or "").strip()
    wallet_binding_ref = str(metadata.get("pending_wallet_binding_ref") or "").strip()
    wallet_did = str(metadata.get("pending_wallet_did") or metadata.get("wallet_did") or callback_payload.get("subject") or "").strip()

    if str(metadata.get("wallet_proof_state") or "").strip().lower() == "verified" and (
        not credential_ref or str(standing_view.get("credential_ref") or "").strip() == credential_ref
    ):
        finalization = {
            "principal_did": principal_did,
            "wallet_proof_state": "verified",
            "request_id": request_id,
            "idempotent": True,
        }
        VERIFIED_ID_REQUESTS.mark_finalization(state=state, finalization=finalization)
        return finalization

    metadata["wallet_provider"] = str(metadata.get("wallet_provider") or "microsoft_authenticator").strip() or "microsoft_authenticator"
    metadata["wallet_proof_state"] = "verified"
    metadata["wallet_verified_at"] = datetime.utcnow().isoformat() + "Z"
    metadata["wallet_verified_request_id"] = request_id
    metadata["wallet_verified_subject"] = str(callback_payload.get("subject") or "").strip() or None
    metadata["vc_status"] = "verified"
    metadata["profile_approval_state"] = "approved_wallet_verified"
    if not str(metadata.get("provisioned_ledger_id") or metadata.get("ledger_id") or "").strip():
        metadata["provisioning_state"] = "pending_provisioning"
    else:
        metadata["provisioning_state"] = str(metadata.get("provisioning_state") or "active").strip() or "active"
    if str(metadata.get("trust_anchor_role") or "").strip() == "approved_pending_wallet_proof":
        metadata["trust_anchor_role"] = "member"
    if wallet_did:
        metadata["wallet_did"] = wallet_did
    if wallet_binding_ref:
        metadata["wallet_binding_ref"] = wallet_binding_ref
    metadata["pending_wallet_did"] = None
    metadata["pending_wallet_binding_ref"] = None
    metadata["pending_credential_ref"] = None

    updated = PRINCIPAL_REGISTRY.upsert(
        principal_did=principal_did,
        principal_key_refs=principal.get("principal_key_refs") if isinstance(principal.get("principal_key_refs"), list) else [],
        tenant_id=str(principal.get("tenant_id") or "").strip() or None,
        display_name=str(principal.get("display_name") or "").strip() or None,
        metadata=metadata,
        status=str(principal.get("status") or "").strip() or None,
    )

    event = None
    if credential_ref and str(standing_view.get("credential_ref") or "").strip() != credential_ref:
        try:
            _, event = PRINCIPAL_REGISTRY.append_standing_event(
                principal_did=principal_did,
                event_type="trust_adjustment",
                issuer="entra:verified-id",
                reason_code="verified_id_presentation_verified",
                delta={
                    "trust_class": standing_view.get("trust_class") or "T3",
                    "posture_class": standing_view.get("posture_class") or "P3",
                    "operator_profile": standing_view.get("operator_profile") or "member",
                    "probation_status": "cleared",
                },
                idempotency_key=f"{request_id}:presentation_verified",
                credential_ref=credential_ref,
                standing_envelope_ref=str(standing_view.get("standing_envelope_ref") or "env:member:user").strip() or "env:member:user",
            )
        except RuntimeError:
            event = None

    finalization = {
        "principal_did": principal_did,
        "wallet_proof_state": "verified",
        "wallet_did": wallet_did or None,
        "wallet_binding_ref": wallet_binding_ref or None,
        "credential_ref": credential_ref or None,
        "request_id": request_id,
        "standing_event_id": event.get("event_id") if isinstance(event, dict) else None,
        "status": "verified",
    }
    VERIFIED_ID_REQUESTS.mark_finalization(state=state, finalization=finalization)
    return finalization


async def create_verified_id_issuance_request(request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    # Support cross-domain token passing via JSON payload
    token_from_body = str(payload.get("_entra_auth") or "").strip()
    if token_from_body:
        payload = dict(payload)
        payload.pop("_entra_auth", None)
        request.state._entra_auth_token = token_from_body

    principal_did = str(payload.get("principal_did") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=422, detail="principal_did is required")
    principal = PRINCIPAL_REGISTRY.get(principal_did)
    if not isinstance(principal, dict):
        raise HTTPException(status_code=404, detail="principal not found")

    metadata_raw = principal.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    if str(metadata.get("profile_approval_state") or "").strip() not in {"approved_pending_wallet_proof", "approved_wallet_verified", "approved_pending_onboarding"}:
        return JSONResponse({"error": "profile_not_approved_for_wallet_proof"}, status_code=409)

    wallet_provider = str(metadata.get("wallet_provider") or "microsoft_authenticator").strip()
    if wallet_provider == "openid4vci":
        return await _create_walt_id_issuance_request(request, principal, principal_did, payload)
    return await _create_entra_issuance_request(principal, principal_did, payload)


async def _create_walt_id_issuance_request(
    request: Request,
    principal: dict[str, Any],
    principal_did: str,
    payload: dict[str, Any],
) -> JSONResponse | dict[str, Any]:
    walt_config = _walt_id_config()
    if not _walt_id_issuer_key(walt_config):
        return JSONResponse(
            {"error": "walt_id_not_configured", "missing": ["issuer_key_jwk"]},
            status_code=503,
        )

    # Require Entra OIDC authentication before issuing via walt.id PRE_AUTHORIZED.
    entra_auth = _entra_auth_from_request(request)
    if not entra_auth:
        # Return the middleware's own login endpoint so the caller can append
        # a `next` query param that flows through the state correctly.
        if ENTRA_OIDC_REDIRECT_URI:
            parsed = urlparse(ENTRA_OIDC_REDIRECT_URI)
            base = f"{parsed.scheme}://{parsed.netloc}"
        else:
            base = _public_base_url(request)
        login_url = f"{base}/api/auth/entra/login?principal_did={quote(principal_did, safe='')}"
        return JSONResponse(
            {
                "error": "auth_required",
                "auth_method": "entra_oidc",
                "login_url": login_url,
                "message": "Entra ID authentication is required before credential issuance.",
            },
            status_code=401,
        )

    state = f"wid_{secrets.token_urlsafe(12)}"
    claims_payload = payload.get("claims") if isinstance(payload.get("claims"), dict) else _verified_id_claims_for_principal(principal, principal_did)
    subject_did = str(payload.get("subject_did") or principal_did).strip()

    try:
        response_payload = await _walt_id_create_issuance_request(
            walt_config,
            state=state,
            claims=claims_payload,
            subject_did=subject_did,
        )
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            {
                "error": "walt_id_request_failed",
                "status_code": exc.response.status_code,
                "text": exc.response.text[:1000],
            },
            status_code=502,
        )
    except Exception as exc:
        return JSONResponse({"error": "walt_id_unavailable", "detail": str(exc)}, status_code=503)

    request_id = str(response_payload.get("credentialOfferUri") or "").strip()[:128] or state

    # Generate QR code for the credential offer URL
    qr_data_uri = _generate_qr_data_uri(response_payload.get("url", ""))
    if qr_data_uri:
        response_payload["qrCode"] = qr_data_uri

    record = VERIFIED_ID_REQUESTS.create(
        state=state,
        request_id=request_id,
        principal_did=principal_did,
        mode="walt_id_issuance",
        request_payload={"claims": claims_payload, "subject_did": subject_did, "wallet_provider": "openid4vci"},
        response_payload=response_payload,
    )
    return {"status": "ok", "request": _verified_id_public_record(record)}


async def _create_entra_issuance_request(
    principal: dict[str, Any],
    principal_did: str,
    payload: dict[str, Any],
) -> JSONResponse | dict[str, Any]:
    config = _verified_id_config()
    missing = _verified_id_missing_issuance_config(config)
    if missing:
        return JSONResponse(
            {"error": "verified_id_not_configured", "missing": missing},
            status_code=503,
        )

    requested_credential_type = str(payload.get("credential_type") or config.get("credential_type") or "").strip()
    if not requested_credential_type:
        raise HTTPException(status_code=422, detail="credential_type is required")
    manifest_url = str(payload.get("manifest_url") or config.get("manifest_url") or "").strip()
    if not manifest_url:
        return JSONResponse({"error": "verified_id_not_configured", "missing": ["manifest_url"]}, status_code=503)

    state = f"vid_{secrets.token_urlsafe(12)}"
    callback_headers: dict[str, str] = {}
    if str(config.get("callback_api_key") or "").strip():
        callback_headers["api-key"] = str(config.get("callback_api_key") or "").strip()
    claims_payload = payload.get("claims") if isinstance(payload.get("claims"), dict) else _verified_id_claims_for_principal(principal, principal_did)
    request_payload = {
        "authority": str(config.get("authority") or "").strip(),
        "includeQRCode": bool(payload.get("include_qr_code", True)),
        "registration": {
            "clientName": str(payload.get("client_name") or config.get("client_name") or "Dual Substrate Identity").strip() or "Dual Substrate Identity",
        },
        "callback": {
            "url": str(config.get("callback_url") or "").strip(),
            "state": state,
        },
        "type": requested_credential_type,
        "manifest": manifest_url,
        "claims": claims_payload,
    }
    issuance_purpose = str(payload.get("purpose") or config.get("issuance_purpose") or "").strip()
    if issuance_purpose:
        request_payload["registration"]["purpose"] = issuance_purpose
    pin_length = int(payload.get("pin_length") or config.get("issuance_pin_length") or 0)
    if pin_length > 0:
        pin_value = ''.join(secrets.choice('0123456789') for _ in range(pin_length))
        request_payload["pin"] = {"value": pin_value, "length": pin_length}
    if callback_headers:
        request_payload["callback"]["headers"] = callback_headers

    try:
        response_payload = await _verified_id_create_issuance_request(config, request_payload)
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            {
                "error": "verified_id_request_failed",
                "status_code": exc.response.status_code,
                "text": exc.response.text[:1000],
            },
            status_code=502,
        )
    except Exception as exc:
        return JSONResponse({"error": "verified_id_unavailable", "detail": str(exc)}, status_code=503)

    request_id = str(response_payload.get("requestId") or "").strip()
    if not request_id:
        return JSONResponse({"error": "verified_id_invalid_response"}, status_code=502)
    record = VERIFIED_ID_REQUESTS.create(
        state=state,
        request_id=request_id,
        principal_did=principal_did,
        mode="issuance",
        request_payload=request_payload,
        response_payload=response_payload,
    )
    return {"status": "ok", "request": _verified_id_public_record(record)}


async def create_verified_id_presentation_request(request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    config = _verified_id_config()
    missing = _verified_id_missing_config(config)
    if missing:
        return JSONResponse(
            {"error": "verified_id_not_configured", "missing": missing},
            status_code=503,
        )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    principal_did = str(payload.get("principal_did") or "").strip()
    mode = str(payload.get("mode") or "presentation").strip().lower() or "presentation"
    if mode not in {"presentation", "wallet_login"}:
        raise HTTPException(status_code=422, detail="mode must be presentation or wallet_login")
    principal = None
    metadata: dict[str, Any] = {}
    if principal_did:
        principal = PRINCIPAL_REGISTRY.get(principal_did)
        if not isinstance(principal, dict):
            raise HTTPException(status_code=404, detail="principal not found")
        metadata_raw = principal.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        if str(metadata.get("profile_approval_state") or "").strip() not in {"approved_pending_wallet_proof", "approved_wallet_verified", "approved_pending_onboarding"}:
            return JSONResponse(
                {"error": "profile_not_approved_for_wallet_proof"},
                status_code=409,
            )
    elif mode != "wallet_login":
        raise HTTPException(status_code=422, detail="principal_did is required")

    requested_credential_type = str(payload.get("credential_type") or config.get("credential_type") or "").strip()
    if not requested_credential_type:
        raise HTTPException(status_code=422, detail="credential_type is required")
    accepted_issuers_raw = payload.get("accepted_issuers")
    accepted_issuers = [str(item).strip() for item in accepted_issuers_raw] if isinstance(accepted_issuers_raw, list) else list(config.get("accepted_issuers") or [])
    purpose = str(payload.get("purpose") or config.get("purpose") or "").strip() or config.get("purpose") or "Verify your identity before wallet authority is activated."
    state = f"vid_{secrets.token_urlsafe(12)}"
    callback_headers: dict[str, str] = {}
    if str(config.get("callback_api_key") or "").strip():
        callback_headers["api-key"] = str(config.get("callback_api_key") or "").strip()
    request_payload = {
        "authority": str(config.get("authority") or "").strip(),
        "includeQRCode": bool(payload.get("include_qr_code", True)),
        "includeReceipt": bool(payload.get("include_receipt", True)),
        "registration": {
            "clientName": str(payload.get("client_name") or config.get("client_name") or "Dual Substrate Identity").strip() or "Dual Substrate Identity",
            "purpose": purpose,
        },
        "callback": {
            "url": str(config.get("callback_url") or "").strip(),
            "state": state,
        },
        "requestedCredentials": [
            {
                "type": requested_credential_type,
                "purpose": purpose,
                "configuration": {
                    "validation": {
                        "allowRevoked": False,
                        "validateLinkedDomain": False,
                    }
                },
            }
        ],
    }
    if callback_headers:
        request_payload["callback"]["headers"] = callback_headers
    if accepted_issuers:
        request_payload["requestedCredentials"][0]["acceptedIssuers"] = accepted_issuers

    try:
        response_payload = await _verified_id_create_presentation_request(config, request_payload)
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            {
                "error": "verified_id_request_failed",
                "status_code": exc.response.status_code,
                "text": exc.response.text[:1000],
            },
            status_code=502,
        )
    except Exception as exc:
        return JSONResponse(
            {"error": "verified_id_unavailable", "detail": str(exc)},
            status_code=503,
        )

    request_id = str(response_payload.get("requestId") or "").strip()
    if not request_id:
        return JSONResponse({"error": "verified_id_invalid_response"}, status_code=502)
    record = VERIFIED_ID_REQUESTS.create(
        state=state,
        request_id=request_id,
        principal_did=principal_did,
        mode=mode,
        request_payload=request_payload,
        response_payload=response_payload,
    )
    return {"status": "ok", "request": _verified_id_public_record(record)}


async def get_verified_id_request(state: str, _: Request):
    record = VERIFIED_ID_REQUESTS.get(state)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="verified id request not found")
    return {"status": "ok", "request": _verified_id_public_record(record)}


async def verified_id_callback(request: Request):
    expected_api_key = _verified_id_callback_api_key()
    provided_api_key = str(request.headers.get("api-key") or "").strip()
    if expected_api_key and not secrets.compare_digest(provided_api_key, expected_api_key):
        raise HTTPException(status_code=401, detail="invalid callback api key")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    state = str(payload.get("state") or "").strip()
    request_id = str(payload.get("requestId") or "").strip() or None
    if not state:
        raise HTTPException(status_code=422, detail="state is required")
    try:
        record = VERIFIED_ID_REQUESTS.update_callback(state=state, request_id=request_id, callback_payload=payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="verified id request not found")

    finalization = None
    request_status = str(payload.get("requestStatus") or "").strip().lower()
    if request_status in {"presentation_verified", "issuance_successful"}:
        try:
            if request_status == "presentation_verified":
                finalization = await _finalize_verified_id_presentation(state=state, callback_payload=payload)
            else:
                finalization = await _finalize_verified_id_issuance(state=state, callback_payload=payload)
        except Exception as exc:
            VERIFIED_ID_REQUESTS.mark_finalization(
                state=state,
                finalization={"status": "error", "detail": str(exc)},
            )
            return JSONResponse(
                {"error": "verified_id_finalization_failed", "detail": str(exc)},
                status_code=500,
            )
        record = VERIFIED_ID_REQUESTS.get(state) or record

    return {
        "status": "ok",
        "request": _verified_id_public_record(record),
        "finalization": finalization,
    }


async def walt_id_callback(request: Request):
    """Handle walt.id issuance status callbacks.

    Walt.id sends status updates to the callback URL set during issuance creation.
    Critical: must return 200 OK for issuance to proceed; walt.id blocks further
    processing if the callback is unreachable or returns an error status.
    """
    expected_api_key = str(WALT_ID_CALLBACK_API_KEY or "").strip()
    provided_api_key = str(request.headers.get("api-key") or "").strip()
    if expected_api_key and not secrets.compare_digest(provided_api_key, expected_api_key):
        # Still return 200 to avoid blocking walt.id issuance, but log the mismatch
        logger.warning("walt_id_callback: invalid api-key header")
        return {"status": "ok", "note": "ignored_unauthenticated"}

    try:
        payload = await request.json()
    except Exception:
        logger.warning("walt_id_callback: invalid JSON payload")
        return {"status": "ok", "note": "ignored_invalid_json"}
    if not isinstance(payload, dict):
        logger.warning("walt_id_callback: non-dict payload")
        return {"status": "ok", "note": "ignored_invalid_payload"}

    state = str(request.query_params.get("state") or "").strip()
    if not state:
        logger.warning("walt_id_callback: missing state query param")
        return {"status": "ok", "note": "ignored_missing_state"}

    try:
        record = VERIFIED_ID_REQUESTS.update_callback(
            state=state,
            request_id=None,
            callback_payload=payload,
        )
    except KeyError:
        logger.warning("walt_id_callback: state not found: %s", state)
        return {"status": "ok", "note": "ignored_state_not_found"}

    event_type = str(payload.get("type") or "").strip().lower()
    if event_type in {"jwt_issue", "sdjwt_issue", "batch_jwt_issue", "batch_sdjwt_issue"}:
        try:
            finalization = await _finalize_walt_id_issuance(state=state, callback_payload=payload)
        except Exception as exc:
            VERIFIED_ID_REQUESTS.mark_finalization(
                state=state,
                finalization={"status": "error", "detail": str(exc)},
            )
            logger.exception("walt_id_callback: finalization failed for state %s", state)
            # Return 200 anyway so walt.id doesn't retry/block
            return {"status": "ok", "note": "finalization_error_logged"}
        record = VERIFIED_ID_REQUESTS.get(state) or record
        return {
            "status": "ok",
            "request": _verified_id_public_record(record),
            "finalization": finalization,
        }

    return {"status": "ok", "request": _verified_id_public_record(record)}


async def entra_oidc_login(request: Request):
    """Redirect user to Microsoft Entra ID for authentication.

    Query params:
      - next: URL to redirect back to after auth (default: /)
      - principal_did: Optional principal to associate with the auth session
    """
    if not ENTRA_OIDC_CLIENT_ID or not ENTRA_OIDC_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="entra_oidc_not_configured")

    next_path = str(request.query_params.get("next") or "/").strip()
    principal_did = str(request.query_params.get("principal_did") or "").strip()

    nonce = secrets.token_urlsafe(16)
    state_payload = {
        "n": nonce,
        "next": next_path,
        "principal_did": principal_did,
    }
    state = base64.urlsafe_b64encode(
        json.dumps(state_payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")

    login_url = _entra_oidc_login_url(request=request, state=state, nonce=nonce)
    return RedirectResponse(url=login_url, status_code=307)


async def entra_oidc_callback(request: Request):
    """Handle the Entra OIDC callback, exchange code for tokens, and set auth cookie."""
    code = str(request.query_params.get("code") or "").strip()
    error = str(request.query_params.get("error") or "").strip()
    error_description = str(request.query_params.get("error_description") or "").strip()
    state_b64 = str(request.query_params.get("state") or "").strip()

    if error:
        detail = f"Entra OIDC error: {error}"
        if error_description:
            detail += f" - {error_description}"
        raise HTTPException(status_code=400, detail=detail)

    if not code:
        raise HTTPException(status_code=400, detail="missing authorization code")

    # Decode state
    next_path = "/"
    principal_did = ""
    try:
        padded = state_b64 + "=" * (4 - len(state_b64) % 4)
        state_payload = json.loads(base64.urlsafe_b64decode(padded))
        if isinstance(state_payload, dict):
            next_path = str(state_payload.get("next") or "/").strip()
            principal_did = str(state_payload.get("principal_did") or "").strip()
    except Exception:
        pass

    redirect_uri = _entra_oidc_redirect_uri(request)
    try:
        token_response = await _entra_oidc_exchange_code(code, redirect_uri)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"token exchange failed: {exc.response.status_code} {exc.response.text[:500]}",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"token exchange error: {str(exc)}")

    id_token = str(token_response.get("id_token") or "").strip()
    access_token = str(token_response.get("access_token") or "").strip()
    if not id_token:
        raise HTTPException(status_code=502, detail="id_token missing from token response")

    # Parse ID token claims (JWT payload, no signature verification for pilot)
    claims: dict[str, Any] = {}
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        raise HTTPException(status_code=502, detail="unable to parse id_token")

    if not isinstance(claims, dict):
        raise HTTPException(status_code=502, detail="invalid id_token payload")

    email = str(claims.get("email") or claims.get("preferred_username") or "").strip()
    name = str(claims.get("name") or "").strip()
    oid = str(claims.get("oid") or claims.get("sub") or "").strip()

    auth_payload = {
        "email": email,
        "name": name,
        "oid": oid,
        "principal_did": principal_did,
        "iat": int(time.time()),
    }
    cookie_value = _sign_entra_auth_payload(auth_payload)
    attrs = _entra_auth_cookie_attrs(request)

    # Append signed auth token to redirect URL so cross-domain dashboards can
    # forward it to the middleware without relying on cookie domain alignment.
    separator = "&" if "?" in next_path else "?"
    redirect_url = f"{next_path}{separator}_entra_auth={quote(cookie_value, safe='')}" if next_path else f"/?_entra_auth={quote(cookie_value, safe='')}"
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(ENTRA_OIDC_AUTH_COOKIE, cookie_value, **attrs)
    return response


def _entra_auth_from_request(request: Request) -> dict[str, Any] | None:
    # Check cookie first
    cookie_value = str(request.cookies.get(ENTRA_OIDC_AUTH_COOKIE) or "").strip()
    if cookie_value:
        result = _verify_entra_auth_token(cookie_value)
        if result:
            return result
    # Check query param (cross-domain flow)
    qp_value = str(request.query_params.get("_entra_auth") or "").strip()
    if qp_value:
        result = _verify_entra_auth_token(qp_value)
        if result:
            return result
    # Check header (server-to-server forwarding)
    hdr_value = str(request.headers.get("x-entra-auth-token") or "").strip()
    if hdr_value:
        result = _verify_entra_auth_token(hdr_value)
        if result:
            return result
    # Check request state (JSON body token injection)
    state_token = getattr(request.state, "_entra_auth_token", None)
    if state_token:
        result = _verify_entra_auth_token(str(state_token).strip())
        if result:
            return result
    return None


def _trust_anchor_config() -> dict[str, str]:
    public_base_url = (os.getenv("TRUST_ANCHOR_PUBLIC_BASE_URL") or os.getenv("PUBLIC_BASE_URL", "")).strip().rstrip("/")
    issuer_did = (os.getenv("TRUST_ANCHOR_ISSUER_DID") or os.getenv("DEFAULT_ISSUER_DID", "")).strip()
    did_document_url = f"{public_base_url}/.well-known/did.json" if public_base_url else ""
    organisation_name = (os.getenv("TRUST_ANCHOR_ORGANISATION_NAME") or "Dual Substrate").strip()
    organisation_uri = (os.getenv("TRUST_ANCHOR_ORGANISATION_URI") or os.getenv("DEFAULT_ORGANISATION_URI", "")).strip()
    organisation_registration_ref = (os.getenv("TRUST_ANCHOR_ORGANISATION_REGISTRATION_REF") or "").strip()
    admin_token = (os.getenv("TRUST_ANCHOR_ADMIN_TOKEN") or os.getenv("ADMIN_TOKEN") or "").strip()
    admin_principal_id = (os.getenv("TRUST_ANCHOR_ADMIN_PRINCIPAL_ID") or "ops-admin").strip()
    admin_principal_type = (os.getenv("TRUST_ANCHOR_ADMIN_PRINCIPAL_TYPE") or "admin").strip()
    context_id = (os.getenv("TRUST_ANCHOR_CONTEXT_ID") or "ctx:middleware:trust-anchor").strip()
    ledger_id = (os.getenv("TRUST_ANCHOR_LEDGER_ID") or "default").strip()
    return {
        "public_base_url": public_base_url,
        "issuer_did": issuer_did,
        "did_document_url": did_document_url,
        "organisation_name": organisation_name,
        "organisation_uri": organisation_uri,
        "organisation_registration_ref": organisation_registration_ref,
        "admin_token": admin_token,
        "admin_principal_id": admin_principal_id,
        "admin_principal_type": admin_principal_type,
        "context_id": context_id,
        "ledger_id": ledger_id,
    }


def _trust_anchor_backend_headers(config: dict[str, str]) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "x-principal-id": config.get("admin_principal_id") or "ops-admin",
        "x-principal-type": config.get("admin_principal_type") or "admin",
        "x-context-id": config.get("context_id") or "ctx:middleware:trust-anchor",
    }
    ledger_id = str(config.get("ledger_id") or "").strip()
    if ledger_id:
        headers["x-ledger-id"] = ledger_id
    admin_token = str(config.get("admin_token") or "").strip()
    if admin_token:
        headers["x-admin-token"] = admin_token
    return headers


async def _trust_anchor_fetch(path: str, config: dict[str, str]) -> dict[str, Any] | None:
    try:
        payload = await _backend_fetch_json(
            method="GET",
            path=path,
            headers=_trust_anchor_backend_headers(config),
        )
        return payload if isinstance(payload, dict) else None
    except HTTPException:
        return None


async def _trust_anchor_fetch_public_backend_document(path: str, config: dict[str, str]) -> dict[str, Any] | None:
    try:
        payload = await _backend_fetch_json(
            method="GET",
            path=path,
            headers=_trust_anchor_backend_headers(config),
        )
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _trust_anchor_public_object_ref(request: Request, object_kind: str, object_id: str) -> str:
    kind = str(object_kind or "").strip()
    identifier = str(object_id or "").strip().strip("/")
    if not kind or not identifier:
        return ""
    return f"{_public_base_url(request)}/o/{kind}/{identifier}"


def _trust_anchor_resolver_proxy_headers(response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for header_name in (
        "Cache-Control",
        "Pragma",
        "X-Resolver-Mode",
        "X-Public-Object-Id",
        "X-Resolver-Ref",
    ):
        value = str(response.headers.get(header_name) or "").strip()
        if value:
            headers[header_name] = value
    return headers


def _trust_anchor_resolver_proxy_payload(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except Exception:
        text = response.text.strip()
        return {"detail": text} if text else {}
    if isinstance(payload, (dict, list)):
        return payload
    return {"detail": payload}


async def _trust_anchor_proxy_public_resolver(
    *,
    ref: str,
    mode: str,
    config: dict[str, str],
) -> JSONResponse:
    normalized_ref = str(ref or "").strip()
    normalized_mode = str(mode or "skim").strip().lower() or "skim"
    if not normalized_ref:
        raise HTTPException(status_code=422, detail="ref is required")

    resolver_url = f"{settings.API_BASE.rstrip('/')}/v1/resolve"
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            response = await client.get(
                resolver_url,
                params={"ref": normalized_ref, "mode": normalized_mode},
                headers=_trust_anchor_backend_headers(config),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"Upstream timeout: {resolver_url}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request error: {resolver_url}") from exc

    return JSONResponse(
        _trust_anchor_resolver_proxy_payload(response),
        status_code=response.status_code,
        headers=_trust_anchor_resolver_proxy_headers(response),
    )


async def _trust_anchor_fetch_public_document(url: str) -> dict[str, Any] | None:
    normalized = str(url or "").strip()
    if not normalized:
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
            response = await client.get(normalized)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _select_trust_anchor_issuer(payload: dict[str, Any] | None, issuer_did: str, anchor_ref: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("issuers")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("issuer_did") or "").strip() == issuer_did:
            return row
        if str(row.get("identity_anchor_ref") or "").strip() == anchor_ref:
            return row
    return None


def _select_trust_anchor_identity_check(payload: dict[str, Any] | None, issuer_did: str, anchor_ref: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("checks")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("subject_ref") or "").strip() == issuer_did:
            return row
        if str(row.get("resolved_identity") or "").strip() == issuer_did:
            return row
        if str(row.get("identity_anchor_ref") or "").strip() == anchor_ref:
            return row
    return None


def _trust_anchor_checks(
    config: dict[str, str],
    issuer_authority: dict[str, Any] | None,
    live_identity_check: dict[str, Any] | None,
    did_document: dict[str, Any] | None,
) -> dict[str, bool]:
    issuer_did = str(config.get("issuer_did") or "").strip()
    anchor_ref = str(config.get("did_document_url") or "").strip()
    issuer_did_match = bool(
        isinstance(issuer_authority, dict)
        and str(issuer_authority.get("issuer_did") or "").strip() == issuer_did
    )
    issuer_anchor_match = bool(
        isinstance(issuer_authority, dict)
        and str(issuer_authority.get("identity_anchor_ref") or "").strip() == anchor_ref
    )
    issuer_policy_explicit = bool(
        isinstance(issuer_authority, dict)
        and str(issuer_authority.get("policy_verdict") or "").strip().lower() == "allow"
        and str(issuer_authority.get("policy_ref") or "").strip()
    )
    issuer_policy_verifier_ref_present = bool(
        isinstance(issuer_authority, dict)
        and str(issuer_authority.get("verifier_policy_ref") or "").strip()
    )
    issuer_binding_anchored = bool(
        isinstance(issuer_authority, dict)
        and str(issuer_authority.get("verification_state") or "").strip().lower() in {"anchored", "verified"}
        and str(issuer_authority.get("credential_ref") or "").strip()
    )
    issuer_vc_verified = bool(
        isinstance(issuer_authority, dict)
        and str(issuer_authority.get("vc_verification_status") or "").strip().lower() == "verified"
    )
    issuer_vc_proof_ref_present = bool(
        isinstance(issuer_authority, dict)
        and str(issuer_authority.get("vc_verification_proof_ref") or "").strip()
    )
    live_subject_match = bool(
        isinstance(live_identity_check, dict)
        and (
            str(live_identity_check.get("subject_ref") or "").strip() == issuer_did
            or str(live_identity_check.get("resolved_identity") or "").strip() == issuer_did
        )
    )
    live_anchor_match = bool(
        isinstance(live_identity_check, dict)
        and str(live_identity_check.get("identity_anchor_ref") or "").strip() == anchor_ref
    )
    live_resolution_verified = bool(
        isinstance(live_identity_check, dict)
        and str(live_identity_check.get("resolution_status") or "").strip().lower() == "verified"
    )
    public_did_resolves = isinstance(did_document, dict)
    public_did_id_match = bool(
        isinstance(did_document, dict)
        and str(did_document.get("id") or "").strip() == issuer_did
    )
    services = did_document.get("service") if isinstance(did_document, dict) else None
    public_service_status_present = False
    public_service_bundle_present = False
    if isinstance(services, list):
        for service in services:
            if not isinstance(service, dict):
                continue
            endpoint = str(service.get("serviceEndpoint") or "").strip()
            if endpoint == f"{config.get('public_base_url', '').rstrip('/')}/api/trust-anchor/status":
                public_service_status_present = True
            if endpoint == f"{config.get('public_base_url', '').rstrip('/')}/.well-known/trust-anchor.json":
                public_service_bundle_present = True
    return {
        "issuer_did_match": issuer_did_match,
        "issuer_anchor_match": issuer_anchor_match,
        "issuer_policy_explicit": issuer_policy_explicit,
        "issuer_policy_verifier_ref_present": issuer_policy_verifier_ref_present,
        "issuer_binding_anchored": issuer_binding_anchored,
        "issuer_vc_verified": issuer_vc_verified,
        "issuer_vc_proof_ref_present": issuer_vc_proof_ref_present,
        "live_subject_match": live_subject_match,
        "live_anchor_match": live_anchor_match,
        "live_resolution_verified": live_resolution_verified,
        "public_did_resolves": public_did_resolves,
        "public_did_id_match": public_did_id_match,
        "public_service_status_present": public_service_status_present,
        "public_service_bundle_present": public_service_bundle_present,
    }


async def trust_anchor_status(_: Request):
    config = _trust_anchor_config()
    warnings: list[str] = []
    admin_configured = bool(config.get("admin_token"))
    if not admin_configured:
        warnings.append("backend_admin_token_not_configured")

    issuer_payload = await _trust_anchor_fetch("/admin/issuer-authorities?status=active", config) if admin_configured else None
    identity_payload = await _trust_anchor_fetch("/admin/live-identity-checks?subject_type=issuer", config) if admin_configured else None
    did_document = await _trust_anchor_fetch_public_document(config.get("did_document_url") or "") if admin_configured else None

    issuer_authority = _select_trust_anchor_issuer(
        issuer_payload,
        config.get("issuer_did") or "",
        config.get("did_document_url") or "",
    )
    live_identity_check = _select_trust_anchor_identity_check(
        identity_payload,
        config.get("issuer_did") or "",
        config.get("did_document_url") or "",
    )
    checks = _trust_anchor_checks(config, issuer_authority, live_identity_check, did_document)

    if admin_configured and issuer_payload is None:
        warnings.append("issuer_authority_lookup_failed")
    if admin_configured and identity_payload is None:
        warnings.append("live_identity_check_lookup_failed")
    if admin_configured and issuer_authority is None:
        warnings.append("issuer_authority_not_found")
    if admin_configured and live_identity_check is None:
        warnings.append("live_identity_check_not_found")
    if admin_configured and issuer_authority is not None and not checks["issuer_did_match"]:
        warnings.append("issuer_authority_did_mismatch")
    if admin_configured and issuer_authority is not None and not checks["issuer_anchor_match"]:
        warnings.append("issuer_authority_anchor_mismatch")
    if admin_configured and issuer_authority is not None and not checks["issuer_policy_explicit"]:
        warnings.append("issuer_policy_not_explicit")
    if admin_configured and issuer_authority is not None and not checks["issuer_policy_verifier_ref_present"]:
        warnings.append("issuer_policy_not_verifier_visible")
    if admin_configured and issuer_authority is not None and not checks["issuer_binding_anchored"]:
        warnings.append("issuer_binding_not_anchored")
    if admin_configured and issuer_authority is not None and not checks["issuer_vc_verified"]:
        warnings.append("issuer_vc_not_verified")
    if admin_configured and issuer_authority is not None and not checks["issuer_vc_proof_ref_present"]:
        warnings.append("issuer_vc_proof_missing")
    if admin_configured and live_identity_check is not None and not checks["live_subject_match"]:
        warnings.append("live_identity_subject_mismatch")
    if admin_configured and live_identity_check is not None and not checks["live_anchor_match"]:
        warnings.append("live_identity_anchor_mismatch")
    if admin_configured and live_identity_check is not None and not checks["live_resolution_verified"]:
        warnings.append("live_identity_not_verified")
    if not checks["public_did_resolves"]:
        warnings.append("public_did_document_unavailable")
    if checks["public_did_resolves"] and not checks["public_did_id_match"]:
        warnings.append("public_did_document_id_mismatch")
    if checks["public_did_resolves"] and not checks["public_service_status_present"]:
        warnings.append("public_did_missing_status_service")
    if checks["public_did_resolves"] and not checks["public_service_bundle_present"]:
        warnings.append("public_did_missing_bundle_service")

    status_value = "ok" if not warnings else ("degraded" if admin_configured else "unconfigured")
    return {
        "status": status_value,
        "trust_anchor": {
            "public_base_url": config.get("public_base_url"),
            "issuer_did": config.get("issuer_did"),
            "did_document_url": config.get("did_document_url"),
            "organisation_name": config.get("organisation_name"),
            "organisation_uri": config.get("organisation_uri"),
            "organisation_registration_ref": config.get("organisation_registration_ref"),
            "backend_url": settings.API_BASE,
            "admin_configured": admin_configured,
        },
        "issuer_authority": issuer_authority,
        "live_identity_check": live_identity_check,
        "did_document": did_document,
        "checks": checks,
        "warnings": warnings,
    }


async def trust_anchor_bundle(_: Request):
    config = _trust_anchor_config()
    backend_bundle = await _trust_anchor_fetch_public_backend_document("/public/trust-anchor/bundle", config)
    if isinstance(backend_bundle, dict):
        return backend_bundle

    status_payload = await trust_anchor_status(_)
    trust_anchor = status_payload.get("trust_anchor") if isinstance(status_payload, dict) else {}
    if not isinstance(trust_anchor, dict):
        trust_anchor = {}
    issuer_authority = status_payload.get("issuer_authority") if isinstance(status_payload, dict) else {}
    if not isinstance(issuer_authority, dict):
        issuer_authority = {}
    checks = status_payload.get("checks") if isinstance(status_payload, dict) else {}
    if not isinstance(checks, dict):
        checks = {}
    public_base_url = str(trust_anchor.get("public_base_url") or "").rstrip("/")
    issuer_did = str(trust_anchor.get("issuer_did") or "")
    did_document_url = str(trust_anchor.get("did_document_url") or "")
    organisation_name = str(trust_anchor.get("organisation_name") or "").strip()
    organisation_uri = str(trust_anchor.get("organisation_uri") or "").strip()
    organisation_registration_ref = str(trust_anchor.get("organisation_registration_ref") or "").strip()
    issuer_authority_url = f"{public_base_url}/.well-known/issuer-authority.json" if public_base_url else ""
    issuer_authority_status_url = f"{public_base_url}/.well-known/issuer-authority-status.json" if public_base_url else ""
    verifier_policy_url = f"{public_base_url}/.well-known/verifier-policy.json" if public_base_url else ""
    status_url = f"{public_base_url}/api/trust-anchor/status" if public_base_url else ""
    bundle_url = f"{public_base_url}/.well-known/trust-anchor.json" if public_base_url else ""
    authority_subject = {
        "id": issuer_did,
        "type": "IssuerAuthoritySubject",
        "issuer_did": issuer_did,
        "organisation_name": organisation_name or None,
        "organisation_uri": organisation_uri or None,
        "organisation_registration_ref": organisation_registration_ref or None,
    }
    public_issuer_authority = {
        "id": issuer_authority_url or f"{issuer_did}#issuer-authority",
        "type": "DssIssuerAuthority",
        "statement_type": "IssuerAuthorityStatement",
        "format": "dss-public-authority-statement-v1",
        "issuer_did": issuer_did,
        "issuer": {
            "id": issuer_did,
            "type": "IssuerAuthority",
        },
        "subject": authority_subject,
        "issued_at": issuer_authority.get("updated_at") or issuer_authority.get("created_at"),
        "not_a_verifiable_credential": True,
        "authority_identity": {
            "issuer_did": issuer_did,
            "identity_anchor_ref": issuer_authority.get("identity_anchor_ref") or did_document_url,
            "verification_state": issuer_authority.get("verification_state"),
            "issuer_class": issuer_authority.get("issuer_class"),
        },
        "organisation_identity": {
            "name": organisation_name or None,
            "homepage": organisation_uri or None,
            "registration_ref": organisation_registration_ref or None,
            "status": "partial",
            "note": "Public issuer identity is established; fuller organisation identity semantics remain future evidence-bearing work.",
        },
        "policy": {
            "policy_ref": issuer_authority.get("policy_ref"),
            "policy_verdict": issuer_authority.get("policy_verdict"),
            "policy_scope": issuer_authority.get("policy_scope") or [],
            "verifier_policy_ref": issuer_authority.get("verifier_policy_ref") or bundle_url,
            "explicit": checks.get("issuer_policy_explicit"),
            "verifier_visible": checks.get("issuer_policy_verifier_ref_present"),
        },
        "status": {
            "authority_active": str(issuer_authority.get("status") or "").strip().lower() == "active",
            "binding_anchored": checks.get("issuer_binding_anchored"),
            "vc_verified": checks.get("issuer_vc_verified"),
            "live_identity_verified": checks.get("live_resolution_verified"),
            "credential_ref": issuer_authority.get("credential_ref"),
            "vc_type": issuer_authority.get("vc_type"),
            "vc_id": issuer_authority.get("vc_id"),
            "vc_verification_proof_ref": issuer_authority.get("vc_verification_proof_ref"),
        },
        "discovery": {
            "did_document": did_document_url,
            "trust_anchor_status": status_url,
            "trust_anchor_bundle": bundle_url,
        },
        "status_discovery": {
            "authority_status_ref": issuer_authority_status_url or status_url,
            "revocation_ref": bundle_url,
            "freshness_model": "live_status_endpoint_plus_trust_bundle_policy",
        },
    }
    public_issuer_authority_status = {
        "id": issuer_authority_status_url or f"{issuer_did}#issuer-authority-status",
        "type": "DssIssuerAuthorityStatus",
        "status_type": "IssuerAuthorityStatusStatement",
        "format": "dss-public-authority-status-v1",
        "issuer": {
            "id": issuer_did,
            "type": "IssuerAuthority",
        },
        "subject": authority_subject,
        "not_a_verifiable_credential": True,
        "status": public_issuer_authority.get("status"),
        "policy": {
            "policy_ref": issuer_authority.get("policy_ref"),
            "policy_verdict": issuer_authority.get("policy_verdict"),
            "policy_scope": issuer_authority.get("policy_scope") or [],
            "verifier_policy_ref": issuer_authority.get("verifier_policy_ref") or bundle_url,
        },
        "discovery": {
            "authority_statement": issuer_authority_url,
            "trust_anchor_status": status_url,
            "trust_anchor_bundle": bundle_url,
        },
        "updated_at": issuer_authority.get("updated_at") or issuer_authority.get("created_at"),
    }
    public_verifier_policy = {
        "id": verifier_policy_url or f"{issuer_did}#verifier-policy",
        "type": "DssVerifierPolicy",
        "policy_type": "VerifierPolicyStatement",
        "format": "dss-public-verifier-policy-v1",
        "issuer": {
            "id": issuer_did,
            "type": "IssuerAuthority",
        },
        "subject": authority_subject,
        "not_a_verifiable_credential": True,
        "policy": {
            "policy_ref": issuer_authority.get("policy_ref"),
            "policy_verdict": issuer_authority.get("policy_verdict"),
            "policy_scope": issuer_authority.get("policy_scope") or [],
            "explicit": checks.get("issuer_policy_explicit"),
            "verifier_visible": checks.get("issuer_policy_verifier_ref_present"),
        },
        "verification_expectations": {
            "resolve_issuer_did_first": True,
            "inspect_authority_statement": True,
            "inspect_authority_status_statement": True,
            "use_live_status_endpoint": True,
        },
        "discovery": {
            "authority_statement": issuer_authority_url,
            "authority_status_statement": issuer_authority_status_url or status_url,
            "trust_anchor_bundle": bundle_url,
            "trust_anchor_status": status_url,
        },
        "updated_at": issuer_authority.get("updated_at") or issuer_authority.get("created_at"),
    }
    verifier_instructions = {
        "resolve_issuer_did": did_document_url,
        "inspect_authority_object": issuer_authority_url,
        "inspect_authority_status_object": issuer_authority_status_url or status_url,
        "inspect_trust_bundle": bundle_url,
        "inspect_status": status_url,
        "inspect_verifier_policy_object": verifier_policy_url or issuer_authority.get("verifier_policy_ref") or bundle_url,
        "verify_policy_via": verifier_policy_url or issuer_authority.get("verifier_policy_ref") or bundle_url,
        "notes": [
            "Resolve the issuer DID document first.",
            "Use the issuer authority object as the typed public authority summary.",
            "Treat the authority object as a public authority statement, not as a full verifiable credential.",
            "Use the verifier policy object as the typed policy discovery surface.",
            "Use trust bundle policy and status surfaces as verifier-facing discovery aids.",
            "Do not treat the issuer DID alone as complete organisation identity evidence.",
        ],
    }
    publication_intent = {
        "profile": "dss-public-trust-discovery-v1",
        "current_publication_state": "partial",
        "published_now": [
            "did_document",
            "trust_anchor_bundle",
            "trust_anchor_status",
            "issuer_authority_statement",
            "issuer_authority_status_statement",
            "verifier_policy_reference",
        ],
        "future_publication_targets": [
            "typed_issuer_authority_credential",
            "organisation_registration_evidence",
            "public_revocation_or_status_credential",
        ],
        "note": "This bundle is a verifier-discovery surface for the current DSS trust contract, not a full UNTP credential publication set.",
    }
    evidence_profile = {
        "issuer_identity_anchor": issuer_authority.get("identity_anchor_ref") or did_document_url,
        "organisation_identity_status": "partial",
        "organisation_registration_published": bool(organisation_registration_ref),
        "authority_statement_published": True,
        "authority_status_statement_published": True,
        "vc_evidence_published": bool(issuer_authority.get("credential_ref") or issuer_authority.get("vc_id")),
        "revocation_surface_published": bool(bundle_url),
    }
    return {
        "issuer_did": issuer_did,
        "did_document_url": did_document_url,
        "trust_anchor_status": status_payload,
        "issuer_policy": {
            "policy_ref": issuer_authority.get("policy_ref"),
            "policy_verdict": issuer_authority.get("policy_verdict"),
            "policy_scope": issuer_authority.get("policy_scope") or [],
            "verifier_policy_ref": verifier_policy_url or issuer_authority.get("verifier_policy_ref"),
            "explicit": checks.get("issuer_policy_explicit"),
            "verifier_visible": checks.get("issuer_policy_verifier_ref_present"),
        },
        "issuer_authority_evidence": {
            "credential_ref": issuer_authority.get("credential_ref"),
            "verification_state": issuer_authority.get("verification_state"),
            "vc_type": issuer_authority.get("vc_type"),
            "vc_id": issuer_authority.get("vc_id"),
            "vc_verification_method": issuer_authority.get("vc_verification_method"),
            "vc_verification_status": issuer_authority.get("vc_verification_status"),
            "vc_verification_proof_ref": issuer_authority.get("vc_verification_proof_ref"),
            "binding_anchored": checks.get("issuer_binding_anchored"),
            "proof_verified": checks.get("issuer_vc_verified"),
        },
        "service_endpoints": {
            "trust_anchor_status": status_url,
            "trust_anchor_bundle": bundle_url,
            "issuer_authority_object": issuer_authority_url,
            "issuer_authority_status_object": issuer_authority_status_url,
            "verifier_policy_object": verifier_policy_url,
        },
        "public_issuer_authority": public_issuer_authority,
        "public_issuer_authority_status": public_issuer_authority_status,
        "public_verifier_policy": public_verifier_policy,
        "publication_intent": publication_intent,
        "evidence_profile": evidence_profile,
        "verifier_instructions": verifier_instructions,
        # This is an interoperability-oriented public bundle, not a full UNTP claim.
        "interop_profile": {
            "untp_alignment": "targeted",
            "did_method": "did:web",
            "notes": [
                "Publishes a public trust bundle for verifier discovery.",
                "Exposes issuer and anchor consistency checks.",
                "Publishes explicit issuer policy and authority evidence summaries.",
                "Publishes a typed public issuer authority object and verifier instructions.",
                "Publishes a separate issuer authority status object for verifier-facing status discovery.",
                "Publishes a typed verifier policy object for policy-specific discovery.",
                "States the current publication boundary explicitly so verifiers can distinguish published surfaces from future credential work.",
            ],
        },
    }


async def well_known_trust_anchor(_: Request):
    config = _trust_anchor_config()
    bundle = await _trust_anchor_fetch_public_backend_document("/public/trust-anchor/bundle", config)
    if isinstance(bundle, dict):
        return bundle
    return await trust_anchor_bundle(_)


async def well_known_issuer_authority(_: Request):
    config = _trust_anchor_config()
    document = await _trust_anchor_fetch_public_backend_document("/public/trust-anchor/issuer-authority", config)
    if isinstance(document, dict):
        return document
    bundle = await trust_anchor_bundle(_)
    if isinstance(bundle, dict) and isinstance(bundle.get("public_issuer_authority"), dict):
        return bundle["public_issuer_authority"]
    raise HTTPException(status_code=404, detail="issuer authority document unavailable")


async def well_known_issuer_authority_status(_: Request):
    config = _trust_anchor_config()
    document = await _trust_anchor_fetch_public_backend_document("/public/trust-anchor/issuer-authority-status", config)
    if isinstance(document, dict):
        return document
    bundle = await trust_anchor_bundle(_)
    if isinstance(bundle, dict) and isinstance(bundle.get("public_issuer_authority_status"), dict):
        return bundle["public_issuer_authority_status"]
    raise HTTPException(status_code=404, detail="issuer authority status document unavailable")


async def well_known_verifier_policy(_: Request):
    config = _trust_anchor_config()
    document = await _trust_anchor_fetch_public_backend_document("/public/trust-anchor/verifier-policy", config)
    if isinstance(document, dict):
        return document
    bundle = await trust_anchor_bundle(_)
    if isinstance(bundle, dict) and isinstance(bundle.get("public_verifier_policy"), dict):
        return bundle["public_verifier_policy"]
    raise HTTPException(status_code=404, detail="verifier policy document unavailable")


async def trust_anchor_credential_status(_: Request, credential_status_ref: str):
    config = _trust_anchor_config()
    document = await _trust_anchor_fetch_public_backend_document(f"/public/status/{credential_status_ref}", config)
    if isinstance(document, dict):
        return document
    raise HTTPException(status_code=404, detail="credential status document unavailable")


async def public_object_resolve(
    request: Request,
    public_ref: str | None = None,
    ref: str | None = None,
    mode: str = "skim",
):
    config = _trust_anchor_config()
    normalized_ref = str(public_ref or ref or "").strip()
    if not normalized_ref:
        raise HTTPException(status_code=422, detail="ref is required")
    return await _trust_anchor_proxy_public_resolver(ref=normalized_ref, mode=mode, config=config)


async def public_object_document(request: Request, object_kind: str, object_id: str):
    config = _trust_anchor_config()
    public_ref = _trust_anchor_public_object_ref(request, object_kind, object_id)
    if not public_ref:
        raise HTTPException(status_code=422, detail="public object ref is required")
    return await _trust_anchor_proxy_public_resolver(ref=public_ref, mode="skim", config=config)


async def public_object_status(request: Request, object_kind: str, object_id: str):
    config = _trust_anchor_config()
    normalized_object_id = str(object_id or "").strip()
    if normalized_object_id.endswith("/status"):
        normalized_object_id = normalized_object_id[: -len("/status")].rstrip("/")
    public_ref = _trust_anchor_public_object_ref(request, object_kind, normalized_object_id)
    if not public_ref:
        raise HTTPException(status_code=422, detail="public object ref is required")
    return await _trust_anchor_proxy_public_resolver(ref=public_ref, mode="full", config=config)

async def start_github_principal_link(request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    github_user_id = str(payload.get("github_user_id") or "").strip()
    github_login = str(payload.get("github_login") or "").strip()
    github_email = str(payload.get("github_email") or "").strip().lower() or None
    tenant_id = str(payload.get("tenant_id") or "").strip() or None
    channel = str(payload.get("contact_channel") or ("email" if github_email else "")).strip().lower()
    contact_value = str(payload.get("contact_value") or "").strip()
    if channel == "email" and not contact_value and github_email:
        contact_value = github_email

    if not github_user_id:
        raise HTTPException(status_code=422, detail="github_user_id is required")
    direct = PRINCIPAL_REGISTRY.find_by_key_ref(f"github:user:{github_user_id}", tenant_id=tenant_id)
    if isinstance(direct, dict):
        return {"status": "linked", "link_state": "linked", "principal": direct}

    if channel not in {"email", "phone"} or not contact_value:
        raise HTTPException(status_code=422, detail=cast(Any, {"error": "contact_required", "allowed_channels": ["email", "phone"]}))

    candidates = PRINCIPAL_REGISTRY.find_by_contact(
        email=contact_value if channel == "email" else None,
        phone=contact_value if channel == "phone" else None,
        tenant_id=tenant_id,
    )
    if not candidates:
        raise HTTPException(status_code=404, detail=cast(Any, {"error": "principal_link_not_found"}))
    if len(candidates) > 1:
        raise HTTPException(status_code=409, detail=cast(Any, {"error": "principal_link_conflict", "candidate_count": len(candidates)}))

    record = candidates[0]
    challenge = PRINCIPAL_LINK_CHALLENGES.create(
        principal_did=str(record.get("principal_did") or "").strip(),
        github_user_id=github_user_id,
        github_login=github_login,
        github_email=github_email,
        contact_channel=channel,
        contact_value=contact_value,
        ttl_seconds=600,
    )
    if channel == "email":
        await _send_principal_link_email(
            to_email=contact_value,
            code=str(challenge.get("code") or "").strip(),
            github_login=github_login,
            expires_at=str(challenge.get("expires_at") or "").strip() or None,
        )
    elif channel == "phone":
        raise HTTPException(status_code=501, detail=cast(Any, {"error": "phone_delivery_not_configured"}))
    response = {
        "status": "verification_required",
        "link_state": "verification_required",
        "challenge_id": challenge["challenge_id"],
        "principal_did": record.get("principal_did"),
        "delivery_channel": channel,
        "delivery_state": "sent",
        "masked_destination": _mask_contact(contact_value, channel=channel),
        "expires_at": challenge.get("expires_at"),
    }
    if os.getenv("PRINCIPAL_LINK_CODE_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}:
        response["debug_code"] = challenge.get("code")
    return response


async def verify_github_principal_link(request: Request):
    if not settings.ENABLE_LEDGER_MANAGEMENT:
        raise HTTPException(status_code=404, detail="Principal registry disabled")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    challenge_id = str(payload.get("challenge_id") or "").strip()
    code = str(payload.get("code") or "").strip()
    try:
        record = PRINCIPAL_LINK_CHALLENGES.verify(challenge_id=challenge_id, code=code)
    except KeyError:
        raise HTTPException(status_code=404, detail=cast(Any, {"error": "link_challenge_not_found"}))
    except TimeoutError:
        raise HTTPException(status_code=410, detail=cast(Any, {"error": "link_challenge_expired"}))
    except PermissionError:
        raise HTTPException(status_code=400, detail=cast(Any, {"error": "link_code_invalid"}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        principal = PRINCIPAL_REGISTRY.link_github_identity(
            principal_did=str(record.get("principal_did") or "").strip(),
            github_user_id=str(record.get("github_user_id") or "").strip(),
            github_login=str(record.get("github_login") or "").strip() or None,
            github_email=str(record.get("github_email") or "").strip() or None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=cast(Any, {"error": "principal_link_not_found"}))
    return {"status": "ok", "link_state": "linked_existing", "principal": principal}


async def get_ledger_founding_purpose(request: Request):
    ledger_id = (request.path_params.get("ledger_id") or "").strip()
    if not ledger_id:
        raise HTTPException(status_code=404, detail="ledger_id is required")
    try:
        data = await api.get_ledger_purpose(ledger_id)
    except Exception as exc:
        logger.warning("Failed to fetch ledger founding purpose for %s: %s", ledger_id, exc)
        return JSONResponse({"ledger_id": ledger_id, "purpose": None, "error": "upstream_failed"})
    return JSONResponse(data)


app.route("/api/ledgers", methods=["GET"])(list_ledgers)
app.route("/api/ledgers", methods=["POST"])(create_or_switch_ledger)
app.route("/api/ledger/{ledger_id}/purpose", methods=["GET"])(get_ledger_founding_purpose)
app.route("/api/control-plane/ledgers", methods=["GET"])(list_control_plane_ledgers)
app.route("/api/control-plane/ledgers", methods=["POST"])(upsert_control_plane_ledger)
app.route("/api/control-plane/submissions", methods=["GET"])(list_control_plane_submissions)
app.route("/api/control-plane/submissions/<submission_ref>/review", methods=["POST"])(review_control_plane_submission)
app.route("/api/control-plane/providers", methods=["GET"])(list_control_plane_providers)
app.route("/api/control-plane/providers", methods=["POST"])(upsert_control_plane_provider)
app.route("/api/control-plane/model-bindings", methods=["GET"])(list_control_plane_model_bindings)
app.route("/api/control-plane/model-bindings", methods=["POST"])(upsert_control_plane_model_binding)
app.route("/api/control-plane/principals", methods=["GET"])(list_control_plane_principals)
app.route("/api/control-plane/principals", methods=["POST"])(upsert_control_plane_principal)
app.route("/api/control-plane/principals/codex/provision", methods=["POST"])(provision_control_plane_codex_principal)
app.route("/api/control-plane/principals/{principal_did:path}/status", methods=["POST"])(update_control_plane_principal_status)
app.route("/api/control-plane/surfaces", methods=["GET"])(list_control_plane_surfaces)
app.route("/api/control-plane/surfaces", methods=["POST"])(upsert_control_plane_surface)
app.route("/api/control-plane/relationships", methods=["GET"])(list_control_plane_relationships)
app.route("/api/control-plane/relationships", methods=["POST"])(upsert_control_plane_relationship)
app.route("/api/control-plane/entities/activate", methods=["POST"])(activate_control_plane_entity)
app.route("/api/control-plane/entities/remove", methods=["POST"])(remove_control_plane_entity)
app.route("/api/principals", methods=["GET"])(list_principals)
app.route("/api/principals/resolve", methods=["GET"])(resolve_principal)
app.route("/api/principals", methods=["POST"])(upsert_principal)
app.route("/api/principals/{principal_did:path}/bindings", methods=["GET"])(get_principal_binding_events)
app.route("/api/principals/{principal_did:path}/bindings", methods=["POST"])(bind_principal_identity)
app.route("/api/principals/{principal_did:path}/provisioning", methods=["GET"])(get_principal_provisioning)
app.route("/api/principals/{principal_did:path}/provisioning", methods=["POST"])(update_principal_provisioning)
app.route("/api/principals/{principal_did:path}/subject/events", methods=["GET"])(get_principal_subject_events)
app.route("/api/principals/{principal_did:path}/subject/events", methods=["POST"])(append_principal_subject_event)
app.route("/api/principals/{principal_did:path}/standing", methods=["GET"])(get_principal_standing_view)
app.route("/api/principals/{principal_did:path}/authority", methods=["GET"])(get_principal_authority)
app.route("/api/principals/{principal_did:path}/authority/history", methods=["GET"])(get_principal_authority_history)
app.route("/api/principals/{principal_did:path}/standing/events", methods=["GET"])(get_principal_standing_events)
app.route("/api/principals/{principal_did:path}/standing/events", methods=["POST"])(append_principal_standing_event)
app.route("/api/principals/{principal_did:path}/disable", methods=["POST"])(disable_principal)
app.route("/api/principals/{principal_did:path}/enable", methods=["POST"])(enable_principal)
app.route("/api/principals/{principal_did:path}", methods=["GET"])(get_principal)
app.route("/api/principals/link/github/start", methods=["POST"])(start_github_principal_link)
app.route("/api/principals/link/github/verify", methods=["POST"])(verify_github_principal_link)
app.route("/account/current/model-library", methods=["GET"])(proxy_account_model_library)
app.route("/account/current/model-library/select", methods=["POST"])(proxy_account_model_library_select)
app.route("/account/current/principals", methods=["GET"])(proxy_account_principals)
app.route("/account/current/principals/agent/bootstrap", methods=["POST"])(proxy_account_agent_principal_bootstrap)
app.route("/account/current/connections", methods=["GET"])(proxy_account_connections)
app.route("/account/current/onboarding", methods=["GET"])(proxy_account_onboarding)
app.route("/account/current/setup-prompt", methods=["GET"])(proxy_account_setup_prompt)
app.route("/account/current/setup-prompt/dismiss", methods=["POST"])(proxy_account_setup_prompt_dismiss)
app.route("/wallet/credential-offer", methods=["GET"])(proxy_wallet_credential_offer)
app.route("/wallet/{wallet_id}/did.json", methods=["GET"])(proxy_wallet_did_document)
app.route("/wallet/providers", methods=["GET"])(proxy_wallet_providers)
app.route("/admin/provisioning/jobs/{job_id}", methods=["GET"])(proxy_admin_provisioning_job)
app.route("/admin/provisioning/jobs/{job_id}/steps", methods=["GET"])(proxy_admin_provisioning_job_steps)
app.route("/account/current/onboarding", methods=["POST"])(proxy_account_onboarding_post)
app.route("/account/current/provisioning", methods=["GET"])(proxy_account_provisioning)
app.route("/account/current/provisioning/run", methods=["POST"])(proxy_account_provisioning_run)
app.route("/account/current/identity/wallet-link/start", methods=["POST"])(proxy_account_identity_wallet_link_start)
app.route("/account/current/identity/wallet-link/complete", methods=["POST"])(proxy_account_identity_wallet_link_complete)
app.route("/account/current", methods=["GET"])(proxy_account_current)
app.route("/account/current/subscription", methods=["GET"])(proxy_account_subscription)
app.route("/account/current/setup-checklist", methods=["GET"])(proxy_account_setup_checklist)
app.route("/account/current/surfaces", methods=["GET"])(proxy_account_surfaces)
app.route("/account/current/identity", methods=["GET"])(proxy_account_identity)
app.route("/api/verified-id/issuance-requests", methods=["POST"])(create_verified_id_issuance_request)
app.route("/api/verified-id/presentation-requests", methods=["POST"])(create_verified_id_presentation_request)
app.route("/api/verified-id/requests/{state}", methods=["GET"])(get_verified_id_request)
app.route("/api/webhooks/entra/verified-id", methods=["POST"])(verified_id_callback)
app.route("/api/webhooks/walt-id/issuance", methods=["POST"])(walt_id_callback)
app.route("/api/trust-anchor/status", methods=["GET"])(trust_anchor_status)
app.route("/api/trust-anchor/bundle", methods=["GET"])(trust_anchor_bundle)
app.route("/.well-known/trust-anchor.json", methods=["GET"])(well_known_trust_anchor)
app.route("/.well-known/issuer-authority.json", methods=["GET"])(well_known_issuer_authority)
app.route("/.well-known/issuer-authority-status.json", methods=["GET"])(well_known_issuer_authority_status)
app.route("/.well-known/verifier-policy.json", methods=["GET"])(well_known_verifier_policy)
app.route("/api/trust-anchor/credential-status/{credential_status_ref:path}", methods=["GET"])(trust_anchor_credential_status)
app.route("/v1/resolve", methods=["GET"])(public_object_resolve)
app.route("/v1/resolve/{public_ref:path}", methods=["GET"])(public_object_resolve)
app.route("/o/{object_kind}/{object_id:path}/status", methods=["GET"])(public_object_status)
app.route("/o/{object_kind}/{object_id:path}", methods=["GET"])(public_object_document)
app.route("/api/auth/entra/login", methods=["GET"])(entra_oidc_login)
app.route("/api/auth/entra/callback", methods=["GET"])(entra_oidc_callback)


if __name__ == "__main__":
    _print_mcp_boot_banner()
    serve()
