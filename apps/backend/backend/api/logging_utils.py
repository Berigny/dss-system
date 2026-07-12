"""Shared helpers for structured request logging."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
from typing import Dict, Generator

from fastapi import HTTPException, Request


def _duration_ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 3)


_RESERVED_LOGRECORD_KEYS = set(logging.makeLogRecord({}).__dict__.keys())


def _safe_extra(payload: Dict[str, object]) -> Dict[str, object]:
    """Prevent collision with reserved ``LogRecord`` attributes."""

    safe: Dict[str, object] = {}
    for key, value in payload.items():
        if key in _RESERVED_LOGRECORD_KEYS:
            safe[f"context_{key}"] = value
            continue
        safe[key] = value
    return safe


def _request_context(request: Request | None) -> Dict[str, object]:
    """Extract a minimal structured context from an HTTP request."""

    if request is None:
        return {}

    client_host = getattr(request.client, "host", None)

    context: Dict[str, object] = {
        "path": request.url.path,
        "method": request.method,
    }
    if client_host:
        context["client"] = client_host

    user_agent = request.headers.get("user-agent")
    if user_agent:
        context["user_agent"] = user_agent

    request_id = request.headers.get("x-request-id") or request.headers.get(
        "x-correlation-id"
    )
    if request_id:
        context["request_id"] = request_id

    return context


@contextmanager
def log_operation(
    logger: logging.Logger,
    operation: str,
    request: Request | None = None,
    **context: object,
) -> Generator[Dict[str, object], None, None]:
    """Emit structured logs around an operation.

    Parameters
    ----------
    logger:
        Logger to emit records to.
    operation:
        Identifier for the operation (e.g. ``"ledger_read"``).
    request:
        Optional request object to capture HTTP context.
    context:
        Additional key/value pairs to include in the log context. The yielded
        mapping can be mutated to add dynamic values before completion.
    """

    start = perf_counter()
    base: Dict[str, object] = {"operation": operation, **_request_context(request), **context}

    try:
        yield base
    except HTTPException as exc:
        logger.warning(
            "%s failed",  # Avoid traceback for expected HTTP errors
            operation,
            extra=_safe_extra({
                **base,
                "status": "error",
                "status_code": exc.status_code,
                "duration_ms": _duration_ms(start),
                "error": exc.detail,
            }),
        )
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception(
            "%s failed",
            operation,
            extra=_safe_extra({
                **base,
                "status": "error",
                "duration_ms": _duration_ms(start),
                "error": str(exc),
            }),
        )
        raise
    else:
        logger.info(
            "%s completed",
            operation,
            extra=_safe_extra({**base, "status": "success", "duration_ms": _duration_ms(start)}),
        )
