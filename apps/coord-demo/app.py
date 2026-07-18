"""FastHTML COORD decoder demo with governed-surface SSO.

Provides a single-page UI and a POST /resolve endpoint that forwards a COORD
JSON payload to the middleware resolver scoped to the configured ledger.
Access is gated by the shared control-plane session (ds_backend_session_token).
"""

import json
import os
import re
from urllib.parse import quote

import httpx
from fasthtml.common import (
    Button,
    Details,
    Div,
    Form,
    H1,
    H2,
    H3,
    Li,
    P,
    Pre,
    Span,
    Style,
    Strong,
    Summary,
    Textarea,
    Titled,
    Ul,
    fast_app,
)
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

# Matches common COORD identifiers referenced inside payloads.
_COORD_RE = re.compile(
    r"\b[a-zA-Z0-9_-]+:[A-Z0-9]+-[A-Za-z0-9-]+(?:-P\d+)?\b"
)

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


# ---------------------------------------------------------------------------
# Decode payload normalization & rendering
# ---------------------------------------------------------------------------


def _extract_payload_text(payload: dict) -> str:
    """Return the first non-empty blob text from payload.segments/blobs."""
    payload_blob = payload.get("payload") if isinstance(payload, dict) else None
    if not isinstance(payload_blob, dict):
        return ""
    blobs = payload_blob.get("blobs")
    segments = payload_blob.get("segments")
    if isinstance(blobs, dict) and isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            blob_ref = segment.get("blob_ref")
            if isinstance(blob_ref, str):
                blob_text = blobs.get(blob_ref)
                if isinstance(blob_text, str) and blob_text.strip():
                    return blob_text.strip()
    return ""


def _extract_summary(payload: dict) -> str:
    """Return a one-line summary, falling back to payload text."""
    skim = payload.get("skim") if isinstance(payload, dict) else None
    if isinstance(skim, dict):
        one_line = skim.get("one_line")
        if isinstance(one_line, str) and one_line.strip():
            return one_line.strip()
    payload_text = _extract_payload_text(payload)
    if payload_text:
        return payload_text
    return "No summary provided."


def _extract_claims(payload: dict) -> list[str]:
    """Return claim labels from payload.interpretation.claims."""
    interpretation = payload.get("interpretation") if isinstance(payload, dict) else None
    if not isinstance(interpretation, dict):
        return []
    claims: list[str] = []
    for claim in interpretation.get("claims") or []:
        if isinstance(claim, dict):
            label = claim.get("label")
            if label:
                claims.append(str(label))
        elif claim:
            claims.append(str(claim))
    return claims


def _extract_topics(payload: dict) -> list[tuple[str, float]]:
    """Return topic labels and scores from payload.interpretation.topics."""
    interpretation = payload.get("interpretation") if isinstance(payload, dict) else None
    if not isinstance(interpretation, dict):
        return []
    topics: list[tuple[str, float]] = []
    for topic in interpretation.get("topics") or []:
        if isinstance(topic, dict):
            label = topic.get("label")
            score = topic.get("score")
            if label:
                topics.append((str(label), float(score) if isinstance(score, (int, float)) else 0.0))
        elif topic:
            topics.append((str(topic), 0.0))
    return topics


def _collect_referenced_coords(payload: dict, resolved_coord: str) -> list[str]:
    """Find COORD references in context refs and payload text."""
    seen: set[str] = set()
    refs: list[str] = []
    candidates: list[str] = []

    refs_obj = payload.get("refs") if isinstance(payload, dict) else None
    if isinstance(refs_obj, dict):
        for key in ("context", "inputs", "evidence", "overlays", "governance", "walk_traces"):
            for item in refs_obj.get(key) or []:
                if isinstance(item, dict):
                    coord = item.get("coord")
                    if isinstance(coord, str) and coord:
                        candidates.append(coord)
                elif isinstance(item, str):
                    candidates.append(item)

    payload_text = _extract_payload_text(payload)
    if payload_text:
        candidates.append(payload_text)

    for block in candidates:
        for coord in _COORD_RE.findall(block):
            if coord == resolved_coord or coord in seen:
                continue
            seen.add(coord)
            refs.append(coord)
    return refs


def _format_metric(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6f}" if abs(value) < 1 else f"{value:.4f}"
    return str(value)


def _render_decode_result(payload: dict) -> Div:
    """Render a backend decode payload as a human-readable result panel."""
    meta = payload.get("meta") or {}
    governance = payload.get("governance") or {}
    appraisal = governance.get("appraisal") if isinstance(governance, dict) else {}
    if not isinstance(appraisal, dict):
        appraisal = {}

    coord = (
        str(payload.get("coord") or "")
        or meta.get("coord")
        or meta.get("identifier")
        or "unknown"
    )
    coord_type = str(payload.get("type") or meta.get("coord_type") or "unknown")
    namespace = str(
        meta.get("namespace_used")
        or meta.get("runtime_namespace")
        or meta.get("namespace")
        or (coord.split(":", 1)[0] if ":" in coord else "unknown")
    )
    ledger_id = str(payload.get("ledger_id") or meta.get("canonical_ledger_id") or namespace)
    canonical_did = str(
        meta.get("canonical_subject")
        or payload.get("canonical_ledger_did")
        or "N/A"
    )
    created_at = str(meta.get("created_at") or meta.get("timestamp") or "N/A")

    score = appraisal.get("score")
    law = appraisal.get("law")
    grace = appraisal.get("grace")
    drift = appraisal.get("drift")
    coherence = appraisal.get("coherence")
    policy_decision = str(governance.get("policy_decision") or "N/A")
    risk_class = str(governance.get("risk_class") or "N/A")
    policy_version = str(governance.get("policy_version") or "N/A")

    summary = _extract_summary(payload)
    payload_text = _extract_payload_text(payload)
    claims = _extract_claims(payload)
    topics = _extract_topics(payload)
    referenced = _collect_referenced_coords(payload, coord)

    metric_card = lambda label, value: Div(
        P(Strong(label), cls="metric-label"),
        P(_format_metric(value), cls="metric-value"),
        cls="metric-card",
    )

    children = [
        Div(
            metric_card("Type", coord_type),
            metric_card("Namespace", namespace),
            metric_card("Ledger", ledger_id),
            cls="metric-row",
        ),
        Div(
            Div(
                P(Strong("Summary"), cls="section-label"),
                P(summary, cls="summary-text"),
                cls="success-box",
            )
        ),
    ]

    if payload_text:
        children.append(
            Div(
                H2("Content"),
                Pre(payload_text, cls="content-body"),
                cls="section",
            )
        )

    if claims:
        children.append(
            Div(
                H3("Claims"),
                Ul(*[Li(claim) for claim in claims]),
                cls="section",
            )
        )

    if topics:
        topic_items = [Li(f"{label} — {score:.2f}") for label, score in topics]
        children.append(
            Div(
                H3("Topics"),
                Ul(*topic_items),
                cls="section",
            )
        )

    if referenced:
        children.append(
            Div(
                H3("Referenced COORDs"),
                Ul(*[Li(ref) for ref in referenced]),
                cls="section",
            )
        )

    children.append(
        Div(
            H2("Governance"),
            Div(
                metric_card("Score", score),
                metric_card("Law", law),
                metric_card("Grace", grace),
                metric_card("Drift", drift),
                metric_card("Coherence", coherence),
                metric_card("Decision", policy_decision),
                metric_card("Risk", risk_class),
                metric_card("Policy", policy_version),
                cls="metric-row governance-row",
            ),
            cls="section",
        )
    )

    children.append(
        Div(
            H2("Meta"),
            P(Span(Strong("COORD: "), Span(coord))),
            P(Span(Strong("Canonical DID: "), Span(canonical_did))),
            P(Span(Strong("Created at: "), Span(created_at))),
            cls="section",
        )
    )

    children.append(
        Details(
            Summary("View Raw Ledger JSON"),
            Pre(json.dumps(payload, indent=2), cls="raw-json"),
            cls="section raw-details",
        )
    )

    return Div(*children, id="result", cls="decode-result")


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
    styles = Style("""
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #fafafa; color: #111; }
        .decode-result { margin-top: 1.5rem; }
        .metric-row { display: flex; flex-wrap: wrap; gap: 1rem; margin: 1rem 0; }
        .metric-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 0.75rem 1rem; min-width: 120px; flex: 1 1 auto; }
        .metric-label { font-size: 0.75rem; text-transform: uppercase; color: #6b7280; margin: 0 0 0.25rem; }
        .metric-value { font-size: 1.1rem; font-weight: 600; margin: 0; }
        .success-box { padding: 1rem 1.25rem; border-left: 4px solid #10b981; background-color: #f0fdf4; border-radius: 0 8px 8px 0; margin: 1rem 0; }
        .section-label { margin: 0 0 0.25rem; }
        .summary-text { margin: 0; line-height: 1.5; }
        .section { margin: 1.5rem 0; }
        .section h2 { font-size: 1.25rem; margin-bottom: 0.5rem; }
        .section h3 { font-size: 1rem; margin-bottom: 0.5rem; }
        .content-body { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 1rem; white-space: pre-wrap; line-height: 1.6; }
        ul { margin: 0.5rem 0; padding-left: 1.25rem; }
        li { margin: 0.25rem 0; }
        .raw-details { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 0.75rem 1rem; }
        .raw-details summary { cursor: pointer; font-weight: 600; }
        .raw-json { max-height: 500px; overflow: auto; background: #111827; color: #f3f4f6; padding: 1rem; border-radius: 6px; margin-top: 0.5rem; }
    """)
    return Titled(
        "COORD Demo",
        styles,
        Div(
            H1("Resolve COORD"),
            header,
            P("Paste a Web4 Coordinate and submit it to the middleware resolver."),
            Form(
                Textarea(
                    name="coordinate",
                    placeholder='loam:WX-A71BA232-1784308498',
                    rows=3,
                    style="width:100%;",
                ),
                Button("Resolve", type="submit"),
                action="/resolve",
                method="post",
            ),
            Div(Pre(id="result"), id="result-container"),
            style="max-width: 900px; margin: 0 auto; padding: 0 1rem;",
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
        decoded = response.json()
        return _render_decode_result(decoded)
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
