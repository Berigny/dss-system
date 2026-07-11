from __future__ import annotations

import html


def render_primary_nav(current_path: str) -> str:
    nav_items = [
        ("/", "Home"),
        ("/connections", "Manage Connections"),
        ("/activity", "Activity"),
        ("/benchmarks", "About"),
    ]
    links: list[str] = []
    for href, label in nav_items:
        is_active = (href == current_path) or (href != "/" and current_path.startswith(f"{href}/"))
        cls = "primary-nav-link active" if is_active else "primary-nav-link"
        links.append(f'<a class="{cls}" href="{href}">{html.escape(label)}</a>')
    return f'<nav id="primary-nav-root" class="primary-nav" aria-label="Primary">{"".join(links)}</nav>'


def render_profile_menu(profile_name: str, *, approval_count: int = 0) -> str:
    label = html.escape((profile_name or "").strip() or "Profile")
    settings_badge = f'<span class="profile-menu-badge">{approval_count}</span>' if approval_count > 0 else ""
    return f"""
    <div class="profile-menu">
      <button
        id="profile-menu-button"
        class="profile-menu-trigger"
        type="button"
        aria-haspopup="menu"
        aria-expanded="false"
        aria-controls="profile-menu-list"
      >
        <span>{label}</span>
        {settings_badge}
        <span aria-hidden="true">▾</span>
      </button>
      <div id="profile-menu-list" class="profile-menu-list" role="menu" tabindex="-1" aria-labelledby="profile-menu-button" hidden>
        <a role="menuitem" tabindex="-1" href="/settings">Settings{settings_badge}</a>
        <a role="menuitem" tabindex="-1" href="/logout">Sign out</a>
      </div>
    </div>
    """


def render_global_header(current_path: str, profile_name: str, *, organisation_name: str, approval_count: int = 0) -> str:
    initials = "".join(part[:1] for part in organisation_name.split()[:2]).upper() or "DS"
    return f"""
    <header class="global-header">
      <div class="global-header-row global-header-row-top">
        <a class="brand-link" href="/" aria-label="DSS Control Plane home">
          <span class="brand-badge">DSS</span>
          <span class="brand-copy"><strong>Control Plane</strong><span>Governed ledger operations</span></span>
        </a>
        <button
          id="primary-nav-toggle"
          class="nav-toggle"
          type="button"
          aria-expanded="false"
          aria-controls="primary-nav-root"
        >
          Menu
        </button>
        <form class="header-search" role="search" action="/connections" method="get">
          <label for="global-search" class="sr-only">Search DSS</label>
          <input id="global-search" name="q" type="search" placeholder="Search ledgers, principals, surfaces" />
          <button type="submit">Search</button>
        </form>
      </div>
      <div class="global-header-row global-header-row-nav">
        {render_primary_nav(current_path)}
        {render_profile_menu(profile_name, approval_count=approval_count)}
      </div>
    </header>
    """


def render_app_shell(
    content: str,
    *,
    current_path: str = "/",
    profile_name: str = "Profile",
    secondary_nav: str = "",
    organisation_name: str,
    approval_count: int = 0,
) -> str:
    secondary = f'<aside class="secondary-nav-shell">{secondary_nav}</aside>' if secondary_nav else ""
    return f"""
    <div class="app-shell">
      {render_global_header(current_path, profile_name, organisation_name=organisation_name, approval_count=approval_count)}
      <div class="app-body">
        {secondary}
        <main class="main-content">{content}</main>
      </div>
    </div>
    """
