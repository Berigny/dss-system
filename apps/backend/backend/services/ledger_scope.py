"""Shared ledger scope resolution and mismatch guards."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request

from backend.services.demo_mode import demo_default_ledger, demo_god_mode_enabled


def _clean_scope(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _header_ledger_scope(request: Request) -> str:
    for header in ("x-ledger-id", "x-ledger", "x-ledger-id-h64"):
        value = request.headers.get(header)
        cleaned = _clean_scope(value)
        if cleaned:
            return cleaned
    return ""


def resolve_ledger_scope_or_raise(
    request: Request,
    *,
    payload_ledger_id: str | None = None,
    path_ledger_id: str | None = None,
    hint: str = "provide ledger_id in payload or x-ledger-id header",
) -> str:
    """Resolve canonical ledger scope and reject deterministic mismatches."""
    payload_scope = _clean_scope(payload_ledger_id)
    header_scope = _header_ledger_scope(request)
    path_scope = _clean_scope(path_ledger_id)

    values = [scope for scope in (payload_scope, header_scope, path_scope) if scope]
    unique = sorted(set(values))
    strict_mode = os.getenv("LEDGER_SCOPE_STRICT", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if len(unique) > 1:
        if strict_mode and not demo_god_mode_enabled():
            # Defensive: casing alone (e.g. "loam" vs "LOAM") should not trigger a
            # deterministic mismatch. Only genuinely different scopes are rejected.
            unique_lower = sorted({v.lower() for v in unique})
            if len(unique_lower) > 1:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "ledger_scope_mismatch",
                        "payload_ledger_id": payload_scope or None,
                        "header_ledger_id": header_scope or None,
                        "path_ledger_id": path_scope or None,
                    },
                )
        # Compat precedence: payload > header > path.
        return payload_scope or header_scope or path_scope

    if unique:
        return unique[0]

    if demo_god_mode_enabled():
        return demo_default_ledger()

    raise HTTPException(
        status_code=422,
        detail={
            "error": "ledger_context_required",
            "hint": hint,
        },
    )
