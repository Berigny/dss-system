# KSR-EVAL v0.4 Partition Acceptance Report

**Date:** 2026-07-18  
**KSR version:** 1.3.1  
**Source registry SHA256:** `13ad50b5782d40692f59dcabe1dd3d64e051fa5fde90f72a6481db106dd4e9bc`  
**Core artifact SHA256:** see `build_manifest.json`

## Artifacts

| Artifact | Path | Validator mode |
|----------|------|----------------|
| ksr-core | `ksr/core/ksr-core-1.3.1.yaml` | `--mode core` |
| ksr-pack-domains | `ksr/pack/ksr-pack-domains-1.3.1.yaml` | `--mode pack` |
| ksr-pack-steward | `ksr/pack/ksr-pack-steward-1.3.1.yaml` | `--mode pack` |

## Results

### Core validation (G01–G16)

```text
KSR-VALIDATE v0.3 core mode | registry: ksr/core/ksr-core-1.3.1.yaml
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
| DSS-274 | `tools/retention_smoke_test.py --dry-run` | PASS | `eval/reports/2026-07-18_e702928caf1c8a9c_v0.4/retention_smoke_report.json` |
| DSS-276 | `tools/seed_distribution_harness.py` | PASS | `eval/reports/benchmarks/seed_distribution_*.json` + `.manifest.json` |
| DSS-277 | `tools/counterfactual_harness.py` | PASS | `eval/reports/benchmarks/counterfactual_baselines_*.json` + `.manifest.json` |
| DSS-277 | `DenseRetrievalBaseline` renamed to `BoWStandInBaseline` | PASS | `apps/backend/backend/benchmarks/comparison_baselines.py` |
| DSS-277 | Real embedding baseline scaffold | PASS | `apps/backend/backend/benchmarks/real_embedding_baseline.py` |
| DSS-277 | Metadata-filter baseline scaffold | PASS | `apps/backend/backend/benchmarks/metadata_filter_baseline.py` |
| DSS-278 | Manifest emission | PASS | `backend/benchmarks/manifest.py` wired into needle, multi-hop, seed-distribution, and counterfactual harnesses |
| DSS-278 | Label-blind ingestion spec | Committed | `eval/label_blind_ingestion_spec.md` (design-only for v0.4) |

### Seed distribution (issue #1, credit: Hugo)

Pinned seeds 193–197 on the LongBench needle harness yield mean `qp_recall@1 = 1.000`
with CI95 `[1.000, 1.000]` and `n = 5`.

### Counterfactual shuffles

Both needle and multi-hop arms show **coordinate-driven** retrieval: shuffling
coordinates destroys Qp performance while shuffling texts does not.

## Backend test suite

`apps/backend` pytest run: **1246 passed** against the `ksr-core` runtime registry.

## Sign-off

Partitioner, validators, and KSR-EVAL v0.4 benchmark harnesses are accepted.
