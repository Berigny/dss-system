"""Chat-related UI components."""

from __future__ import annotations

import re
from typing import Any, Iterable

from fasthtml.common import Div, Span

from utils.coordinates import format_coordinate

# `fasthtml` does not expose a concrete Tag type; use `Any` for type hints.
Tag = Any


def render_history(messages: Iterable[dict]) -> list[Tag]:
    """Render stored chat messages using the rich bubble components."""

    rendered = []
    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            latency, cost, memory_count, model_id = _extract_stats(msg)
            metadata = msg.get("metadata", {})
            rendered.append(
                assistant_message(
                    msg.get("content", ""),
                    idx,
                    latency,
                    cost,
                    memory_count,
                    model_id,
                    msg.get("timestamp") or msg.get("ts") or msg.get("time"),
                    msg.get("coordinate")
                    or metadata.get("coordinate"),
                    msg.get("id")
                    or msg.get("message_id")
                    or msg.get("guid")
                    or idx,
                    metadata.get("knowledge_tree"),
                    metadata,
                )
            )
        else:
            rendered.append(user_message(msg.get("content", ""), idx, msg.get("metadata")))
    return rendered


def user_message(message: str, msg_id: int, metadata: dict[str, Any] | None = None) -> Tag:
    """Render a user message bubble for the chat stream."""
    badges = _build_message_badges(metadata)
    attribution = _message_attribution_text(metadata, role="user")
    children: list[Any] = []
    if badges:
        children.append(Div(*badges, cls="meta"))
    if attribution:
        children.append(Div(attribution, cls="meta"))
    children.append(Div(message, cls="message-content break-words"))
    return Div(
        *children,
        cls="message user",
        id=f"msg-user-{msg_id}",
    )


def assistant_message(
    content: str,
    msg_id: int,
    latency: int,
    cost: float,
    memory_count: int,
    model: str | None,
    timestamp: Any | None = None,
    coordinate: str | None = None,
    message_identifier: Any | None = None,
    knowledge_tree: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Tag:
    """Render an assistant reply with metadata."""

    message_identifier = msg_id if message_identifier is None else message_identifier
    metadata = metadata or {}
    latency_seconds = max(latency, 0) / 1000
    model_text = model or "Agent"
    timestamp_display, coordinate_text = format_coordinate(
        timestamp=timestamp,
        coordinate=coordinate,
        message_id=message_identifier,
        content=content,
    )
    walk_ids = []
    if isinstance(metadata, dict):
        walk_ids = metadata.get("walk_ids") or []
    meta_children: list[Any] = [
        f"{timestamp_display} | ",
        Span(
            coordinate_text,
            cls="coordinate",
            data_coordinate=coordinate_text,
            onclick="navigator.clipboard.writeText(this.dataset.coordinate)",
            style="text-decoration: underline; cursor: pointer;",
            title="Copy coordinate",
        ),
    ]
    if isinstance(walk_ids, list) and walk_ids:
        walk_coord = str(walk_ids[0])
        meta_children.append(" | ")
        meta_children.append(
            Span(
                walk_coord,
                cls="coordinate",
                data_coordinate=walk_coord,
                onclick="navigator.clipboard.writeText(this.dataset.coordinate)",
                style="text-decoration: underline; cursor: pointer;",
                title="Copy coordinate",
            )
        )
    if model:
        meta_children.append(f" | {model}")
    attribution = _message_attribution_text(metadata, role="assistant")
    if attribution:
        meta_children.append(f" | {attribution}")
    integrity = _answer_surface_integrity_text(metadata)
    if integrity:
        meta_children.append(f" | {integrity}")
    resolver = _public_object_resolution_text(metadata)
    if resolver:
        meta_children.append(f" | {resolver}")
    badges = _build_message_badges(metadata)
    if badges:
        meta_children.append(" | ")
        meta_children.extend(badges)
    bubble = Div(
        Div(
            Div(
                content,
                cls=(
                    "prose prose-xl prose-p:font-serif prose-headings:font-serif markdown-content "
                    "max-w-none text-gray-900 leading-loose break-words"
                ),
                data_markdown="true",
            ),
            cls="message-content",
        ),
        Div(*meta_children, cls="meta"),
        cls="message assistant fade-in-up",
    )

    return Div(
        bubble,
        id=f"msg-assistant-{msg_id}",
    )


def _build_message_badges(metadata: dict[str, Any] | None) -> list[Tag]:
    meta = metadata if isinstance(metadata, dict) else {}
    source = str(meta.get("source") or "").strip().lower()
    sync_state = str(
        meta.get("sync_state")
        or meta.get("status")
        or meta.get("sync_status")
        or ""
    ).strip().lower()
    badges: list[Tag] = []
    if source == "sync_v0":
        badges.append(Span("sync", cls="inline-block text-[10px] font-semibold uppercase tracking-wide text-cyan-700"))
    if sync_state in {"queued", "pending"}:
        badges.append(Span(sync_state, cls="inline-block text-[10px] font-semibold uppercase tracking-wide text-amber-700"))
    elif sync_state in {"quarantine", "quarantined", "failed"}:
        badges.append(Span("quarantine", cls="inline-block text-[10px] font-semibold uppercase tracking-wide text-rose-700"))
    return badges


def _message_attribution_text(metadata: dict[str, Any] | None, *, role: str) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    prompt = _prompt_principal_label(meta)
    requested_by = _requested_by_label(meta)
    answered_by = _response_model_label(meta) if role == "assistant" else ""
    parts: list[str] = []
    if prompt:
        parts.append(f"asked by: {prompt}")
    if requested_by:
        parts.append(f"requested by: {requested_by}")
    if answered_by:
        parts.append(f"answered by: {answered_by}")
    return " | ".join(parts)


def _answer_surface_integrity_text(metadata: dict[str, Any] | None) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    integrity = meta.get("answer_surface_integrity") if isinstance(meta.get("answer_surface_integrity"), dict) else {}
    status = str(integrity.get("status") or "").strip().lower()
    reason = str(integrity.get("reason") or "").strip().lower()
    if status == "diverged" and reason == "assembly_summary_richer_than_visible_answer":
        return "summary richer than visible answer"
    if status == "collapsed" and reason == "visible_answer_preamble_collapse_under_blocked_context":
        return "visible answer collapsed under blocked context"
    if status:
        return f"answer integrity: {status}"
    return ""


def _public_object_resolution_text(metadata: dict[str, Any] | None) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    sources: list[dict[str, Any]] = []
    for candidate in (
        meta,
        meta.get("decision_artifact_identity"),
        meta.get("public_object"),
        meta.get("resolver"),
        meta.get("evidence"),
    ):
        if isinstance(candidate, dict):
            sources.append(candidate)

    def _first_text(*keys: str) -> str:
        for source in sources:
            for key in keys:
                text = str(source.get(key) or "").strip()
                if text:
                    return text
        return ""

    public_object_id = _first_text("public_object_id", "publicObjectId", "id")
    resolver_ref = _first_text("resolverRef", "resolver_ref")
    resolver_url = _first_text("resolverUrl", "resolver_url")
    native_coord_state = _first_text("nativeCoordState", "native_coord_state")

    parts: list[str] = []
    if public_object_id:
        parts.append(f"public object: {public_object_id}")
    if resolver_ref:
        parts.append(f"resolver: {resolver_ref}")
    elif resolver_url:
        parts.append(f"resolver: {resolver_url}")
    if native_coord_state:
        parts.append(f"native coord: {native_coord_state}")
    return " | ".join(parts)


def _prompt_principal_label(metadata: dict[str, Any] | None) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    delegated = meta.get("delegated_prompt_path") if isinstance(meta.get("delegated_prompt_path"), dict) else {}
    contributor = meta.get("contributor") if isinstance(meta.get("contributor"), dict) else {}
    principal_display_name = str(
        delegated.get("prompt_principal_display_name")
        or contributor.get("principal_display_name")
        or meta.get("principal_display_name")
        or ""
    ).strip()
    if principal_display_name:
        return principal_display_name
    explicit_label = str(meta.get("prompt_principal_label") or "").strip()
    if explicit_label:
        return explicit_label
    principal_type = str(
        delegated.get("prompt_principal_type")
        or contributor.get("principal_type")
        or ""
    ).strip().lower()
    principal_id = str(
        delegated.get("prompt_principal_id")
        or contributor.get("principal_id")
        or ""
    ).strip()
    principal_did = str(
        delegated.get("prompt_principal_did")
        or contributor.get("principal_did")
        or ""
    ).strip()
    return _principal_label(principal_type, principal_id, principal_did)


def _requested_by_label(metadata: dict[str, Any] | None) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    delegated = meta.get("delegated_prompt_path") if isinstance(meta.get("delegated_prompt_path"), dict) else {}
    return str(delegated.get("requested_by_principal_did") or "").strip()


def _response_model_label(metadata: dict[str, Any] | None) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    for candidate in (
        meta.get("model_id"),
        meta.get("model"),
        meta.get("provider_id"),
        meta.get("provider"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _principal_label(principal_type: str, principal_id: str, principal_did: str) -> str:
    if principal_type == "agent":
        if principal_id.startswith("openai:agent:"):
            agent_name = principal_id[len("openai:agent:") :].strip()
            if agent_name:
                return f"openai/{agent_name}"
        if principal_id.startswith("openai:"):
            agent_name = principal_id[len("openai:") :].strip()
            if agent_name:
                return f"openai/{agent_name}"
    if principal_id.startswith("openai:agent:"):
        agent_name = principal_id[len("openai:agent:") :].strip()
        if agent_name:
            return f"openai/{agent_name}"
    if principal_id.startswith("openai:"):
        agent_name = principal_id[len("openai:") :].strip()
        if agent_name:
            return f"openai/{agent_name}"
    marker = ":principals:agent:openai:"
    if marker in principal_did:
        agent_name = principal_did.split(marker, 1)[1].strip()
        if agent_name:
            return f"openai/{agent_name}"
    if principal_type in {"user", "did", "principal"} and principal_id:
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", principal_id):
            return principal_id.replace("-", " ").title()
    return principal_id or principal_did


def _extract_stats(msg: dict) -> tuple[int, float, int, str | None]:
    """Pull latency, cost, and memory counts from a ledger message."""

    stats = msg.get("metadata", {}).get("stats") or msg.get("stats") or {}

    latency = (
        stats.get("last_latency")
        or stats.get("latency_ms")
        or stats.get("latency")
        or 0
    )
    cost = stats.get("cost")
    if cost is None:
        cost = stats.get("total_cost", 0.0)
    memory_count = stats.get("memory_count", 0)
    model = (
        msg.get("metadata", {}).get("model")
        or msg.get("metadata", {}).get("model_id")
        or stats.get("model")
        or msg.get("model")
    )

    try:
        latency_val = int(latency)
    except (TypeError, ValueError):
        latency_val = 0

    try:
        cost_val = float(cost)
    except (TypeError, ValueError):
        cost_val = 0.0

    try:
        memory_val = int(memory_count)
    except (TypeError, ValueError):
        memory_val = 0

    return latency_val, cost_val, memory_val, model


def system_message(content: str, type_: str = "info") -> Tag:
    """Render a system-level message (errors, notices)."""

    return Div(
        Div(content, cls=f"message system {type_}"),
        cls="system-message",
    )
