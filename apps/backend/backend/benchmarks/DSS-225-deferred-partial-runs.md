# DSS-225 — Deferred / Partial Benchmark Runs

This document records benchmark suites that are currently **partial** and the
rationale for deferring completion.  It satisfies DSS-225 acceptance criterion
#3: "Previously partial runs are completed or documented as deferred."

## Statistical-rigor infrastructure (completed)

- Multi-seed orchestration via `backend/benchmarks/harness.py`.
- Per-metric mean, standard deviation, min/max, and approximate 95% CI.
- Hardware/GPU profile attachment via `backend/benchmarks/hardware.py`.
- Default seed count raised to 5 in `reproduce.py` (minimum 3 enforced).

## Completed runs

| Suite | Runner | Status | Notes |
|-------|--------|--------|-------|
| Ablation retrieval | `ablation_runner.py` | success | Emits full `retrieval` and `latency` metric groups; run via `reproduce.py` with ≥3 seeds. |

## Partial runs

| Suite | Runner | Status | Missing metric groups | Rationale / Deferred to |
|-------|--------|--------|----------------------|-------------------------|
| Dual-substrate retrieval | `run_dual_retrieval_benchmark.py` | partial | `traceability`, `governance` | Out of scope for the retrieval runner; traceability/governance metrics require dedicated provenance and abstention instrumentation (BACKLOG-EPIC-30 follow-on tickets). |
| LongBench needle | `longbench_needle_benchmark.py` | partial | `traceability`, `governance` | Needle-recall benchmark focuses on coordinate-vs-vector retrieval; broader metric groups deferred to a traceability-aware runner. |
| LongBench multi-hop | `longbench_multihop_benchmark.py` | partial | `traceability`, `governance` | Multi-hop QA runner currently emits retrieval and latency/cost only. |
| RULER 256K | `ruler_256k_benchmark.py` | partial | `traceability`, `governance` | Synthetic retrieval task; governance/traceability not yet instrumented. |
| Retrieval architecture | `retrieval_architecture_benchmark.py` | partial | `traceability`, `governance` | Architecture comparison runner measures retrieval latency/cost only. |
| Qp vs RAG | `retrieval_qp_vs_rag.py` | partial | `traceability`, `governance` | Pairwise comparison runner; broader metric groups deferred. |
| Shadow replay | `run_shadow_replay_benchmark.py` | partial | varies by replay source | Replay harness depends on upstream telemetry schema stabilisation. |
| Production telemetry rollup | `rollup_prod_telemetry_benchmarks.py` | partial | varies | Aggregates telemetry signals; full metric-group coverage pending telemetry contract finalisation. |

## How to complete a deferred suite

1. Add the missing metric-group measurement to the runner.
2. Switch the emitted artefact `status` from `"partial"` to `"success"` when all
   `REQUIRED_METRIC_GROUPS` are present.
3. Re-run the suite through `backend/benchmarks/harness.py` with ≥3 seeds.
4. Remove or update the corresponding row in this document.
