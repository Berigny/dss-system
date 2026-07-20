"""Tests for DSS-300 LLM surface policy and budget tracker."""

from __future__ import annotations

import os

import pytest

from backend.benchmarks import llm_surface_policy as policy


class TestLlmSurfaceBudget:
    def test_record_increments_and_reports(self) -> None:
        budget = policy.LlmSurfaceBudget(
            calls_budget=2,
            prompt_tokens_budget=100,
            completion_tokens_budget=100,
        )
        budget.record(prompt_tokens=30, completion_tokens=20)
        budget.record(prompt_tokens=40, completion_tokens=10)
        assert budget.calls_actual == 2
        assert budget.prompt_tokens_actual == 70
        assert budget.completion_tokens_actual == 30

    def test_calls_cap_raises(self) -> None:
        budget = policy.LlmSurfaceBudget(calls_budget=1)
        budget.record()
        with pytest.raises(policy.LlmBudgetExceeded):
            budget.record()

    def test_prompt_token_cap_raises(self) -> None:
        budget = policy.LlmSurfaceBudget(prompt_tokens_budget=10)
        with pytest.raises(policy.LlmBudgetExceeded):
            budget.record(prompt_tokens=11)

    def test_completion_token_cap_raises(self) -> None:
        budget = policy.LlmSurfaceBudget(completion_tokens_budget=10)
        with pytest.raises(policy.LlmBudgetExceeded):
            budget.record(completion_tokens=11)


class TestResolveSurface:
    def test_default_is_kimi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DSS_LLM_SURFACE", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert policy.resolve_surface() == "kimi-delegated"

    def test_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DSS_LLM_SURFACE", "openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        assert policy.resolve_surface(cli_surface="kimi") == "kimi-delegated"

    def test_openrouter_requires_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DSS_LLM_SURFACE", "openrouter")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(RuntimeError):
            policy.resolve_surface()

    def test_openrouter_key_argument(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert policy.resolve_surface(cli_surface="openrouter", openrouter_key="k") == "openrouter"


class TestEnvBudget:
    def test_default_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DSS_LLM_CALLS_BUDGET", raising=False)
        monkeypatch.delenv("DSS_PROMPT_TOKENS_BUDGET", raising=False)
        monkeypatch.delenv("DSS_COMPLETION_TOKENS_BUDGET", raising=False)
        budget = policy.default_budget_from_env()
        assert budget.calls_budget == policy.DEFAULT_CALLS_BUDGET

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DSS_LLM_CALLS_BUDGET", "5")
        monkeypatch.setenv("DSS_PROMPT_TOKENS_BUDGET", "500")
        monkeypatch.setenv("DSS_COMPLETION_TOKENS_BUDGET", "250")
        budget = policy.default_budget_from_env()
        assert budget.calls_budget == 5
        assert budget.prompt_tokens_budget == 500
        assert budget.completion_tokens_actual == 0
