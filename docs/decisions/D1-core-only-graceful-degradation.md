# D1: Runtime core-only graceful degradation

## Status

Accepted — Epic 38 / DSS-270.

## Context

The DSS Kernel Semantic Registry (KSR) contains:

- Engineering kernel fields (digit/prime/lattice registries, checksum invariants, quaternary gates, patches) that are required for runtime encode/decode, ledger foundations, and governance.
- Steward-only overlays (Hebrew-letter lattice mappings, iChing trigram overlays, religious commandment text, esoteric glossary entries, cross-domain A/E/P/H nodes) that are not required for the verified engineering use-cases.

The public repository must not ship steward-only content. We needed to decide whether the runtime requires the full registry as a deploy-time private artifact, or whether it can operate on `ksr-core` alone with steward features degrading gracefully.

## Decision

**Default: core-only graceful degradation.**

The public runtime loads `ksr-core` as `apps/backend/backend/kernel/semantic_registry.yaml`. Steward-dependent functions degrade gracefully:

- `BaseFoundationService._load_cross_domain_registry()` returns an empty `domains` dict when only `ksr-core` is present; the ledger foundation record remains valid and operational writes are accepted.
- `generate_kernel_constants.py` regenerates `constants.py` from `ksr-core` engineering fields.
- Steward-only lattice enrichment modules (`coord_enrichment.py`, `embeddings.py`, `output_formatter.py`, `reverse_parser.py`) remain in the repository but are treated as steward-only; they are excluded from PUB-1 esoteric-content scans and are only meaningful when the steward pack is deployed.

The private full-source registry lives at `private/semantic_registry.yaml` (git-ignored). Build tooling (`tools/ksr_build.py`) reads from that path and emits `ksr-core`, `ksr-pack-domains`, and `ksr-pack-steward` artifacts.

The encrypted envelope `apps/backend/backend/kernel/semantic_registry.enc` remains a private deploy artifact for environments that need full-source integrity verification, but it is not required for the public core runtime.

## Consequences

- Public repo size and risk surface are reduced.
- Existing backend tests pass against `ksr-core` (1246 passing).
- Downstream consumers that need cross-domain overlays must deploy `ksr-pack-domains` and load it as an overlay; this is not yet automated and is tracked for future work.
- The PUB-1 esoteric-content scan is the single gate that blocks accidental steward-term leakage into the public tree.

## Alternatives considered

- **Private full-registry deploy artifact**: Keep the full YAML out of the repo but inject it at deploy time. Rejected because it complicates CI and public reproduction; `ksr-core` is sufficient for the verified claims.

## Owner

agent:codex
