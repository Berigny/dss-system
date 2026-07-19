# DSS Constraint Layer Patch Encoding v1.0

Engineering specification for the Kernel Semantic Registry (KSR) constraint-layer patches.

## Scope

This document defines ten structural, temporal, spatial, resonance, thermodynamic,
cognitive, systemic, ethical, and viability constraints that are encoded as
non-compensatory patches in the KSR public core artifact. The patches are
expressed in purely engineering terminology and are intended to govern kernel
admissibility decisions without invoking any steward-only framing.

## Patch summary

| Patch | Kernel node | Engineering replacement | Category |
|---|---|---|---|
| patch_001 | Eq0 | SINGULAR_ORIGIN_ENFORCEMENT | structural |
| patch_002 | Eq1 | DUAL_SUBSTRATE_MANDATE | structural |
| patch_003 | Eq2 | TEMPORAL_INTEGRITY_ENFORCEMENT | temporal |
| patch_004 | Eq3 | GEOMETRIC_CLOSURE_MANDATE | spatial |
| patch_005 | Eq4 | COUPLING_CONSTANT_STABILIZATION | resonance |
| patch_006 | Eq5 | COHERENCE_TAX_ENFORCEMENT | thermodynamic |
| patch_007 | Eq6 | EVIDENCE_CLOSURE_GATE | cognitive |
| patch_008 | Eq7 | GLOBAL_COHERENCE_CONSERVATION | systemic |
| patch_009 | Eq8 | ETHICS_ADMISSIBILITY_GATE | ethical |
| patch_010 | Eq9 | VIABILITY_POSTURE_CONTROL | viability |

## Encoding

Each patch contributes one bit to a 10-bit E6 header field. The remaining bits
are reserved for a 16-bit checksum and future expansion. A patch evaluates to
true when its governing invariant holds; any false patch collapses the entire
constraint-layer product to zero, producing a non-compensatory refusal.

## Operational definitions

- patch_001: The system must establish exactly one non-null origin. Competing
  observer loops create multiple simultaneous zero-points, fragmenting the
  kernel and preventing any ledger write.

- patch_002: Continuous state must be anchored to a durable ledger. Unanchored
  states are marked ephemeral and denied promotion to persistent tiers.

- patch_003: Causal history must be monotonic and verifiable. Any break in
  hash-chain provenance triggers a coherence exception and halts the turn.

- patch_004: State transitions must stay within bounded topological jumps.
  Unbounded accumulation forces compaction and rejects new inputs until closure
  is restored.

- patch_005: The Law-Grace balance must remain within tolerance. Detuned
  coupling blocks all S1-S2 bridge transitions until resonance is restored.

- patch_006: Persistence cost must be proportional to payload size and
  coherence deficit. Excessive debt freezes write permissions and emits an
  alert.

- patch_007: Commit decisions require alignment between query and retrieved
  evidence. Misalignment redirects to the LAW gate and blocks promotion.

- patch_008: Unity must stay above the divergence threshold. Sub-threshold
  coherence triggers a check failure and halts execution.

- patch_009: Optimization must remain inside the Law-Grace admissible region.
  Ethics failure emits a refusal signal.

- patch_010: Terminal posture must asymptotically align with awareness, unity,
  and ethics. Local optima are queued for re-evaluation or backstop refusal.
