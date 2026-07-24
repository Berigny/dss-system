"""Layout components reused from the DSS chat surface."""

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
    P,
    Script,
    Span,
    Title,
)

from config.settings import settings


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


def settings_panel():
    """Slide-out settings panel."""
    return Div(
        Div(cls="menu-overlay", id="menu-overlay", onclick="toggleMenu()"),
        Div(
            Div(
                Div(H2("Document surface"), cls="panel-title"),
                cls="panel-header",
            ),
            Div(
                P("Append-only, event-sourced document composer.", cls="panel-subtitle"),
                cls="setting-group",
            ),
            A("Open chat surface", href="https://chat.dualsubstrate.com", cls="panel-link"),
            cls="settings-panel",
            id="settings-panel",
        ),
    )


def page_header():
    """Simple page header with title."""
    return Div(Div(H1("docs.DSS"), cls="header-title"), cls="header")


def page_shell(content, title="docs.DSS"):
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
            Script(f"window.dsApiBase = \"{settings.API_BASE.rstrip('/')}\";"),
            Script(src=f"/static/js/app.js?v={asset_rev}", defer="defer"),
        ),
        Body(
            page_header(),
            hamburger_menu(),
            content,
            settings_panel(),
            Div("", id="toast-container", cls="toast-container"),
        ),
    )
