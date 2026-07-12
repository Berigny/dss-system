# API Compatibility Policy: v0 -> v1

Status date: 2026-02-23
Scope: `ds-backend-local` sync, ledger, and tenancy/authz contracts

## Purpose

Define strict compatibility rules so v1 capabilities can ship without breaking active v0 clients during ledger decoupling and multi-tenant rollout.

## Compatibility Guarantees

1. Existing v0 routes remain available and behaviorally stable during rollout:
- `/sync/v0/handshake`
- `/sync/v0/push`
- `/sync/v0/pull`
- `/sync/v0/backfill`
- `/sync/v0/status`

2. Changes are additive-first:
- New fields may be added as optional.
- Existing required fields are not removed or retyped in-place.
- New validation strictness is released behind explicit env gates.

3. Backward compatibility is required for registry metadata:
- Legacy key `__ledgers__` remains readable/writable while v1 registry `__ledgers_v1__` is active.
- Admin list responses expose both:
  - `ledgers` (legacy list)
  - `ledger_records` (v1 structured records)

4. Error semantics remain deterministic:
- Known sync rejection reasons use canonical reason codes.
- Unknown verifier reasons normalize to `verification_failed` with detail preserved separately.

## Field Evolution Rules

1. Additive field introduction:
- New request fields default to current behavior when omitted.
- New response fields are optional for clients and must not change meaning of existing fields.

2. Required-field promotions:
- Any optional->required promotion must be staged through compatibility mode.
- Promotion requires:
  - rollout gate in env
  - contract tests for old and new request shapes
  - rollback switch documented in runbook

3. Type and semantic stability:
- No silent type changes.
- No field meaning changes without versioned replacement field.

## Tenancy/Authz Migration Gates

Staged rollout order:

1. `LEDGER_AUTHZ_MODE=allow_all`
- Compatibility baseline for route wiring and telemetry.

2. `LEDGER_AUTHZ_MODE=registry`
- Registry-backed policy decisions active.
- Observe deny candidates and policy mismatches.

3. `LEDGER_CONTEXT_MODE=enforce`
- Explicit ledger context required on guarded write paths.

4. `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`
- Unknown-ledger requests rejected by policy.

5. `RESOLVER_NAMESPACE_GATE_MODE=strict`
- Resolver namespace/tenant gate fully enforced.

Operational details and rollback commands are tracked in:
- `backend/utils/ref/migration-runbook-commands.md`
- `backend/utils/ref/fly-backend.env.example`

## Sync Contract Invariants

1. Event identity and chain invariants are preserved:
- `event_id`, `prev_event_id`, `stream_key`, `seq`, and proof fields remain stable.

2. Replay safety and quarantine behavior remain first-class:
- Duplicate/replay/divergence handling must not regress.
- Quarantine reasons remain machine-readable and stable.

3. Scope consistency:
- `ledger_id_h64` remains required for `/sync/v0/push`.
- Ledger scope mismatches continue to reject deterministically.

## Rollback Policy

If rollout gates produce elevated denies or route regressions:

1. Revert strictness in reverse order:
- `RESOLVER_NAMESPACE_GATE_MODE=strict -> audit`
- `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny -> allow`
- `LEDGER_CONTEXT_MODE=enforce -> compat`
- `LEDGER_AUTHZ_MODE=registry -> allow_all`

2. Keep data compatibility in place:
- Continue dual-write/read for `__ledgers__` and `__ledgers_v1__`.

3. Preserve observability:
- Keep authz/context metrics and deny logs enabled for root cause analysis.

