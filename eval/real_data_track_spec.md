# B8 Real-Data Track (Phase R) Spec

**Status:** Implemented for DSS-EVAL v0.5 as DSS-299.  
**Scope:** Public artefact. No steward-only content.  
**Gate:** Phase R runs are gated by the Phase I label-blind coverage gate (DSS-298).

## Goal

Extend the v0.5 benchmark suite from synthetic corpora to public, real-world
retrieval QA datasets.  The real-data track measures whether DSS's label-blind
coordinate derivation and structural retrieval generalise to text that was not
engineered for the kernel, and it compares DSS against the same baselines used
in the synthetic track.

## Datasets

The track uses fixed validation splits from the HuggingFace `datasets` hub:

- **HotpotQA** (`hotpot_qa`, `distractor` subset, `validation` split) —
  multi-hop question answering over multiple Wikipedia paragraphs.
- **NarrativeQA** (`narrativeqa`, `validation` split) — long-document question
  answering over book/story summaries.

Each dataset is sampled deterministically per seed using
`datasets.Dataset.shuffle(seed=seed).select(range(n))`.  The sample indices and
dataset versions are recorded in every BenchmarkArtifact run config so the
splits are reproducible.

## Methodology

1. **Load pinned splits.** For each configured dataset and seed, load the
   validation split and select `samples_per_dataset` examples.
2. **Budget enforcement.** Trim the loaded examples so that the run stays
   within `max_total_documents`, `max_queries`, `max_embedding_calls`, and
   `budget_tokens`.
3. **Label-blind coordinate derivation.** Derive a `QpCoordinate` independently
   for every document and query using the same public ingestion path as
   DSS-298 (`backend.ingestion.pipeline` → `derive_p_adic_coordinate`).
4. **Coverage measurement.** Count the fraction of queries whose coordinate is
   structurally compatible with at least one document coordinate under
   `qp_pure_compatible`.  This is the same coverage metric as DSS-298.
5. **Phase I gate.** If `coverage_score < coverage_gate`, the run status is
   `partial` and cost metrics are marked absent; results are explicitly
   exploratory until the gate is earned.
6. **Baseline comparison.** Run the same matched-information baselines as the
   synthetic track:
   - BM25 (`rank-bm25`)
   - Brute-force dense retrieval (`sentence-transformers/all-MiniLM-L6-v2`)
   - HNSW-indexed dense retrieval (`hnswlib`)
   - Long-context model stand-in (token-budget lexical overlap)
   - DSS Qp-router (label-blind compatible-document retrieval)
7. **Multi-seed aggregation.** Run across 5+ pinned seeds.  The harness reports
   mean, standard deviation, min/max, and approximate 95% confidence
   intervals for every numeric metric.

## Interface

### Inputs

- `datasets`: tuple of dataset names (`hotpotqa`, `narrativeqa`).
- `samples_per_dataset`: int, default 50.
- `top_k`: int, default 5.
- `seeds`: tuple of ints, default `(193, 42, 7, 13, 21)`.
- `coverage_gate`: float, default 0.8 (same as DSS-298).
- `max_total_documents`: int, default 1000.
- `max_queries`: int, default 100.
- `max_embedding_calls`: int, default 2500.
- `budget_tokens`: int, default 500_000.
- `skip_real_embedding`: bool — use a deterministic mock embedder for CI.
- `dry_run`: bool — use the built-in synthetic corpus instead of network data.

### Outputs

- `TrackSummary` with per-system recall@1, recall@k, MRR, P@1, abstention rate,
  latency, token cost, plus coverage score and gate status.
- Validated `BenchmarkArtifact` per seed and an aggregate artifact with
  distribution statistics and CI95.

## Relation to existing work

- Reuses the label-blind ingestion contract from DSS-298.
- Reuses BM25, HNSW, real-embedding, and long-context baselines from DSS-294
  and DSS-295.
- Reuses the multi-seed harness from `backend.benchmarks.harness`.

## Acceptance criteria

1. Real-data track harness committed (`apps/backend/backend/benchmarks/dss299_real_data_track_benchmark.py`).
2. Pinned public datasets loaded via `datasets` with deterministic per-seed sampling.
3. Baselines wired: BM25, dense, HNSW, long-context, DSS.
4. Phase I coverage gate enforced; below-gate results marked `partial`.
5. Budget cap enforced on documents, queries, embedding calls, and tokens.
6. 5+ seeds with distribution + CI95 emitted by the harness.
7. Claim registered in `eval/claims_registry.yaml`.
