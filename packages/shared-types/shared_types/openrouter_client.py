"""Placeholder OpenRouter client wrapper.

Populated in DSS-240.
"""

from __future__ import annotations

import os

import httpx


class OpenRouterClient:
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
