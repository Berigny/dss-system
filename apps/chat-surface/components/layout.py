"""
Layout components and page structure.
"""

import os

from fasthtml.common import (
    A,
    Body,
    Button,
    Div,
    H1,
    H2,
    Head,
    Html,
    Input,
    Label,
    Link,
    Meta,
    Option,
    P,
    Script,
    Select,
    Span,
    Title,
)

from config.settings import settings
from components.wake import wake_trigger


def hamburger_menu():
    """Hamburger menu button (top right)."""
    return Button(
        Div(Span(), Span(), Span(), Span(), cls="hamburger", id="hamburger-icon", aria_hidden="true"),
        cls="icon-button hamburger-btn",
        onclick="toggleMenu()",
        aria_label="Open menu",
        id="hamburger-btn",
        type="button",
        name="hamburger-btn",
    )


def settings_panel(active_ledger=None):
    """Slide-out settings panel."""

    ledger_label = active_ledger or settings.DEFAULT_LEDGER_ID

    return Div(
        Div(cls="menu-overlay", id="menu-overlay", onclick="toggleMenu()"),
        Div(
            Div(
                Div(
                    H2(ledger_label),
                    P("Ledger founding purpose", cls="panel-subtitle", id="ledger-purpose-subtitle"),
                    cls="panel-title",
                ),
                cls="panel-header",
            ),
            Div(
                Label("Ledger", for_="ledger-select"),
                Select(
                    Option(ledger_label, value=ledger_label, selected="selected"),
                    id="ledger-select",
                    name="ledger",
                ),
                cls="setting-group",
            ),
            Div(
                Label("Model", for_="agent-select"),
                Select(
                    Option("Loading models...", value="", disabled="disabled", selected="selected"),
                    id="agent-select",
                    name="agent",
                    hx_get="/api/models",
                    hx_trigger="load",
                    hx_target="#agent-select",
                ),
                cls="setting-group",
            ),
            cls="settings-panel",
            id="settings-panel",
        ),
    )


def page_header():
    """Simple page header with title."""
    return Div(Div(H1("chat.DSS"), cls="header-title"), cls="header")


def page_shell(
    content,
    title="chat.DSS",
    session_id=None,
    ledger_id=None,
    entity=None,
    backend_stream_enabled=False,
):
    """Main page wrapper."""
    commit_sha = (os.getenv("VERCEL_GIT_COMMIT_SHA") or "").strip()[:8]
    asset_rev = settings.STATIC_ASSET_VERSION if not commit_sha else f"{settings.STATIC_ASSET_VERSION}-{commit_sha}"
    return Html(
        Head(
            Title(title),
            Meta(charset="UTF-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
            Link(rel="stylesheet", href=f"/static/css/styles.css?v={asset_rev}"),
            Script(src="https://unpkg.com/htmx.org@1.9.10"),
            Script(src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"),
            Script(
                "window.MathJax = {"
                "tex: {"
                "inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],"
                "displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],"
                "}"
                "};"
            ),
            Script("window.dsChatStreamEnabled = true;"),
            Script(f"window.dsBackendStreamEnabled = {str(bool(backend_stream_enabled)).lower()};"),
            Script(f"window.dsAttachmentMaxBytes = {settings.ATTACHMENT_MAX_BYTES};"),
            Script(f"window.dsApiBase = \"{settings.API_BASE.rstrip('/')}\";"),
            Script(f"window.dsBackendIngestBase = \"{settings.BACKEND_ADMIN_BASE.rstrip('/')}\";"),
            Script(f"window.dsAccountApiBase = \"{settings.BACKEND_ADMIN_BASE.rstrip('/')}\";"),
            Script(f"window.dsControlPlaneBase = \"{settings.CONTROL_PLANE_BASE.rstrip('/')}\";"),
            Script(f"window.dsActiveLedger = \"{(ledger_id or settings.DEFAULT_LEDGER_ID)}\";"),
            Script(f"window.dsContextId = \"{settings.FRONTEND_CONTEXT_ID}\";"),
            Script(src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js", defer="defer"),
            Script(src=f"/static/js/app.js?v={asset_rev}", defer="defer"),
        ),
        Body(
            page_header(),
            hamburger_menu(),
            content,
            settings_panel(active_ledger=ledger_id),
            Div("", id="toast-container", cls="toast-container"),
            Input(type="hidden", id="session-id", value=session_id or ""),
            Input(type="hidden", id="entity-id", value=entity or ""),
            Input(type="hidden", id="active-ledger-id", value=ledger_id or settings.DEFAULT_LEDGER_ID),
            wake_trigger(),
        ),
    )
