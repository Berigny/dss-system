#!/usr/bin/env python3
"""Diagnostic script for ledger-principal authorization issues.

Dumps the backend control-plane state relevant to chat authorization so an
operator can see why a principal is rejected for a ledger.

Usage:
    export BACKEND_API_URL=https://ds-backend-new.fly.dev
    export BACKEND_SESSION_TOKEN=<session cookie value>
    python scripts/diagnose_ledger_authz.py loam

Or pass the token on the command line:
    python scripts/diagnose_ledger_authz.py loam --token <token>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


def _get(url: str, token: str | None) -> dict[str, Any]:
    headers = {}
    if token:
        headers["authorization"] = f"Bearer {token}"
        headers["x-session-token"] = token
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
    if resp.status_code == 401:
        print(f"ERROR: 401 from {url} — token missing or expired", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose ledger principal authz")
    parser.add_argument("ledger_id", help="Ledger id to inspect (e.g. loam)")
    parser.add_argument("--token", help="Backend session token (or set BACKEND_SESSION_TOKEN)")
    parser.add_argument("--backend", help="Backend base URL (or set BACKEND_API_URL)")
    args = parser.parse_args()

    base = (args.backend or os.getenv("BACKEND_API_URL") or "").rstrip("/")
    if not base:
        print("ERROR: set BACKEND_API_URL or pass --backend", file=sys.stderr)
        sys.exit(1)
    token = args.token or os.getenv("BACKEND_SESSION_TOKEN")

    ledger_id = args.ledger_id

    print(f"=== Ledger principals for '{ledger_id}' ===")
    principals = _get(f"{base}/api/control-plane/ledger-principals?ledger_id={ledger_id}", token)
    print(json.dumps(principals, indent=2, default=str))

    print("\n=== All relationships ===")
    relationships = _get(f"{base}/api/control-plane/relationships", token)
    print(json.dumps(relationships, indent=2, default=str))

    print("\n=== Surfaces ===")
    surfaces = _get(f"{base}/api/control-plane/surfaces", token)
    print(json.dumps(surfaces, indent=2, default=str))

    print("\n=== Principals ===")
    principals_all = _get(f"{base}/api/control-plane/principals", token)
    print(json.dumps(principals_all, indent=2, default=str))

    print("\n=== Model bindings ===")
    bindings = _get(f"{base}/api/control-plane/model-bindings", token)
    print(json.dumps(bindings, indent=2, default=str))


if __name__ == "__main__":
    _main()
