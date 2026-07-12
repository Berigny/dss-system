"""Shared context scope resolution and enforcement guards."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request

from backend.services.demo_mode import demo_god_mode_enabled


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _header_context_id(request: Request) -> str:
    return _clean(request.headers.get("x-context-id"))


def resolve_context_id_or_raise(
    request: Request,
    *,
    payload_context_id: str | None = None,
    require_for_write: bool = False,
    hint: str = "provide context_id in payload or x-context-id header",
) -> str | None:
    """Resolve context identity with optional strict enforcement."""
    payload_scope = _clean(payload_context_id)
    header_scope = _header_context_id(request)

    if payload_scope and header_scope and payload_scope != header_scope and not demo_god_mode_enabled():
        raise HTTPException(
            status_code=400,
            detail={
                "error": "context_scope_mismatch",
                "payload_context_id": payload_scope,
                "header_context_id": header_scope,
            },
        )

    context_id = payload_scope or header_scope or ""
    mode = os.getenv("LEDGER_CONTEXT_ID_MODE", "compat").strip().lower()
    if mode not in {"compat", "enforce", "off", "disabled"}:
        mode = "compat"
    if mode in {"off", "disabled"}:
        mode = "compat"

    if require_for_write and mode == "enforce" and not context_id:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "context_id_required",
                "hint": hint,
            },
        )

    # Store resolved context for downstream policy checks (authz, telemetry, etc.).
    if hasattr(request, "state"):
        try:
            request.state.context_id = context_id or None  # type: ignore[attr-defined]
        except Exception:
            pass

    return context_id or None
