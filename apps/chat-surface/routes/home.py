"""Home page route for 'ourIP.AI' Threadless UI."""

# 1. EXPANDED IMPORTS: We need all these for the new layout
import asyncio
import time
import json
import hashlib
import re

import httpx
from datetime import datetime, timezone
from typing import Any
from fastcore.xml import FT

from fasthtml.common import Button, Div, Form, H1, Input, NotStr, P, Span, Textarea
from starlette.exceptions import HTTPException
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import RedirectResponse, StreamingResponse

from api.client import api
from components.chat import assistant_message, render_history, user_message
from urllib.parse import quote
from components.layout import page_shell
from config.settings import DEFAULT_SESSION_ID, settings
from utils.session import (
    build_entity_namespace,
    get_session,
    update_session,
)
from utils.stats import build_stats_payload
from utils.coordinates import _parse_timestamp

HISTORY_ALL_DISCOVERY_TIMEOUT_SECONDS = 4.0
HISTORY_ALL_THREAD_TIMEOUT_SECONDS = 4.0
HISTORY_ALL_SYNC_TIMEOUT_SECONDS = 4.0
HISTORY_ALL_MAX_ENTITIES = 6
HISTORY_ALL_DISCOVERY_ENTITY_LIMIT = 100
HISTORY_ALL_DISCOVERY_ENTRY_LIMIT = 100


SANITIZE_RESPONSE_SCRIPT = r"""
function sanitizeResponseText(text) {
    return text.replace(/```json[\s\S]*?```/g, '');
}
"""


def _entity_from_coord(coord: str | None) -> str:
    raw = str(coord or "").strip()
    if not raw:
        return ""
    parts = [part for part in raw.split(":") if part]
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return ""


def _entity_from_entry(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ""

    key = entry.get("key")
    if isinstance(key, dict):
        namespace = str(key.get("namespace") or "").strip()
        if namespace:
            return namespace
    elif isinstance(key, str):
        derived = _entity_from_coord(key)
        if derived:
            return derived

    for field in ("entry_id", "coordinate", "coord"):
        value = entry.get(field)
        if isinstance(value, str):
            derived = _entity_from_coord(value)
            if derived:
                return derived

    state_raw = entry.get("state")
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

    nested_entry_raw = entry.get("entry")
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
    if text.startswith("chat-"):
        return True
    return bool(_CHAT_ENTITY_PATTERN.match(text))


def _resolve_session_scope(session_id: str, session: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Normalize session ledger/entity for demo ledger mode."""

    desired_ledger = str(session.get("ledger_id") or getattr(settings, "DEFAULT_LEDGER_ID", "") or "").strip()
    if not desired_ledger:
        desired_ledger = "LOAM"
    demo_ledger = str(getattr(settings, "DEFAULT_LEDGER_ID", "") or "").strip()
    if desired_ledger == "default" and demo_ledger:
        desired_ledger = demo_ledger

    desired_entity = build_entity_namespace(desired_ledger, session_id)
    if session.get("ledger_id") != desired_ledger or session.get("entity") != desired_entity:
        session = dict(session)
        session["ledger_id"] = desired_ledger
        session["entity"] = desired_entity
        update_session(session_id, session)

    return desired_ledger, desired_entity, session


def _coordinate_from_entry(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ""

    for field in ("coordinate", "coord", "entry_id"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()

    key = entry.get("key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    if isinstance(key, dict):
        namespace = str(key.get("namespace") or "").strip()
        identifier = str(key.get("identifier") or "").strip()
        if namespace and identifier:
            return f"{namespace}:{identifier}"
    return ""


def _message_from_entry(entry: dict) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None

    state_raw = entry.get("state")
    state: dict[str, Any] = state_raw if isinstance(state_raw, dict) else {}
    metadata_raw = state.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}

    role = str(
        metadata.get("role")
        or metadata.get("speaker")
        or entry.get("role")
        or entry.get("speaker")
        or entry.get("source")
        or ""
    ).strip().lower()
    if role not in {"assistant", "user"}:
        if isinstance(metadata.get("user_message"), str) and not isinstance(metadata.get("assistant_reply"), str):
            role = "user"
        else:
            role = "assistant"

    content = (
        metadata.get("content")
        or metadata.get("assistant_reply")
        or metadata.get("user_message")
        or metadata.get("msg")
        or metadata.get("text")
        or entry.get("text")
        or entry.get("message")
        or entry.get("body")
        or ""
    )
    if not isinstance(content, str) or not content.strip():
        return None

    coord = _coordinate_from_entry(entry)
    entity = _entity_from_entry(entry) or _entity_from_coord(coord)

    message_meta: dict[str, Any] = {}
    if isinstance(metadata, dict):
        message_meta.update(metadata)
    if coord and not message_meta.get("coordinate"):
        message_meta["coordinate"] = coord
    if entity and not message_meta.get("entity"):
        message_meta["entity"] = entity

    return {
        "role": role,
        "content": content.strip(),
        "coordinate": coord,
        "timestamp": (
            entry.get("created_at")
            or metadata.get("created_at")
            or metadata.get("timestamp")
            or metadata.get("ts")
        ),
        "metadata": message_meta,
    }


def _coerce_history_list(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        nested = payload.get("history") or payload.get("messages") or []
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def _hash64(text: str) -> str:
    raw = hashlib.blake2b(str(text or "").encode("utf-8"), digest_size=8).digest()
    return raw.hex()


def _decode_sync_payload(envelope_hex: str) -> dict[str, Any]:
    value = str(envelope_hex or "").strip()
    if not value:
        return {}
    try:
        raw = bytes.fromhex(value)
    except Exception:
        return {}
    if len(raw) < 18:
        return {}
    payload_len = int.from_bytes(raw[16:18], "big")
    if len(raw) < 18 + payload_len:
        return {}
    payload_bytes = raw[18 : 18 + payload_len]
    payload_text = payload_bytes.decode("utf-8", errors="ignore")
    payload_json: dict[str, Any] | None = None
    try:
        parsed = json.loads(payload_text)
        if isinstance(parsed, dict):
            payload_json = parsed
    except Exception:
        payload_json = None
    return {
        "payload_text": payload_text,
        "payload_json": payload_json,
    }


def _message_from_sync_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    decoded = _decode_sync_payload(str(item.get("envelope_hex") or ""))
    payload_json_raw = decoded.get("payload_json")
    payload_json: dict[str, Any] = payload_json_raw if isinstance(payload_json_raw, dict) else {}
    payload_text = str(decoded.get("payload_text") or "").strip()

    role = str(payload_json.get("role") or "").strip().lower()
    if role not in {"assistant", "user"}:
        if isinstance(payload_json.get("user_message"), str) and not isinstance(payload_json.get("assistant_reply"), str):
            role = "user"
        else:
            role = "assistant"

    content = ""
    for key in ("content", "assistant_reply", "user_message", "msg", "text"):
        value = payload_json.get(key)
        if isinstance(value, str) and value.strip():
            content = value.strip()
            break
    if not content:
        content = payload_text or json.dumps(payload_json, separators=(",", ":"), sort_keys=True)
    if not content:
        return None

    stream_key = str(item.get("stream_key") or "").strip()
    event_id = str(item.get("event_id") or "").strip().lower()
    seq = item.get("seq")

    entity = ""
    if stream_key:
        parts = [part for part in stream_key.split(":") if part]
        if len(parts) >= 2:
            entity = f"{parts[0]}:{parts[1]}"

    metadata: dict[str, Any] = {
        "source": "sync_v0",
        "event_id": event_id,
        "stream_key": stream_key,
        "seq": seq,
    }
    status_value = str(item.get("status") or "").strip().lower()
    if status_value:
        metadata["sync_state"] = status_value
    else:
        metadata["sync_state"] = "synced"
    if isinstance(payload_json, dict) and payload_json:
        metadata["sync_payload"] = payload_json
    if entity:
        metadata["entity"] = entity

    return {
        "role": role,
        "content": content,
        "coordinate": event_id or stream_key,
        "timestamp": item.get("created_at"),
        "metadata": metadata,
    }


def _sync_event_key(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return ""
    meta_raw = message.get("metadata")
    meta: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
    event_id = str(meta.get("event_id") or "").strip().lower()
    if event_id:
        return event_id
    stream_key = str(meta.get("stream_key") or "").strip().lower()
    seq_value = meta.get("seq")
    if stream_key and isinstance(seq_value, (int, float, str)):
        seq_text = str(seq_value).strip()
        if seq_text:
            return f"{stream_key}:{seq_text}"
    return ""


def _is_smoke_history_entry(message: dict[str, Any]) -> bool:
    if not isinstance(message, dict):
        return False
    metadata_raw = message.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    provider = str(metadata.get("provider") or "").strip().lower()
    model = str(metadata.get("model") or "").strip().lower()
    source = str(metadata.get("source") or message.get("source") or "").strip().lower()
    reason = str(metadata.get("reason") or "").strip().lower()
    text = str(message.get("content") or message.get("text") or "").strip().lower()
    if source == "acceptance":
        return True
    if provider == "smoke" or model == "smoke":
        return True
    if reason == "post-reset-check":
        return True
    if text == "post reset known ledger write":
        return True
    return False


async def _fetch_sync_messages_for_ledger(ledger_id: str, limit: int) -> list[dict[str, Any]]:
    ledger_h64 = _hash64(ledger_id or "default")
    payload = {
        "peer_id": "frontend-all-history",
        "ledger_id_h64": ledger_h64,
        "cursors": {},
        "limit": max(10, min(limit, 500)),
    }
    url = f"{api.base_url}/sync/v0/pull"
    try:
        async with httpx.AsyncClient(timeout=api.timeout) as client:
            resp = await client.post(url, json=payload, headers=api.headers)
            if resp.status_code >= 400:
                return []
            body = resp.json() if resp.content else {}
    except Exception:
        return []

    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        msg = _message_from_sync_item(item)
        if msg:
            rows.append(msg)
    return rows


async def conversation_history(entity: str, request: Request):
    session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
    session = get_session(session_id)
    ledger_id, session_entity, session = _resolve_session_scope(session_id, session)
    try:
        limit = int(request.query_params.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 500))

    api.set_ledger(ledger_id)

    history: list[dict] = []
    selected_entity = (entity or "").strip()

    if selected_entity == "__all__":
        entities: list[str] = []
        seen_entities: set[str] = set()

        if session_entity and _is_chat_entity(session_entity):
            entities.append(session_entity)
            seen_entities.add(session_entity)

        try:
            discovered_payload = await asyncio.wait_for(
                api.get_history_entities(limit=max(limit * 4, HISTORY_ALL_DISCOVERY_ENTITY_LIMIT)),
                timeout=HISTORY_ALL_DISCOVERY_TIMEOUT_SECONDS,
            )
        except Exception:
            discovered_payload = {}
        discovered_entities = (
            discovered_payload.get("entities")
            if isinstance(discovered_payload, dict)
            else []
        )
        if isinstance(discovered_entities, list):
            for value in discovered_entities:
                candidate = str(value or "").strip()
                if not _is_chat_entity(candidate) or candidate in seen_entities:
                    continue
                seen_entities.add(candidate)
                entities.append(candidate)

        try:
            all_entries = await asyncio.wait_for(
                api.get_all_entries(limit=max(limit * 4, HISTORY_ALL_DISCOVERY_ENTRY_LIMIT)),
                timeout=HISTORY_ALL_DISCOVERY_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # pragma: no cover - passthrough render
            print(f"All entries fetch failed: {exc}")
            all_entries = []

        raw_entries: list[dict] = []
        if isinstance(all_entries, list):
            raw_entries = [item for item in all_entries if isinstance(item, dict)]
        elif isinstance(all_entries, dict):
            for key in ("entries", "items", "recent", "results", "history", "messages"):
                value = all_entries.get(key)
                if isinstance(value, list):
                    raw_entries.extend([item for item in value if isinstance(item, dict)])

        for entry in raw_entries:
            candidate = _entity_from_entry(entry)
            if not _is_chat_entity(candidate) or candidate in seen_entities:
                continue
            seen_entities.add(candidate)
            entities.append(candidate)

        merged: list[dict[str, Any]] = []
        for entity_name in entities[:HISTORY_ALL_MAX_ENTITIES]:
            try:
                entity_history = await asyncio.wait_for(
                    api.thread(entity=entity_name, limit=limit),
                    timeout=HISTORY_ALL_THREAD_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # pragma: no cover - passthrough render
                print(f"History fetch failed for {entity_name}: {exc}")
                continue
            for item in _coerce_history_list(entity_history):
                row: dict[str, Any] = dict(item)
                metadata_raw = row.get("metadata")
                metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
                if metadata.get("entity") in (None, ""):
                    metadata = dict(metadata)
                    metadata["entity"] = entity_name
                row["metadata"] = metadata
                merged.append(row)

        seen_coords = {
            str(item.get("coordinate") or "").strip()
            for item in merged
            if isinstance(item, dict) and str(item.get("coordinate") or "").strip()
        }
        for entry in raw_entries:
            normalized = _message_from_entry(entry)
            if not normalized:
                continue
            coord = str(normalized.get("coordinate") or "").strip()
            if coord and coord in seen_coords:
                continue
            if coord:
                seen_coords.add(coord)
            merged.append(normalized)

        try:
            sync_messages = await asyncio.wait_for(
                _fetch_sync_messages_for_ledger(ledger_id=ledger_id, limit=max(limit * 3, 50)),
                timeout=HISTORY_ALL_SYNC_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # pragma: no cover - passthrough render
            print(f"Sync message fetch failed: {exc}")
            sync_messages = []
        seen_sync_ids = {
            key
            for key in (_sync_event_key(item) for item in merged if isinstance(item, dict))
            if key
        }
        for msg in sync_messages:
            event_key = _sync_event_key(msg)
            if event_key and event_key in seen_sync_ids:
                continue
            if event_key:
                seen_sync_ids.add(event_key)
            merged.append(msg)

        history = merged
    else:
        target_entity = selected_entity or session_entity
        try:
            payload = await api.thread(entity=target_entity, limit=limit)
        except Exception as exc:  # pragma: no cover - passthrough render
            print(f"History fetch failed: {exc}")
            payload = []
        history = _coerce_history_list(payload)

    if isinstance(history, list):
        history = [
            item
            for item in history
            if isinstance(item, dict) and not _is_smoke_history_entry(item)
        ]
        history.sort(
            key=lambda msg: (
                _parse_timestamp(
                    msg.get("timestamp")
                    or msg.get("ts")
                    or msg.get("time")
                    or msg.get("created_at")
                    or (msg.get("metadata", {}) if isinstance(msg.get("metadata"), dict) else {}).get("created_at")
                )
                or datetime.fromtimestamp(0, tz=timezone.utc)
            ),
            reverse=True,
        )
        if limit > 0:
            cutoff = min(limit, len(history))
            # Avoid splitting adjacent user/assistant turns across lazy-load pages.
            if 0 < cutoff < len(history):
                left_role = str(history[cutoff - 1].get("role") or "").strip().lower()
                right_role = str(history[cutoff].get("role") or "").strip().lower()
                if {left_role, right_role} == {"user", "assistant"}:
                    cutoff += 1
            history = history[:cutoff]

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


def _strip_bool_fragments(payload: Any) -> Any:
    """Guard against FastHTML/fastcore bool fragments in rendered node trees."""
    if isinstance(payload, bool):
        return None

    if isinstance(payload, FT):
        attrs = {}
        for key, value in dict(payload.attrs or {}).items():
            if value is None:
                continue
            if isinstance(value, bool):
                attrs[key] = str(value).lower()
            else:
                attrs[key] = value

        children = []
        for child in tuple(payload.children or ()):  # type: ignore[arg-type]
            cleaned = _strip_bool_fragments(child)
            if cleaned is not None:
                children.append(cleaned)

        node = FT(payload.tag, tuple(children), attrs, void_=payload.void_)
        node.listeners_ = list(getattr(payload, "listeners_", []))
        return node

    if isinstance(payload, tuple):
        return tuple(
            cleaned for item in payload if (cleaned := _strip_bool_fragments(item)) is not None
        )

    if isinstance(payload, list):
        return [
            cleaned for item in payload if (cleaned := _strip_bool_fragments(item)) is not None
        ]

    return payload


def register_home_routes(rt):
    @rt("/")
    async def home(request: Request):
        session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
        session = get_session(session_id)
        ledger_id, session_entity, session = _resolve_session_scope(session_id, session)
        backend_stream_enabled = bool(session.get("backend_stream_enabled", False))
        
        history_entity_path = quote("__all__", safe="")
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
                            autofocus="autofocus",
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
                    Div(
                        NotStr('<div class="ds-spinner"><svg viewBox="0 0 44 44"><circle cx="22" cy="22" r="20"></circle></svg></div>'),
                        id="attachment-spinner",
                        cls="attachment-spinner",
                        style="display:none;",
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
                    Div(
                        P("Loading history..."),
                        id="history-list",
                        data_role="history-list",
                        cls="flex flex-col",
                    ),
                    Div(
                        NotStr('<div class="ds-spinner"><svg viewBox="0 0 44 44"><circle cx="22" cy="22" r="20"></circle></svg></div>'),
                        id="history-spinner",
                        cls="history-spinner",
                        style="display:none;",
                    ),
                    cls="history-wrap",
                ),
                Div(
                    "",
                    id="history-loader",
                    data_history_limit="5",
                    data_history_step="5",
                    data_history_entity=session_entity,
                ),
                id="chat-stream",
                cls="loading-history flex flex-col pt-36 md:pt-48 px-4 pb-20 transition-opacity duration-300 z-0",
            ),

            cls="main"
        )

        shell = page_shell(
            main_content,
            session_id=session_id,
            ledger_id=ledger_id,
            entity=session_entity,
            backend_stream_enabled=backend_stream_enabled,
        )
        return _strip_bool_fragments(shell)

    @rt("/account/setup")
    async def account_setup_page(request: Request):
        return RedirectResponse(url=f"{settings.CONTROL_PLANE_BASE.rstrip('/')}/account/setup", status_code=303)

    @rt("/chat_turn")
    async def chat_turn(request: Request):
        form = await request.form()
        message = _form_text(form.get("cmd-input"))
        provider_value = form.get("agent") or form.get("provider") or settings.LLM_MODEL
        provider = _form_text(provider_value, settings.LLM_MODEL)
        agent_instructions = _form_text(form.get("agent-instructions"))
        session_id = request.cookies.get("ds_session", DEFAULT_SESSION_ID)
        session = get_session(session_id)
        ledger_id, entity, session = _resolve_session_scope(session_id, session)

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
        ledger_id, canonical_entity, session = _resolve_session_scope(session_id, session)
        entity = str(canonical_entity).strip()
        api.set_ledger(ledger_id)

        data = {}
        for key, value in form.multi_items():
            if key == "file":
                continue
            data[key] = value
        data.setdefault("entity", entity)
        data.setdefault("kind", "attachment")
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
