# DSS Benchmark Data-Handling Review

**Scope:** All benchmark scripts and artefacts under `ds-backend-local/backend/benchmarks/`.  
**Review date:** 2026-07-11  
**Status:** Complete

## 1. Data sources

The benchmark suite uses only **synthetic, generated corpora** that are checked into the repository:

- `backend/benchmarks/benchmark_dataset.jsonl`
- `backend/benchmarks/corpus/qp_retrieval/qp_architecture_corpus_v1.jsonl`
- `backend/benchmarks/corpus/qp_retrieval/qp_retrieval_corpus_v1.jsonl`
- `backend/benchmarks/corpus/qp_retrieval/transparency_corpus_v1.jsonl`
- Generated needle-in-haystack and multi-hop corpora from `longbench_needle_benchmark.py` and `longbench_multihop_benchmark.py`

No production user data, chat logs, attachments, or PII are consumed by the benchmark harness.

## 2. PII and sensitive-data policy

- No names, addresses, identifiers, credentials, or contact details appear in benchmark corpora.
- All memories and queries are hand-written synthetic text or deterministically generated strings.
- Benchmark artefacts do not include model API keys, secrets, or environment files.
- The `run_config` field of `BenchmarkArtifact` is restricted to `str | int | float | bool` and must not be used for free-text user content.

## 3. Artefact handling

- Artefacts are written as JSON/Markdown to `backend/benchmarks/output/`.
- Artefacts contain only aggregate metrics, coordinate paths, and synthetic sample traces.
- The transparency report includes an explicit `screening_note` asserting synthetic data.
- Before publishing any future artefact that uses real-world datasets, a manual screening step must redact or anonymise any person-identifiable content.

## 4. Retention and access

- Benchmark outputs are local files and are not uploaded automatically.
- Committing outputs to git is optional and should be reviewed for size and sensitivity.
- Old outputs can be deleted safely; reproducibility comes from the scripts and seeds, not the output files.

## 5. Determinism and reproducibility

- Seeds are recorded in every per-seed artefact and in aggregate `run_config`.
- `DSS_DETERMINISTIC` and `DSS_DETERMINISTIC_SEED` environment variables support deterministic replay.
- The benchmark harness calls `set_global_seed(seed)` before each seeded run.

## 6. Reviewer sign-off

- Code review checklist: see `findings/06-code-and-infrastructure-quality.md`.
- This review was conducted as part of DSS-229 and is recorded for audit purposes.
