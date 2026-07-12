"""Legacy compatibility routes that mirror the historic API surface.

These handlers translate legacy request bodies into the modern ledger
schemas so older clients can continue working without RocksDB access.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Dict, List, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.api.http import get_ledger_store
from backend.api.schemas import ContinuousStateSchema, LedgerEntrySchema, LedgerKeySchema
from backend.fieldx_kernel.substrate import LedgerStoreV2

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["compatibility"])


class Factor(BaseModel):
    prime: int
    delta: int


class AnchorRequest(BaseModel):
    entity: str
    factors: List[Factor]
    text: str | None = None


class RotateRequest(BaseModel):
    entity: str
    axis: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    angle: float


class RotateResponse(BaseModel):
    status: str
    reason: str


class Edge(BaseModel):
    src: int
    dst: int
    via_c: bool
    label: str


class MetricsResponse(BaseModel):
    entity: str
    entry_count: int
    last_updated: int | None = Field(None, description="Timestamp in milliseconds")


def _build_entry(payload: AnchorRequest, *, timestamp_ms: int) -> LedgerEntrySchema:
    coordinates: Dict[str, float] = {str(f.prime): float(f.delta) for f in payload.factors}
    metadata: Dict[str, object] = {"primes": [f.prime for f in payload.factors]}
    if payload.text:
        metadata["text"] = payload.text

    key = LedgerKeySchema(namespace=payload.entity, identifier=str(timestamp_ms))
    state = ContinuousStateSchema(
        coordinates=coordinates,
        metadata=metadata,
        phase="compat",
    )
    created_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

    return LedgerEntrySchema(key=key, state=state, created_at=created_at)


def _ledger_metrics(entity: str, store: LedgerStoreV2) -> MetricsResponse:
    count = 0
    last_updated: int | None = None

    try:
        with store._lock:  # type: ignore[attr-defined]
            snapshots = list(store._db.items())  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive fallback
        snapshots = []

    for _, raw_value in snapshots:
        try:
            entry = store._decode(raw_value)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - skip malformed rows
            continue

        if entry.key.namespace != entity:
            continue

        count += 1
        created_ms = int(entry.created_at.timestamp() * 1000)
        if last_updated is None or created_ms > last_updated:
            last_updated = created_ms

    return MetricsResponse(entity=entity, entry_count=count, last_updated=last_updated)


@router.post("/anchor")
def anchor(payload: AnchorRequest, store: LedgerStoreV2 = Depends(get_ledger_store)):
    timestamp_ms = int(time.time() * 1000)
    entry = _build_entry(payload, timestamp_ms=timestamp_ms)
    model = entry.to_model()
    store.write(model)

    edges = [
        Edge(src=f.prime, dst=f.prime, via_c=False, label=f"{f.prime}->{f.prime}")
        for f in payload.factors
    ]

    metadata = model.state.metadata or {}
    energy = metadata.get("p_adic_write_cost")

    return {
        "status": "anchored",
        "edges": edges,
        "centroid_at_write": 0,
        "timestamp": timestamp_ms,
        "cycle": None,
        "energy": energy if isinstance(energy, (int, float)) else None,
    }


@router.get("/ledger", response_model=MetricsResponse)
@router.get("/ledger/metrics", response_model=MetricsResponse)
def ledger_metrics(entity: str = Query(...), store: LedgerStoreV2 = Depends(get_ledger_store)):
    return _ledger_metrics(entity, store)


@router.post("/rotate", response_model=RotateResponse)
def rotate(payload: RotateRequest):
    LOGGER.info(
        "Legacy rotation requested; disabled in compatibility shim",
        extra={"entity": payload.entity, "axis": payload.axis, "angle": payload.angle},
    )
    return RotateResponse(status="skipped", reason="rotation-disabled")


__all__ = ["router"]
