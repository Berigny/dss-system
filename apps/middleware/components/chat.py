"""Chat-related UI components."""

from __future__ import annotations

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
            rendered.append(user_message(msg.get("content", ""), idx))
    return rendered


def user_message(message: str, msg_id: int) -> Tag:
    """Render a user message bubble for the chat stream."""

    return Div(
        Div(message, cls="message-content"),
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
    meta_text = (
        f"{model_text} • {latency_seconds:.1f}s • {memory_count} memories • ${cost:.6f}"
    )
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
    bubble = Div(
        Div(
            Div(
                content,
                cls=(
                    "prose prose-xl prose-p:font-serif prose-headings:font-serif markdown-content "
                    "max-w-none text-gray-900 leading-loose"
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
