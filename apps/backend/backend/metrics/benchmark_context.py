"""Helpers for attaching canonical production benchmark context to telemetry."""

from __future__ import annotations

import hashlib
import os

from fastapi import Request

from backend.metrics.prod_benchmark_contract import SurfaceName
from backend.metrics.telemetry import TurnTelemetry

_BUILD_SHA = os.getenv("GIT_SHA", "").strip() or "unknown"


def attach_request_benchmark_context(
    telemetry: TurnTelemetry,
    request: Request,
    *,
    surface: SurfaceName = SurfaceName.BACKEND,
    mode: str = "default",
    tenant_id: str | None = None,
) -> TurnTelemetry:
    """Attach low-overhead correlation fields needed for prod benchmark rollups."""

    headers = request.headers
    request_id = headers.get("x-request-id") or headers.get("x-correlation-id") or telemetry.ids.turn_id
    principal_id = headers.get("x-principal-did") or headers.get("x-principal-id") or telemetry.principal_id
    principal_hash = headers.get("x-principal-hash") or telemetry.principal_hash
    if not principal_id and not principal_hash:
        digest = hashlib.sha256(telemetry.ids.session_id.encode("utf-8")).hexdigest()[:16]
        principal_hash = f"sha256:{digest}"
    resolved_tenant_id = (
        tenant_id
        or headers.get("x-tenant-id")
        or headers.get("x-ledger-id")
        or telemetry.tenant_id
        or telemetry.ids.namespace
    )
    resolved_mode = headers.get("x-ds-mode") or telemetry.mode or mode
    build_sha = headers.get("x-build-sha") or telemetry.build_sha or _BUILD_SHA
    return telemetry.model_copy(
        update={
            "request_id": request_id,
            "tenant_id": resolved_tenant_id,
            "surface": surface.value,
            "mode": resolved_mode,
            "build_sha": build_sha,
            "principal_id": principal_id,
            "principal_hash": principal_hash,
        }
    )

