"""Retrieve S1/S2/body primes without restrictions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, model_validator

from backend.api.http import get_ledger_store, get_memory_ledger, get_memory_substrate
from backend.config.settings import QP_PURE_OVERRIDE
from backend.fieldx_kernel import assemble_context
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.search.token_index import TokenPrimeIndex


router = APIRouter(tags=["assemble"])


class AssembleRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {"entity": "test"}}}

    entity: str | None = None
    message: str | None = None
    k: int | None = None
    quote_safe: int | None = None
    since: Optional[str] = None
    until: Optional[str] = None
    query_primes: list[int] | None = None
    hardening_level: int | None = None
    include_padic_diagnostics: bool | None = True
    qp_pure: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def allow_missing_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        return values


async def assemble_payload(
    *,
    entity: str,
    query: str | None,
    k: int,
    quote_safe: bool,
    since: datetime | None,
    until: datetime | None,
    ledger,
    substrate,
    store,
    token_index: TokenPrimeIndex | None,
    query_primes: list[int] | None = None,
    hardening_level: int | None = None,
    include_padic_diagnostics: bool | None = True,
) -> Dict[str, Any]:
    padic_store = store._padic_store if isinstance(store, LedgerStoreV2) else None
    return await assemble_context(
        entity=entity,
        query=query,
        k=k,
        quote_safe=quote_safe,
        since=since,
        until=until,
        ledger=ledger,
        substrate=substrate,
        store=store,
        token_index=token_index,
        padic_store=padic_store,
        query_primes=query_primes,
        hardening_level=hardening_level,
        include_padic_diagnostics=bool(include_padic_diagnostics),
    )


@router.post("/assemble")
async def assemble(
    request: Request,
    payload: AssembleRequest | None = Body(None),
    entity: str | None = Query(None),
    k: int = Query(3),
    quote_safe: int = Query(0),
    since: str | None = Query(None),
    until: str | None = Query(None),
    ledger=Depends(get_memory_ledger),
    substrate=Depends(get_memory_substrate),
    store=Depends(get_ledger_store),
):
    token_index = TokenPrimeIndex(request.app) if request is not None else None
    entity_value = payload.entity if payload and payload.entity is not None else entity
    if entity_value is None:
        raise HTTPException(status_code=422, detail="Entity is required")

    message_value = payload.message if payload and payload.message is not None else None
    k_value = payload.k if payload and payload.k is not None else k
    quote_safe_value = payload.quote_safe if payload and payload.quote_safe is not None else quote_safe
    since_value = payload.since if payload and payload.since is not None else since
    until_value = payload.until if payload and payload.until is not None else until
    query_primes_value = payload.query_primes if payload and payload.query_primes is not None else None
    hardening_level_value = payload.hardening_level if payload and payload.hardening_level is not None else None
    include_padic_diagnostics_value = (
        payload.include_padic_diagnostics if payload and payload.include_padic_diagnostics is not None else True
    )
    qp_pure_value = payload.qp_pure if payload and payload.qp_pure is not None else None
    qp_pure_token = None
    if qp_pure_value is not None:
        qp_pure_token = QP_PURE_OVERRIDE.set(qp_pure_value)

    since_dt: datetime | None = None
    if since_value:
        try:
            since_dt = datetime.fromisoformat(since_value.replace("Z", "+00:00"))
        except ValueError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=422, detail="Invalid since timestamp") from exc

    until_dt: datetime | None = None
    if until_value:
        try:
            until_dt = datetime.fromisoformat(until_value.replace("Z", "+00:00"))
        except ValueError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=422, detail="Invalid until timestamp") from exc

    try:
        payload = await assemble_payload(
            entity=entity_value,
            query=message_value,
            k=k_value,
            quote_safe=quote_safe_value == 1,
            since=since_dt,
            until=until_dt,
            ledger=ledger,
            substrate=substrate,
            store=store,
            token_index=token_index,
            query_primes=query_primes_value,
            hardening_level=hardening_level_value,
            include_padic_diagnostics=include_padic_diagnostics_value,
        )
    finally:
        if qp_pure_token is not None:
            QP_PURE_OVERRIDE.reset(qp_pure_token)

    return payload


__all__ = ["router", "assemble_payload"]
