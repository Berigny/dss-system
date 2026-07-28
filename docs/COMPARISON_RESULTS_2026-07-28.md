# DSS-EVAL Comparative Results — 2026-07-28 Refresh

Purpose: populate the evals-and-comparisons surface of `dss-system` with (a) measured
baseline figures for non-DSS systems and (b) updated DSS figures with proof artifacts
from `dss-codebase`. Every number below is freshly measured on 2026-07-28 (UTC) and
traceable to an artifact listed in the two manifests:

- `reports/baseline_matrix_manifest.md` — non-DSS adapters, environment, commands, caveats
- `reports/dss_figures_for_publication_manifest.md` — DSS-side per-figure provenance

---

## 1. Headline gate table (suites: poisoning / integrity / abstention)

Non-DSS adapters ran the public harness (`dss-benchmark-standalone`, seeds 42/43/44,
identical results on all seeds). DSS ran its native suites in `dss-codebase`
(seeds 193/42/7, 108 poisoning cases and 300 abstention queries per seed — full-scale,
not smoke).

| Suite | Metric | Gate | DSS (Qp) | FAISS (ref) | Chroma* | Qdrant* | ST* | LangChain† | LlamaIndex† |
|---|---|---|---|---|---|---|---|---|---|
| Poisoning | Silent displacement rate | == 0.00 | **0.00 PASS** (108 cases/seed) | 0.00 PASS | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| Poisoning | Flagged-or-preserved | ≥ 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| Integrity | Incoherent retrieval rate | ≤ 0.05 | 0.071 FAIL as-measured (7 q) | 0.00 PASS | 0.333 FAIL | 0.333 FAIL | 0.333 FAIL | 0.00 | 0.00 |
| Integrity | Transparency rate | ≥ 0.95 | 0.875 FAIL as-measured (8 q) | 1.00 PASS | 0.00 FAIL | 0.00 FAIL | 0.00 FAIL | 0.00 FAIL | 0.00 FAIL |
| Abstention | Precision (absent) | ≥ 0.98 | **1.000 PASS** (300 q/seed) | 1.00 PASS | 0.00 FAIL | 1.00 PASS | 1.00 PASS | 1.00 | 1.00 |
| Abstention | Recall (present) | ≥ 0.95 | **1.000 PASS** | 1.00 PASS | 0.00 FAIL | 0.00 FAIL | 0.00 FAIL | 0.00 FAIL | 0.00 FAIL |
| Abstention | False abstention rate | ≤ 0.10 | **0.000 PASS** | 0.00 PASS | 1.00 FAIL | 1.00 FAIL | 1.00 FAIL | 1.00 FAIL | 1.00 FAIL |

\* Chroma/Qdrant/ST ran with the new opt-in deterministic **lexical embedding**
(`--embedding lexical`, patch in `patches/lexical-embedding-adapters.patch`) because
Hugging Face Hub was unreachable from the run machine. These cells measure the vector
store's structural/retrieval-integrity behaviour, **not** neural embedding quality.
Crucially, FAISS passes every gate with the *same* lexical embedding — the
chroma/qdrant/ST failures trace to their top-1-only result contract (FAISS returns up
to 10 ranked results), not to embeddings.

† LangChain/LlamaIndex adapters are repo stubs (no retriever wired in) — they return
empty result sets, trivially passing poisoning/incoherence and failing
transparency/recall. Do not cite as framework-quality signal.

**Reading the DSS integrity cells honestly:** DSS-293 (poisoning, 108 cases/seed) and
DSS-292 (abstention, 300 queries/seed) pass all gates at full scale. The two integrity
cells fail as-measured on **small pinned query sets** (7 and 8 queries — one miss moves
the rate past the gate). Qp still beats the in-harness vector baseline by ~7x on
incoherence (0.071 vs 0.486) and 1.4x on transparency (0.875 vs 0.625). Publish as
measured; the small-n caveat is the defensible framing.

## 2. DSS extended benchmark figures (proofs in dss-codebase artifacts)

| Track | Figure | Value | Comparator |
|---|---|---|---|
| LongBench needle | Qp recall@1 | **1.0** (p=0.0146) | vector 0.0 |
| LongBench multihop | chain recall@5 | **1.0** (p=0.003) | vector 0.296 |
| Dual retrieval | recall@10 / MRR / latency | 1.0 / 1.0 / 0.19 ms | — |
| Qp-vs-RAG | R@1 / P@5 | 0.9 / 0.56 | RAG 0.9 / 0.20 |
| DSS-294 BM25 track | recall@1 / latency / token cost | 0.143 / 0.43 ms / **0 tokens** | bm25 0.0 / 63 ms / 2,065 tok; MiniLM 0.0 / 27.7 s / 68,598 tok |
| DSS-295 latency/storage (quick mode, seed 193) | p50 / bytes-per-event | 0.037 ms @100 ev / 326 B | bm25 1.22 ms / 159 B; MiniLM 876 ms / 1,536 B |
| DSS-297 citation faithfulness | citation integrity / chain valid | 0.778 / 1.0 | — |
| DSS-298 label-blind ingestion | coverage score | 1.0 (gate ≥0.8 PASS, 0 LLM calls) | — |
| Ablation (run ds-benchmark-20260728-013028-193) | semantic_only R@1 | 1.0 (6 conditions × 5 seeds, all completed) | full_dss 0.167 — see manifest |

## 3. What could NOT be measured (disclose when sharing)

- **Chroma/Qdrant/ST with real neural embeddings** — HF Hub unreachable; lexical-mode
  figures stand in and are labelled.
- **DSS-299 real-data track** (HotpotQA/NarrativeQA) — dataset download blocked;
  synthetic dry-run only, **not citable as real-data** (synthetic dry-run had DSS
  recall@1 0.333 vs baselines 1.0 — do not publish).
- **DSS-295 full corpus matrix** (99,999/9,999-event arms) — CPU-infeasible in-window;
  quick-mode single-seed only.
- **Hosted LLM baselines** (Grok/OpenAI) — no API keys, blocked by design.
- **Milvus adapter** — repo stub by design (requires external MilvusClient).

## 4. Reproducibility notes

- Baseline harness: `python harness/runner.py --adapter <a> --suites poisoning integrity abstention --seeds 42 43 44 [--embedding lexical]`; matrix via `scripts/run_matrix.py`. Reports: `eval/reports/matrix.json` (lexical default faiss), `matrix_mockemb.json`, `matrix_lexical.json`.
- DSS side: `DSS_DETERMINISTIC=true`, seeds 193/42/7; ablation run ID `ds-benchmark-20260728-013028-193`; artifacts under `apps/backend/backend/benchmarks/output/` and `apps/backend/runs/`.
- Determinism verified: repeat DSS-293 run produced identical non-latency metrics; faiss rerun after the patch showed zero metric diffs.
- Deviations from the Docker-pinned path (documented in the DSS manifest): Python 3.12.12 vs 3.11.9; hash-pinned install fails on repo's own unhashed rank-bm25; MiniLM via hf-mirror then offline; seeded runtime query generation where pinned query sets are absent.
