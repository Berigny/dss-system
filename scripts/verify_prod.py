#!/usr/bin/env python3
"""Post-deployment smoke test for the DSS production environment.

Usage:
    python scripts/verify_prod.py

Exits non-zero if any required health endpoint fails.
"""
from __future__ import annotations

import sys
import urllib.request
from urllib.error import HTTPError, URLError

ENDPOINTS = {
    "backend": "https://dss-system-backend.fly.dev/health",
    "middleware": "https://dss-system-middleware.fly.dev/health",
    "control-plane": "https://id.dualsubstrate.com/health",
    "chat-surface": "https://chat.dualsubstrate.com/health",
    "coord-demo": "https://coord-demo.vercel.app/health",
    "did-issuer": "https://dss-system-did-issuer.fly.dev/livez",
}


def check(name: str, url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read(1024).decode("utf-8", errors="replace")
            print(f"✅ {name}: HTTP {resp.status} -> {body[:120]}")
            return True
    except HTTPError as exc:
        print(f"❌ {name}: HTTP {exc.code} {exc.reason}")
        return False
    except URLError as exc:
        print(f"❌ {name}: {exc.reason}")
        return False
    except Exception as exc:  # pragma: no cover
        print(f"❌ {name}: {exc}")
        return False


def main() -> int:
    results = [check(name, url) for name, url in ENDPOINTS.items()]
    if all(results):
        print("\nAll production health checks passed.")
        return 0
    failed = [name for name, ok in zip(ENDPOINTS, results) if not ok]
    print(f"\nFailed checks: {', '.join(failed)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
