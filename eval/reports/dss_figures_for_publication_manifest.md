# DSS Figures for Publication — Provenance Manifest

**Generated:** 2026-07-28 (all figures below are fresh runs from 2026-07-28 UTC unless marked otherwise)
**Repo:** `dss-codebase` @ commit `ce7a27b` (`chore: trigger Vercel redeploy after relinking projects to dss-codebase`)
**Deterministic mode:** `DSS_DETERMINISTIC=true` for every run
**Environment:** CPU-only, Linux x86_64 (kernel 5.10.134-18.0.11.lifsea8), 2 vCPU, no GPU, Python 3.12.12 (pinned image is 3.11.9 — see Deviations)
**No pre-computed/committed benchmark artifacts exist in this repo** (no `runs/` directories, no dated JSONs, no `docs/decisions` benchmark entries were found); every figure below was produced by executing the harnesses on 2026-07-28.

All aggregate artifacts follow `artifact_schema.py` (`BenchmarkArtifact`) and include per-seed run IDs, hardware profile, and git SHA. Per-seed artifacts live under `<suite>/seeds/<seed>/<timestamp>.json` beside each aggregate.

---

## 1. DSS-293 — Adversarial poisoning (RAG poisoning gate)

| Metric | Value (mean over seeds 193, 42, 7) | Public gate | Pass/Fail |
|---|---|---|---|
| silent_displacement_rate | **0.0** (0 / 108 cases per seed) | == 0.0 | **PASS** |
| conflict_flagged_rate | 1.0 | — | — |
| original_preserved_rate | 1.0 | — | — |
| flagged_or_preserved_rate | 1.0 | — | — |
| compatibility_pass_rate | 0.667 | — | — |
| invariant_flag_rate | 0.333 | — | — |

- Cases per seed: 108 (36 same-ID overwrite, 36 incompatible-coordinate, 36 compatible-coordinate/invariant-layer).
- Aggregate artifact: `apps/backend/backend/benchmarks/output/dss293_adversarial_poisoning/aggregate/20260728T011254Z.json`
- Aggregate run ID: `dss293-adversarial-poisoning-aggregate-20260728T011254Z`
- Per-seed run IDs: `dss293-adversarial-poisoning-20260728T011253Z-193`, `…T011254Z-42`, `…T011254Z-7`
- Per-seed artifacts: `apps/backend/backend/benchmarks/output/dss293_adversarial_poisoning/seeds/{193,42,7}/`
- Status: success. Determinism check: rerun with same seeds reproduced every non-latency metric identically.

## 2. DSS-292 — Known/unknown abstention

| Metric (Qp / DSS) | Value (mean over seeds 193, 42, 7) | Public gate | Pass/Fail |
|---|---|---|---|
| qp_abstention_precision | **1.000** | >= 0.98 | **PASS** |
| qp_abstention_recall | **1.000** | >= 0.95 | **PASS** |
| qp_false_abstention_rate | **0.000** | <= 0.10 | **PASS** |
| qp_borderline_abstention_recall | 0.500 | — | — |
| qp_present_recall | 0.250 | — | — |

Vector-RAG comparator: precision 1.000, recall 0.000, false_abstention 0.000 (abstains on everything absent-arm only).
300 queries per seed (100 present / 100 absent / 100 borderline).

- Aggregate artifact: `apps/backend/backend/benchmarks/output/dss292_known_unknown/aggregate/20260728T011333Z.json`
- Aggregate run ID: `dss292-known-unknown-aggregate-20260728T011333Z` (see artifact `run_id`)
- Per-seed artifacts: `apps/backend/backend/benchmarks/output/dss292_known_unknown/seeds/{193,42,7}/`
- Status: success.

## 3. Retrieval integrity / transparency (architecture-validity gates)

### 3a. retrieval_architecture_benchmark (single-seed artifact)

| Metric | Value | Public gate | Pass/Fail |
|---|---|---|---|
| qp_incoherent_rate | 0.0714 (7 queries) | incoherent <= 0.05 | **FAIL (below gate)** |
| vector_incoherent_rate | 0.4857 | — | — |
| qp_precision_at_1 | 1.000 | — | — |
| vector_precision_at_1 | 0.714 | — | — |
| p_value | 0.018 | — | — |

- Artifact: `apps/backend/backend/benchmarks/output/qp_architecture/20260728T013338Z.json`
- Note: small pinned corpus (7 queries); the one Qp incoherent case is an empty-result case. Freshly measured 2026-07-28; no gate adjustments made.

### 3b. transparency_report (pinned transparency corpus v1)

| Metric | Value | Public gate | Pass/Fail |
|---|---|---|---|
| Qp transparency (architecture-valid top-1 success rate) | 0.875 (7/8) | transparency >= 0.95 | **FAIL (below gate)** |
| Vector transparency | 0.625 (5/8) | — | — |

- Artifacts: `apps/backend/backend/benchmarks/output/transparency/transparency_report_20260728T013337Z.json`, `…md`, `sample_traces_20260728T013337Z.jsonl`
- Corpus: `apps/backend/backend/benchmarks/corpus/qp_retrieval/transparency_corpus_v1.jsonl`
- Qp failure mode: 1 × `failure_empty` (compatibility gate filtered all candidates); Vector failures: 3 × `failure_incoherent_top1`.

## 4. Comparison suite (DSS-227 baselines, seeds 193/42/7)

Baselines are deterministic stand-ins (no external APIs); `grok_latest` is structurally blocked (no API key — by design).

| Baseline | Benchmark | Recall@1 | Recall@K | MRR | Latency ms |
|---|---|---|---|---|---|
| bow_stand_in | longbench-needle | 0.000 | 0.000 | 0.000 | 24.2 |
| bow_stand_in | longbench-multihop | 0.444 | 0.778 | 0.559 | 2.2 |
| bow_stand_in | ruler-256k | 0.000 | 0.000 | 0.000 | 20.0 |
| hierarchical_rag | longbench-needle | 0.000 | 0.000 | 0.000 | 19.0 |
| hierarchical_rag | longbench-multihop | 0.444 | 0.778 | 0.559 | 2.6 |
| hierarchical_rag | ruler-256k | 0.000 | 0.000 | 0.000 | 19.9 |
| long_context_model | longbench-needle | 0.000 | 0.000 | 0.000 | 30.7 |
| long_context_model | longbench-multihop | 0.667 | 0.889 | 0.726 | 3.4 |
| long_context_model | ruler-256k | 0.000 | 0.000 | 0.000 | 12.4 |
| grok_latest | all three | — | — | — | blocked (no API key) |

- Report: `apps/backend/backend/benchmarks/output/comparisons/comparison_report_20260728T012917Z.{json,md}`
- 12 aggregate artifacts: `apps/backend/backend/benchmarks/output/comparisons/<baseline>-<benchmark>/aggregate/*.json`

## 5. DSS retrieval figures (Qp adapter) — for the public repo's DSS column

| Suite | Metric | DSS (Qp) | Vector comparator | Artifact |
|---|---|---|---|---|
| LongBench needle (7 lengths) | recall@1 | **1.000** | 0.000 | `output/longbench_needle/20260728T014400Z.json` |
| | recall@5 | 1.000 | 0.429 | same |
| | MRR | 1.000 | 0.185 | p=0.0146 |
| LongBench multi-hop (9 chains) | chain recall@5 | **1.000** | 0.296 | `output/longbench_multihop/20260728T014400Z.json` |
| | full-chain rate | 1.000 | 0.000 | p=0.003 |
| Dual retrieval (6 queries) | recall@10 | **1.000** (target >= 0.60) | — | `output/dual_retrieval/dual_retrieval_benchmark/*.json` |
| | MRR | 1.000 (target >= 0.50) | — | avg latency 0.19 ms, token cost 480 |
| Qp vs RAG (filters_on, 10 queries) | recall@1 | 0.9 | 0.9 | `output/qp_vs_rag/filters_on/20260728T020743Z.json` |
| | precision@5 | 0.56 | 0.20 | p=1.0 |

## 6. DSS-294 — BM25 / dense baselines vs DSS router (seeds 193/42/7)

Needle-style ranking, lengths 4–256, top-k 5. Systems: dss_qp_router, real_embedding (MiniLM-L6-v2), hnsw_dense, bm25, metadata_filter, bow_stand_in.

| Metric | dss_qp_router | bm25 | real_embedding | hnsw_dense |
|---|---|---|---|---|
| recall@1 | 0.143 | 0.0 | 0.0 | 0.0 |
| MRR | 0.143 | 0.0 | 0.0 | 0.0 |
| avg latency ms | 0.43 | 63.0 | 27 670 | 24 255 |
| token cost | 0.0 | 2 065 | 68 598 | 1 295 |

- Governance: `dss_no_fabricated_rankings = 1.0`.
- Aggregate: `apps/backend/backend/benchmarks/output/dss294_bm25_ranking/aggregate/20260728T013931Z.json`; per-seed under `…/seeds/`.
- Real-embedding model: `sentence-transformers/all-MiniLM-L6-v2` (pinned; weights SHA recorded in run config when cache path resolvable). Model fetched via mirror — see Deviations.

## 7. DSS-295 — Latency / storage (partial: quick-mode, single seed)

Full default configuration (corpus sizes 999 / 9 999 / 99 999 × 3 seeds, real MiniLM encoding on 2-CPU) was CPU-infeasible in-window (>40 CPU-min without completing; a reduced 999/9 999 run also exceeded 57 CPU-min and was stopped). A `--quick` smoke run (corpus sizes 100 / 200 events, real MiniLM embeddings, 50 query iterations per bucket) completed **seed 193 only** before the time budget; its per-seed artifact is status=success. Multi-seed aggregate NOT produced.

Measured (seed 193, quick corpora, 2026-07-28):

| System | p50 latency @100 ev (ms) | p95 @100 ev (ms) | p50 @200 ev (ms) | bytes/event @100 | bytes/event @200 |
|---|---|---|---|---|---|
| dss_qp_router | 0.037 | 0.048 | 0.119 | 326.4 | 328.2 |
| real_embedding (MiniLM) | 875.8 | 1121.7 | 1521.8 | 1536.0 | 1536.0 |
| hnsw_dense | 883.9 | 1114.8 | 1520.9 | 1685.0 | 1685.6 |
| bm25 | 1.22 | 1.64 | 2.35 | 159.0 | 158.7 |
| metadata_filter | 0.126 | 0.137 | 0.251 | 83.0 | 83.0 |
| bow_stand_in | 2.07 | 4.02 | 3.91 | 352.0 | 352.0 |

- Governance: `extrapolation_labelled=1.0`; both buckets `measured` (no extrapolated rows at these sizes).
- Artifact: `apps/backend/backend/benchmarks/output/dss295_latency_storage_quick/seeds/193/20260728T025044Z.json` (+ `.manifest.json`); run ID `dss295-latency-storage-20260728T025044Z-193`.
- Latency ratios (DSS p50 vs BM25) at these tiny sizes: ~33× faster at 100 events, ~20× at 200 events; MiniLM/HNSW arms are dominated by CPU encoding cost and are not comparable at production scale.

## 8. DSS-297 — Citation faithfulness (seeds 193/42/7)

| Metric | Value |
|---|---|
| citation_integrity | 0.778 |
| chain_valid_rate | 1.000 |
| judge_score_mean (informational, no LLM calls) | 0.736 |
| citation_gate_passed | 0.0 (gate configured to require full citation coverage; 1 missing + 1 unexpected citation across 9 cases) |

- Aggregate: `apps/backend/backend/benchmarks/output/dss297_citation_faithfulness/aggregate/20260728T011344Z.json`; per-seed under `…/seeds/`.

## 9. DSS-298 — Label-blind ingestion (seeds 193/42/7)

| Metric | Value | Gate | Pass/Fail |
|---|---|---|---|
| coverage_score | 1.000 | >= 0.8 (Phase I gate) | PASS |
| gate_pass | 1.0 | — | — |
| llm_calls | 0 | — | — |

- Aggregate: `apps/backend/backend/benchmarks/output/dss298_label_blind_ingestion/aggregate/20260728T013031Z.json`

## 10. DSS-299 — Real-data track (dry-run synthetic fallback, seeds 193/42/7/13/21)

Real HuggingFace datasets (hotpot_qa, narrativeqa) could not be loaded in this environment (see Deviations); per the harness's designed offline behavior it fell back to the deterministic synthetic corpus (`dry_run=True`, flagged in run_config).

| Metric | dss_qp_router | bm25 | hnsw_dense | real_embedding | long_context |
|---|---|---|---|---|---|
| recall@1 | 0.333 | 1.0 | 1.0 | 1.0 | 1.0 |
| recall@k | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 |
| MRR | 0.417 | 1.0 | 1.0 | — | 1.0 |
| abstention_rate | 0.5 | — | — | — | — |

- Governance: coverage_gate 0.8, coverage_score 1.0, gate_pass 1.0, phase_r_gated 1.0.
- Aggregate: `apps/backend/backend/benchmarks/output/dss299_real_data_track/aggregate/20260728T013957Z.json` (dry-run; `dry_run: true` in run_config). **Do not cite as real-data results.**

## 11. Ablation suite (reproduce.py, seeds 193–197)

Run ID `ds-benchmark-20260728-013028-193`, artifacts under `apps/backend/runs/ds-benchmark-20260728-013028-193/` (`manifest.json`, `run.log`, `summary.md`, per-condition `seeds/` + `aggregate/`).

| Condition | recall@1 | recall@10 | MRR |
|---|---|---|---|
| semantic_only | 1.000 | 1.000 | 1.000 |
| coordinate_guided | 0.000 | 0.167 | 0.083 |
| coordinate_token_index | 0.000 | 0.167 | 0.083 |
| coordinate_no_filters | 0.333 | 1.000 | 0.639 |
| full_dss | 0.167 | 0.167 | 0.167 |
| abstention_on | 0.167 | 0.167 | 0.167 |

All six conditions status=success, zero variance across the 5 seeds for retrieval metrics.

---

## Deviations from pinned reproduction

1. **Python 3.12.12** used instead of the pinned `python:3.11.9-slim-bookworm` (no Docker daemon in this environment; host Python is 3.12).
2. **Hash-pinned install failed**: `requirements.txt` contains `rank-bm25` without hashes (line 1275), which breaks `--require-hashes` mode itself. Dependencies were installed from `requirements.in` (unpinned latest) plus `sentence-transformers`, `torch` (CPU wheel), `datasets`. Recorded as a pin-integrity bug in the repo, not an environment workaround.
3. **`backend/__init__.py` is un-importable at commit `ce7a27b`**: `backend/fieldx_kernel/geometry/` lacks `__init__.py`, so `from backend.fieldx_kernel.geometry import Lattice` in `backend/api/governance_routes.py` (pulled in via `backend/main.py`) raises `ImportError`. Benchmarks were executed via a wrapper that stubs `backend.main` and pre-imports `backend.fieldx_kernel` to break a `token_index ↔ ledger_store_v2` circular import. **No scoring or benchmark code was modified.**
4. **Pinned query sets absent**: `eval/queries/*.json` does not exist anywhere in the repo, so every suite logged its designed fallback warning and generated cases at runtime under seed control (`DSS_DETERMINISTIC=true`). Case generation is seeded; a repeat run of DSS-293 reproduced all non-latency metrics identically.
5. **HuggingFace hub unreachable** (huggingface.co HEAD requests time out; pypi reachable). MiniLM weights were fetched once via `HF_ENDPOINT=https://hf-mirror.com` and cached; benchmarks then ran with `HF_HUB_OFFLINE=1`. The HF `datasets` library (v5.0.0) failed to load `hotpot_qa`/`narrativeqa` with an `Invalid HF URI` resolver error even via the mirror, so DSS-299 used its designed synthetic fallback (dry-run).
6. **DSS-295 full corpus matrix not completed**: the default 99 999-event arm and a reduced 999/9 999-event run were both CPU-infeasible in-window (killed after >40 and >57 CPU-min respectively, before writing artifacts). Figures above come from a `--quick` (100/200-event) single-seed run instead — cite only as smoke-scale measurements.
7. `grok_latest` baseline: blocked by design (no API key) — matches the harness's documented behavior.

## What could not run (and why)

- **DSS-299 real-data arm (hotpot_qa, narrativeqa)**: `datasets` resolver error via mirror + direct HF unreachable. Synthetic dry-run only.
- **DSS-295 99 999-event arm**: CPU-time infeasible in-window (extrapolation path exists in code via `--max-measured-events` but was not substituted; reduced corpus run used instead).
- **Docker-pinned image reproduction**: no Docker daemon available.
- **Grok / OpenAI-backed external baselines**: no API keys/network; represented by deterministic stand-ins per repo design.
