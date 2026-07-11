#!/usr/bin/env python3
"""Lightweight prod debugger for DSS Control Plane.

Run with your browser session token:

    DSS_SESSION_TOKEN="<value of ds_backend_session_token cookie>" \
    python scripts/prod_check.py

Or use the longer-lived refresh token to obtain a fresh session token:

    DSS_REFRESH_TOKEN="<value of ds_backend_refresh_token cookie>" \
    python scripts/prod_check.py

The script prints identity card info, fetches authenticated pages, and writes
the raw HTML to /tmp/dss-prod-check-* for inspection.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx

# Allow importing shared auth helper from the primary workspace repo.
_SCRIPTS_DIR = Path("/Users/davidberigny/Documents/GitHub/ds-review/scripts")
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from dss_auth import refresh_session_token

PROD_BASE = os.getenv("DSS_PROD_BASE", "")
SESSION_TOKEN = os.getenv("DSS_SESSION_TOKEN", "").strip()
REFRESH_TOKEN = os.getenv("DSS_REFRESH_TOKEN", "").strip()

if not SESSION_TOKEN and not REFRESH_TOKEN:
    print("Error: set DSS_SESSION_TOKEN or DSS_REFRESH_TOKEN.")
    sys.exit(1)

if REFRESH_TOKEN:
    refreshed_session, refreshed_refresh, info = refresh_session_token(REFRESH_TOKEN, PROD_BASE)
    if not refreshed_session:
        print("Error: failed to refresh session token.")
        print(json.dumps(info, indent=2, sort_keys=True), file=sys.stderr)
        sys.exit(1)
    SESSION_TOKEN = refreshed_session
    if refreshed_refresh:
        os.environ["DSS_REFRESH_TOKEN"] = refreshed_refresh
    print("Refreshed DSS session token via /api/auth/session/refresh", file=sys.stderr)
    principal = info.get("principal_did") or "?"
    print(f"  principal_did: {principal}", file=sys.stderr)

HEADERS = {
    "accept": "application/json",
    "x-session-token": SESSION_TOKEN,
}
COOKIE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _identity_card(client: httpx.Client) -> dict:
    resp = client.get(
        urljoin(PROD_BASE, "/api/auth/identity_card"),
        headers=HEADERS,
        cookies={"ds_backend_session_token": SESSION_TOKEN},
    )
    print(f"GET /api/auth/identity_card -> {resp.status_code}")
    return resp.json() if resp.status_code == 200 else {}


def _save(name: str, text: str) -> None:
    path = f"/tmp/dss-prod-check-{name}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  saved {path} ({len(text)} bytes)")


def _grep(text: str, *patterns: str) -> None:
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        print(f"  pattern {pattern!r}: {len(matches)} matches")
        for m in matches[:5]:
            snippet = (m[:120] + "...") if isinstance(m, str) and len(m) > 120 else m
            print(f"    - {snippet}")


def _check_page(client: httpx.Client, path: str) -> str:
    url = urljoin(PROD_BASE, path)
    resp = client.get(url, headers=COOKIE_HEADERS, cookies={"ds_backend_session_token": SESSION_TOKEN})
    print(f"GET {path} -> {resp.status_code}")
    if "Login" in resp.text or 'href="/login' in resp.text or "Sign In" in resp.text:
        print("  WARNING: response appears to be the login page (session token may be invalid/expired).")
    return resp.text


def main() -> int:
    with httpx.Client(follow_redirects=True, timeout=20.0) as client:
        card = _identity_card(client)
        identity_vc = card.get("identity_vc") or {}
        print("Identity:")
        print(f"  principal_did: {identity_vc.get('principal_did')}")
        print(f"  tenant_id:     {identity_vc.get('tenant_id')}")
        print(f"  ledger_id:     {identity_vc.get('ledger_id')}")
        print(f"  display_name:  {identity_vc.get('display_name') or identity_vc.get('principal_display_name')}")
        print(f"  verified:      {identity_vc.get('verified')}")

        print("\nChecking /connections ...")
        connections_html = _check_page(client, "/connections")
        _save("connections", connections_html)
        _grep(
            connections_html,
            r"Finish setting up your account",
            r"Continue setup",
            r"/connections/setup-guide",
            r"chat-demo",
            r"LOAM",
            r'<div class="collection-list-row[^"]*"[^>]*>',
        )

        print("\nChecking /connections/setup-guide ...")
        guide_html = _check_page(client, "/connections/setup-guide")
        _save("setup-guide", guide_html)
        _grep(
            guide_html,
            r"Continue Account Setup",
            r"Add a ledger",
            r"Add a principal",
            r"Add a surface",
            r"Configure permissions",
            r"Upload documents",
        )

        print("\nChecking control-plane API responses (should be scoped by auth) ...")
        for path in [
            "/api/control-plane/ledgers",
            "/api/control-plane/principals?limit=200",
            "/api/control-plane/surfaces",
            "/api/control-plane/relationships",
        ]:
            resp = client.get(
                urljoin(PROD_BASE, path),
                headers=HEADERS,
                cookies={"ds_backend_session_token": SESSION_TOKEN},
            )
            print(f"GET {path} -> {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                for key in ["ledgers", "principals", "surfaces", "relationships"]:
                    items = data.get(key)
                    if isinstance(items, list):
                        print(f"  {key}: {len(items)} items")
                        for item in items[:3]:
                            if isinstance(item, dict):
                                tid = item.get("tenant_id")
                                id_field = (
                                    item.get("ledger_id")
                                    or item.get("principal_did")
                                    or item.get("surface_id")
                                    or item.get("relationship_id")
                                    or "?"
                                )
                                print(f"    - {id_field} (tenant_id={tid!r})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
