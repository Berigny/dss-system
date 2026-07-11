from __future__ import annotations

import html
import json
import os
from typing import Any


def _flow_slug(value: str, fallback: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    tokens = [token for token in cleaned.split("-") if token]
    return "-".join(tokens) or fallback


def render_submenu_tabs(current_type: str) -> str:
    items = [
        ("all", "All", "/connections"),
        ("ledgers", "Ledgers", "/connections?type=ledgers"),
        ("principals", "Principals", "/connections?type=principals"),
        ("surfaces", "Surfaces", "/connections?type=surfaces"),
    ]
    parts = []
    for key, label, href in items:
        active = (current_type in {"", "all"} and key == "all") or key == current_type
        cls = "active" if active else ""
        parts.append(f'<a href="{href}" class="{cls}">{html.escape(label)}</a>')
    return f'<nav class="ledger-subnav">{"".join(parts)}</nav>'


def render_stepper(steps: list[str], current_step: str) -> str:
    normalized_current = current_step.strip().lower()
    items: list[str] = []
    for label in steps:
        normalized_label = label.strip().lower()
        is_active = normalized_label == normalized_current
        cls = "page-tab active" if is_active else "page-tab"
        dot_class = "status-dot" if is_active else "status-dot pending"
        items.append(f'<span class="{cls}"><span class="{dot_class}" style="display:inline-block; margin-right:8px;"></span>{html.escape(label)}</span>')
    return f'<nav class="page-tabs" aria-label="Wizard steps">{"".join(items)}</nav>'


def render_flow_shell(*, title: str, description: str, steps: list[str], current_step: str, main_html: str, aside_html: str = "", breadcrumb_html: str = "") -> str:
    return f"""
    {breadcrumb_html}
    <div class="page-header">
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(description)}</p>
    </div>
    {render_stepper(steps, current_step)}
    <div class="card">
      {main_html}
    </div>
    """


def _hidden_inputs(state: dict[str, str], *, exclude: set[str] | None = None) -> str:
    excluded = exclude or set()
    return "".join(
        f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(value)}" />'
        for key, value in state.items()
        if key not in excluded
    )


def _text_input(name: str, label: str, value: str, *, placeholder: str = "") -> str:
    return f"""
    <label for="{html.escape(name)}">{html.escape(label)}</label>
    <input id="{html.escape(name)}" name="{html.escape(name)}" value="{html.escape(value)}" placeholder="{html.escape(placeholder)}" />
    """


def _password_input(name: str, label: str) -> str:
    return f"""
    <label for="{html.escape(name)}">{html.escape(label)}</label>
    <input id="{html.escape(name)}" name="{html.escape(name)}" type="password" autocomplete="off" value="" />
    """


def _readonly_input(name: str, label: str, value: str) -> str:
    return f"""
    <label for="{html.escape(name)}">{html.escape(label)}</label>
    <input id="{html.escape(name)}" name="{html.escape(name)}" value="{html.escape(value)}" readonly />
    """


def _select_input(name: str, label: str, value: str, options: list[tuple[str, str]]) -> str:
    option_html = "".join(
        f'<option value="{html.escape(option_value)}"{" selected" if option_value == value else ""}>{html.escape(option_label)}</option>'
        for option_value, option_label in options
    )
    return f"""
    <label for="{html.escape(name)}">{html.escape(label)}</label>
    <select id="{html.escape(name)}" name="{html.escape(name)}">{option_html}</select>
    """


def _radio_list_input(name: str, label: str, value: str, options: list[tuple[str, str, str]]) -> str:
    rows = "".join(
        f"""
        <label style="display:flex; align-items:flex-start; gap:10px; margin:0 0 12px; font-weight:500; color:var(--text);">
          <input type="radio" name="{html.escape(name)}" value="{html.escape(option_value)}"{" checked" if option_value == value else ""} />
          <span>
            <strong>{html.escape(option_label)}</strong>
            <span class="muted" style="display:block; margin-top:2px;">{html.escape(option_copy)}</span>
          </span>
        </label>
        """
        for option_value, option_label, option_copy in options
    )
    return f"""
    <fieldset style="border:none; padding:0; margin:12px 0 0;">
      <legend style="font-weight:600; margin-bottom:8px;">{html.escape(label)}</legend>
      {rows}
    </fieldset>
    """


def _truncate_identifier(value: str, limit: int = 28) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _status_dot_class(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"active", "enabled", "ready", "connected"}:
        return ""
    if normalized in {"error", "failed", "blocked", "revoked"}:
        return "error"
    return "pending"


def _principal_type_options() -> list[tuple[str, str, str]]:
    return [
        ("human", "Human", "Individual person with a governed identity"),
        ("organisation", "Group / organisation", "Team, company, or collective principal"),
        ("model", "Model / agent", "Model-backed or autonomous software principal"),
        ("service", "Service", "Non-human service identity, including delegated external agents"),
        ("device", "Device", "Physical device or hardware-bound actor"),
    ]


def _service_subtype_options() -> list[tuple[str, str, str]]:
    return [
        ("delegated_agent", "Delegated agent", "External non-human actor operating only on explicit operator request."),
        ("automation", "Automation", "Scheduled or event-driven service actor."),
        ("integration", "Integration", "External system or app connector principal."),
        ("control_plane_service", "Control plane service", "DSS-internal governance or migration actor."),
        ("background_worker", "Background worker", "Non-interactive queue or batch worker."),
        ("verifier_auditor", "Verifier / auditor", "Inspection, attestation, or policy-check principal."),
    ]


def _surface_type_key(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw == "chat":
        return "chat"
    if raw == "telegram":
        return "telegram"
    if "mcp" in raw or "third-party" in raw:
        return "mcp"
    return "custom"


def _surface_runtime_panels(state: dict[str, str], binding_options: list[tuple[str, str]]) -> str:
    selected_type = _surface_type_key(state.get("surface_type", "Custom"))
    endpoint_value = str(state.get("endpoint") or "").strip()
    telegram_bot_api = str(state.get("telegram_bot_api") or "").strip()
    surface_api_label = str(state.get("surface_api_label") or "").strip()
    surface_api_value = str(state.get("surface_api_value") or "").strip()
    provider_type = str(state.get("provider_type") or "").strip()
    model_id = str(state.get("model_id") or "").strip()
    credential_ref = str(state.get("credential_ref") or "").strip()
    return f"""
    <div data-surface-type-panel="chat"{" hidden" if selected_type != "chat" else ""}>
      <div class="card compact" style="margin-top:14px;">
        <h3 style="margin:0 0 8px;">Chat runtime</h3>
        <p class="muted" style="margin:0 0 12px;">Choose the approved chat binding. Provider, model, and credential fields are derived from that governed binding.</p>
        {_text_input("endpoint", "Chat URL", endpoint_value, placeholder="https://chat.example.com")}
        {_select_input("default_binding_id", "Approved model binding", state.get("default_binding_id", ""), binding_options)}
        {_readonly_input("provider_type", "Provider type", provider_type)}
        {_readonly_input("model_id", "Model ID", model_id)}
        {_readonly_input("credential_ref", "Credential ref", credential_ref)}
      </div>
    </div>
    <div data-surface-type-panel="telegram"{" hidden" if selected_type != "telegram" else ""}>
      <div class="card compact" style="margin-top:14px;">
        <h3 style="margin:0 0 8px;">Telegram runtime</h3>
        <p class="muted" style="margin:0 0 12px;">Telegram needs an explicit Bot API field in addition to the approved model binding used behind the surface.</p>
        {_text_input("telegram_bot_api", "Telegram Bot API", telegram_bot_api, placeholder="https://api.telegram.org/bot<token>")}
        {_text_input("endpoint", "Webhook URL", endpoint_value, placeholder="https://example.com/webhooks/telegram")}
        {_select_input("default_binding_id", "Approved model binding", state.get("default_binding_id", ""), binding_options)}
        {_readonly_input("provider_type", "Provider type", provider_type)}
        {_readonly_input("model_id", "Model ID", model_id)}
        {_readonly_input("credential_ref", "Credential ref", credential_ref)}
      </div>
    </div>
    <div data-surface-type-panel="mcp"{" hidden" if selected_type != "mcp" else ""}>
      <div class="card compact" style="margin-top:14px;">
        <h3 style="margin:0 0 8px;">MCP / third-party runtime</h3>
        <p class="muted" style="margin:0 0 12px;">For non-built-in surfaces, capture the external API or server field explicitly instead of implying a chat-style binding-only setup.</p>
        {_text_input("surface_api_label", "API field label", surface_api_label, placeholder="Telegram Bot API")}
        {_text_input("surface_api_value", "API field value", surface_api_value, placeholder="mcp://server-name or provider-specific API value")}
        {_text_input("endpoint", "Runtime endpoint", endpoint_value, placeholder="mcp://server-name")}
      </div>
    </div>
    <div data-surface-type-panel="custom"{" hidden" if selected_type != "custom" else ""}>
      <div class="card compact" style="margin-top:14px;">
        <h3 style="margin:0 0 8px;">Custom runtime</h3>
        <p class="muted" style="margin:0 0 12px;">Capture the operational API field needed to launch or govern this surface. This keeps non-built-in surfaces extensible without overloading chat-only fields.</p>
        {_text_input("surface_api_label", "API field label", surface_api_label, placeholder="Surface API")}
        {_text_input("surface_api_value", "API field value", surface_api_value, placeholder="Surface-specific API or credential handle")}
        {_text_input("endpoint", "Runtime endpoint", endpoint_value, placeholder="https://surface.example.com")}
      </div>
    </div>
    <script>
    (() => {{
      const select = document.getElementById("surface_type");
      const panels = Array.from(document.querySelectorAll("[data-surface-type-panel]"));
      const normalize = (value) => {{
        const raw = String(value || "").toLowerCase().trim();
        if (raw === "chat") return "chat";
        if (raw === "telegram") return "telegram";
        if (raw.includes("mcp") || raw.includes("third-party")) return "mcp";
        return "custom";
      }};
      const render = () => {{
        const current = normalize(select ? select.value : "");
        panels.forEach((panel) => {{
          panel.hidden = panel.getAttribute("data-surface-type-panel") !== current;
        }});
      }};
      if (select) {{
        select.addEventListener("change", render);
      }}
      render();
    }})();
    </script>
    """


def _summary_rows(state: dict[str, str]) -> str:
    items = "".join(
        f"<li><strong>{html.escape(key.replace('_', ' ').title())}:</strong> {html.escape(value or '—')}</li>"
        for key, value in sorted(state.items())
        if key not in {"submit_action", "wizard_action", "available_models_json", "provider_api_key"} and str(value or "").strip()
    )
    return items or '<li class="muted">No details provided yet.</li>'


def _title_with_name(base_title: str, name: str) -> str:
    name_text = str(name or "").strip()
    return f"{base_title}: {name_text}" if name_text else base_title


def render_add_connection_type_flow(*, state: dict[str, str]) -> str:
    entity_kind = str(state.get("entity_kind") or "").strip().lower()
    options = [
        ("ledger", "Ledger", "governed memory boundary for AI activity"),
        ("principal", "Principal", "human, organisation, model, or agent"),
        ("surface", "Surface", "channel or interface where interactions happen"),
    ]
    option_html = "".join(
        f"""
        <label style="display:flex; align-items:flex-start; gap:10px; margin:0 0 12px; font-weight:500; color:var(--text);">
          <input type="radio" name="entity_kind" value="{html.escape(value)}"{" checked" if entity_kind == value else ""} />
          <span><strong>{html.escape(label)}</strong> ({html.escape(copy)})</span>
        </label>
        """
        for value, label, copy in options
    )
    modal_form = f"""
    <form method="get" action="/connections/add">
      <h2>Details</h2>
      <p>Select type of new governed relationship/s.</p>
      {option_html}
      <div class="inline-actions" style="justify-content:flex-end; width:100%; margin-top:16px;">
        <a class="btn" href="/connections">Cancel</a>
        <button class="btn primary" type="submit">Continue</button>
      </div>
    </form>
    """
    return f"""
    <div class="modal-shell" id="add-connection-type-modal">
      <div class="modal-backdrop"></div>
      <section class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="add-connection-type-title" aria-describedby="add-connection-type-desc">
        <div class="modal-header">
          <div>
            <h2 id="add-connection-type-title">Add or edit</h2>
            <p id="add-connection-type-desc" class="muted">Add a ledger, principal or surface to start new governed relationships.</p>
          </div>
        </div>
        <div class="modal-body">
          {modal_form}
        </div>
      </section>
    </div>
    """


def _flow_form_actions(
    *,
    action: str,
    state: dict[str, str],
    current_step: str,
    back_step: str = "",
    next_step: str = "",
    final_submit: bool = False,
    cancel_href: str = "/connections",
) -> str:
    method = "post" if final_submit else "get"
    hidden = _hidden_inputs(state, exclude={"step", "submit_action"})
    back_button = (
        f'<button class="btn" type="submit" name="step" value="{html.escape(back_step)}">Back</button>'
        if back_step else ""
    )
    next_button = f'<button class="btn primary" type="submit" name="step" value="{html.escape(next_step)}">Next</button>' if next_step else ""
    final_button = '<button class="btn primary" type="submit" name="submit_action" value="apply">Submit</button>' if final_submit else ""
    return f"""
    <form method="{method}" action="{html.escape(action)}" style="margin-top:16px;">
      {hidden}
      <input type="hidden" name="step" value="{html.escape(current_step)}" />
      <div class="inline-actions" style="justify-content:space-between; width:100%;">
        <div class="inline-actions">
          <a class="btn" href="{html.escape(cancel_href)}">Cancel</a>
          {back_button}
        </div>
        <div class="inline-actions">
          {next_button}
          {final_button}
        </div>
      </div>
    </form>
    """


def _step_frame(
    *,
    action: str,
    state: dict[str, str],
    current_step: str,
    back_step: str = "",
    next_step: str = "",
    final_submit: bool = False,
    next_label: str = "Next",
    final_label: str = "Submit",
    hidden_exclude: set[str] | None = None,
    fields_html: str = "",
    summary_html: str = "",
    after_form_html: str = "",
) -> str:
    method = "post" if final_submit else "get"
    hidden = _hidden_inputs(state, exclude={"step", "submit_action"} | (hidden_exclude or set()))
    back_button = (
        f'<button class="btn" type="submit" name="step" value="{html.escape(back_step)}">Back</button>'
        if back_step else ""
    )
    next_button = (
        f'<button class="btn primary" type="submit" name="step" value="{html.escape(next_step)}">{html.escape(next_label)}</button>'
        if next_step else ""
    )
    final_button = (
        f'<button class="btn primary" type="submit" name="submit_action" value="apply">{html.escape(final_label)}</button>'
        if final_submit else ""
    )
    content = fields_html or summary_html
    return f"""
    <form method="{method}" action="{html.escape(action)}">
      {hidden}
      {content}
      <div class="inline-actions" style="justify-content:space-between; width:100%; margin-top:16px;">
        <div class="inline-actions">
          <a class="btn" href="/connections">Cancel</a>
        </div>
        <div class="inline-actions">
          {back_button}
          {next_button}
          {final_button}
        </div>
      </div>
    </form>
    {after_form_html}
    """


def _principal_access_rows(
    *,
    principals: list[dict[str, str]],
    current_principal_did: str,
    linked_principal_ids: set[str],
) -> str:
    if not principals:
        return '<div class="empty-state"><p>No principals are available to link yet.</p></div>'
    def _principal_type_label(item: dict[str, str]) -> str:
        actor_type = str(item.get("actor_type") or item.get("principal_type") or "principal").strip().lower()
        if actor_type == "human":
            return "Principal | Human"
        if actor_type == "organisation":
            return "Principal | Group / organisation"
        if actor_type in {"model", "agent"}:
            return "Principal | Model / agent"
        if actor_type == "service":
            return "Principal | Service"
        if actor_type == "device":
            return "Principal | Device / machine identity"
        return f"Principal | {actor_type.replace('_', ' ').title()}" if actor_type else "Principal | Other"

    rows: list[str] = []
    for item in principals:
        principal_did = str(item.get("principal_did") or "").strip()
        if not principal_did:
            continue
        display_name = str(item.get("display_name") or principal_did).strip() or principal_did
        principal_type = _principal_type_label(item)
        verified_id = str(item.get("canonical_subject") or principal_did).strip() or principal_did
        status = str(item.get("status") or "Unknown").strip().title() or "Unknown"
        is_current = bool(current_principal_did and principal_did == current_principal_did)
        checked = is_current or principal_did in linked_principal_ids
        linked_control = (
            f'<input type="hidden" name="linked_principal_ids" value="{html.escape(principal_did)}" />'
            f'<input type="checkbox" checked disabled aria-label="Linked {html.escape(display_name)}" />'
            if is_current
            else f'<input type="checkbox" name="linked_principal_ids" value="{html.escape(principal_did)}"{" checked" if checked else ""} aria-label="Linked {html.escape(display_name)}" />'
        )
        rows.append(
            f"""
            <tr>
              <td><strong>{html.escape(display_name)}</strong>{' <span class="muted">(You)</span>' if is_current else ''}</td>
              <td title="{html.escape(verified_id)}">{html.escape(_truncate_identifier(verified_id))}</td>
              <td>{html.escape(principal_type)}</td>
              <td><span class="status-chip"><span class="status-dot {html.escape(_status_dot_class(status))}"></span>{html.escape(status)}</span></td>
              <td>{linked_control}</td>
            </tr>
            """
        )
    return f"""
    <table class="entity-list-table" style="width:100%;">
      <thead>
        <tr>
          <th>Name</th>
          <th>Verified ID</th>
          <th>Type</th>
          <th>Status</th>
          <th>Linked</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    """


def _selection_list(
    *,
    items: list[dict[str, Any]],
    field_name: str,
    kind: str,
    selected_values: set[str],
    multi: bool,
    locked_values: set[str] | None = None,
) -> str:
    locked = locked_values or set()
    rows: list[str] = []
    input_type = "checkbox" if multi else "radio"
    for item in items:
        if not isinstance(item, dict):
            continue
        verified_id = str(item.get("canonical_subject") or "").strip()
        if kind == "ledger":
            item_id = str(item.get("ledger_id") or "").strip()
            title = str(item.get("ledger_name") or item_id).strip() or item_id
            title_html = f"<strong>{html.escape(title)}</strong>"
            ledger_tail = item_id.split(":", 1)[1] if ":" in item_id else item_id
            if item_id and item_id != title:
                title_html += f'<span class="muted" style="display:block; margin-top:2px;">Operational ID: {html.escape(item_id)}</span>'
            elif item_id.startswith("ledger:") and ledger_tail == title:
                title_html = (
                    f"<strong>{html.escape(title)}</strong>"
                    f'<span class="muted" style="display:block; margin-top:2px;">Operational ID: {html.escape(item_id)}</span>'
                )
        elif kind == "principal":
            item_id = str(item.get("principal_did") or "").strip()
            title = str(item.get("display_name") or item_id).strip() or item_id
            title_html = f"<strong>{html.escape(title)}</strong>"
        else:
            item_id = str(item.get("surface_id") or "").strip()
            title = str(item.get("label") or item.get("name") or item_id).strip() or item_id
            title_html = f"<strong>{html.escape(title)}</strong>"
        if not item_id:
            continue
        verified_id = verified_id or item_id
        checked = item_id in selected_values or item_id in locked
        disabled = item_id in locked
        control_html = (
            f'<input type="hidden" name="{html.escape(field_name)}" value="{html.escape(item_id)}" />'
            if disabled and checked
            else f'<input type="{input_type}" name="{html.escape(field_name)}" value="{html.escape(item_id)}"{" checked" if checked else ""} />'
        )
        rows.append(
            f"""
            <label class="collection-list-row no-extended" data-filter-name="{html.escape(f'{title} {verified_id}'.lower())}">
              <span class="collection-list-cell-name">{title_html}</span>
              <span class="collection-list-meta" title="{html.escape(verified_id)}">{html.escape(_truncate_identifier(verified_id))}</span>
              <span class="collection-list-meta">
                <span class="status-dot {html.escape(_status_dot_class(str(item.get('status') or 'unknown')))}" style="margin-right:5px;"></span>{html.escape(str(item.get("status") or "unknown"))}
              </span>
              <span class="collection-list-meta">{control_html}</span>
            </label>
            """
        )
    rendered_ids = {
        str(item.get("ledger_id") or item.get("principal_did") or item.get("surface_id") or "").strip()
        for item in items
        if isinstance(item, dict)
    }
    for extra_id in sorted((selected_values | locked) - rendered_ids):
        rows.append(
            f"""
            <label class="collection-list-row no-extended" data-filter-name="{html.escape(str(extra_id).lower())}">
              <span class="collection-list-cell-name"><strong>{html.escape(str(extra_id))}</strong></span>
              <span class="collection-list-meta" title="{html.escape(str(extra_id))}">{html.escape(_truncate_identifier(str(extra_id)))}</span>
              <span class="collection-list-meta"><span class="status-dot pending" style="margin-right:5px;"></span>unavailable</span>
              <span class="collection-list-meta">
                <input type="hidden" name="{html.escape(field_name)}" value="{html.escape(str(extra_id))}" />
                <input type="{input_type}" checked disabled />
              </span>
            </label>
            """
        )
    if not rows:
        return '<div class="empty-state"><p>No records are available yet.</p></div>'
    return (
        '<div class="collection-list"><div class="collection-list-head no-extended">'
        "<span>Name</span><span>Canonical ID</span><span>Status</span><span>Select</span>"
        f"</div>{''.join(rows)}</div>"
    )


def _modal_shell(*, modal_id: str, title: str, description: str, body_html: str, actions_html: str) -> str:
    return f"""
    <div class="modal-shell" id="{html.escape(modal_id)}" hidden>
      <div class="modal-backdrop" data-modal-overlay="true"></div>
      <section class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="{html.escape(modal_id)}-title" aria-describedby="{html.escape(modal_id)}-desc">
        <div class="modal-header">
          <div>
            <h2 id="{html.escape(modal_id)}-title">{html.escape(title)}</h2>
            <p id="{html.escape(modal_id)}-desc" class="muted">{html.escape(description)}</p>
          </div>
          <button type="button" class="btn small" data-close-modal="true" aria-label="Close dialog">Close</button>
        </div>
        <div class="modal-body">
          {body_html}
        </div>
        <div class="modal-actions">
          {actions_html}
        </div>
      </section>
    </div>
    """


def _wizard_table_section(
    *,
    title: str,
    description: str,
    table_html: str,
    add_button_html: str,
) -> str:
    return f"""
    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:12px;">
      <div style="flex:1 1 auto;">
        <h2>{html.escape(title)}</h2>
        <p>{html.escape(description)}</p>
      </div>
      <div style="flex:0 0 auto;">
        {add_button_html}
      </div>
    </div>
    {table_html}
    """


def _wizard_table_modal(
    *,
    action: str,
    state: dict[str, str],
    current_step: str,
    add_entity_kind: str,
    modal_title: str,
    modal_description: str,
    modal_body_html: str,
    modal_target_field: str,
    modal_target_mode: str,
) -> tuple[str, str]:
    modal_id = f"{_flow_slug(current_step, 'wizard-step')}-{add_entity_kind}-add-modal"
    modal_form = f"""
    <form method="post" action="{html.escape(action)}">
      {_hidden_inputs(state, exclude={'submit_action'})}
      <input type="hidden" name="step" value="{html.escape(current_step)}" />
      <input type="hidden" name="wizard_action" value="modal_add" />
      <input type="hidden" name="modal_entity_kind" value="{html.escape(add_entity_kind)}" />
      <input type="hidden" name="modal_target_field" value="{html.escape(modal_target_field)}" />
      <input type="hidden" name="modal_target_mode" value="{html.escape(modal_target_mode)}" />
      {modal_body_html}
      <div class="modal-actions">
        <button type="button" class="btn" data-close-modal="true">Cancel</button>
        <button type="submit" class="btn primary">Add</button>
      </div>
    </form>
    """
    return (
        f'<button type="button" class="btn" data-open-modal="{html.escape(modal_id)}">Add</button>',
        _modal_shell(
            modal_id=modal_id,
            title=modal_title,
            description=modal_description,
            body_html=modal_form,
            actions_html="",
        ),
    )


def _surface_binding_options(binding_records: list[dict[str, Any]] | None) -> list[tuple[str, str]]:
    options = [("", "Select a governed model binding")]
    for item in binding_records or []:
        binding_id = str(item.get("binding_id") or "").strip()
        if not binding_id:
            continue
        label = str(item.get("name") or binding_id).strip() or binding_id
        provider_type = str(item.get("provider_type") or "unknown").strip()
        model_id = str(item.get("model_id") or "unknown").strip()
        options.append((binding_id, f"{label} ({provider_type} / {model_id})"))
    return options


def render_add_ledger_flow(*, step: str, state: dict[str, str], principals: list[dict[str, str]] | None = None, surfaces: list[dict[str, str]] | None = None, current_principal_did: str = "", binding_records: list[dict[str, str]] | None = None) -> str:
    steps = ["Ledger details", "Principal/s access", "Surfaces used", "Summary"]
    current_index = steps.index(step) if step in steps else 0
    back_step = steps[max(current_index - 1, 0)] if current_index > 0 else ""
    next_step = steps[min(current_index + 1, len(steps) - 1)]
    action = "/connections/add/ledger"
    state = dict(state)
    state["entity_kind"] = "ledger"
    ledger_name = str(state.get("name") or state.get("ledger_name") or "").strip()
    ledger_slug = _flow_slug(ledger_name, "pending")
    ledger_id = str(state.get("ledger_id") or "").strip() or f"ledger:{ledger_slug}"
    tenant_id = str(state.get("tenant_id") or "tenant:demo").strip() or "tenant:demo"
    provisioned_verified_id = str(state.get("provisioned_verified_id") or "").strip() or f"did:web:{os.getenv('DEFAULT_DID_HOST', '')}:ledgers:{ledger_slug}"
    founding_constitution_name = str(state.get("founding_constitution_name") or "").strip() or ledger_name
    founding_constitution_personality = str(state.get("founding_constitution_personality") or "").strip()
    founding_constitution_purpose = str(state.get("founding_constitution_purpose") or "").strip()
    topology_mode = str(state.get("ledger_topology") or "prime").strip().lower() or "prime"
    linked_principal_ids = {
        value.strip()
        for value in str(state.get("linked_principal_ids") or "").split(",")
        if value.strip()
    }
    linked_surface_ids = {
        value.strip()
        for value in str(state.get("linked_surface_ids") or "").split(",")
        if value.strip()
    }
    if current_principal_did:
        linked_principal_ids.add(current_principal_did)
    state["ledger_id"] = ledger_id
    state["tenant_id"] = tenant_id
    state["provisioned_verified_id"] = provisioned_verified_id
    state["founding_constitution_name"] = founding_constitution_name
    state["founding_constitution_personality"] = founding_constitution_personality
    state["founding_constitution_purpose"] = founding_constitution_purpose
    state["ledger_topology"] = topology_mode
    state["linked_principal_ids"] = ",".join(sorted(linked_principal_ids))
    state["linked_surface_ids"] = ",".join(sorted(linked_surface_ids))
    if step == "Ledger details":
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            next_step=next_step,
            next_label="Next",
            hidden_exclude={"name", "tenant_id", "provisioned_verified_id", "founding_constitution_name", "founding_constitution_personality", "founding_constitution_purpose", "ledger_topology", "linked_principal_ids"},
            fields_html=f"""
            <h2>Ledger details</h2>
            <p>Provide the ledger name. DSS will derive the governed identifiers and initial topology from it, then show them in the summary.</p>
            {_text_input("name", "Ledger name", ledger_name)}
            {_text_input("founding_constitution_name", "What the ledger calls itself", founding_constitution_name, placeholder="")}
            <label for="founding_constitution_personality">Starter personality</label>
            <textarea id="founding_constitution_personality" name="founding_constitution_personality" rows="4" placeholder="Deliberate, layered, patient with complexity.">{html.escape(founding_constitution_personality)}</textarea>
            <label for="founding_constitution_purpose">Founding purpose</label>
            <textarea id="founding_constitution_purpose" name="founding_constitution_purpose" rows="3" placeholder="What this ledger is for.">{html.escape(founding_constitution_purpose)}</textarea>
            <fieldset style="border:none; padding:0; margin:12px 0 0;">
              <legend style="font-weight:600; margin-bottom:8px;">Ledger topology</legend>
              <label style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
                <input type="radio" name="ledger_topology" value="prime"{" checked" if topology_mode != "graph" else ""} />
                <span>Prime-based</span>
              </label>
              <label style="display:flex; align-items:center; gap:8px; color:var(--text-muted);">
                <input type="radio" name="ledger_topology" value="graph" disabled{" checked" if topology_mode == "graph" else ""} />
                <span>Graph-based (coming soon)</span>
              </label>
            </fieldset>
            """,
        )
    elif step == "Principal/s access":
        add_button_html, modal_html = _wizard_table_modal(
            action=action,
            state=state,
            current_step=step,
            add_entity_kind="principal",
            modal_title="Add Principal",
            modal_description="Capture the principal details without leaving this wizard step.",
            modal_body_html=f"""
            {_text_input("modal_display_name", "Display name", "")}
            {_radio_list_input("modal_principal_type", "Type", "human", _principal_type_options())}
            """,
            modal_target_field="linked_principal_ids",
            modal_target_mode="multi",
        )
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            next_step=next_step,
            next_label="Next",
            hidden_exclude={"linked_principal_ids"},
            fields_html=_wizard_table_section(
                title="Principal/s access",
                description="Select which principals should be linked when this ledger is added. The signed-in principal remains linked by default.",
                table_html=_selection_list(items=principals or [], field_name="linked_principal_ids", kind="principal", selected_values=linked_principal_ids, multi=True, locked_values={current_principal_did} if current_principal_did else set()),
                add_button_html=add_button_html,
            ),
            after_form_html=modal_html,
        )
    elif step == "Surfaces used":
        add_button_html, modal_html = _wizard_table_modal(
            action=action,
            state=state,
            current_step=step,
            add_entity_kind="surface",
            modal_title="Add Surface",
            modal_description="Capture the surface details without leaving this wizard step.",
            modal_body_html=f"""
            {_text_input("modal_name", "Surface name", "")}
            {_select_input("modal_surface_type", "Surface type", "Custom", [("Custom", "Custom"), ("Chat", "Chat"), ("Telegram", "Telegram"), ("MCP / third-party app", "MCP / third-party app")])}
            {_select_input("modal_default_binding_id", "Approved model binding", "", _surface_binding_options(binding_records))}
            """,
            modal_target_field="linked_surface_ids",
            modal_target_mode="multi",
        )
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            next_step=next_step,
            next_label="Next",
            hidden_exclude={"linked_surface_ids"},
            fields_html=_wizard_table_section(
                title="Surfaces used",
                description="Select the surfaces that should operate through this ledger boundary.",
                table_html=_selection_list(items=surfaces or [], field_name="linked_surface_ids", kind="surface", selected_values=linked_surface_ids, multi=True),
                add_button_html=add_button_html,
            ),
            after_form_html=modal_html,
        )
    else:
        selected_names: list[str] = []
        principal_map = {
            str(item.get("principal_did") or "").strip(): str(item.get("display_name") or item.get("principal_did") or "").strip()
            for item in (principals or [])
            if isinstance(item, dict)
        }
        for principal_id in sorted(linked_principal_ids):
            label = principal_map.get(principal_id) or principal_id
            if current_principal_did and principal_id == current_principal_did:
                label = f"{label} (You)"
            selected_names.append(label)
        surface_map = {
            str(item.get("surface_id") or "").strip(): str(item.get("label") or item.get("name") or item.get("surface_id") or "").strip()
            for item in (surfaces or [])
            if isinstance(item, dict)
        }
        selected_surfaces = [surface_map.get(surface_id) or surface_id for surface_id in sorted(linked_surface_ids)]
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            final_submit=True,
            summary_html=f"""
            <h2>Summary</h2>
            <p>Review the ledger details and linked principals before adding it.</p>
            <ul>
              <li><strong>Name:</strong> {html.escape(ledger_name or '—')}</li>
              <li><strong>Ledger ID:</strong> {html.escape(ledger_id)}</li>
              <li><strong>Tenant ID:</strong> {html.escape(tenant_id)}</li>
              <li><strong>Provisioned verified ID:</strong> {html.escape(provisioned_verified_id)}</li>
              <li><strong>Ledger self-name:</strong> {html.escape(founding_constitution_name or '—')}</li>
              <li><strong>Starter personality:</strong> {html.escape(founding_constitution_personality or '—')}</li>
              <li><strong>Founding purpose:</strong> {html.escape(founding_constitution_purpose or '—')}</li>
              <li><strong>Topology:</strong> {'Prime-based' if topology_mode != 'graph' else 'Graph-based'}</li>
              <li><strong>Linked principals:</strong> {html.escape(', '.join(selected_names) or '—')}</li>
              <li><strong>Surfaces used:</strong> {html.escape(', '.join(selected_surfaces) or '—')}</li>
            </ul>
            """,
        )
    return render_flow_shell(
        title=_title_with_name("Add or edit Ledger", ledger_name),
        description="Define a ledger; a governed memory boundary for AI activity, including which principals can act and which surfaces can be used.",
        steps=steps,
        current_step=step,
        main_html=main_html,
    )


def render_add_principal_flow(*, step: str, state: dict[str, str], ledgers: list[dict[str, str]] | None = None, surfaces: list[dict[str, str]] | None = None, binding_records: list[dict[str, str]] | None = None) -> str:
    steps = ["Principal details", "Ledger/s access", "Principal/s access", "Summary"]
    current_index = steps.index(step) if step in steps else 0
    back_step = steps[max(current_index - 1, 0)] if current_index > 0 else ""
    next_step = steps[min(current_index + 1, len(steps) - 1)]
    action = "/connections/add/principal"
    state = dict(state)
    state["entity_kind"] = "principal"
    is_edit = bool(str(state.get("principal_id") or "").strip())
    display_name = str(state.get("display_name") or state.get("name") or "").strip()
    if display_name and not str(state.get("principal_did") or "").strip():
        state["principal_did"] = f"principal:{_flow_slug(display_name, 'pending')}"
    principal_type = str(state.get("principal_type") or "human").strip().lower() or "human"
    if principal_type == "agent":
        principal_type = "model"
        state["principal_type"] = "model"
    service_subtype = str(state.get("service_subtype") or "delegated_agent").strip().lower() or "delegated_agent"
    state["service_subtype"] = service_subtype
    if principal_type == "service" and service_subtype == "delegated_agent":
        state["display_name"] = "OpenAI: Codex"
        state["principal_did"] = os.getenv("CODEX_PRINCIPAL_DID", "")
        display_name = "OpenAI: Codex"
    linked_ledger_ids = {
        value.strip()
        for value in str(state.get("linked_ledger_ids") or "").split(",")
        if value.strip()
    }
    linked_surface_ids = {
        value.strip()
        for value in str(state.get("linked_surface_ids") or "").split(",")
        if value.strip()
    }
    available_models: list[dict[str, str]] = []
    try:
        decoded_models = json.loads(str(state.get("available_models_json") or "[]"))
        if isinstance(decoded_models, list):
            available_models = [item for item in decoded_models if isinstance(item, dict)]
    except Exception:
        available_models = []
    if step == "Principal details":
        current_email = str(state.get("contact_email") or "").strip()
        current_did_values = [
            value.strip()
            for value in str(state.get("current_did_values") or "").splitlines()
            if value.strip()
        ]
        email_editor_default_open = not current_email
        did_editor_default_open = not current_did_values
        model_option_pairs = [
            (str(item.get("id") or "").strip(), str(item.get("label") or item.get("id") or "").strip())
            for item in available_models
            if str(item.get("id") or "").strip()
        ]
        model_lookup_error = str(state.get("model_lookup_error") or "").strip()
        type_options = [
            ("human", "Human", "A person who can be invited and linked to a governed identity."),
            ("organisation", "Group / organisation", "A collective or institutional principal."),
            ("model", "Model / agent", "A provider-backed model or automated agent principal."),
            ("service", "Service", "A non-human service principal, including delegated external agents."),
            ("device", "Device", "A hardware or endpoint principal."),
        ]
        service_subtype_options = _service_subtype_options()
        default_ledger_scope = next(iter(linked_ledger_ids), str(state.get("ledger_id") or "").strip())
        default_surface_scope = next(iter(linked_surface_ids), str(state.get("surface_id") or "").strip())
        panel_map = {
            "human": f"""
                <div class="field-summary-stack">
                  <div class="field-summary-row">
                    <div>
                      <div class="field-summary-label">Email address</div>
                      <div class="field-summary-value">{html.escape(current_email or "No email recorded yet")}</div>
                    </div>
                    <button class="btn subtle" type="button" data-toggle-target="principal-contact-email-editor">Edit</button>
                  </div>
                  <div id="principal-contact-email-editor"{" hidden" if not email_editor_default_open else ""} style="margin-top:10px;">
                    {_text_input("contact_email", "Email address", state.get("contact_email", ""), placeholder="person@example.com")}
                  </div>
                </div>
                <div class="field-summary-stack" style="margin-top:14px;">
                  <div class="field-summary-row">
                    <div>
                      <div class="field-summary-label">DID values</div>
                      <div class="field-summary-value">{('<br />'.join(html.escape(item) for item in current_did_values) if current_did_values else 'No DID recorded yet')}</div>
                    </div>
                    <button class="btn subtle" type="button" data-toggle-target="principal-did-editor">Edit</button>
                  </div>
                  <div id="principal-did-editor"{" hidden" if not did_editor_default_open else ""} style="margin-top:10px;">
                    <fieldset style="border:none; padding:0; margin:12px 0 0;">
                      <legend style="font-weight:600; margin-bottom:8px;">DID preference</legend>
                      <label style="display:flex; align-items:flex-start; gap:10px; margin:0 0 8px; font-weight:500; color:var(--text);">
                        <input type="radio" name="did_mode" value="provision_new"{" checked" if str(state.get("did_mode") or "provision_new").strip().lower() != "use_existing" else ""} />
                        <span>Provision new</span>
                      </label>
                      <label style="display:flex; align-items:flex-start; gap:10px; margin:0; font-weight:500; color:var(--text);">
                        <input type="radio" name="did_mode" value="use_existing"{" checked" if str(state.get("did_mode") or "").strip().lower() == "use_existing" else ""} />
                        <span>Add existing DID</span>
                      </label>
                    </fieldset>
                    {_text_input("existing_did", "Existing DID", state.get("existing_did", ""), placeholder="did:key:... or did:web:...")}
                  </div>
                </div>
                <div class="inline-actions" style="margin-top:10px;">
                  <button class="btn" type="submit" name="wizard_action" value="invite_human">Invite</button>
                  <span class="muted">Send an onboarding email that starts Microsoft Authenticator / DID setup.</span>
                </div>
            """,
            "organisation": '<p class="muted" style="margin-top:4px;">Use this when the principal represents a team, entity, or organisation rather than an individual.</p>',
            "service": f"""
                {_radio_list_input("service_subtype", "Service subtype", service_subtype, service_subtype_options)}
                <div data-service-subtype-panel="delegated_agent"{" hidden" if service_subtype != "delegated_agent" else ""} class="card compact" style="margin-top:12px;">
                  <h3 style="margin:0 0 8px;">Codex delegated agent preset</h3>
                  <p class="muted" style="margin:0 0 12px;">Use this path for Codex. The wizard provisions the stable delegated principal through the governed Codex flow rather than creating a generic provider-backed model principal.</p>
                  {_readonly_input("codex_display_name", "Name", "OpenAI: Codex")}
                  {_readonly_input("codex_principal_did", "Principal DID", os.getenv("CODEX_PRINCIPAL_DID", ""))}
                  {_readonly_input("codex_principal_key_ref", "Principal key ref", "openai:agent:codex")}
                  {_readonly_input("default_ledger_scope", "Ledger scope", default_ledger_scope)}
                  {_readonly_input("default_surface_scope", "Surface scope", default_surface_scope)}
                  <label style="display:flex; align-items:flex-start; gap:10px; margin:12px 0 0; font-weight:500; color:var(--text);">
                    <input type="checkbox" name="confirm_delegated_only" value="yes"{" checked" if str(state.get("confirm_delegated_only") or "").strip().lower() == "yes" else ""} />
                    <span><strong>Confirm delegated-only posture</strong><span class="muted" style="display:block; margin-top:2px;">Codex stays scoped, attributable, and operator-requested. Generic provider/API fields do not apply here.</span></span>
                  </label>
                </div>
                <div data-service-subtype-panel="automation"{" hidden" if service_subtype != "automation" else ""}><p class="muted" style="margin-top:4px;">Use this for scheduled or event-driven service execution.</p></div>
                <div data-service-subtype-panel="integration"{" hidden" if service_subtype != "integration" else ""}><p class="muted" style="margin-top:4px;">Use this for external system or app connector principals.</p></div>
                <div data-service-subtype-panel="control_plane_service"{" hidden" if service_subtype != "control_plane_service" else ""}><p class="muted" style="margin-top:4px;">Use this for DSS-internal governance or migration actors.</p></div>
                <div data-service-subtype-panel="background_worker"{" hidden" if service_subtype != "background_worker" else ""}><p class="muted" style="margin-top:4px;">Use this for non-interactive queue or batch workers.</p></div>
                <div data-service-subtype-panel="verifier_auditor"{" hidden" if service_subtype != "verifier_auditor" else ""}><p class="muted" style="margin-top:4px;">Use this for verification, attestation, or audit-only principals.</p></div>
            """,
            "device": '<p class="muted" style="margin-top:4px;">Use this when the principal represents a device or endpoint rather than a person or service.</p>',
            "model": f"""
                {('<div class="banner warn" style="margin-bottom:12px;">Unable to process API: ' + html.escape(model_lookup_error) + '</div>' if model_lookup_error else '')}
                <fieldset style="border:none; padding:0; margin:0 0 12px;">
                  <legend style="font-weight:600; margin-bottom:8px;">API scope</legend>
                  <label style="display:flex; align-items:flex-start; gap:10px; margin:0 0 8px; font-weight:500; color:var(--text);">
                    <input type="radio" name="provider_scope" value="shared"{" checked" if str(state.get("provider_scope") or "shared").strip().lower() != "principal" else ""} />
                    <span>Global</span>
                  </label>
                  <label style="display:flex; align-items:flex-start; gap:10px; margin:0; font-weight:500; color:var(--text);">
                    <input type="radio" name="provider_scope" value="principal"{" checked" if str(state.get("provider_scope") or "").strip().lower() == "principal" else ""} />
                    <span>Local</span>
                  </label>
                </fieldset>
                <label for="provider_api_key">API</label>
                <div class="inline-actions" style="align-items:flex-end; gap:10px;">
                  <input id="provider_api_key" name="provider_api_key" type="password" autocomplete="off" value="{html.escape(str(state.get('provider_api_key') or ''))}" style="flex:1 1 auto;" />
                  <button class="btn" type="submit" name="wizard_action" value="load_models">Process API</button>
                </div>
                {_readonly_input("provider_ref", "Provider ref", state.get("provider_ref", ""))}
                {_readonly_input("credential_ref", "Credential ref", state.get("credential_ref", ""))}
                {(
                    f'<div class="card compact" style="margin-top:10px;"><p class="muted" style="margin:0;"><strong>{len(model_option_pairs)} model(s)</strong> loaded from the OpenRouter catalog. The standard set is derived automatically; no per-model selection is required.</p></div>'
                    if model_option_pairs
                    else '<p class="muted" style="margin-top:10px;">Process the API to load available models.</p>'
                )}
                <input type="hidden" name="model_id" value="{html.escape(str(state.get('model_id') or (model_option_pairs[0][0] if model_option_pairs else '')))}" />
            """,
        }
        if is_edit:
            panel_html = f"""
            {_text_input("display_name", "Name", state.get("display_name", ""))}
            <div class="card compact" style="margin-top:12px;">
              <p><strong>Type:</strong> {html.escape(next((label for value, label, _ in type_options if value == principal_type), principal_type.title()))}</p>
              <div style="margin-top:12px;">{panel_map.get(principal_type, '')}</div>
            </div>
            """
        else:
            panel_html = f"""
            {_text_input("display_name", "Name", state.get("display_name", ""))}
            {_radio_list_input("principal_type", "Type", principal_type, type_options)}
            {''.join(
                f'<div class="card compact" data-principal-type-panel="{html.escape(value)}"{" hidden" if principal_type != value else ""} style="margin-top:12px;">{panel_map.get(value, "")}</div>'
                for value, _, _ in type_options
            )}
            <script>
              (() => {{
                const syncPrincipalPanels = () => {{
                  const selected = document.querySelector('input[name="principal_type"]:checked')?.value || "human";
                  document.querySelectorAll("[data-principal-type-panel]").forEach((panel) => {{
                    panel.hidden = panel.getAttribute("data-principal-type-panel") !== selected;
                  }});
                  const serviceSelected = selected === "service";
                  const delegatedSubtype = document.querySelector('input[name="service_subtype"]:checked')?.value || "delegated_agent";
                  document.querySelectorAll("[data-service-subtype-panel]").forEach((panel) => {{
                    panel.hidden = !serviceSelected || panel.getAttribute("data-service-subtype-panel") !== delegatedSubtype;
                  }});
                  const nameInput = document.getElementById("display_name");
                  if (nameInput) {{
                    if (serviceSelected && delegatedSubtype === "delegated_agent") {{
                      if (!nameInput.dataset.manualValue) {{
                        nameInput.dataset.manualValue = nameInput.value || "";
                      }}
                      nameInput.value = "OpenAI: Codex";
                      nameInput.readOnly = true;
                    }} else {{
                      if (nameInput.readOnly && nameInput.dataset.manualValue !== undefined) {{
                        nameInput.value = nameInput.dataset.manualValue;
                      }}
                      nameInput.readOnly = false;
                    }}
                  }}
                }};
                document.querySelectorAll('input[name="principal_type"]').forEach((input) => {{
                  input.addEventListener("change", syncPrincipalPanels);
                }});
                document.querySelectorAll('input[name="service_subtype"]').forEach((input) => {{
                  input.addEventListener("change", syncPrincipalPanels);
                }});
                syncPrincipalPanels();
              }})();
            </script>
            """
        main_html = f"""
        <form method="post" action="{html.escape(action)}">
          {_hidden_inputs(state, exclude={"step", "submit_action", "provider_api_key"})}
          <input type="hidden" name="step" value="{html.escape(step)}" />
          <h2>Principal details</h2>
          <p>Capture the principal identity that should participate in governed operations. Name is independent of principal type for generic principals, while the Codex delegated-agent preset keeps a stable governed identity.</p>
          {panel_html}
          <script>
            (() => {{
              document.querySelectorAll("[data-toggle-target]").forEach((button) => {{
                button.addEventListener("click", () => {{
                  const targetId = button.getAttribute("data-toggle-target");
                  const target = targetId ? document.getElementById(targetId) : null;
                  if (!target) return;
                  target.hidden = !target.hidden;
                }});
              }});
            }})();
          </script>
          <div class="inline-actions" style="justify-content:space-between; width:100%; margin-top:16px;">
            <div class="inline-actions">
              <a class="btn" href="/connections">Cancel</a>
            </div>
            <div class="inline-actions">
              <button class="btn primary" type="submit" name="step" value="{html.escape(next_step)}">Next</button>
            </div>
          </div>
        </form>
        """
    elif step == "Ledger/s access":
        add_button_html, modal_html = _wizard_table_modal(
            action=action,
            state=state,
            current_step=step,
            add_entity_kind="ledger",
            modal_title="Add Ledger",
            modal_description="Capture the ledger details without leaving this wizard step.",
            modal_body_html=f"""
            {_text_input("modal_name", "Ledger name", "")}
            """,
            modal_target_field="linked_ledger_ids",
            modal_target_mode="multi",
        )
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            next_step=next_step,
            hidden_exclude={"linked_ledger_ids"},
            fields_html=_wizard_table_section(
                title="Ledger/s access",
                description="Select the ledgers this principal should be able to act within.",
                table_html=_selection_list(items=ledgers or [], field_name="linked_ledger_ids", kind="ledger", selected_values=linked_ledger_ids, multi=True),
                add_button_html=add_button_html,
            ),
            after_form_html=modal_html,
        )
    elif step == "Principal/s access":
        add_button_html, modal_html = _wizard_table_modal(
            action=action,
            state=state,
            current_step=step,
            add_entity_kind="surface",
            modal_title="Add Surface",
            modal_description="Capture the surface details without leaving this wizard step.",
            modal_body_html=f"""
            {_text_input("modal_name", "Surface name", "")}
            {_select_input("modal_surface_type", "Surface type", "Custom", [("Custom", "Custom"), ("Chat", "Chat"), ("Telegram", "Telegram"), ("MCP / third-party app", "MCP / third-party app")])}
            {_select_input("modal_default_binding_id", "Approved model binding", "", _surface_binding_options(binding_records))}
            """,
            modal_target_field="linked_surface_ids",
            modal_target_mode="multi",
        )
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            next_step=next_step,
            hidden_exclude={"linked_surface_ids", "enabled_state", "permission_scope"},
            fields_html=f"""
            {_wizard_table_section(
                title="Principal/s access",
                description="Select the surfaces this principal should receive access to. Initial access defaults to enabled with full scope; fine-grained edits happen after creation.",
                table_html=_selection_list(items=surfaces or [], field_name="linked_surface_ids", kind="surface", selected_values=linked_surface_ids, multi=True),
                add_button_html=add_button_html,
            )}
            <input type="hidden" name="enabled_state" value="enabled" />
            <input type="hidden" name="permission_scope" value="full" />
            """,
            after_form_html=modal_html,
        )
    else:
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            final_submit=True,
            summary_html=f"""
            <h2>Summary</h2>
            <p>Review the principal mutation before final confirmation.</p>
            <ul>{_summary_rows(state)}</ul>
            """,
        )
    return render_flow_shell(
        title=_title_with_name("Add or edit Principal", display_name),
        description="Define a principal; a human, runtime model, service, device, or delegated agent with permission to act within selected ledgers and surfaces.",
        steps=steps,
        current_step=step,
        main_html=main_html,
    )


def render_add_surface_flow(*, step: str, state: dict[str, str], binding_records: list[dict[str, str]] | None = None, ledgers: list[dict[str, str]] | None = None, principals: list[dict[str, str]] | None = None) -> str:
    steps = ["Surface details", "Ledger/s access", "Principal/s access", "Summary"]
    current_index = steps.index(step) if step in steps else 0
    back_step = steps[max(current_index - 1, 0)] if current_index > 0 else ""
    next_step = steps[min(current_index + 1, len(steps) - 1)]
    action = "/connections/add/surface"
    state = dict(state)
    state["entity_kind"] = "surface"
    surface_name = str(state.get("name") or state.get("label") or "").strip()
    if surface_name and not str(state.get("surface_id") or "").strip():
        state["surface_id"] = f"surface:{_flow_slug(surface_name, 'pending')}"
    if surface_name and not str(state.get("template_id") or "").strip():
        state["template_id"] = f"template:{_flow_slug(surface_name, 'pending')}"
    linked_ledger_ids = {
        value.strip()
        for value in str(state.get("linked_ledger_ids") or "").split(",")
        if value.strip()
    }
    linked_principal_ids = {
        value.strip()
        for value in str(state.get("linked_principal_ids") or "").split(",")
        if value.strip()
    }
    binding_options = _surface_binding_options(binding_records)
    if step == "Surface details":
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            next_step=next_step,
            fields_html=f"""
            <h2>Surface details</h2>
            <p>Define the governed surface identity and runtime classification. DSS will derive the surface and template IDs from the name, then show only the runtime fields that fit the selected surface type.</p>
            {_text_input("name", "Surface name", state.get("name", ""))}
            {_select_input("surface_type", "Surface type", state.get("surface_type", "Custom"), [("Custom", "Custom"), ("Chat", "Chat"), ("Telegram", "Telegram"), ("MCP / third-party app", "MCP / third-party app")])}
            {_surface_runtime_panels(state, binding_options)}
            """,
        )
    elif step == "Ledger/s access":
        add_button_html, modal_html = _wizard_table_modal(
            action=action,
            state=state,
            current_step=step,
            add_entity_kind="ledger",
            modal_title="Add Ledger",
            modal_description="Capture the ledger details without leaving this wizard step.",
            modal_body_html=f"""
            {_text_input("modal_name", "Ledger name", "")}
            """,
            modal_target_field="linked_ledger_ids",
            modal_target_mode="multi",
        )
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            next_step=next_step,
            hidden_exclude={"linked_ledger_ids"},
            fields_html=_wizard_table_section(
                title="Ledger access",
                description="Select the primary ledger this surface should operate through. This flow currently uses a single governed ledger boundary per surface.",
                table_html=_selection_list(items=ledgers or [], field_name="linked_ledger_ids", kind="ledger", selected_values=linked_ledger_ids, multi=False),
                add_button_html=add_button_html,
            ),
            after_form_html=modal_html,
        )
    elif step == "Principal/s access":
        add_button_html, modal_html = _wizard_table_modal(
            action=action,
            state=state,
            current_step=step,
            add_entity_kind="principal",
            modal_title="Add Principal",
            modal_description="Capture the principal details without leaving this wizard step.",
            modal_body_html=f"""
            {_text_input("modal_display_name", "Display name", "")}
            {_radio_list_input("modal_principal_type", "Type", "human", _principal_type_options())}
            """,
            modal_target_field="linked_principal_ids",
            modal_target_mode="multi",
        )
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            next_step=next_step,
            hidden_exclude={"linked_principal_ids"},
            fields_html=f"""
            {_wizard_table_section(
                title="Principal/s access",
                description="Select the principals that can access this surface, then set the initial access posture.",
                table_html=_selection_list(items=principals or [], field_name="linked_principal_ids", kind="principal", selected_values=linked_principal_ids, multi=True),
                add_button_html=add_button_html,
            )}
            {_select_input("enabled_state", "Access state", state.get("enabled_state", "enabled"), [("enabled", "Enabled"), ("disabled", "Disabled")])}
            {_select_input("permission_scope", "Permission scope", state.get("permission_scope", "full"), [("full", "Full"), ("custom", "Custom")])}
            """,
            after_form_html=modal_html,
        )
    else:
        main_html = _step_frame(
            action=action,
            state=state,
            current_step=step,
            back_step=back_step,
            final_submit=True,
            summary_html=f"""
            <h2>Summary</h2>
            <p>Review the surface mutation before final confirmation.</p>
            <ul>{_summary_rows(state)}</ul>
            """,
        )
    return render_flow_shell(
        title=_title_with_name("Add or edit Surface", surface_name),
        description="Define a surface; a channel or interface where interactions happen, connected to ledgers and accessed by authorised principals.",
        steps=steps,
        current_step=step,
        main_html=main_html,
    )


def render_accept_connection_flow(*, step: str, state: dict[str, str]) -> str:
    steps = ["Code entry", "Summary", "Final confirmation"]
    code = state.get("code") or state.get("invite_code") or ""
    current_index = steps.index(step) if step in steps else 0
    next_href = f"/connections/accept?step={html.escape(steps[min(current_index + 1, len(steps) - 1)])}"
    back_href = f"/connections/accept?step={html.escape(steps[max(current_index - 1, 0)])}" if current_index > 0 else ""
    actions_html = f"""
    <div class="inline-actions" style="justify-content:space-between; width:100%; margin-top:16px;">
      <div class="inline-actions">
        <a class="btn" href="/connections">Cancel</a>
      </div>
      <div class="inline-actions">
        {'<a class="btn" href="' + html.escape(back_href) + '">Back</a>' if back_href else ''}
        <a class="btn primary" href="{html.escape(next_href)}">Continue</a>
      </div>
    </div>
    """
    return render_flow_shell(
        title="Accept Connection",
        description="Enter an invitation code, review the proposed relationship, and confirm before acceptance.",
        steps=steps,
        current_step=step,
        main_html=f"""
        <h2>{html.escape(step)}</h2>
        <p><strong>Invitation code:</strong> {html.escape(code or '—')}</p>
        <p class="muted">Use this flow to review the connection request before it is accepted into governed operations.</p>
        {actions_html}
        """,
        aside_html="""
        <h2>Review Boundary</h2>
        <p>Accepting a connection confirms a relationship after you inspect the invite and its access impact.</p>
        <ul class="muted">
          <li>Verify the invite code and target relationship first.</li>
          <li>Check whether the resulting access should be immediate or governed.</li>
          <li>Confirm only when the boundary is the one you intend to create.</li>
        </ul>
        """,
    )
