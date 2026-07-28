# DSS-EVAL v0.5 — Non-DSS Baseline Matrix Run Manifest

**Date:** 2026-07-28
**Benchmark:** RAG Poisoning & Retrieval Integrity Benchmark (DSS-EVAL v0.5), `dss-benchmark-standalone`
**Purpose:** Vendor-agnostic baseline metrics for NON-DSS retrieval systems.

## Environment

- **Machine/OS:** Linux 5.10.134-18.0.11.lifsea8.x86_64 (x86_64), CPU-only
- **Python:** 3.12.12 (venv at `/tmp/dss-venv`; created with `python3 -m venv`)
  - Note: venv could not be created inside `/mnt/agents` (FUSE mount does not
    support the `lib64` symlink); the venv lives in `/tmp` but all code, reports,
    and artifacts are under the repo path.
- **Network:** PyPI reachable; **huggingface.co NOT reachable** (HEAD requests
  timed out). This is decisive for the ST-based adapters (see Failures).

## Package versions (pip freeze subset)

```
PyYAML==6.0.3
numpy==2.5.1
faiss-cpu==1.14.3
chromadb==1.5.9
qdrant-client==1.18.0
sentence-transformers==5.6.1
torch==2.13.0
transformers==5.14.1
huggingface_hub==1.25.1
onnxruntime==1.28.0
scikit-learn==1.9.0
scipy==1.18.0
langchain==1.3.14
langchain-core==1.5.1
langchain-community==0.4.2
langchain-classic==1.0.8
langchain-text-splitters==1.1.2
llama-index==0.14.23
llama-index-core==0.14.23
pymilvus==3.0.0
```

All requested backends **installed cleanly**; none were skipped at install time.

## Commands used

```bash
python3 -m venv /tmp/dss-venv
/tmp/dss-venv/bin/pip install pyyaml numpy faiss-cpu chromadb qdrant-client \
    sentence-transformers langchain langchain-community llama-index pymilvus

cd /mnt/agents/dss-system/dss-benchmark-standalone
HF_HUB_OFFLINE=1 ANONYMIZED_TELEMETRY=False /tmp/dss-venv/bin/python scripts/run_matrix.py \
    --adapters faiss chroma qdrant sentence_transformers langchain llama_index milvus \
    --suites poisoning integrity abstention --seeds 42 43 44

/tmp/dss-venv/bin/python scripts/generate_leaderboard.py \
    --adapters faiss chroma qdrant sentence_transformers langchain llama_index milvus \
    --seeds 42 43 44 > eval/reports/leaderboard.md
```

- Seeds: **42, 43, 44** (repo defaults); one subprocess per (adapter, seed) as
  `run_matrix.py` does; suites run together per subprocess (repo default).
- **No `--mock-embeddings`**: repo convention (`make eval`, `run_matrix.py`,
  `config/adapters.yaml` `mock_embeddings: false`) runs the faiss adapter in its
  default deterministic lexical-vector mode; mock mode is only used by
  `make eval-fast`. We follow the repo default — no new modes invented.
- `HF_HUB_OFFLINE=1` was set only to make the unavoidable model-download
  failures fast and deterministic (huggingface.co is unreachable from this
  machine); it does not change any scoring path. Without it the chroma
  subprocess hung >6 minutes in HF retry backoff and was killed.
- No suite/harness/scoring code was modified.

## Results (per-suite metrics, averaged over seeds 42/43/44; per-seed values identical)

### faiss — PASS (all gates)

| Suite | Metric | Value | Gate | Pass |
|---|---|---|---|---|
| poisoning | silent_displacement_rate | 0.0 | == 0.0 | ✅ |
| poisoning | flagged_or_preserved_rate | 1.0 | — | ✅ |
| poisoning | total_conflicts | 3 | — | — |
| integrity | incoherent_retrieval_rate | 0.0 | <= 0.05 | ✅ |
| integrity | transparency_rate | 1.0 | >= 0.95 | ✅ |
| abstention | precision | 1.0 | >= 0.98 | ✅ |
| abstention | recall | 1.0 | >= 0.95 | ✅ |
| abstention | false_abstention_rate | 0.0 | <= 0.10 | ✅ |

overall_pass = True, exit code 0 for all 3 seeds.

### langchain — ran, FAILED gates (deterministic empty retrieval)

The adapter instantiates with `retriever=None`; with no retriever wired into
the corpus it returns `[]` for every query (adapter's documented stub
behavior). Metrics identical across seeds 42/43/44:

| Suite | Metric | Value | Gate | Pass |
|---|---|---|---|---|
| poisoning | silent_displacement_rate | 0.0 | == 0.0 | ✅ |
| poisoning | flagged_or_preserved_rate | 1.0 | — | ✅ |
| integrity | incoherent_retrieval_rate | 0.0 | <= 0.05 | ✅ |
| integrity | transparency_rate | 0.0 | >= 0.95 | ❌ |
| abstention | precision | 1.0 | >= 0.98 | ✅ |
| abstention | recall | 0.0 | >= 0.95 | ❌ |
| abstention | false_abstention_rate | 1.0 | <= 0.10 | ❌ |

overall_pass = False, exit code 1.

### llama_index — ran, FAILED gates

Identical stub behavior and identical metric values to langchain (see table
above): poisoning pass; integrity transparency 0.0 fail; abstention recall 0.0
/ false_abstention 1.0 fail. overall_pass = False, exit code 1.

## Failures / skipped adapters

| Adapter | Status | Reason |
|---|---|---|
| chroma | FAILED (no report) | Requires `all-MiniLM-L6-v2` via HF Hub; huggingface.co unreachable → `OSError: ... couldn't find them in the cached files` (all 3 seeds) |
| qdrant | FAILED (no report) | Same HF model download failure (all 3 seeds) |
| sentence_transformers | FAILED (no report) | Same HF model download failure (all 3 seeds) |
| milvus | FAILED (no report) | `NotImplementedError: MilvusAdapter requires a MilvusClient instance or corpus['client']` — adapter `query()` is an explicit stub in the repo (all 3 seeds) |

Matrix totals: 63 cells (7 adapters × 3 suites × 3 seeds), 54 failed cells
(failures include langchain/llama_index cells, which produced reports but
exited 1 on gate failure — counted as failed by `run_matrix.py`).

## Artifacts

- `eval/reports/matrix.json` — full 63-cell matrix with embedded reports/errors
- `eval/reports/leaderboard.md` — cross-adapter leaderboard (gate-averaged over seeds)
- `eval/reports/faiss_all_suites_s{42,43,44}/benchmark_report.json` + `benchmark_summary.md`
- `eval/reports/langchain_all_suites_s{42,43,44}/benchmark_report.json` + `benchmark_summary.md`
- `eval/reports/llama_index_all_suites_s{42,43,44}/benchmark_report.json` + `benchmark_summary.md`
- `eval/reports/baseline_matrix_manifest.md` — this file
- Second pass (mocked embeddings): `eval/reports/matrix_mockemb.json`,
  `eval/reports/faiss_mockemb_all_suites_s{42,43,44}/benchmark_report.json` + `benchmark_summary.md`

Note: `eval/reports/benchmark_report.json` (top level) predates this run; it
was not produced by the matrix and is left untouched.

## Second pass — mocked-embeddings mode (2026-07-28)

A second matrix pass was attempted for the HF-blocked adapters (chroma,
qdrant, sentence_transformers) using the repo's `--mock-embeddings` flag.

**Wiring finding (blocking):** `--mock-embeddings` is only wired to
`FaissAdapter` — `harness/runner.py` constructs every other adapter with no
arguments (`adapter_cls()`), and only faiss receives
`adapter_cls(mock_embeddings=...)`. Moreover, in `adapters/faiss_adapter.py`
the flag is stored (`self.mock_embeddings`) but **never read**, so it is a
no-op even for faiss. Chroma/Qdrant/SentenceTransformers adapters build their
HF/`all-MiniLM-L6-v2` embedding functions unconditionally and have no mock
path. Consequently, with huggingface.co unreachable:

| Adapter | Mock-mode status |
|---|---|
| chroma | SKIPPED — adapter ignores `--mock-embeddings`; still requires HF download |
| qdrant | SKIPPED — adapter ignores `--mock-embeddings`; still requires HF download |
| sentence_transformers | SKIPPED — adapter ignores `--mock-embeddings`; still requires HF download |
| faiss | RAN with `--mock-embeddings` for reference (flag is a no-op) |

Commands:

```bash
for s in 42 43 44; do
  HF_HUB_OFFLINE=1 /tmp/dss-venv/bin/python harness/runner.py --adapter faiss \
    --mock-embeddings --suites poisoning integrity abstention --seeds $s \
    --report-dir eval/reports/faiss_mockemb_all_suites_s$s
done
# matrix_mockemb.json assembled following scripts/run_matrix.py's structure
```

### Mocked-embeddings metric values — PUBLICATION CAVEAT

> All figures in this subsection come from a run with `--mock-embeddings`.
> In DSS-EVAL v0.5 this flag is a **no-op** (accepted only by faiss, never
> read), so these values are byte-identical to the lexical-mode faiss run
> above — they are **not** produced by a distinct embedding model and must be
> labeled "mocked-embeddings mode" if cited.

**faiss (mock-embeddings), seeds 42/43/44 — overall PASS (all gates):**

| Suite | Metric | Value (all seeds) | Gate | Pass |
|---|---|---|---|---|
| poisoning | silent_displacement_rate | 0.0 | == 0.0 | ✅ |
| poisoning | flagged_or_preserved_rate | 1.0 | — | ✅ |
| poisoning | total_conflicts | 3 | — | — |
| integrity | incoherent_retrieval_rate | 0.0 | <= 0.05 | ✅ |
| integrity | transparency_rate | 1.0 | >= 0.95 | ✅ |
| abstention | precision | 1.0 | >= 0.98 | ✅ |
| abstention | recall | 1.0 | >= 0.95 | ✅ |
| abstention | false_abstention_rate | 0.0 | <= 0.10 | ✅ |

Verified diff vs. lexical-mode faiss reports: all metrics identical; only
`poisoning.avg_conflict_detection_latency_s` (wall-clock timing, not a gate)
differs (e.g. 0.000229 vs 0.000237 s on seed 42).

**Leaderboard:** `scripts/generate_leaderboard.py` hardcodes the report path
pattern `<adapter>_all_suites_s<seed>/benchmark_report.json`, so it cannot
incorporate the `*_mockemb_*` cells without modifying the script (out of
scope — no harness/scoring code was changed). Re-running it therefore yields
the same table as `eval/reports/leaderboard.md`. The mockemb cells are
captured in `eval/reports/matrix_mockemb.json` (36 cells: 9 faiss with
embedded reports, 27 recorded skips for chroma/qdrant/sentence_transformers).

**Bottom line for publication:** there is no offline mocked-embeddings path
for chroma/qdrant/sentence_transformers in DSS-EVAL v0.5; their evaluation
requires HF Hub access (or a pre-populated HF cache) for
`all-MiniLM-L6-v2`. (Superseded by the opt-in lexical-embedding patch below,
which now provides a deterministic offline path.)

## Third pass — opt-in lexical embedding mode (repo patch, 2026-07-28)

### The patch (proposed repo change; default behavior unchanged)

- **NEW `adapters/lexical_embedding.py`** — extracts FaissAdapter's exact
  deterministic embedding (stopword tokenization, corpus vocabulary,
  L2-normalized TF vectors, cosine) plus a `LexicalEmbeddingFunction` class
  implementing the Chroma EF protocol (`__call__`/`embed_documents`/
  `embed_query`/`name`/`get_config`/`build_from_config`). Vocabulary is built
  from the first (document) batch and frozen, so queries embed in the same
  space — identical to how FaissAdapter works.
- **`adapters/faiss_adapter.py`** — now imports those helpers from
  `adapters/lexical_embedding.py` instead of defining them locally. Verified
  zero metric drift vs. the pass-1 `matrix.json` embedded reports (only
  wall-clock latency varies).
- **`adapters/chroma_adapter.py`, `adapters/qdrant_adapter.py`,
  `adapters/sentence_transformers_adapter.py`** — new opt-in constructor
  kwarg `embedding: str = "neural"`. Default `"neural"` is byte-for-byte the
  old HF sentence-transformers path (verified: neural mode still attempts the
  HF download and fails identically offline). `"lexical"` routes all
  encode/index calls through the shared lexical embedding; the
  sentence-transformers import requirement is waived only in lexical mode.
  Qdrant: collection vector size taken from the lexical vocab dim. Chroma:
  lexical mode resets the persisted collection per run (deterministic
  re-indexing; corpus ids collide across seeds) and deduplicates deliberate
  duplicate ids in the poisoning corpus keeping the LAST occurrence (Chroma
  enforces unique ids). Also fixed a pre-existing incompatibility:
  `QdrantClient.search` was removed in qdrant-client ≥1.10; the adapter now
  falls back to `query_points` (affects both modes; required for ANY qdrant
  run with current qdrant-client).
- **`harness/runner.py`** — new CLI flag `--embedding {neural,lexical}`
  (default `neural`), passed only to chroma/qdrant/sentence_transformers
  adapters. No suite, scoring, gate, or default-behavior changes.
- Diff: 5 files changed, +111/−72; tests: `pytest tests/` → **4 passed**.

Run commands:

```bash
for ad in chroma qdrant sentence_transformers; do for s in 42 43 44; do
  HF_HUB_OFFLINE=1 /tmp/dss-venv/bin/python harness/runner.py --adapter $ad \
    --embedding lexical --suites poisoning integrity abstention --seeds $s \
    --report-dir eval/reports/${ad}_lexical_all_suites_s$s
done; done
```

### Lexical-embedding metric values — PUBLICATION CAVEAT

> All figures below use the **deterministic lexical embedding** (opt-in
> patch). They measure the structural / retrieval-integrity behavior of each
> vector store and abstention logic — **NOT neural embedding quality**. They
> must be labeled "lexical embedding" if published. Values were identical
> across seeds 42/43/44 (only latency varied).

| Adapter | Suite | Metric | Value | Gate | Pass |
|---|---|---|---|---|---|
| chroma | poisoning | silent_displacement_rate | 0.0 | ==0.0 | ✅ |
| chroma | poisoning | flagged_or_preserved_rate | 1.0 | — | ✅ |
| chroma | integrity | incoherent_retrieval_rate | 0.3333 | ≤0.05 | ❌ |
| chroma | integrity | transparency_rate | 0.0 | ≥0.95 | ❌ |
| chroma | abstention | precision | 0.0 | ≥0.98 | ❌ |
| chroma | abstention | recall | 0.0 | ≥0.95 | ❌ |
| chroma | abstention | false_abstention_rate | 1.0 | ≤0.10 | ❌ |
| qdrant | poisoning | silent_displacement_rate | 0.0 | ==0.0 | ✅ |
| qdrant | poisoning | flagged_or_preserved_rate | 1.0 | — | ✅ |
| qdrant | integrity | incoherent_retrieval_rate | 0.3333 | ≤0.05 | ❌ |
| qdrant | integrity | transparency_rate | 0.0 | ≥0.95 | ❌ |
| qdrant | abstention | precision | 1.0 | ≥0.98 | ✅ |
| qdrant | abstention | recall | 0.0 | ≥0.95 | ❌ |
| qdrant | abstention | false_abstention_rate | 1.0 | ≤0.10 | ❌ |
| sentence_transformers | poisoning | silent_displacement_rate | 0.0 | ==0.0 | ✅ |
| sentence_transformers | poisoning | flagged_or_preserved_rate | 1.0 | — | ✅ |
| sentence_transformers | integrity | incoherent_retrieval_rate | 0.3333 | ≤0.05 | ❌ |
| sentence_transformers | integrity | transparency_rate | 0.0 | ≥0.95 | ❌ |
| sentence_transformers | abstention | precision | 1.0 | ≥0.98 | ✅ |
| sentence_transformers | abstention | recall | 0.0 | ≥0.95 | ❌ |
| sentence_transformers | abstention | false_abstention_rate | 1.0 | ≤0.10 | ❌ |

Also: chroma `borderline_abstention_rate` 0.0; qdrant & sentence_transformers
`borderline_abstention_rate` 0.5 (all seeds).

Overall pass = False for all three adapters (all seeds). The failures stem
from the adapters' top-1-only result contract (vs. FaissAdapter returning up
to 10 ranked results), not from embedding quality — faiss passes every gate
with the SAME lexical embedding. These numbers are a faithful measure of
each adapter's integration contract under a shared deterministic embedding.

Artifacts: `eval/reports/matrix_lexical.json` (27 cells with embedded
reports), `eval/reports/{chroma,qdrant,sentence_transformers}_lexical_all_suites_s{42,43,44}/benchmark_report.json` + `benchmark_summary.md`.

### Anomaly note (pass-1 artifact)

`eval/reports/faiss_all_suites_s42/benchmark_report.json` was externally
overwritten (keys stripped, values unchanged) at 10:02 local by a process
outside this run; it was restored from the full report embedded in
`eval/reports/matrix.json` (ground truth). All other pass-1 reports verified
consistent with `matrix.json`.

## Anomalies

1. **langchain / llama_index "ran" but are stub-backed**: with no external
   retriever wired in they return empty result sets, which *passes* the
   poisoning gate (nothing to displace) and integrity incoherence gate
   (nothing incoherent retrieved) while failing transparency and abstention
   recall/false-abstention. These numbers reflect the stub path, not a real
   LangChain/LlamaIndex retriever — treat as "adapter present but
   unconfigured", not as a quality signal for those frameworks.
2. **faiss passes every gate perfectly** (1.0/0.0 across the board) in its
   default deterministic lexical-vector mode — expected for this harness's
   synthetic corpora.
3. chroma/qdrant/sentence_transformers could not be evaluated at all in this
   environment due to HF Hub unreachability; re-run on a machine with HF
   access (or a pre-populated HF cache) to obtain their numbers.

## Wall-clock time

- Successful matrix run (21 subprocesses, incl. fast-failing HF-offline
  failures): **~14 minutes**.
- End-to-end including venv creation, package installs (~9 min, torch/CUDA
  wheels dominate), one aborted first matrix attempt (~6 min, chroma HF retry
  hang), leaderboard, and verification: **~50 minutes**.
