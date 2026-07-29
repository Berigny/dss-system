# DSS Benchmark Standalone

A standalone, vendor-agnostic benchmark harness for evaluating RAG poisoning
resistance, retrieval integrity, and abstention capacity. This package contains
**no DSS runtime code** — no Qp coordinates, prime schema, dual-circuit DAG, or
COORD machinery.

## Usage

```bash
# Full deterministic run (uses mocked small embeddings, CPU-friendly)
make eval

# Smoke pass with mocked embeddings, completes in < 60 seconds
make eval-fast

# Run against a specific adapter
python harness/runner.py --adapter langchain

# Run a subset of suites
python harness/runner.py --suites poisoning abstention
```

Reports are written to `eval/reports/` as `benchmark_report.json` and
`benchmark_summary.md`.

## Adapter authoring guide

Implement `RetrievalAdapter` from `adapters/base.py`:

```python
from adapters.base import RetrievalAdapter, RetrievalResult
from typing import Any, List

class MyAdapter(RetrievalAdapter):
    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        ...
        return [RetrievalResult(text=..., score=..., identifier=..., metadata={...})]
```

Register the adapter in `harness/runner.py` under `ADAPTER_MAP`. Suites pass
corpus objects built by `corpora/synthetic.py` and `corpora/real.py` directly to
the adapter.

## Suite summaries

- `suites/poisoning.py` — compatible/incompatible coordinate conflicts and
  same-identifier overwrites; measures silent displacement and flagged-or-preserved
  rates.
- `suites/integrity.py` — structural vs. semantic coherence on synthetic
  distractors and optional real corpora; measures incoherent retrieval and
  transparency rates.
- `suites/abstention.py` — absent, borderline, and present queries; measures
  precision, recall, and false abstention rate.
- `tests/test_h3_ablation.py` — 336 efficiency-floor ablation using synthetic
  telemetry. Proves the gateway rejects sub-θ_A (`A_corr < 0.20`) commits when
  enabled and that rejection rate materially drops when disabled.

## Claims registry

`eval/claims_registry.yaml` binds every public claim to a metric and threshold.
The default policy is `unknown_claims_policy: fail_ci`, so any new claim must be
registered before CI will pass.

## Limitations

These benchmarks test structural integrity and abstention behavior, not general
retrieval quality on unstructured corpora.
