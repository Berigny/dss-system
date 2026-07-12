"""Home page route for 'ourIP.AI' Threadless UI."""

# 1. EXPANDED IMPORTS: We need all these for the new layout
import time
import json

import httpx

from fasthtml.common import Button, Div, Form, Input, P, Span, Textarea
from starlette.exceptions import HTTPException
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import StreamingResponse

from api.client import api
from components.chat import assistant_message, render_history, user_message
from urllib.parse import quote
from components.layout import page_shell
from config.settings import DEFAULT_SESSION_ID, settings
from utils.session import (
    build_entity_namespace,
    get_session,
    hash_ledger_id,
    hash_session_id,
    update_session,
)
from utils.stats import build_stats_payload


# Embedded frontend sanitization helper surfaced in the home route markup.
SANITIZE_RESPONSE_TEXT = r"""
function sanitizeResponseText(text) {
    if (typeof text !== "string") {
        return text;
    }
    return text.replace(/```json[\s\S]*?```/g, "");
}
"""


async def conversation_history(entity: str, request: Request):
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
    try:
        limit = int(request.query_params.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50

    api.set_ledger(ledger_id)

    try:
        history = await api.thread(entity=entity, limit=limit)
    except Exception as exc:  # pragma: no cover - passthrough render
        print(f"History fetch failed: {exc}")
        history = []

    if isinstance(history, dict):
        history = history.get("history") or history.get("messages") or []

    rendered_history = render_history(history) if isinstance(history, list) else []

    if not rendered_history:
        rendered_history = [
            Div(
                "No conversation yet. Start chatting to see it here.",
                cls="text-sm text-gray-500 text-center py-6",
            )
        ]

    return Div(
        *rendered_history,
        cls="flex flex-col",
        id="history-list",
        data_role="history-list",
    )


def _form_text(value, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default

def _estimate_turn_cost(stats: dict, tokens: dict | None) -> float:
    token_data = {}
    if isinstance(stats.get("tokens"), dict):
        token_data = stats.get("tokens", {})
    if isinstance(tokens, dict):
        token_data = {**token_data, **tokens}
    prompt = token_data.get("prompt", 0) or 0
    completion = token_data.get("completion", 0) or 0
    try:
        prompt_tokens = int(prompt)
    except (TypeError, ValueError):
        prompt_tokens = 0
    try:
        completion_tokens = int(completion)
    except (TypeError, ValueError):
        completion_tokens = 0
    return (prompt_tokens * 5.0 + completion_tokens * 15.0) / 1_000_000


def register_home_routes(rt):
    @rt("/")
    async def home(request: Request):
        session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
        session = get_session(session_id)
        ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
        session_entity = session.get("entity") or build_entity_namespace(
            ledger_id,
            session_id,
        )
        backend_stream_enabled = bool(session.get("backend_stream_enabled", False))
        ledger_hash = hash_ledger_id(ledger_id)
        session_hash = hash_session_id(ledger_id, session_id)
        
        history_entity_path = quote(session_entity, safe="")
        # --- THE MAIN LAYOUT ---
        main_content = Div(
            Div(
                # 1. Silent Alarm (Backend Wake-up)
                Div(hx_get="/api/wake", hx_trigger="load", hx_swap="none", style="display:none"),

                # 2. Input Area (Top)
                Div(
                    Form(
                        Textarea(
                            "",
                            id="cmd-input",
                            name="cmd-input",
                            placeholder="Say something...",
                            rows=1,
                            cls=(
                                "w-full bg-transparent border-b border-gray-200 p-4 text-center "
                                "font-serif text-xl text-gray-900 placeholder-gray-400 "
                                "focus:outline-none focus:border-black transition-all "
                                "resize-none overflow-hidden leading-relaxed"
                            ),
                            autocomplete="off",
                            autofocus=True,
                            enterkeyhint="send",
                        ),
                        Input(
                            type="file",
                            id="attachment-input",
                            name="file",
                            accept=".txt,.md,.csv,.json,.log,.yaml,.yml,.xml,.pdf,.docx,.html,.htm",
                            style="display:none;",
                            onchange="uploadAttachment(this)",
                        ),
                        Div(
                            Button(
                                "",
                                type="button",
                                cls="action-btn secondary plus-button",
                                onclick="document.getElementById('attachment-input').click()",
                                aria_label="Add attachment",
                            ),
                            cls="input-actions",
                        ),
                        Textarea(
                            "",
                            id="agent-instructions",
                            name="agent-instructions",
                            style="display:none;"
                        ),
                        id="chat-form",
                    ),
                    Div(
                        "",
                        id="attachment-coordinate-list",
                        cls="attachment-coordinate-list",
                    ),
                    id="input-shell",
                    cls=(
                        "sticky top-0 z-30 bg-white"
                    )
                ),

                id="landing-zone",
            ),

            Div(
                Div(
                    Div(cls="side triangle"),
                    Div(cls="side triangle"),
                    Div(cls="side triangle"),
                    Div(cls="side triangle"),
                    cls="polyhedron tetrahedron",
                ),
                Div("Initializing...", id="loading-status", cls="status-ticker fade-in"),
                id="loading-overlay",
                cls="loading-overlay",
            ),

            Div(
                Div(
                    P("Loading history..."),
                    id="history-list",
                    data_role="history-list",
                    cls="flex flex-col",
                ),
                Div(
                    "",
                    id="history-loader",
                    hx_get=f"/ui/history/{history_entity_path}?limit=50",
                    hx_trigger="load",
                    hx_target="#history-list",
                    hx_swap="outerHTML",
                    data_history_limit="50",
                    data_history_step="50",
                    data_history_entity=session_entity,
                ),
                id="chat-stream",
                cls="loading-history flex flex-col pt-48 px-4 pb-20 transition-opacity duration-300 z-0",
            ),

            cls="main"
        )

        return page_shell(
            main_content,
            session_id=session_id,
            ledger_id=ledger_id,
            entity=session_entity,
            ledger_hash=ledger_hash,
            session_hash=session_hash,
            backend_stream_enabled=backend_stream_enabled,
        )

    @rt("/chat_turn")
    async def chat_turn(request: Request):
        form = await request.form()
        message = _form_text(form.get("cmd-input"))
        provider_value = form.get("agent") or form.get("provider") or settings.LLM_MODEL
        provider = _form_text(provider_value, settings.LLM_MODEL)
        agent_instructions = _form_text(form.get("agent-instructions"))
        session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
        session = get_session(session_id)
        ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
        entity = session.get("entity") or build_entity_namespace(ledger_id, session_id)

        api.set_ledger(ledger_id)

        if not message:
            return ()

        start_time = time.time()
        try:
            response = await api.chat(
                message=message,
                provider=provider,
                session_id=session_id,
                entity=entity,
            )
        except Exception as exc:  # pragma: no cover - passthrough render
            print(f"Chat failed: {exc}")
            return ()

        stats = response.stats or {}
        if not isinstance(stats, dict):
            stats = {}

        latency = stats.get("last_latency") or stats.get("latency_ms") or stats.get("latency") or 0
        memory_count = stats.get("memory_count") or stats.get("memories") or stats.get("entry_count") or 0
        cost = (
            stats.get("cost")
            or stats.get("total_cost")
            or stats.get("cost_usd")
            or response.cost_usd
            or 0.0
        )
        model = response.model or stats.get("model") or provider
        knowledge_tree = response.knowledge_tree or []
        coordinate = response.coordinate

        if not latency:
            latency = int((time.time() - start_time) * 1000)

        cost_delta = stats.get("cost") or stats.get("cost_usd") or response.cost_usd
        total_cost = stats.get("total_cost")
        if cost_delta is None and total_cost is not None:
            cost_delta = total_cost
        if cost_delta is None:
            cost_delta = _estimate_turn_cost(stats, response.tokens)

        try:
            latency_ms = int(latency)
        except (TypeError, ValueError):
            latency_ms = 0

        if cost_delta is not None:
            try:
                cost_value = float(cost_delta)
            except (TypeError, ValueError):
                cost_value = 0.0
        else:
            cost_value = 0.0

        session["last_latency_ms"] = latency_ms
        if total_cost is not None and cost_delta == total_cost:
            session["total_cost"] = cost_value
        else:
            session["total_cost"] = session.get("total_cost", 0.0) + cost_value
        update_session(session_id, session)

        metadata = dict(response.metadata or {})
        if response.appraisal is not None:
            metadata.setdefault("appraisal", response.appraisal)
        metadata.update(
            {
                "stats": stats,
                "knowledge_tree": knowledge_tree,
                "coordinate": coordinate,
                "model": model,
                "agent_instructions": agent_instructions,
            }
        )

        assistant_text = response.primary_text or ""
        timestamp = metadata.get("timestamp") or stats.get("timestamp") or time.time()

        msg_id = int(time.time() * 1000)
        bubbles = [
            user_message(message, msg_id),
            assistant_message(
                assistant_text,
                msg_id + 1,
                latency,
                float(cost) if cost is not None else 0.0,
                int(memory_count) if memory_count is not None else 0,
                model,
                timestamp,
                coordinate,
                response.web4_key,
                knowledge_tree,
                metadata,
            ),
        ]

        return tuple(bubbles)

    @rt("/ui/stats")
    async def stats(request: Request):
        return await build_stats_payload(request)

    @rt("/api/ingest/stream-file", methods=["POST"])
    async def ingest_stream_file(request: Request):
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="file is required")

        session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
        session = get_session(session_id)
        entity = session.get("entity") or f"chat-{session_id}"
        ledger_id = session.get("ledger_id", settings.DEFAULT_LEDGER_ID)
        api.set_ledger(ledger_id)

        data = {}
        for key, value in form.multi_items():
            if key == "file":
                continue
            data[key] = value
        data.setdefault("entity", entity)
        data.setdefault("kind", "attachment")

        files = {
            "file": (
                upload.filename or "attachment",
                upload.file,
                upload.content_type or "application/octet-stream",
            )
        }

        url = f"{settings.API_BASE.rstrip('/')}/api/ingest/file"
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
                    yield json.dumps(
                        {
                            "type": "error",
                            "detail": resp.text or f"Upload failed ({resp.status_code})",
                        }
                    ) + "\n"
                    return
                payload = resp.json() if resp.content else {}
                coordinate = payload.get("coordinate") or payload.get("coord")
                yield json.dumps(
                    {
                        "type": "meta",
                        "coordinate": coordinate,
                        "entity": data.get("entity"),
                    }
                ) + "\n"

        return StreamingResponse(_stream(), media_type="application/x-ndjson")

    rt("/ui/history/{entity}")(conversation_history)
