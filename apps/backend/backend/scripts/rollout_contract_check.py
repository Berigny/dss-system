#!/usr/bin/env python3
"""Cross-service rollout contract checks for Fly + Vercel stack."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    insecure: bool = False,
) -> tuple[int, dict[str, Any]]:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    ssl_context = ssl._create_unverified_context() if insecure else None
    req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl_context) as resp:
            status = int(resp.status)
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read().decode("utf-8", errors="ignore")
    payload: dict[str, Any]
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {"raw": raw}
    return status, payload


def _ok(condition: bool, message: str, failures: list[str]) -> None:
    prefix = "PASS" if condition else "FAIL"
    print(f"[{prefix}] {message}")
    if not condition:
        failures.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate deployed Fly/Vercel contract.")
    parser.add_argument("--backend-url", default=os.getenv("BACKEND_URL", ""))
    parser.add_argument("--middleware-url", default=os.getenv("MIDDLEWARE_URL", ""))
    parser.add_argument("--frontend-url", default="https://ds-frontend-local-new.vercel.app")
    parser.add_argument("--expected-backend-sha", default="")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification.")
    args = parser.parse_args()

    backend = args.backend_url.rstrip("/")
    middleware = args.middleware_url.rstrip("/")
    frontend = args.frontend_url.rstrip("/")
    expected_sha = args.expected_backend_sha.strip()

    failures: list[str] = []

    b_status, b_health = _request_json(f"{backend}/health", insecure=args.insecure)
    _ok(b_status == 200, f"backend /health status={b_status}", failures)
    _ok(str(b_health.get("status")) == "ok", f"backend health status payload={b_health.get('status')}", failures)
    backend_sha = str(b_health.get("git_sha") or "").strip()
    _ok(bool(backend_sha), f"backend git_sha present ({backend_sha or 'missing'})", failures)
    if expected_sha:
        _ok(backend_sha == expected_sha, f"backend git_sha matches expected ({expected_sha})", failures)

    m_status, m_health = _request_json(f"{middleware}/health", insecure=args.insecure)
    _ok(m_status == 200, f"middleware /health status={m_status}", failures)
    _ok(str(m_health.get("status")) == "ok", f"middleware health status payload={m_health.get('status')}", failures)
    m_backend = str(m_health.get("backend_url") or "").strip()
    _ok(
        m_backend.rstrip("/") == backend.rstrip("/"),
        f"middleware backend_url routes to backend ({m_backend})",
        failures,
    )

    f_wake_status, f_wake = _request_json(f"{frontend}/api/wake", insecure=args.insecure)
    _ok(f_wake_status == 200, f"frontend /api/wake status={f_wake_status}", failures)
    _ok(str(f_wake.get("status")) in {"awake", "waking"}, f"frontend wake payload status={f_wake.get('status')}", failures)

    f_models_status, f_models = _request_json(f"{frontend}/api/models?agent=", insecure=args.insecure)
    _ok(f_models_status == 200, f"frontend /api/models status={f_models_status}", failures)
    _ok(
        isinstance(f_models, dict) or "raw" in f_models,
        "frontend models endpoint returns payload",
        failures,
    )

    f_limits_status, f_limits = _request_json(f"{frontend}/api/ingest/limits", insecure=args.insecure)
    _ok(f_limits_status == 200, f"frontend /api/ingest/limits status={f_limits_status}", failures)
    _ok(
        isinstance(f_limits.get("attachment_max_bytes"), int),
        "frontend ingest limits include attachment_max_bytes",
        failures,
    )

    if failures:
        print("\nRollout contract check FAILED:")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\nRollout contract check PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
