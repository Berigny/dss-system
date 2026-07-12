"""Provider billing helpers for the chat backend."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

_BALANCE_KEYS = {
    "balance",
    "balances",
    "credit_balance",
    "credits",
    "remaining_credits",
    "remaining_credit",
    "usd_balance",
}


def _coerce_float(value: Any) -> float | None:
    try:
        if isinstance(value, str):
            value = value.strip().lstrip("$")
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_from_mapping(payload: dict[str, Any]) -> float | None:
    limit = None
    usage = None
    for key, value in payload.items():
        lowered = key.lower().replace(" ", "_")
        if lowered in _BALANCE_KEYS:
            parsed = _coerce_float(value)
            if parsed is not None:
                return parsed
        if lowered in {"limit", "credit_limit", "total_credits"}:
            limit = _coerce_float(value)
        if lowered in {"usage", "used", "credits_used"}:
            usage = _coerce_float(value)
        if isinstance(value, dict):
            nested = _extract_from_mapping(value)
            if nested is not None:
                return nested
        if isinstance(value, list):
            nested = _extract_from_sequence(value)
            if nested is not None:
                return nested
    if limit is not None and usage is not None:
        return limit - usage
    return None


def _extract_from_sequence(payload: list[Any]) -> float | None:
    for item in payload:
        if isinstance(item, dict):
            nested = _extract_from_mapping(item)
        elif isinstance(item, list):
            nested = _extract_from_sequence(item)
        else:
            nested = None
        if nested is not None:
            return nested
    return None


async def _fetch_openrouter_balance(api_key: str) -> tuple[float | None, dict[str, str]]:
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get("https://openrouter.ai/api/v1/auth/key", headers=headers)
        LOGGER.info(f"OpenRouter response: {response.text}")
        response.raise_for_status()
    header_map = {k.lower(): v for k, v in response.headers.items()}
    balance = None
    for candidate in (
        "x-openrouter-balance",
        "x-openrouter-credits-remaining",
        "x-remaining-credits",
        "x-credits-remaining",
    ):
        if candidate in header_map:
            balance = _coerce_float(header_map[candidate])
            if balance is not None:
                break

    if balance is None:
        try:
            data = response.json()
        except Exception:  # pragma: no cover - defensive
            data = {}
        if isinstance(data, dict):
            balance = _extract_from_mapping(data)
        elif isinstance(data, list):
            balance = _extract_from_sequence(data)

    return balance, header_map


@router.get("/openrouter", summary="Fetch the current OpenRouter credit balance")
async def openrouter_balance() -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="OPENROUTER_API_KEY is not configured")

    try:
        balance, headers = await _fetch_openrouter_balance(api_key)
    except httpx.HTTPStatusError as exc:
        LOGGER.warning("OpenRouter balance request failed", exc_info=exc)
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        LOGGER.warning("OpenRouter balance request errored", exc_info=exc)
        raise HTTPException(status_code=502, detail="Failed to contact OpenRouter") from exc

    header_prefixes = ("x-openrouter", "x-credits", "x-remaining")
    return {
        "balance_usd": balance,
        "headers": {k: v for k, v in headers.items() if k.startswith(header_prefixes)},
    }
