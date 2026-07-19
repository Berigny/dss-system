# KSR-EVAL v0.4 Partition Acceptance Report

**Date:** 2026-07-19  
**KSR version:** 1.3.1  
**Validator:** KSR-VALIDATE v0.3.1  
**Source registry SHA256:** `00d63d0bba51387527c467761beadc52719b2d71e6ea8b84ddceda1b541be1a1`  
**Core artifact SHA256:** `79218c3b20a57ef80a83b5b95e62330e808a014e0c645000bd3e8c4bc9a1869c`  
**Repo commit SHA:** `4d94cebc3693e240df70090abb2311f22d64f283`

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

`scripts/pub1_esoteric_scan.py --skip-history` reports **0 hits** on the current public tree.

> **Note on full git history:** adding the two legacy steward-flavored phrases removed by DSS-290 to the PUB-1 lexicon now surfaces their historical references in pre-DSS-290 commits. The current HEAD is clean; the historical hits are expected unless a history rewrite is performed.

## Benchmark harnesses (DSS-274 through DSS-291)

| Ticket | Harness | Status | Key output |
|--------|---------|--------|------------|
| DSS-274 | `tools/retention_smoke_test.py --dry-run` | PASS | `eval/reports/2026-07-19_79218c3b20a57ef8_v0.4/retention_smoke_report.json` |
| DSS-274 | `tools/retention_smoke_test.py --model moonshotai/kimi-k3` | Deferred | Requires `OPENROUTER_API_KEY` |
| DSS-291 | `tools/retention_smoke_test.py --delegated-kimi` | Instrumented / deferred | Harness implemented; live run requires `DSS_SESSION_TOKEN` or `DSS_REFRESH_TOKEN` |
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
- `vector_recall@1`: mean `0.171`, CI95 `[0.034, 0.309]`, per-seed distribution `0 / 0 / 0.286 / 0.286 / 0.286`
- `metadata_filter` needle recall@1: mean `0.143`
- `real_embedding` (sentence-transformers/all-MiniLM-L6-v2) recall@1: mean `0.000`, recall@5: mean `0.000`

The per-seed vector distribution is exposed in the artifact under `metrics.retrieval.per_seed_vector_recall_at_1`. Per-seed real embedding recall@1 and recall@5 are exposed under `metrics.retrieval.per_seed_real_embedding_recall_at_1` and `metrics.retrieval.per_seed_real_embedding_recall_at_k`.

### Counterfactual shuffles

Both needle and multi-hop arms show **coordinate-driven** retrieval: shuffling coordinates destroys Qp performance while shuffling texts does not.

B3 matched-information baselines on the same small needle split:

- `bow_stand_in` needle recall@1: `0.000`
- `metadata_filter` needle recall@1: `0.333`
- `real_embedding` (sentence-transformers/all-MiniLM-L6-v2) needle recall@1: `0.000`, recall@5: `0.000`

The embedding baseline also runs on the multi-hop split:

- `real_embedding` multi-hop recall@1: `0.800`, recall@5: `1.000`

The needle corpus is intentionally adversarial: the synthetic needle texts are not semantically related to the query text, so a dense embedding ranker returns `0.0` at both `@1` and `@5`. This is the expected behaviour for this synthetic control, not a harness failure. On the multi-hop split, where query and chain texts do share semantic signal, the same embedding baseline scores `0.8@1` and `1.0@5`. The contrast confirms that the needle baseline measures the coordinate-driven property of the corpus, not a general retrieval upper bound.

These numbers are intentionally produced with identical structural metadata as the DSS retrieval path so they serve as a true control, not an upper bound.

### Retention smoke test A4 gate (DSS-291)

DSS-291 adds `--delegated-kimi` mode to `tools/retention_smoke_test.py`. The harness now posts each decode prompt through the chat surface smart stream using the Kimi Code delegated principal, avoiding direct OpenRouter calls and honouring the Epic 38 preference to route agent-heavy benchmarks through the Kimi Code surface identity.

- Deterministic dry-run recall over the 50-item sample: `1.000` (gate `>= 0.89` PASS).
- Live delegated-kimi recall: **deferred** pending a valid `DSS_SESSION_TOKEN` or `DSS_REFRESH_TOKEN` in the runtime environment.
- Canonical report: `eval/reports/2026-07-19_79218c3b20a57ef8_v0.4/retention_smoke_report.json`

To run the live gate once credentials are available:

```bash
DSS_SESSION_TOKEN="..." \
  python3 tools/retention_smoke_test.py --delegated-kimi
```

### Public-core frame hardening (DSS-289)

Decision D6: **Option A applied.** The public-core registry key `commandment_patch_registry` is renamed to `constraint_layer_registry`. The source-document reference is neutralised from `DSS_Commandment_System_Patch_Encoding_v1.0.md` to `DSS_Constraint_Layer_Patch_Encoding_v1.0.md`, and the ten patch `operational_definition` texts are corrected so each patch has a unique, engineering-faithful definition instead of the previous duplicated text.

All code paths that loaded the old key are updated: `tools/ksr_build.py`, `tools/ksr_validate.py`, `tools/apply_ksr_phase0.py`, `scripts/merge_ksr_population.py`, and `scripts/generate_kernel_constants.py`. The private KSR source and the backend plaintext mirror keep the renamed key and the neutral source reference for consistency.

### PUB-1 lexicon escapes (DSS-290)

Removed steward-flavored strings from public engineering files:

- Operator override flag `DEMO_..._MODE` (legacy `DEMO_GOD_*`) → `DEMO_OVERRIDE_MODE` across `ENV_VARS.md`, `backend/services/demo_mode.py`, call sites in `authz.py`, `ledger_scope.py`, `context_scope.py`, `api/chat.py`, `api/ledger.py`, and the `pilot_qp_retrieval_smoke.py` script.
- `DEMO_..._DEFAULT_LEDGER` (legacy `DEMO_GOD_*`) → `DEMO_DEFAULT_LEDGER` in the same files.
- Function `demo_..._mode_enabled()` (legacy `demo_god_*`) → `demo_override_mode_enabled()`.
- The legacy perfect-diagonal phrase comment in `backend/fieldx_kernel/kernel_origin_equations.py` rewritten as `"perfect diagonal alignment check"`.
- Test fixtures in `backend/kernel/tests/test_esoteric_stripper.py` updated to use neutral example terms.
- Added the two legacy steward-flavored phrases to `scripts/pub1_esoteric_lexicon.txt` to prevent regression.

## Status semantics

Benchmark artifacts use `status: "partial"`. In this release that means the **traceability** and **governance** metric groups are intentionally out-of-scope; it does **not** indicate retrieval failure. Each artifact's `run_config.partial_status_note` field documents this explicitly.

## Path alignment

`tools/ksr_build.py` writes canonical artifacts under `ksr/core/` and `ksr/pack/` and records those paths in `build_manifest.json`. The canonical build manifest for this acceptance run is:

`eval/reports/2026-07-19_79218c3b20a57ef8_v0.4/build_manifest.json`

## Backend test suite

`apps/backend` pytest run: **1246 passed** against the `ksr-core` runtime registry.

## Sign-off

Partitioner, validators, and KSR-EVAL v0.4 benchmark harnesses are accepted. The A4 live retention gate is instrumented and ready to run as soon as a DSS session token is supplied.
