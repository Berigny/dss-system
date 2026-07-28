# dss-system eval updates — 2026-07-28 package

Repo-ready file updates/additions toward populating DSS-EVAL comparisons before sharing.
Everything measured fresh on 2026-07-28; nothing here overwrites your repo — apply what you want.

## Contents

- `docs/COMPARISON_RESULTS_2026-07-28.md` — **start here.** Full comparative results,
  honest gate table (DSS + 7 baseline adapters), extended DSS benchmark figures,
  blocked items to disclose, reproducibility notes.
- `docs/README_results_table_update.md` — drop-in replacement for the README Results
  table (fills the DSS column, adds Chroma/Qdrant columns) + replacement row-scope text.
- `reports/comparison_table_2026-07-28.csv` — gate table in CSV (fast-scan format).
- `reports/baseline_matrix_manifest.md` — provenance for all non-DSS runs (env,
  versions, commands, failures, caveats). Belongs in `dss-benchmark-standalone/eval/reports/`.
- `reports/dss_figures_for_publication_manifest.md` — per-figure DSS provenance from
  dss-codebase (metric → artifact path → seeds → fresh vs blocked).
- `reports/matrix.json`, `matrix_mockemb.json`, `matrix_lexical.json` — raw matrix
  outputs (embedded per-seed reports). Belong in `dss-benchmark-standalone/eval/reports/`.
- `reports/leaderboard.md` — generated leaderboard (lexical-default adapters).
- `patches/lexical-embedding-adapters.patch` — proposed harness patch (+111/−72):
  shared deterministic lexical embedding (`adapters/lexical_embedding.py`), opt-in
  `embedding="lexical"` on chroma/qdrant/sentence_transformers adapters,
  `--embedding` CLI flag on `harness/runner.py`, qdrant-client ≥1.10 `query_points`
  fix. Default behaviour unchanged; repo tests pass (4/4); faiss regression-verified
  (zero metric diffs). Apply with `git apply` from `dss-benchmark-standalone/`.

## Headline

- DSS passes poisoning (0.0 silent displacement, 108 cases/seed) and all abstention
  gates (P=1.0, R=1.0, FA=0.0, 300 queries/seed) at full scale.
- DSS integrity cells fail as-measured on small pinned sets (0.071 vs ≤0.05, n=7;
  0.875 vs ≥0.95, n=8) but beat the in-harness vector baseline ~7x/1.4x. Published
  unaltered with the small-n caveat.
- FAISS ref adapter passes all gates (3-case smoke scale). Chroma/Qdrant/ST fail
  integrity/abstention gates due to top-1-only result contracts — a real, defensible
  cross-system finding enabled by the new lexical-embedding patch.
- Blocked (disclose): neural-embedding chroma/qdrant/ST runs (no HF Hub), DSS-299
  real-data track, DSS-295 full corpus matrix, hosted LLM baselines, Milvus stub.
