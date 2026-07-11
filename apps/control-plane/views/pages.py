from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote


def render_page_header(*, title: str, description: str, actions: str = "") -> str:
    action_html = f'<div class="page-header-actions">{actions}</div>' if actions else ""
    return f"""
    <header class="page-header page-header-shell">
      <div>
        <h1>{html.escape(title)}</h1>
        <p>{html.escape(description)}</p>
      </div>
      {action_html}
    </header>
    """


def render_breadcrumbs(items: list[tuple[str, str | None]]) -> str:
    parts: list[str] = []
    for index, (label, href) in enumerate(items):
        if index:
            parts.append('<span class="crumb-sep" aria-hidden="true">/</span>')
        if href:
            parts.append(f'<a href="{html.escape(href)}">{html.escape(label)}</a>')
        else:
            parts.append(f'<span aria-current="page">{html.escape(label)}</span>')
    return f'<nav class="breadcrumbs" aria-label="Breadcrumb">{"".join(parts)}</nav>'


def render_connections_page_content(
    *,
    breadcrumbs_html: str = "",
    header_html: str,
    toolbar_html: str,
    tabs_html: str,
    list_html: str,
) -> str:
    return f"""
      {breadcrumbs_html}
      {header_html}
      {toolbar_html}
      {tabs_html}
      {list_html}
    """


def render_page_toolbar(
    *,
    search_placeholder: str = "Search connections...",
    search_query: str = "",
    search_action: str = "/connections",
    hidden_fields: dict[str, str] | None = None,
    primary_cta_label: str = "Add",
    primary_cta_href: str = "/connections/add",
    primary_cta_html: str = "",
    secondary_cta_label: str = "",
    secondary_cta_href: str = "",
    secondary_cta_new_tab: bool = False,
) -> str:
    hidden_inputs = "".join(
        f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value)}">'
        for key, value in (hidden_fields or {}).items()
    )
    secondary_target_attrs = ' target="_blank" rel="noreferrer noopener"' if secondary_cta_new_tab else ""
    secondary_html = (
        f'<a class="toolbar-btn" href="{html.escape(secondary_cta_href)}"{secondary_target_attrs}>{html.escape(secondary_cta_label)}</a>'
        if secondary_cta_label and secondary_cta_href
        else ""
    )
    primary_html = primary_cta_html if primary_cta_html else (
        f'<a class="toolbar-btn primary" href="{html.escape(primary_cta_href)}">{html.escape(primary_cta_label)}</a>'
        if primary_cta_label and primary_cta_href
        else ""
    )
    actions_html = f'<div class="inline-actions">{secondary_html}{primary_html}</div>' if secondary_html or primary_html else ""
    return f"""
    <div class="page-toolbar">
      <form class="page-toolbar-search" role="search" method="get" action="{html.escape(search_action)}">
        <label for="connections-search" class="sr-only">Search connections</label>
        {hidden_inputs}
        <input
          id="connections-search"
          type="search"
          name="q"
          value="{html.escape(search_query)}"
          placeholder="{html.escape(search_placeholder)}"
          aria-label="Search connections"
          oninput="filterCollection(this.value)"
        >
      </form>
      {actions_html}
    </div>
    """


def render_toolbar_menu_cta(*, label: str, items_html: str) -> str:
    return f"""
    <details class="page-title-status-menu toolbar-menu">
      <summary class="toolbar-btn primary">
        <span>{html.escape(label)}</span>
        <span class="page-title-status-chevron" aria-hidden="true">▾</span>
      </summary>
      <div class="page-title-status-list">
        {items_html}
      </div>
    </details>
    """


def render_entity_tabs(
    current_type: str,
    *,
    search_query: str = "",
    sort_by: str = "name",
    sort_dir: str = "asc",
    counts: dict[str, int] | None = None,
) -> str:
    tabs = [
        ("all", "All"),
        ("ledgers", "Ledgers"),
        ("principals", "Principals"),
        ("surfaces", "Surfaces"),
        ("sources", "Sources"),
    ]
    links: list[str] = []
    for tab_value, label in tabs:
        href = (
            f"/connections?type={quote(tab_value, safe='')}"
            f"&sort_by={quote(sort_by, safe='')}"
            f"&sort_dir={quote(sort_dir, safe='')}"
        )
        if search_query:
            href += f"&q={quote(search_query, safe='')}"
        cls = "page-tab active" if current_type == tab_value else "page-tab"
        suffix = f" ({int(counts.get(tab_value, 0))})" if counts is not None else ""
        links.append(f'<a class="{cls}" href="{href}">{html.escape(label)}{suffix}</a>')
    return f'<nav class="page-tabs" aria-label="Connection types">{"".join(links)}</nav>'


def render_relationship_filter_tabs(
    current_type: str = "all",
    *,
    counts: dict[str, int] | None = None,
    summary_panel_id: str = "",
    summary_active: bool = False,
    summary_label: str = "Summary",
) -> str:
    tabs = [
        ("all", "All"),
        ("ledgers", "Ledgers"),
        ("principals", "Principals"),
        ("surfaces", "Surfaces"),
    ]
    controls: list[str] = []
    for tab_value, label in tabs:
        cls = "page-tab active" if (not summary_active and current_type == tab_value) else "page-tab"
        suffix = f" ({int(counts.get(tab_value, 0))})" if counts is not None else ""
        controls.append(
            f'<button type="button" class="{cls}" data-relationship-filter="{html.escape(tab_value)}">{html.escape(label)}{suffix}</button>'
        )
    overview_label = "Overview"
    if summary_panel_id:
        controls.insert(
            0,
            f'<button type="button" class="page-tab{" active" if summary_active else ""}" data-detail-panel-toggle="{html.escape(summary_panel_id)}" aria-controls="{html.escape(summary_panel_id)}" aria-expanded="{"true" if summary_active else "false"}">{html.escape(overview_label)}</button>',
        )
    return f'<nav class="page-tabs" aria-label="Relationship types">{"".join(controls)}</nav>'


def render_action_cards() -> str:
    return """
    <section class="action-card-grid" aria-label="Primary tasks">
      <article class="action-card action-card-secondary">
        <h2>Manage existing connections</h2>
        <p>Browse, filter, and adjust governed connections across ledgers, principals, and surfaces.</p>
        <a class="btn" href="/connections">Manage Connections</a>
      </article>
      <article class="action-card action-card-primary">
        <h2>Add a ledger</h2>
        <p>Create a new Ledger (governed memory layer) and define its initial governance settings.</p>
        <a class="btn primary" href="/connections/add/ledger">Add a ledger</a>
      </article>
      <article class="action-card action-card-primary">
        <h2>Add or accept a connection</h2>
        <p>Start a new connection or accept an invite code to link a Principal or Surface.</p>
        <div class="inline-actions">
          <a class="btn primary" href="/connections/add">Add connection</a>
          <a class="btn" href="/connections/accept">Accept connection</a>
        </div>
      </article>
    </section>
    """


def render_home_page_content(*, header_html: str, actions_html: str, support_html: str = "") -> str:
    return f"""
      {header_html}
      {actions_html}
      {support_html}
    """


def render_not_found_content(message: str) -> str:
    return f"""
        <div class="page-header"><h1>Not Found</h1><p>{html.escape(message)}</p></div>
        """


def render_empty_state(title: str = "No connections", message: str = "No ledgers, principals, or surfaces were found.") -> str:
    return f"""
    <div class="empty-state">
      <h2>{html.escape(title)}</h2>
      <p>{html.escape(message)}</p>
    </div>
    """


def render_entity_list(
    conn_items: list[dict[str, Any]],
    *,
    type_filter: str,
    sort_by: str,
    sort_dir: str,
    search_query: str = "",
    current_principal_did: str = "",
) -> str:
    def _status_class(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"active", "enabled", "ready", "connected"}:
            return ""
        if normalized in {"error", "failed", "blocked", "revoked"}:
            return "error"
        return "pending"

    def _status_is_pending(value: str) -> bool:
        normalized = str(value or "").strip().lower()
        return normalized in {"queued", "running", "processing", "pending"}

    def _status_spinner_html(value: str) -> str:
        return (
            '<span class="status-dot pending" style="margin-right:5px; display:inline-flex; align-items:center; justify-content:center;">'
            '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="animation:spin 0.8s linear infinite;">'
            '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>'
            '</svg></span>'
        )

    def _truncate_verified_id(value: str, limit: int = 25) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[:22]}..."

    def _next_dir(field: str) -> str:
        if sort_by == field and sort_dir == "asc":
            return "desc"
        return "asc"

    def _indicator(field: str) -> str:
        if sort_by != field:
            return ""
        return " ▲" if sort_dir == "asc" else " ▼"

    def _sort_link(field: str) -> str:
        href = (
            f"/connections?type={quote(type_filter, safe='')}"
            f"&sort_by={quote(field, safe='')}"
            f"&sort_dir={quote(_next_dir(field), safe='')}"
        )
        if search_query:
            href += f"&q={quote(search_query, safe='')}"
        return href

    rows: list[str] = []
    for item in conn_items:
        status_text = str(item.get("status") or "Unknown")
        status_class = _status_class(status_text)
        detail_path = f"/connections/{quote(str(item.get('type') or ''), safe='')}/{quote(str(item.get('id') or ''), safe='')}"
        verified_id = str(item.get("did") or "")
        verified_id_short = _truncate_verified_id(verified_id)
        item_type = str(item.get("type") or "").strip().lower()
        item_name = str(item.get("name") or "")
        item_subtitle = str(item.get("subtitle") or "").strip()
        filter_name = f"{str(item.get('name') or '')} {item_subtitle} {verified_id} {str(item.get('type_label') or '')}".lower()
        name_html = html.escape(item_name)
        rows.append(
            f"""
            <div class="collection-list-row no-extended" data-filter-name="{html.escape(filter_name)}" data-pageable-item data-pageable-kind="connections">
              <span class="collection-list-cell-name">
                <a class="collection-list-primary-link" href="{detail_path}">
                  <strong>{name_html}</strong>
                  {f'<span class="muted" style="display:block; margin-top:2px;">Operational ID: {html.escape(item_subtitle)}</span>' if item_subtitle else ''}
                </a>
              </span>
              <span class="relationship-cell-meta-wrap">
                <span class="collection-list-meta" title="{html.escape(verified_id)}">{html.escape(verified_id_short)}</span>
                <button type="button" class="relationship-copy-btn" data-copy-value="{html.escape(verified_id)}" aria-label="Copy full Verified ID">⧉</button>
              </span>
              <span class="collection-list-meta">{html.escape(str(item.get("type_label") or str(item.get("type") or "").title()))}</span>
              <span class="collection-list-meta">
                {_status_spinner_html(status_text) if _status_is_pending(status_text) else f'<span class="status-dot {status_class}" style="margin-right:5px;"></span>'}
                {html.escape(status_text)}
              </span>
            </div>
            """
        )
    return (
        '<div class="pageable-group" data-pageable-group data-page-size="10" data-pageable-label="connections">'
        '<div class="collection-list"><div class="collection-list-head no-extended">'
        f'<a href="{_sort_link("name")}" class="table-sort">Name{_indicator("name")}</a>'
        f'<a href="{_sort_link("did")}" class="table-sort">Verified ID{_indicator("did")}</a>'
        f'<span>Type</span>'
        f'<a href="{_sort_link("status")}" class="table-sort">Status{_indicator("status")}</a>'
        f"</div>{''.join(rows)}</div>"
        '<div class="pageable-actions"><button type="button" class="btn pageable-load-more" data-pageable-trigger>View all</button></div>'
        '</div>'
    )


def render_connection_detail_page(
    *,
    title: str,
    identifier: str,
    status: str,
    description: str,
    summary_rows: list[tuple[str, str]],
    related_sections: list[tuple[str, str, str]],
    primary_actions: str = "",
) -> str:
    header_html = render_connection_detail_header(title=title, status=status, description=description)
    sections_html = render_connection_detail_sections(
        identifier=identifier,
        summary_rows=summary_rows,
        related_sections=related_sections,
        primary_actions=primary_actions,
    )
    return f"""
    {header_html}
    {sections_html}
    """


def render_connection_detail_header(
    *,
    title: str,
    status: str,
    description: str,
    status_menu_href: str = "",
    status_menu_label: str = "Edit details",
    secondary_action_html: str = "",
) -> str:
    status_html = f'<span class="pill page-title-status">{html.escape(status)}</span>'
    return f"""
    <div class="page-header">
      <h1>{html.escape(title)} {status_html}</h1>
      <p>{html.escape(description)}</p>
    </div>
    """


def render_connection_detail_sections(
    *,
    identifier: str,
    summary_rows: list[tuple[str, str]],
    related_sections: list[tuple[str, str, str]],
    primary_actions: str = "",
    summary_panel_id: str = "connection-summary-panel",
    summary_open: bool = False,
    summary_extra_html: str = "",
) -> str:
    summary_table = "".join(
        f'<tr><th style="text-align:left; padding:10px 12px; color:var(--text-muted); font-weight:500; width:220px;">{html.escape(label)}</th><td style="padding:10px 12px;">{html.escape(value)}</td></tr>'
        for label, value in summary_rows
        if str(label or "").strip() and str(value or "").strip()
    )
    summary_html = f"""
    <section class="connection-detail-section detail-summary-panel{' open' if summary_open else ''}" id="{html.escape(summary_panel_id)}">
      <div class="card">
        <h2>Summary</h2>
        <p class="muted">Stable identity first, with operational identifiers and counts kept as supporting context.</p>
        <table style="width:100%; border-collapse:collapse;">{summary_table or '<tr><td class="muted" style="padding:10px 12px;" colspan="2">No summary fields are available yet.</td></tr>'}</table>
        {summary_extra_html}
        {f'<div class="inline-actions" style="margin-top:12px;">{primary_actions}</div>' if primary_actions else ''}
      </div>
    </section>
    """
    related_html = "".join(
        f"""
        <section class="connection-detail-section">
          {f'<div class="page-header" style="margin-bottom:12px;"><div><h2 style="margin:0;">{html.escape(section_title)}</h2><p style="margin:6px 0 0;">{html.escape(section_description)}</p></div></div>' if section_title or section_description else ''}
          {section_body}
        </section>
        """
        for section_title, section_description, section_body in related_sections
    )
    return f"""
    <div class="connection-detail-stack">
      {summary_html}
      {related_html}
    </div>
    """
