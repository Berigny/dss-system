# B6 Label-Blind Ingestion Spec

**Status:** Design-only for KSR-EVAL v0.4; implementation targeted for v0.5 or Hugo PR.  
**Scope:** Public artefact. No steward-only content.  
**Credit:** hugooconnor — issue #1 reproduction and critique.

## Problem

Current DSS benchmarks (B1–B3) start from synthetic corpora where each document
already carries an explicit coordinate label. This is legitimate for measuring
*structural filtering given coordinates*, but it does not test whether the
system can *discover* coordinates from raw text without human-provided relevance
labels. Label-blind ingestion closes that gap.

## Goal

Define an ingestion path that derives both document coordinates and query
coordinates independently from raw text, with no shared relevance labels between
the two derivation steps. A coverage gate marks results as exploratory when the
automatically derived coordinates fail to cover at least 80% of the intended
semantic targets.

## Interface spec

### Inputs

- `documents`: list of raw text strings.
- `queries`: list of raw text strings.
- `registry`: `ksr-core` only (public runtime).
- `config`:
  - `coverage_gate`: float, default `0.8`.
  - `max_tokens_per_document`: int, default `4096`.
  - `transport`: `"R1"` (local deterministic) or `"LLM"` (lightweight LLM for
    concept extraction).

### Outputs

- `document_coords`: list of `QpCoordinate` objects, one per document.
- `query_coords`: list of `QpCoordinate` objects, one per query.
- `coverage_score`: fraction of query coordinates that are structurally
  compatible with at least one document coordinate under `qp_pure_compatible`.
- `gate_pass`: `True` if `coverage_score >= coverage_gate`.
- `status`: `"supported"` if gate passes, `"exploratory"` otherwise.

## Methodology (reuses KSR-EVAL Phase 2 machinery)

1. **Concept extraction.** Map each document/query text to a set of KSR concept
   candidates using:
   - exact glossary term matches,
   - synonym_registry expansions,
   - digit symbol and prime-name lookups,
   - optional LLM tagging constrained to the public `ksr-core` vocabulary.
2. **Coordinate derivation.** Convert the concept set into a `QpCoordinate` via
   the existing encode path (`backend/ingestion/pipeline.py`).
3. **Compatibility check.** For each query coordinate, count how many document
   coordinates satisfy `qp_pure_compatible(query, doc)`.
4. **Coverage score.**
   ```
   coverage_score = compatible_queries / total_queries
   ```
5. **Gate decision.**
   - If `coverage_score >= coverage_gate`: mark `"supported"`.
   - Else: mark `"exploratory"` and attach the raw coverage score.

## Coverage gate

- Default: `0.8`.
- Rationale: Below 0.8, too many queries have no structurally compatible
document, meaning the label-blind derivation is not yet reliable enough to
support the public recall claims.
- Below-gate results are still reported but are explicitly labeled
exploratory and cannot be used to claim B6 support.

## Relation to existing work

- Reuses the deterministic encode/decode round-trip verified by G01–G16.
- Reuses the `qp_pure_compatible` filter already exercised in the needle and
  multi-hop harnesses.
- Does **not** require steward-only overlays; runs on `ksr-core` only.

## Implementation status

- v0.4: this document is the design spec. The interface and gate are stable.
- v0.5 / Hugo PR: implement the extraction service, run it on a label-blind
  corpus, and emit a manifest-validated BenchmarkArtifact.

## Acceptance criteria for v0.4

1. Interface spec committed (`eval/label_blind_ingestion_spec.md`).
2. Coverage gate default `>= 0.8` documented.
3. Methodology explicitly reuses existing KSR-EVAL Phase 2 machinery.
4. B6 marked design-only for v0.4.
5. Hugo credited in the document.
