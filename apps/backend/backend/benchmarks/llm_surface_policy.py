"""DSS-300 — LLM surface preference policy and budget tracker for v0.5 evals.

- Default surface: Kimi Code delegated through the chat surface.
- Explicit fallback: OpenRouter, opt-in only.
- Budget tracking with hard caps on calls, prompt tokens, and completion tokens.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


LlmSurface = Literal["kimi-delegated", "openrouter", "r1"]

DEFAULT_SURFACE: LlmSurface = "kimi-delegated"
FALLBACK_SURFACE: LlmSurface = "openrouter"

DEFAULT_CALLS_BUDGET = 100
DEFAULT_PROMPT_TOKENS_BUDGET = 100_000
DEFAULT_COMPLETION_TOKENS_BUDGET = 50_000


class LlmBudgetExceeded(RuntimeError):
    """Raised when an LLM-facing eval exceeds its declared budget."""


@dataclass
class LlmSurfaceBudget:
    """Tracks consumption against a declared LLM budget.

    The budget is enforced incrementally so that a long eval is aborted as soon
    as a cap is crossed, not after the fact.
    """

    calls_budget: int = DEFAULT_CALLS_BUDGET
    prompt_tokens_budget: int = DEFAULT_PROMPT_TOKENS_BUDGET
    completion_tokens_budget: int = DEFAULT_COMPLETION_TOKENS_BUDGET
    calls_actual: int = field(default=0, init=False)
    prompt_tokens_actual: int = field(default=0, init=False)
    completion_tokens_actual: int = field(default=0, init=False)

    def record(
        self,
        *,
        calls: int = 1,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Record consumption and raise if any cap is exceeded."""
        self.calls_actual += calls
        self.prompt_tokens_actual += prompt_tokens
        self.completion_tokens_actual += completion_tokens
        if self.calls_actual > self.calls_budget:
            raise LlmBudgetExceeded(
                f"LLM calls budget exceeded: {self.calls_actual} > {self.calls_budget}"
            )
        if self.prompt_tokens_actual > self.prompt_tokens_budget:
            raise LlmBudgetExceeded(
                f"Prompt token budget exceeded: {self.prompt_tokens_actual} > {self.prompt_tokens_budget}"
            )
        if self.completion_tokens_actual > self.completion_tokens_budget:
            raise LlmBudgetExceeded(
                f"Completion token budget exceeded: {self.completion_tokens_actual} > {self.completion_tokens_budget}"
            )

    def as_dict(self) -> dict[str, int]:
        return {
            "calls_budget": self.calls_budget,
            "prompt_tokens_budget": self.prompt_tokens_budget,
            "completion_tokens_budget": self.completion_tokens_budget,
            "calls_actual": self.calls_actual,
            "prompt_tokens_actual": self.prompt_tokens_actual,
            "completion_tokens_actual": self.completion_tokens_actual,
        }


def resolve_surface(
    *,
    cli_surface: str | None = None,
    openrouter_key: str | None = None,
) -> LlmSurface:
    """Return the active LLM surface respecting the v0.5 preference order.

    Precedence:
      1. Explicit CLI choice if valid.
      2. DSS_LLM_SURFACE environment variable if valid.
      3. OpenRouter if DSS_LLM_SURFACE=openrouter or OPENROUTER_API_KEY is present
         and no explicit Kimi choice was made.
      4. Kimi-delegated default.
    """
    env_surface = os.environ.get("DSS_LLM_SURFACE", "").strip().lower()
    candidates: list[str] = []
    if cli_surface:
        candidates.append(cli_surface.strip().lower())
    if env_surface:
        candidates.append(env_surface)

    for surface in candidates:
        if surface in ("kimi", "kimi-delegated"):
            return "kimi-delegated"
        if surface == "openrouter":
            if not openrouter_key and not os.environ.get("OPENROUTER_API_KEY"):
                raise RuntimeError(
                    "OpenRouter surface requested but OPENROUTER_API_KEY is not set"
                )
            return "openrouter"
        if surface in ("r1", "local", "deterministic"):
            return "r1"

    # If an OpenRouter key is present without an explicit surface choice, do not
    # auto-fall back; the default remains Kimi-delegated.
    return DEFAULT_SURFACE


def default_budget_from_env() -> LlmSurfaceBudget:
    """Build a budget from DSS_*_BUDGET environment variables."""
    return LlmSurfaceBudget(
        calls_budget=int(os.environ.get("DSS_LLM_CALLS_BUDGET", DEFAULT_CALLS_BUDGET)),
        prompt_tokens_budget=int(
            os.environ.get("DSS_PROMPT_TOKENS_BUDGET", DEFAULT_PROMPT_TOKENS_BUDGET)
        ),
        completion_tokens_budget=int(
            os.environ.get(
                "DSS_COMPLETION_TOKENS_BUDGET", DEFAULT_COMPLETION_TOKENS_BUDGET
            )
        ),
    )


def log_surface_and_budget() -> dict[str, object]:
    """Return a dict suitable for logging at eval entrypoint start."""
    surface = resolve_surface()
    budget = default_budget_from_env()
    return {
        "llm_surface": surface,
        "llm_surface_policy": "DSS-EVAL-v0.5-llm-surface-policy.md",
        "llm_budget": budget.as_dict(),
        "openrouter_available": bool(os.environ.get("OPENROUTER_API_KEY")),
    }
