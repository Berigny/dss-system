"""
Helper functions for the UI-generating endpoints.
"""
from __future__ import annotations

import html
from typing import Any, Dict, List

try:
    from fasthtml.common import Div
except ImportError:  # pragma: no cover - optional dependency
    Div = None


def transform_and_filter(entries: List[Any]) -> List[Dict[str, Any]]:
    """
    Transforms a list of raw LedgerEntry objects into a list of clean
    "message" dictionaries, filtering out any that are invalid or lack content.
    """
    messages = []
    for entry in entries:
        if not hasattr(entry, "key") or not hasattr(entry, "state"):
            continue

        metadata = entry.state.metadata or {}
        coordinates = entry.state.coordinates or {}

        # Find the role and content of the message
        role = metadata.get("role", "system")
        content = metadata.get("content") or metadata.get("text") or entry.notes or ""

        # Find the Web4 key
        web4_key = metadata.get("web4_key")

        if not content:
            continue  # Don't include messages with no content

        messages.append({
            "id": entry.key.identifier,
            "role": role,
            "content": content,
            "timestamp": entry.created_at.isoformat(),
            "coordinates": coordinates,
            "web4_key": web4_key,
            "coordinate": entry.key.as_path(),
        })
    
    return messages


def format_entry_as_html(message: Dict[str, Any]) -> str:
    """
    Formats a single message dictionary into an HTML string for display.
    Applies different styles for user, assistant, and system roles.
    """
    role = message.get("role", "system")
    content = message.get("content", "").replace("\n", "<br>")
    coordinates = message.get("coordinates", {})
    web4_key = message.get("web4_key")
    entry_id = message.get("id")

    # --- 1. Prepare the display key ---
    # Prefer the full coordinate (namespace:identifier) so the UI shows the
    # canonical Web4 address instead of just the identifier or body prime.
    display_key = message.get("coordinate") or web4_key or entry_id

    # --- 2. Generate HTML for Prime Coordinate Tags ---
    coordinate_tags = []
    if coordinates:
        sorted_coords = sorted(coordinates.items())
        for key, value in sorted_coords:
            tag_style = "display: inline-block; background-color: #e0e0e0; color: #333; padding: 2px 6px; margin: 2px; border-radius: 4px; font-size: 0.75em; font-family: monospace;"
            coordinate_tags.append(f'<span style="{tag_style}">{key}: {value}</span>')
    
    coordinate_html = f"""
    <div style="margin-top: 8px; border-top: 1px solid #eee; padding-top: 6px;">
        {''.join(coordinate_tags)}
    </div>
    """ if coordinate_tags else ""

    # --- 3. Generate Header with Web4 Key ---
    # Escaping the key for both the display and the JavaScript
    safe_key_html = html.escape(display_key)
    safe_key_js = html.escape(display_key, quote=True)

    header_html = f"""
    <div style="font-size: 10px; font-family: monospace; color: #005A9C; text-decoration: underline; cursor: pointer; margin-bottom: 5px;"
         onclick="navigator.clipboard.writeText('{safe_key_js}')"
         title="Click to copy key">
      {safe_key_html}
    </div>
    """

    # --- 4. Main message bubble styles ---
    base_style = "padding: 10px; margin: 5px; border-radius: 10px; max-width: 70%;"
    
    if role == "user":
        style = f"{base_style} background-color: #dcf8c6; margin-left: auto;"
        align_container = "display: flex; justify-content: flex-end;"
        # User messages typically don't show a header
        final_header = ""
    elif role == "assistant":
        style = f"{base_style} background-color: #f1f0f0;"
        align_container = "display: flex; justify-content: flex-start;"
        final_header = header_html
    else: # system
        style = f"padding: 5px; margin: 10px auto; color: #888; font-size: 0.8em; text-align: center; max-width: 80%;"
        align_container = "display: flex; justify-content: center;"
        final_header = header_html

    body_html = f"""
        {final_header}
        <div>{content}</div>
        {coordinate_html}
    """

    if Div is not None:
        # Prefer proper component rendering when available
        return Div(
            Div(
                body_html,
                cls="",
                style=style,
            ),
            cls="",
            style=align_container,
        )

    # Fallback: raw HTML string if fasthtml is unavailable
    return f"""
    <div style="{align_container}">
        <div style="{style}">
            {body_html}
        </div>
    </div>
    """
