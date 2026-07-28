# Proposed replacement: `README.md` Results table (dss-system)

Replaces the current N/A-DSS-column table. Keep the existing caveat paragraph and
update the row-scope sentence as noted below the table.

| Suite | Metric | Gate | Naive vector RAG (FAISS ref adapter) | Structured adapter (DSS) | Chroma (lexical emb.) | Qdrant (lexical emb.) |
|---|---|---|---|---|---|---|
| Poisoning | Silent displacement rate | == 0.00 | 0.00 PASS | **0.00 PASS** (108 cases/seed, seeds 193/42/7) | 0.00 | 0.00 |
| Poisoning | Flagged-or-preserved rate | ≥ 1.00 | 1.00 PASS | 1.00 | 1.00 | 1.00 |
| Integrity | Incoherent retrieval rate | ≤ 0.05 | 0.00 PASS | 0.071 FAIL as-measured (n=7; vector baseline 0.486) | 0.333 FAIL | 0.333 FAIL |
| Integrity | Transparency rate | ≥ 0.95 | 1.00 PASS | 0.875 FAIL as-measured (n=8; vector baseline 0.625) | 0.00 FAIL | 0.00 FAIL |
| Abstention | Precision (absent queries) | ≥ 0.98 | 1.00 PASS | **1.000 PASS** (300 queries/seed) | 0.00 FAIL | 1.00 PASS |
| Abstention | Recall (present queries) | ≥ 0.95 | 1.00 PASS | **1.000 PASS** | 0.00 FAIL | 0.00 FAIL |
| Abstention | False abstention rate | ≤ 0.10 | 0.00 PASS | **0.000 PASS** | 1.00 FAIL | 1.00 FAIL |

Suggested replacement row-scope paragraph:

> Row scope: FAISS column — poisoning over 3 conflict cases, integrity over 15 queries,
> abstention over 3 absent / 2 borderline / 3 present queries, seeds 42/43/44 (identical
> across seeds). DSS column — measured 2026-07-28 in dss-codebase at full scale: 108
> poisoning cases and 300 abstention queries per seed (seeds 193/42/7); integrity cells
> on small pinned sets (7 and 8 queries) fail the gates as-measured and are published
> unaltered. Chroma/Qdrant columns ran with the opt-in deterministic lexical embedding
> (`--embedding lexical`); with the same embedding FAISS passes every gate, so those
> failures reflect top-1-only result contracts, not embedding quality. Neural-embedding
> cells for Chroma/Qdrant pending a run with HF Hub access. Full per-figure provenance:
> `eval/reports/baseline_matrix_manifest.md` and the DSS figures manifest.
