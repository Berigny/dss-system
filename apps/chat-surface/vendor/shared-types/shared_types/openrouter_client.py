"""Shared OpenRouter client and response normaliser."""

from __future__ import annotations

import os
from typing import Any

import httpx


class OpenRouterClient:
    """Thin async client for the OpenRouter chat completions API."""

    def __init__(self, api_key: str | None = None, base_url: str = "https://openrouter.ai/api/v1"):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.base_url = base_url

    async def chat(self, payload: dict) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            return response.json()


def normalise_openrouter_response(raw: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert any OpenRouter-style response into a stable front-end shape.

    Output shape::

        {
          "text": str,
          "model": str,
          "tokens": {"prompt": int, "completion": int, "total": int},
          "cost": float,
          "raw": dict,
        }

    If the response is unusable, an ``error`` key is added instead of raising.
    """

    out: dict[str, Any] = {"text": "", "model": model, "raw": raw}

    try:
        choice = raw["choices"][0]
        msg = choice.get("message") or choice.get("delta") or {}
        content = msg.get("content") or ""

        # Some models wrap content inside a list of multimodal parts.
        if isinstance(content, list):
            text_parts = [c["text"] for c in content if c.get("type") == "text"]
            content = "".join(text_parts)

        out["text"] = content.strip()

        usage = raw.get("usage") or {}
        out["tokens"] = {
            "prompt": usage.get("prompt_tokens", 0),
            "completion": usage.get("completion_tokens", 0),
            "total": usage.get("total_tokens", 0),
        }

        if "total_cost" in raw:
            out["cost"] = float(raw["total_cost"])
        elif usage and "total_cost" in usage:
            out["cost"] = float(usage["total_cost"])
        else:
            out["cost"] = 0.0

    except Exception as exc:
        out["error"] = str(exc)

    return out
