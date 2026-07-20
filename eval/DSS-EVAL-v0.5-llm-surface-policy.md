# DSS-EVAL v0.5 LLM Surface Policy

**Status:** Active  
**Scope:** All DSS-EVAL v0.5 benchmarks and LLM-facing evals (DSS-292..DSS-301).  
**Owner:** agent:kimi

## Principle

DSS-EVAL v0.5 is designed to be deterministic and local.  Most benchmarks need no
LLM at all.  When an eval *does* require a language model — for concept
extraction, judge scoring, or live chat-surface probes — the default surface is
**Kimi Code delegated through the chat-surface transport**.  OpenRouter is an
explicit, opt-in fallback only for models that are not available via Kimi.

## Preference order

1. **Kimi Code delegated agent** (default)
   - Uses the Kimi Code CLI/agent protocol through the DSS chat surface.
   - No per-token API spend; consumption is governed by the user's Kimi Code
     membership/quota, not by OpenRouter credits.
   - Required for any v0.5 benchmark that claims "delegated-kimi" results.

2. **OpenRouter** (fallback)
   - Enabled only when:
     - the target model is not available through Kimi, **and**
     - `OPENROUTER_API_KEY` is set, **and**
     - `DSS_LLM_SURFACE=openrouter` is explicitly set or the harness is invoked
       with `--openrouter`.
   - Budget must be capped before the call; unbounded OpenRouter runs are not
     permitted in published v0.5 artifacts.

3. **Local deterministic transport (`R1`)**
   - Used for label-blind ingestion and structural checks that do not need a
     generative model.
   - Zero external API cost and fully reproducible.

## Budget tracking

Every LLM-facing eval must declare and track:

- `llm_calls_budget`: maximum number of model invocations.
- `prompt_tokens_budget`: maximum prompt tokens.
- `completion_tokens_budget`: maximum completion tokens.
- `estimated_cost_usd`: optional cap when using metered fallbacks.

The `backend.benchmarks.llm_surface_policy.LlmSurfaceBudget` helper records
actual consumption and raises `LlmBudgetExceeded` when any cap is breached.
Published artifacts must report:

- `llm_calls_actual`
- `prompt_tokens_actual`
- `completion_tokens_actual`
- `llm_surface_used`

## Configuration

Environment variables:

- `DSS_LLM_SURFACE` — `kimi-delegated` (default) or `openrouter`.
- `OPENROUTER_API_KEY` — required only for OpenRouter fallback.
- `DSS_LLM_CALLS_BUDGET` — default 100.
- `DSS_PROMPT_TOKENS_BUDGET` — default 100_000.
- `DSS_COMPLETION_TOKENS_BUDGET` — default 50_000.

CLI convention:

- `--delegated-kimi` — confirm Kimi as the surface (no-op when it is the default).
- `--openrouter` — explicitly opt in to OpenRouter fallback.
- `--llm-budget-calls N`, `--llm-budget-prompt-tokens N`, `--llm-budget-completion-tokens N`.

## v0.5 implications

- DSS-292..DSS-299 run without API keys in their default configurations.
- DSS-298 label-blind ingestion defaults to transport `R1`; transport `LLM` is
  permitted only with `--llm-budget-calls` set.
- Any future DSS-300+ live-judge eval must log `llm_surface_used` and the
  consumed budget in its BenchmarkArtifact.

## Enforcement

- CI runs `scripts/check_readme_claims.py`; any claim of live-LLM results must
  be registered with the surface and budget fields.
- The eval entrypoint logs the active surface and budget caps before running
  any LLM-dependent benchmark.
