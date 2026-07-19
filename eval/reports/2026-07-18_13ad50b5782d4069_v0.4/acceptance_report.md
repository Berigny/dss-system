# KSR-EVAL v0.4 Partition Acceptance Report

**Date:** 2026-07-19  
**KSR version:** 1.3.1  
**Validator:** KSR-VALIDATE v0.3.1  
**Source registry SHA256:** `13ad50b5782d40692f59dcabe1dd3d64e051fa5fde90f72a6481db106dd4e9bc`  
**Core artifact SHA256:** see `build_manifest.json` (regenerated after removing stale `dist/` references from `surface_policy`)

## Artifacts

| Artifact | Path | Validator mode |
|----------|------|----------------|
| ksr-core | `ksr/core/ksr-core-1.3.1.yaml` | `--mode core` |
| ksr-pack-domains | `ksr/pack/ksr-pack-domains-1.3.1.yaml` | `--mode pack` |
| ksr-pack-steward | `ksr/pack/ksr-pack-steward-1.3.1.yaml` | `--mode pack` |

## Results

### Core validation (G01–G16)

```text
KSR-VALIDATE v0.3.1 core mode | registry: ksr/core/ksr-core-1.3.1.yaml
ksr_version: 1.3.1 | gates: 16

[G01] YAML duplicate keys                        PASS
[G02] checksum_336 consistency                   PASS
[G03] digit_registry schema                      PASS
[G04] prime_registry integrity                   PASS
[G05] corner_map consistency                     PASS
[G06] bridge_edges integrity                     PASS
[G07] lattice 27-node coverage                   PASS
[G08] 'day' field semantic overload              PASS
[G09] eq->prime wiring consistency               PASS
[G10] glossary <-> synonym_registry agreement    PASS
[G11] glossary.priority <-> stripping_priority   PASS
[G12] synonym/symbol ambiguity                   PASS
[G13] steward-only enforcement (P/H)             PASS
[G14] surface_policy private_paths coverage      PASS
[G15] cross_domain relation_type validity        PASS
[G16] core referential closure                   PASS

NEW failures: 0
```

### Pack validation (P01–P04)

Both `ksr-pack-domains` and `ksr-pack-steward` pass all four pack-mode gates with pinned source SHA references.

## PUB-1 esoteric-content scan

`scripts/pub1_esoteric_scan.py` reports **0 hits** on the public tree (code, docs, and full git history).

## Benchmark harnesses (DSS-274 through DSS-278)

| Ticket | Harness | Status | Key output |
|--------|---------|--------|------------|
| DSS-274 | `tools/retention_smoke_test.py --dry-run` | PASS | `eval/reports/2026-07-18_126b44e5f23833e5_v0.4/retention_smoke_report.json` |
| DSS-274 | `tools/retention_smoke_test.py --model moonshotai/kimi-k3` | Deferred | Requires `OPENROUTER_API_KEY`; ~60 live calls to verify ≥0.89 recall |
| DSS-276 | `tools/seed_distribution_harness.py` | PASS | `eval/reports/benchmarks/seed_distribution_20260719T043606Z_2b9161f71a14.json` + `.manifest.json` |
| DSS-277 | `tools/counterfactual_harness.py` | PASS | `eval/reports/benchmarks/counterfactual_baselines_20260719T043712Z_2b9161f71a14.json` + `.manifest.json` |
| DSS-277 | `DenseRetrievalBaseline` renamed to `BoWStandInBaseline` | PASS | `apps/backend/backend/benchmarks/comparison_baselines.py` |
| DSS-277 | Real embedding baseline (MiniLM) | PASS | `apps/backend/backend/benchmarks/real_embedding_baseline.py` |
| DSS-277 | Metadata-filter baseline (matched-information control) | PASS | `apps/backend/backend/benchmarks/metadata_filter_baseline.py` |
| DSS-278 | Manifest emission + notes field | PASS | `apps/backend/backend/benchmarks/manifest.py` wired into needle, multi-hop, seed-distribution, and counterfactual harnesses |
| DSS-278 | Label-blind ingestion spec | Committed | `eval/label_blind_ingestion_spec.md` (design-only for v0.4) |

### Seed distribution (issue #1, credit: hugooconnor — reproduction and critique)

Pinned seeds 193–197 on the LongBench needle harness yield:

- `qp_recall@1`: mean `1.000`, CI95 `[1.000, 1.000]`, `n = 5`
- `vector_recall@1`: mean `0.171`, per-seed distribution `0 / 0 / 0.286 / 0.286 / 0.286`
- `real_embedding` (sentence-transformers/all-MiniLM-L6-v2) recall@1: mean `0.000`, recall@5: mean `0.000`

The per-seed vector distribution is now exposed in the artifact under
`metrics.retrieval.per_seed_vector_recall_at_1`. Per-seed real embedding
recall@1 and recall@5 are exposed under
`metrics.retrieval.per_seed_real_embedding_recall_at_1` and
`metrics.retrieval.per_seed_real_embedding_recall_at_k`.

### Counterfactual shuffles

Both needle and multi-hop arms show **coordinate-driven** retrieval: shuffling
coordinates destroys Qp performance while shuffling texts does not.

B3 matched-information baselines on the same small needle split:

- `bow_stand_in` needle recall@1: `0.000`
- `metadata_filter` needle recall@1: `0.333`
- `real_embedding` (sentence-transformers/all-MiniLM-L6-v2) needle recall@1: `0.000`, recall@5: `0.000`

The embedding baseline also runs on the multi-hop split:

- `real_embedding` multi-hop recall@1: `0.800`, recall@5: `1.000`

The needle corpus is intentionally adversarial: the synthetic needle texts are
not semantically related to the query text, so a dense embedding ranker returns
`0.0` at both `@1` and `@5`. This is the expected behaviour for this synthetic
control, not a harness failure. On the multi-hop split, where query and chain
texts do share semantic signal, the same embedding baseline scores `0.8@1` and
`1.0@5`. The contrast confirms that the needle baseline measures the
coordinate-driven property of the corpus, not a general retrieval upper bound.

These numbers are intentionally produced with identical structural metadata as
the DSS retrieval path so they serve as a true control, not an upper bound.

### Public-core frame hardening (DSS-289)

Decision D6: **Option A applied.** The public-core registry key
`commandment_patch_registry` is renamed to `constraint_layer_registry`. The
source-document reference is neutralised from
`DSS_Commandment_System_Patch_Encoding_v1.0.md` to
`DSS_Constraint_Layer_Patch_Encoding_v1.0.md`, and the ten patch
`operational_definition` texts are corrected so each patch has a unique,
engineering-faithful definition instead of the previous duplicated text.

All code paths that loaded the old key are updated:
`tools/ksr_build.py`, `tools/ksr_validate.py`, `tools/apply_ksr_phase0.py`,
`scripts/merge_ksr_population.py`, and `scripts/generate_kernel_constants.py`.
The private KSR source and the backend plaintext mirror keep the renamed key
and the neutral source reference for consistency.

Verification:

- `tools/ksr_validate.py --mode core ksr/core/ksr-core-1.3.1.yaml`: 16/16 PASS.
- `scripts/pub1_esoteric_scan.py`: 0 hits (private plaintext KSR mirror now
  excluded from the public scan).

## Status semantics

Benchmark artifacts use `status: "partial"`. In this release that means the
**traceability** and **governance** metric groups are intentionally out-of-scope;
it does **not** indicate retrieval failure. Each artifact's `run_config.partial_status_note`
field documents this explicitly.

## Path alignment

`tools/ksr_build.py` now writes canonical artifacts under `ksr/core/` and
`ksr/pack/` and records those paths in `build_manifest.json`. Stale `dist/`
references have been removed from the generated `surface_policy`. The acceptance
report, validator defaults, and CI command all reference the same `ksr/` paths.

## Backend test suite

`apps/backend` pytest run: **1246 passed** against the `ksr-core` runtime registry.

## Sign-off

Partitioner, validators, and KSR-EVAL v0.4 benchmark harnesses are accepted.
