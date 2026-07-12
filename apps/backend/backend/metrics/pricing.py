"""Model pricing helpers for token-based cost estimation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float
    output_per_million: float


_MODEL_PRICING: dict[str, ModelPricing] = {
    "meta-llama/llama-3.1-8b-instruct": ModelPricing(input_per_million=0.18, output_per_million=0.24),
    "meta-llama/llama-3.1-70b-instruct": ModelPricing(input_per_million=0.88, output_per_million=1.18),
    "gpt-4o-mini": ModelPricing(input_per_million=0.15, output_per_million=0.60),
}


def _normalize_model_name(model: str) -> str:
    trimmed = model.strip()
    if "/" in trimmed:
        trimmed = trimmed.split("/", 1)[-1]
    if ":" in trimmed:
        trimmed = trimmed.split(":", 1)[0]
    return trimmed


def get_model_pricing(model: str | None) -> ModelPricing | None:
    if not model:
        return None
    trimmed = model.strip()
    if trimmed in _MODEL_PRICING:
        return _MODEL_PRICING[trimmed]
    normalized = _normalize_model_name(model)
    return _MODEL_PRICING.get(normalized)


def estimate_cost_usd(
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> float | None:
    pricing = get_model_pricing(model)
    if pricing is None:
        return None
    input_tokens = max(prompt_tokens or 0, 0)
    output_tokens = max(completion_tokens or 0, 0)
    if input_tokens == 0 and output_tokens == 0:
        return None
    return (
        (input_tokens * pricing.input_per_million)
        + (output_tokens * pricing.output_per_million)
    ) / 1_000_000.0
