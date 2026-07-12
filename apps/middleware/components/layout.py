"""
Layout components and page structure.
"""

from fasthtml.common import (
    A,
    Body,
    Button,
    Br,
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


def settings_panel(
    active_ledger=None,
    session_id=None,
    namespace=None,
    entity=None,
    ledger_hash=None,
    session_hash=None,
    backend_stream_enabled=False,
):
    """Slide-out settings panel."""

    session_label = session_hash or session_id or settings.DEFAULT_SESSION_ID
    namespace_label = entity or namespace or active_ledger or settings.DEFAULT_LEDGER_ID
    ledger_label = ledger_hash or active_ledger or settings.DEFAULT_LEDGER_ID

    return Div(
        Div(cls="menu-overlay", id="menu-overlay", onclick="toggleMenu()"),
        Div(
            Div(
                Div(
                    H2("Settings"),
                    P(
                        "Session: ",
                        Span(session_label, cls="panel-code"),
                        Br(),
                        "Namespace: ",
                        Span(namespace_label, cls="panel-code"),
                        Br(),
                        "Ledger: ",
                        Span(ledger_label, cls="panel-code"),
                        cls="panel-subtitle",
                    ),
                    cls="panel-title",
                ),
                cls="panel-header",
            ),
            Div(
                Label("Model", for_="agent-select"),
                Select(
                    Option("Loading models...", value="", disabled=True, selected=True),
                    id="agent-select",
                    name="agent",
                    hx_get="/api/models",
                    hx_trigger="load",
                    hx_target="#agent-select",
                ),
                cls="setting-group",
            ),
            Div(
                Label("Memory Health"),
                Div(
                    Span("Resolved Coords ", cls="stat-label"),
                    Span("—", cls="stat-number", id="panel-accuracy-rate"),
                    cls="stat-row",
                ),
                Div(
                    Span("Performance", cls="stat-label"),
                    Span("—", cls="stat-number", id="panel-performance"),
                    cls="stat-row",
                ),
                cls="setting-group",
            ),
            Div(
                Label("Unit Economics"),
                Div(
                    Span("Chat Cost / turn", cls="stat-label"),
                    Span("—", cls="stat-number", id="panel-chat-unit-cost"),
                    cls="stat-row",
                ),
                Div(
                    Span("Chat Cost / 1M tokens", cls="stat-label"),
                    Span("—", cls="stat-number", id="panel-memory-cost"),
                    cls="stat-row",
                ),
                Div(
                    Span("Session Spend", cls="stat-label"),
                    Span("—", cls="stat-number", id="panel-total-cost"),
                    cls="stat-row",
                ),
                cls="setting-group",
            ),
            Div(
                Button(
                    "Library Admin",
                    cls="accordion-toggle",
                    type="button",
                    onclick="toggleAccordion(this)",
                    aria_expanded="false",
                ),
                Div(
                    Div(
                        Label("Appraisal"),
                        Div(
                            Span("Constraint ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-law-score"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Explore ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-grace-score"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Drift ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-drift-score"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Eq6 Commit ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-eq6-commit"),
                            cls="stat-row",
                        ),
                        cls="setting-group compact",
                    ),
                    Div(
                        Label("Governance Metrics"),
                        Div(
                            Span("L ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-gov-L"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("H ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-gov-H"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("U ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-gov-U"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("V ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-gov-V"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("I1 ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-gov-I1"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("I2 ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-gov-I2"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("dW ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-gov-dW"),
                            cls="stat-row",
                        ),
                        cls="setting-group compact",
                    ),
                    Div(
                        Label("Resolve Timing"),
                        Div(
                            Span("Assemble ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-assemble-ms"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Decode ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-decode-ms"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("LLM ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-llm-ms"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Assess ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-assess-ms"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Commit ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-commit-ms"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Total ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-total-ms"),
                            cls="stat-row",
                        ),
                        cls="setting-group compact",
                    ),
                    Div(
                        Label("Context Budget"),
                        Div(
                            Span("Prompt Tokens ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-context-prompt"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Completion Tokens ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-context-completion"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Retrieved Coords ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-context-retrieved"),
                            cls="stat-row",
                        ),
                        cls="setting-group compact",
                    ),
                    Div(
                        Label("Resolve Coords"),
                        Div(
                            Span("Queued ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-coords-queued"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Decoded ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-coords-decoded"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Children ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-coords-child"),
                            cls="stat-row",
                        ),
                        cls="setting-group compact",
                    ),
                    Div(
                        Label("Router Decision"),
                        Div(
                            Span("Route ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-router-route"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Reason ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-router-reason"),
                            cls="stat-row",
                        ),
                        Div(
                            Span("Walk ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-router-walk"),
                            cls="stat-row",
                        ),
                        cls="setting-group compact",
                    ),
                    Div(
                        Label("Stream Mode"),
                        Div(
                            Span("Backend Stream ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-stream-mode"),
                            cls="stat-row",
                        ),
                        Div(
                            Div(
                                Span("Force Backend Stream", cls="toggle-label"),
                                Label(
                                    Input(
                                        type="checkbox",
                                        id="backend-stream-toggle",
                                        cls="switch-input",
                                        aria_label="Force Backend Stream",
                                        checked=bool(backend_stream_enabled),
                                    ),
                                    Span("", cls="switch-slider"),
                                    cls="switch",
                                ),
                                cls="toggle-row",
                            ),
                        ),
                        Div(
                            Span("Walk Debug ", cls="stat-label"),
                            Span("—", cls="stat-number", id="panel-walk-debug"),
                            cls="stat-row",
                        ),
                        cls="setting-group compact",
                    ),
                    Div(
                        Button(
                            "Sync Ledger/s (ALL)",
                            id="sync-ledgers-btn",
                            cls="action-btn secondary",
                            hx_post="/api/sync/all",
                            hx_confirm="Sync all configured ledgers between local and cloud now?",
                            hx_swap="none",
                        ),
                        Button(
                            "Export chat",
                            id="export-chat-btn",
                            cls="action-btn secondary",
                            onclick="exportChat(this)",
                        ),
                        cls="setting-group action-buttons compact",
                    ),
                    cls="accordion-content",
                ),
                cls="setting-group accordion",
            ),
            cls="settings-panel",
            id="settings-panel",
        ),
    )


def page_header():
    """Simple page header with title."""
    return Div(Div(H1("ourIP.AI"), cls="header-title"), cls="header")


def page_shell(
    content,
    title="ourIP.AI Chat",
    session_id=None,
    ledger_id=None,
    entity=None,
    ledger_hash=None,
    session_hash=None,
    backend_stream_enabled=False,
):
    """Main page wrapper."""
    return Html(
        Head(
            Title(title),
            Meta(charset="UTF-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
            Link(rel="stylesheet", href=f"/static/css/styles.css?v={settings.STATIC_ASSET_VERSION}"),
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
            Script(src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js", defer=True),
            Script(src=f"/static/js/app.js?v={settings.STATIC_ASSET_VERSION}", defer=True),
        ),
        Body(
            page_header(),
            hamburger_menu(),
            content,
            settings_panel(
                active_ledger=ledger_id,
                session_id=session_id,
                namespace=ledger_id,
                entity=entity,
                ledger_hash=ledger_hash,
                session_hash=session_hash,
                backend_stream_enabled=backend_stream_enabled,
            ),
            Div("", id="toast-container", cls="toast-container"),
            Input(type="hidden", id="session-id", value=session_id or ""),
            Input(type="hidden", id="entity-id", value=entity or ""),
            wake_trigger(),
        ),
    )
