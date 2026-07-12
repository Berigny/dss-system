# Dual Substrate Ledger Decoupling Rollout

Status date: 2026-03-02  
Scope: `ds-backend-local`, `ds-middleware-local`, `ds-frontend-local`

## Current State

Completed:

- E6 protocol/spec baseline drafted:
  - `backend/utils/spec/e6-header-v0.md`
  - `backend/utils/spec/e6-envelope-v0.md`
  - `backend/utils/spec/e6-sync-v0.md`
- Envelope crypto/runtime:
  - HMAC (`alg_id=2`) and Ed25519 (`alg_id=1`) verification in backend.
- Sync v0 API implemented:
  - `/sync/v0/handshake`
  - `/sync/v0/push`
  - `/sync/v0/pull`
  - `/sync/v0/backfill`
  - `/sync/v0/status`
- Middleware signed push smoke path implemented and validated.
- Middleware OpenClaw `P0` integration harness implemented and passed:
  - `tests/integrations/openclaw_p0_harness.py` in `ds-middleware-local`
  - passed checks:
    - handshake (`alg_id=1`)
    - signed push accept
    - same-stream chain continuity (`seq=1 -> seq=2`)
    - duplicate/replay detection
- Neo4j projection blueprint drafted:
  - `backend/utils/ref/ledger-neo4j-projection-v0.md`

Completed on 2026-02-25 (frontend/middleware model authority alignment):

- Frontend model list authority moved to middleware:
  - `ds-frontend-local/app.py` now fetches `/api/models` from middleware with JSON accept header.
  - frontend no longer curates runtime model options independently.
- Frontend selection behavior narrowed to persistence-only:
  - UI stores selected agent/model in session/cookie.
  - fallback selection now uses middleware-provided options.
- Middleware stream model routing fixed:
  - `ds-middleware-local/routes/orchestrator.py` now resolves execution model in this order:
    - `payload.agent` -> `payload.model` -> `payload.provider` -> `session.agent` -> `LLM_MODEL`.
  - provider/agent normalization prevents unintended fallback to `llama3.2:latest` when online provider is selected.
- Production verification completed (2026-02-25):
  - `GET /health` on backend -> `200`.
  - `POST /api/chat/smart_stream` on middleware -> `200` stream with `model=provider` for online model requests.
  - `POST /api/chat/smart_stream` via frontend -> `200` stream with propagated online model/provider metadata.
- Commit refs:
  - `ds-frontend-local`: `3400d54` (`Use middleware as model source of truth in frontend`)
  - `ds-middleware-local`: `7ea3af4` (`Honor provider as smart_stream model fallback`)

Completed on 2026-02-25 (latest pass: strict provisioning bootstrap + cleanup normalization):

- Backend strict provisioning bootstrap fix shipped:
  - `POST /admin/ledgers` now authorizes bootstrap against default admin scope rather than requiring pre-existing target ledger authz.
  - resolves strict-mode failure where unknown-ledger policy `deny` blocked new ledger creation.
  - files:
    - `backend/api/admin.py`
    - `backend/tests/test_admin_ledger_provisioning.py`
- Regression coverage added:
  - bootstrap create succeeds in `LEDGER_AUTHZ_MODE=registry` with `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`.
- Backend deployed and validated in cloud:
  - `GET /health` -> `200`
  - `POST /admin/ledgers` (`gate-alpha`) -> `200`, `created=true`
  - `GET /admin/ledgers` shows `default` + `gate-alpha`
- Local cleanup completed for backend workspace:
  - removed local `data/ledger.db` test-era RocksDB state
  - removed generated cache artifacts (`__pycache__`, `.pyc`, local pytest cache)
  - preserved `backend/utils/ref/` content as requested
- Middleware repo normalization completed:
  - added `.gitignore` to stop future tracking of virtualenv/cache artifacts
  - removed already-tracked generated artifacts in a dedicated cleanup commit
- Tenant bootstrap API scaffolding implemented (local + tests):
  - added `POST /admin/tenants` (idempotent tenant + default ledger bootstrap)
  - added `GET /admin/tenants` (tenant registry visibility)
  - added strict-mode regression tests for tenant provisioning
  - deployment/promotion to cloud pending for this endpoint block
- Commit refs:
  - `ds-backend-local`: `e6dd81d` (`fix(admin): allow ledger bootstrap under strict authz`)
  - `ds-middleware-local`: `586d27e` (`chore(repo): stop tracking venv/cache artifacts`)

In progress:

- Platform migration planning and deployment topology hardening:
  - Vercel (frontend + middleware)
  - Fly.io (backend/ledger authority)

Completed on 2026-02-23 (backend boundary extraction):

- Ledger boundary extraction from route layer is now complete for current backend routes.
  - Introduced and expanded `backend/services/ledger_service.py` as the request-scoped boundary.
  - All `backend/api/*` routes now resolve DB/store/substrate/telemetry via `LedgerService` helpers.
  - Added constructor helpers:
    - `memory_ledger()`
    - `memory_substrate()`
    - `telemetry_store()`
  - Route migrations completed across:
    - `backend/api/http.py`
    - `backend/api/sync.py`
    - `backend/api/ledger.py`
    - `backend/api/chat.py`
    - `backend/api/admin.py`
    - `backend/api/stats.py`
    - `backend/api/ui.py`
    - `backend/api/enrich.py`
    - `backend/api/ingest.py`
    - `backend/api/resolver.py`
  - Commit refs:
    - `28cb2e0` (`backend: add ledger service constructors for ledger/substrate/telemetry`)
    - `773f0d6` (`backend: route migrations use ledger service constructors`)

Recently completed hardening:

- Flow topology guardrails now locked in code (`backend/fieldx_kernel/flow_rules.py`):
  - static topology assertions
  - bridge-only cross-substrate policy
  - centroid (`C`) substrate-context guard
  - simplified mediator switch logic
- Flow governance test coverage added:
  - `backend/tests/test_flow_rules.py`
  - `backend/tests/test_coord_walk.py` updated with flow-violation propagation check
- Middleware walk alignment improved:
  - walk `flow_diagnostic` now propagated into orchestrator router decision + walk payload metadata
  - candidate ranking penalizes flow-violation diagnostics during guided walk selection
- Coordinate feedback + pin extension delivered (no new scoring family):
  - backend store supports appendable 0..3 feedback with reason, actor, timestamp
  - anti-gaming daily aggregation: one effective actor contribution per day (latest wins for the day)
  - compatibility maintained:
    - `POST /ledger/pin/{entry_id}` maps to feedback rating `3`
    - `POST /ledger/unpin/{entry_id}` maps to feedback rating `0`
  - new APIs:
    - `POST /ledger/feedback/{entry_id}`
    - `GET /ledger/feedback/{entry_id}`
  - resolver payload now includes `feedback_rollup` for agent consumption
- Middleware feedback propagation delivered:
  - `coord_feedback` now emitted in stream `context_meta`
  - `coord_feedback` included in commit metadata and final stream `meta` payload
- Telemetry redundancy trim delivered (minimal-change path):
  - turn telemetry build deduplicated via shared helper in `backend/api/chat.py`
  - session stats now return a single latest event payload (`latest_event`) + compact `e6_diagnostics`
  - `gen_cost` deprecated from public telemetry request schema while remaining backward-compatible as legacy input
  - `memory_cost_per_1m_tokens` now prefers explicit `memory_tokens` when present (fallback to word estimate only when missing)

Latest pipeline consistency and latency hardening (current branch):

- Search telemetry semantics aligned:
  - middleware emit path now uses explicit search flags (`eligible_for_search`, `search_used`) instead of heuristic proxies.
  - reduces backend-side invariant repair churn and improves metric trustworthiness.
- EQ9 pre/post consistency stabilized:
  - pre-commit and post-commit EQ9 now separated and labeled (`eq9_eval_pre_commit`, `eq9_eval_post_commit`, `eq9_eval_source`).
  - post-commit update path supports trailing `meta_patch` instead of blocking primary `meta`.
  - short TTL post-introspect cache added to reduce repeated post-commit introspection latency.
- Frontend diagnostic event path unblocked:
  - orchestrator consumer now captures diagnostic events (`grounding_override`, `anchor_resolution`, `walk_metric_delta`, `walk_stop`, `meta_patch`) when enabled.
  - pipeline diagnostics are now opt-in (default off) with bounded buffers and event compaction.
  - `walk_metric_delta` is downsampled via stride to reduce event pressure.
- Anchor resolution reliability improved:
  - "yesterday" now resolves in local/reference timezone (not forced UTC boundary).
  - anchor snippet label now avoids `[anchor:None]` cosmetic noise via safe fallback.
- Grounding fallback correctness improved:
  - fallback message now uses dynamic `eq9_target.output_tokens_soft` (defaults only when missing), removing hardcoded target drift.
- Debug payload size controls:
  - `meta_patch` introspect snapshot is now opt-in (auto-enabled only in telemetry debug mode), reducing default payload size.
- Capture-all quarantine observability:
  - blocked-turn fallback now persists quarantined writes (`loop_blocked|audit_blocked|persistence_error`) instead of dropping.
  - telemetry/rollups now track:
    - `quarantine_writes`
    - `quarantine_loop_blocked`
    - `quarantine_audit_blocked`
    - `quarantine_persistence_error`
    - `quarantine_write_rate`
  - stats alerts now include quarantine pressure signals:
    - `quarantine_write_alert_active`
    - thresholds: count + rate
    - dominant reason and reason breakdown
- Ops rollup publication (EQ9 + meta patch):
  - telemetry ingest and rollups now track:
    - `eq9_eval_source_{pre_commit|post_commit_metadata|post_commit_cache|pending_post_commit_introspect|post_commit_introspect}`
    - `meta_patch_{applied|skipped|timeout|error|other_skip}`
  - stats surfaces derived ops rates:
    - `meta_patch_applied_rate`
    - `meta_patch_timeout_rate`
    - `meta_patch_error_rate`
  - coverage now includes:
    - `eq9_eval_source_samples`
    - `meta_patch_samples`
- Tenancy/authz contract hardening delivered (2026-02-23):
  - write-path explicit ledger context enforced for:
    - `/chat`
    - `/chat/stream`
    - `/api/chat/commit-answer`
    - `/ingest`
    - `/ingest/stream`
    - `/ingest/stream-file`
    - `/ingest/file`
    - `/enrich`
    - `/enrich/guardian`
  - read/admin/stats authorization hooks added for ledger-scoped routes:
    - `backend/api/ledger.py`
    - `backend/api/resolver.py` (plus optional strict namespace gate mode)
    - `backend/api/stats.py`
    - `backend/api/admin.py`
  - route contract tests added:
    - `backend/tests/test_tenancy_route_contracts.py`
  - chat route error-path correctness fix:
    - `HTTPException` now preserved (no unintended 500 wrapping).
- Ledger provisioning baseline delivered (2026-02-23):
  - `backend/api/admin.py` now maintains structured v1 registry key `__ledgers_v1__`.
  - `POST /admin/ledgers` is idempotent and persists ownership/policy metadata.
  - `GET /admin/ledgers` now returns both `ledgers` and `ledger_records`.
  - compatibility maintained with legacy `__ledgers__` list key.
  - provisioning tests added:
    - `backend/tests/test_admin_ledger_provisioning.py`

Completed on 2026-03-02 (namespace drift containment + orphan ledger migration/cleanup):

- Backend namespace write hardening for coord walk persistence:
  - `POST /chat/walk/write` now resolves canonical ledger scope and enforces explicit write authz before persisting.
  - client-provided `namespace` is no longer authoritative for storage key; persisted namespace is canonical ledger scope.
  - implementation:
    - `backend/api/chat.py` (`walk_write_endpoint`)
    - reuse of existing scope/authz primitives:
      - `_resolve_explicit_ledger_id(...)`
      - `authorize_or_raise(..., explicit_context=True)`
- Middleware write-path containment to canonical ledger scope:
  - coord-walk write payload now sends `ledger_id` and canonical namespace bound to active ledger.
  - telemetry write payload now emits `namespace/entity` as canonical ledger scope, avoiding hashed session namespace drift in metrics namespaces.
  - implementation:
    - `ds-middleware-local/routes/orchestrator.py`
- Middleware session namespace default aligned with backend canonical policy:
  - `build_entity_namespace(...)` now defaults to ledger namespace mode.
  - legacy hashed mode preserved behind explicit opt-in (`MIDDLEWARE_ENTITY_MODE=session_hash`).
  - implementation:
    - `ds-middleware-local/utils/session.py`
- Middleware fallback defaults changed to canonical demo ledger:
  - `DEFAULT_LEDGER`/`DEFAULT_LEDGER_ID` now default to `chat-demo` instead of `default`.
  - implementation:
    - `ds-middleware-local/config/settings.py`
- Namespace-policy pinning:
  - local backend env pinned to `LEDGER_NAMESPACE_SOURCE=ledger_id`.
  - Fly deploy config pinned in code (`fly.toml`) to prevent accidental `entity_compat` drift on deploy.
  - note: direct Fly secret update required authenticated `flyctl` session; codified in `fly.toml` for next deploy.
- Orphan ledger audit + migration + cleanup completed:
  - cloud and local chat-like orphan namespaces audited.
  - one-shot migration utility added and executed:
    - `backend/scripts/migrate_orphan_namespaces.py`
  - migrated orphan entries into canonical `chat-demo` namespace using idempotent `MIGR-*` identifiers and migration metadata.
  - removed orphan namespaces post-migration on cloud (`/admin/clear_ledger`) and local DB.
  - post-clean verification:
    - cloud audit reduced to canonical chat namespace only
    - local chat-like namespaces reduced to `chat-demo` only

## Architecture Snapshot

1. Backend (`ds-backend-local`)
- Current authority for ledger persistence and sync validation.
- Verifies envelope authenticity and replay/chain controls.

2. Middleware (`ds-middleware-local`)
- Signs envelopes and integrates model/provider orchestration.
- Candidate "Developer Memory Gateway" control plane.

3. Frontend (`ds-frontend-local`)
- UX layer only; no private signing keys.

### Canonical Domain Model v2 (Demo Foundation, 2026-02-25)

Objective: stop scope conflation between ledger namespace, actor identity, and UI/runtime source.

Core entities:

1. `ledger`
- Canonical memory boundary and namespace authority.
- Primary key: `ledger_id` (must be explicit on all read/write/feedback endpoints).
- Ownership/policy fields remain in `__ledgers_v1__` (`tenant_id`, owner principal, policy profile, status, metadata).

2. `contributor`
- Actor that performed the action.
- Types: `user`, `model`, `service`.
- Canonical identity: `(principal_id, principal_type)` from headers/contracts.
- Ledger binding is role-based (owner/admin/writer/reader) via authz policy.

Identity hardening direction (DID/VC, reuse-first):
- User, model, and tool/service principals should each have a stable DID-backed identity.
- Session/turn identifiers remain ephemeral (`session_id`, `turn_id`, nonce), not long-lived DID subjects.
- Keep existing principal tuple as compatibility key while introducing DID fields:
  - `principal_did` (canonical),
  - `principal_key_id` (verification key reference),
  - optional `principal_vc_ref` (attested role/capability profile).
- Reuse existing authz/scope/signature primitives:
  - `backend/services/authz.py` for action authorization,
  - `backend/services/ledger_scope.py` for ledger/context boundary enforcement,
  - E6 envelope signature + nonce/replay checks for write-time integrity.
- Policy target:
  - capabilities are granted by verified principal identity and role, not by ad-hoc string IDs.
  - one canonical active principal per actor class in demo mode (user/model/tool), with controlled expansion later.

3. `context`
- Runtime surface that initiated the operation (frontend/client/channel).
- Examples for current demo scope:
  - `ctx:frontend:vercel`
  - `ctx:frontend:local`
  - `ctx:decoder`
  - `ctx:openclaw`
  - `ctx:chatgpt`
- Proposed request contract field/header: `context_id` / `x-context-id`.

4. `entry` (coordinate record)
- Primary record remains `namespace:identifier`, where `namespace == ledger_id`.
- Provenance must include:
  - `ledger_id`
  - `contributor_id` (derived from principal)
  - `context_id`
  - optional `provider_id`, `model_id`, `session_id`, `turn_id`

Relationship rules:

1. One ledger has many contributors.
2. One ledger has many contexts.
3. One context may be bound to multiple ledgers only via explicit policy.
4. Every entry belongs to exactly one ledger.
5. `entity` is conversation/session metadata, not namespace authority.

Contract invariants (must hold in strict mode):

1. Request `ledger_id` must match:
  - header scope (`x-ledger-id`) when provided
  - coordinate namespace on coordinate-addressed operations
  - persisted namespace at write time
2. Unknown ledger in strict mode returns deterministic `403` (`reason=unknown_ledger`).
3. Writes are allowed only when both are true:
  - contributor is authorized for ledger
  - context is allowed for ledger (after context binding is introduced)

Current implementation fit (validated):

1. Fits:
  - entries already have unique coordinates carrying prompt/response/metadata/telemetry.
  - provisioning/authz primitives exist (`/admin/ledgers`, registry-backed authz).
2. Gaps:
  - some write paths still authorize `ledger_id` but persist under `entity` namespace.
  - context identity is not yet first-class across write/read contracts.
  - providers are currently mixed with contributor semantics in some flows.

## Parallel Delivery Tracks

Track A: Control Plane (middleware service)

- Source: `backend/utils/ref/middleware-service.md`
- Focus: tenant onboarding, ledger provisioning, provider key refs, channel integrations.
- Primary repo: `ds-middleware-local`
- Integration certification reference:
  - `backend/utils/ref/integration-certification-matrix.md`
- Adaptive autonomy/pressure policy references:
  - `backend/utils/ref/adaptive-execution-policy-v0.json`
  - `backend/utils/ref/adaptive-execution-hooks-v0.md`

Track B: Data Plane (ledger + sync + projection)

- Source: `backend/utils/ref/ledger-neo4j-projection-v0.md`
- Focus: signed event ingest, replay-safe sync, Neo4j read projection.
- Primary repo: `ds-backend-local`

Track C: Migration/Platform

- Focus: deploy topology, env contracts, cutover/rollback.
- Platforms:
  - Vercel: `ds-frontend-local`, `ds-middleware-local`
  - Fly.io: `ds-backend-local` (ledger authority + sync + projector worker)

### Passkey + DID/VC Frontdoor Auth (Planned, Reuse-First)

Goal:
- use passkeys (WebAuthn) for authentication proof while keeping DID/VC as canonical identity and authorization root.

Authority split:
1. Passkey/WebAuthn:
- validates possession + user verification (`navigator.credentials.get` assertion).
2. DID/VC layer:
- resolves principal semantics (`who`) and grants (`what`) after passkey verification.
3. Middleware:
- trust bridge that binds `credential_id -> principal_did` and issues short-lived session tokens with scoped claims.

Binding record (minimum):
- `credential_id`
- `principal_did`
- `principal_key_id`
- status (`active|revoked`)
- timestamps (`created_at`, `last_used_at`, optional `revoked_at`)
- optional `principal_vc_ref` list for capability derivation

Verification requirements:
1. WebAuthn checks:
- `origin`, `rpIdHash`, `challenge`, `userVerification`, signature validity, sign-count/replay rules.
2. Policy checks:
- bound DID exists and is active
- VC/capability set valid for requested scope/action
- ledger/context authz passes existing policy evaluator

Session issuance:
- middleware issues short-lived session JWT/opaque token with:
  - `sub` = `principal_did`
  - `auth_method` = `passkey`
  - scoped claims (`roles`, `allowed_context_ids`, ledger grants)
  - `iat`, `exp`, and session nonce/jti
- avoid long-lived VC payload storage in browser; store references/derived claims.

Reuse mapping (existing components):
- `backend/services/authz.py` for action authorization
- `backend/services/ledger_scope.py` for ledger/context enforcement
- existing E6 envelope signature + nonce/replay controls for write provenance
- existing contributor provenance fields extended with DID/key-id metadata

Phased implementation:
1. Phase P1 (binding + verify):
- add passkey binding store and `/auth/challenge`, `/auth/verify` middleware endpoints.
- persist audit fields for auth events and credential usage.
2. Phase P2 (claim bridge):
- resolve DID/VC after passkey verify and issue scoped session token.
- integrate with current principal extraction path without breaking legacy tuple fields.
3. Phase P3 (strict mode):
- enforce DID-backed principal for sensitive write/admin operations.
- keep compatibility path for controlled demo environments behind explicit flag.

Exit criteria:
- passkey-authenticated sessions map deterministically to DID principal identity.
- authz decisions are based on DID/VC-derived claims and existing policy engine.
- revocation/unbind of credential or VC takes effect without key rotation in clients.
- no parallel auth stack introduced; all checks compose through existing scope/authz/provenance controls.

## Identity Plane Decoupling Plan (IAM/CIAM + DID/VC)

Goal:
- decouple identity/authentication/authorization control state from ledger persistence while preserving ledger provenance and existing route contracts during migration.

### Target Architecture

1. Identity Control Plane (new authority)
- Responsibilities:
  - principal registry (DID-backed identities for `user|model|service`)
  - passkey credential binding and lifecycle
  - VC/capability reference resolution and revocation
  - session/token minting with scoped claims
- Recommended primary store:
  - relational (authoritative) for lifecycle + revocation + query ergonomics
  - optional RocksDB projection/cache for hot-path reads and offline recovery

2. Ledger Data Plane (existing authority)
- Responsibilities:
  - coordinate writes/reads, sync chain, provenance capture
  - immutable/audit-grade event history
- Non-responsibilities:
  - authoritative credential binding
  - primary revocation state
  - primary policy grant authoring

3. Policy Decision Bridge (reuse-first)
- Keep authorization enforcement in existing path:
  - `backend/services/authz.py`
  - `backend/services/ledger_scope.py`
- Expand inputs:
  - `principal_did`
  - capability/grant claims
  - allowed contexts
  - ledger-bound permissions

### Contract Changes (Backward-Compatible First)

1. Principal envelope/header surface
- Add fields (optional initially, required in strict mode):
  - `principal_did`
  - `principal_key_id`
  - `context_id`
  - `session_jti` / nonce
- Keep legacy compatibility fields:
  - `principal_id`
  - `principal_type`

2. Session token claims contract
- Required token claims:
  - `sub` (`principal_did`)
  - `auth_method` (`passkey` or compatible fallback)
  - `roles` / capabilities
  - `allowed_context_ids`
  - `allowed_ledgers` or ledger policy references
  - `iat`, `exp`, `jti`

3. Provenance extension on writes
- Persist (when available):
  - `principal_did`
  - `principal_key_id`
  - `context_id`
  - `auth_method`
  - `auth_session_jti`
- Keep current contributor fields for compatibility during transition.

### Delivery Workstreams

1. Workstream A: Identity registry + credential binding
- Implement control-plane endpoints:
  - `POST /auth/challenge`
  - `POST /auth/verify`
  - `POST /auth/passkeys/bind`
  - `POST /auth/passkeys/revoke`
  - `GET /auth/principals/{did}`
- Add data model tables/collections:
  - principals
  - credentials
  - vc_refs/capabilities
  - sessions
  - revocations
- Add operator-safe admin APIs for emergency revoke/disable.

2. Workstream B: Token bridge into existing backend/middleware
- Middleware:
  - verify passkey assertions
  - resolve DID/VC capabilities
  - issue short-lived signed session token
- Backend:
  - validate token and map claims into existing authz call sites
  - continue accepting legacy principal tuple behind compatibility flag

3. Workstream C: Policy model upgrade (no second engine)
- Extend current policy evaluator inputs, do not fork logic.
- Add policy dimensions:
  - ledger action grants (read/write/admin/sync)
  - context allowlist constraints
  - model/service principal constraints
- Preserve deterministic deny reasons (`unknown_ledger`, `context_not_allowed`, `capability_missing`, etc).

4. Workstream D: Data-plane decoupling and audit link
- Keep auth authority out of ledger DB.
- Mirror auth events to ledger as audit records only:
  - credential bound/revoked
  - session issued/expired/revoked
  - capability grant/revoke events
- Ensure mirrored records are clearly marked non-authoritative.

### Migration Sequence

1. Phase D0: Observability before enforcement
- Emit auth-context diagnostics in stream/meta:
  - principal resolution source
  - context_id used
  - allow/deny reason summary
- Add dashboards for:
  - legacy-principal usage rate
  - DID-backed usage rate
  - deny reason distribution

2. Phase D1: Dual-write / dual-read compatibility
- On auth success:
  - produce DID claims
  - also populate legacy principal tuple for old consumers
- On write provenance:
  - store both legacy and DID fields
- No hard enforcement yet.

3. Phase D2: Soft enforcement gates
- Require valid session token for sensitive routes:
  - admin/provisioning/sync/write
- Legacy path allowed only with explicit compatibility flag + environment guard.

4. Phase D3: Strict enforcement
- DID-backed principal required for sensitive operations.
- Legacy tuple accepted for read-only demo endpoints only (optional).
- Unknown/invalid capability paths return deterministic `403`.

5. Phase D4: Legacy contraction
- remove write-authority semantics from legacy tuple
- keep legacy fields as denormalized metadata only

### Decoupling Tasks for Current Architecture

1. Backend (`ds-backend-local`)
- Add token claim parsing utility and pass into existing authz boundary.
- Extend route contracts with optional DID fields.
- Add strict-mode switch:
  - `AUTH_PRINCIPAL_MODE=compat|did_strict`
- Add deterministic deny reason mapping for new claim failures.

2. Middleware (`ds-middleware-local`)
- Implement auth challenge/verify + token issue path.
- Bind passkey credential IDs to DID principals.
- Attach signed session token to backend-bound requests.
- Keep current flow when auth mode is `compat`.

3. Frontend (`ds-frontend-local`)
- Add passkey login UX and session bootstrap.
- Store short-lived session token only; avoid long-lived VC payload storage.
- Surface auth/context diagnostics in debug panel for troubleshooting.

### Acceptance Criteria

1. Functional
- passkey-authenticated session deterministically maps to one active `principal_did`.
- authz decisions include DID/capability inputs and remain deterministic.
- revocation takes effect within one token TTL window or faster.

2. Security/Policy
- no sensitive write/admin route executes without token validation in strict mode.
- context restrictions enforced at policy boundary.
- replay/nonce/session-jti checks active and tested.

3. Migration Safety
- rollback path exists:
  - toggle to `compat` mode without data loss
  - preserve provenance continuity
- dual-write provenance parity validated across sample turns.

4. Operational
- dashboards/alerts for deny spikes, revocation events, and auth error classes.
- runbook published for key rotation, credential revoke, and emergency lockout.

## Prioritized Execution Backlog (P0/P1/P2)

Status date: 2026-03-06

Legend:
- Owner:
  - BE = backend (`ds-backend-local`)
  - MW = middleware (`ds-middleware-local`)
  - FE = frontend (`ds-frontend-local`)
  - OPS = deployment/observability
- Size:
  - S (<=1 day), M (1-3 days), L (3-7 days)

### P0 (Foundation + Safe Cut-In)

1. P0-01: Auth mode flags and routing guards
- Owner: BE
- Size: S
- Depends on: none
- Tasks:
  - add `AUTH_PRINCIPAL_MODE=compat|did_strict` runtime switch
  - thread mode into auth boundary and deny reason mapping
- Done when:
  - strict mode can be enabled without route breakage in compat mode
  - deny reasons are deterministic and logged

2. P0-02: Principal claim ingestion shim
- Owner: BE
- Size: M
- Depends on: P0-01
- Tasks:
  - parse optional claims (`principal_did`, `principal_key_id`, `context_id`, `session_jti`)
  - preserve legacy tuple extraction path
  - pass both into `authz.py` and `ledger_scope.py`
- Done when:
  - all sensitive routes accept optional DID claims in compat mode
  - existing tests remain green and new contract tests added

3. P0-03: Stream/meta auth-context diagnostics
- Owner: BE
- Size: S
- Depends on: P0-02
- Tasks:
  - emit compact diagnostics (`principal_source`, `context_id`, `authz_reason`)
  - include in stream `context_meta` and/or final `meta`
- Done when:
  - diagnostics visible in one cloud turn and one local turn
  - no secrets leaked in payloads

4. P0-04: Middleware token pass-through envelope
- Owner: MW
- Size: M
- Depends on: P0-02
- Tasks:
  - accept bearer/opaque session token from FE
  - forward normalized auth claims to backend request headers/payload
  - maintain legacy fallback path under compat mode
- Done when:
  - backend sees consistent auth claim envelope from middleware
  - existing non-auth smoke tests still pass

5. P0-05: Frontend debug visibility for auth/context
- Owner: FE
- Size: S
- Depends on: P0-03
- Tasks:
  - display auth/context diagnostics in debug panel
  - include upstream route marker (`x-ds-upstream-url`, fallback flag)
- Done when:
  - operator can diagnose auth/context decisions from browser only

6. P0-06: Observability baseline
- Owner: OPS
- Size: M
- Depends on: P0-03
- Tasks:
  - dashboard panels: deny reasons, legacy-vs-did usage, auth error classes
  - alert thresholds for deny spikes and token validation failures
- Done when:
  - dashboards and alerts published with runbook links

### P1 (Identity Control Plane MVP)

1. P1-01: Principal registry schema + API
- Owner: MW
- Size: L
- Depends on: P0-04
- Tasks:
  - create principal records keyed by `principal_did`
  - add read endpoints for principal status + key references
- Done when:
  - principal lifecycle (create/disable/read) is operational

2. P1-02: Passkey/WebAuthn challenge+verify endpoints
- Owner: MW
- Size: L
- Depends on: P1-01
- Tasks:
  - implement `/auth/challenge`, `/auth/verify`
  - verify origin/rpId/challenge/signature/sign-count
  - bind `credential_id -> principal_did`
- Done when:
  - one passkey login path works end-to-end in staging

3. P1-03: Session token issuer + validator
- Owner: MW + BE
- Size: L
- Depends on: P1-02, P0-02
- Tasks:
  - MW issues short-lived signed token with scoped claims
  - BE validates token and maps claims into existing authz boundary
- Done when:
  - token-authenticated writes succeed with DID provenance fields
  - expired/invalid tokens fail with deterministic reason

4. P1-04: Provenance dual-write rollout
- Owner: BE
- Size: M
- Depends on: P1-03
- Tasks:
  - persist DID fields alongside legacy contributor tuple
  - add parity checks for dual-write integrity
- Done when:
  - sampled turns show both field families populated correctly

5. P1-05: Frontend passkey bootstrap UX
- Owner: FE
- Size: M
- Depends on: P1-02, P1-03
- Tasks:
  - add login flow using WebAuthn
  - persist short-lived session token only
- Done when:
  - passkey login usable in cloud and local with fallback disabled in test mode

6. P1-06: Revocation and emergency disable
- Owner: MW + OPS
- Size: M
- Depends on: P1-01, P1-03
- Tasks:
  - credential/session revoke endpoints
  - operator runbook for emergency lockout
- Done when:
  - revoked credential/session blocked within token TTL window

### P2 (Strict Enforcement + Legacy Contraction)

1. P2-01: Enable `did_strict` for sensitive routes
- Owner: BE
- Size: M
- Depends on: P1 completion
- Tasks:
  - require DID-backed claims for write/admin/sync routes
  - keep compatibility exceptions explicitly listed
- Done when:
  - strict mode enabled in staging without policy regressions

2. P2-02: Context-bound authorization enforcement
- Owner: BE
- Size: M
- Depends on: P2-01
- Tasks:
  - enforce `allowed_context_ids` per ledger/action
  - return deterministic deny reasons for context violations
- Done when:
  - context misbinding tests pass and denies are observable

3. P2-03: Remove legacy write authority semantics
- Owner: BE + MW
- Size: M
- Depends on: P2-01
- Tasks:
  - legacy tuple remains metadata only
  - authz decisions use DID/capabilities exclusively
- Done when:
  - no sensitive route authorization depends on legacy tuple

4. P2-04: IAM authority hard split validation
- Owner: OPS + BE + MW
- Size: M
- Depends on: P2-03
- Tasks:
  - confirm ledger DB is non-authoritative for identity state
  - confirm audit-mirror auth events remain queryable
- Done when:
  - architecture review sign-off and rollback plan validated

### Suggested Sprint Packaging

1. Sprint A (1-2 weeks)
- P0-01 through P0-06
- objective: observability + compatibility scaffolding

2. Sprint B (2-3 weeks)
- P1-01 through P1-04
- objective: control-plane MVP + tokenized auth bridge

3. Sprint C (1-2 weeks)
- P1-05, P1-06, P2-01
- objective: end-user passkey flow + initial strict enforcement

4. Sprint D (1-2 weeks)
- P2-02 through P2-04
- objective: full DID strict mode and legacy contraction

### Definition of Ready (applies to all tickets)

1. route/contract impact identified
2. migration/rollback notes included
3. telemetry fields specified
4. test coverage target specified (unit + route contract + smoke)

### Definition of Done (applies to all tickets)

1. code merged with tests green
2. runbook updated where operator behavior changes
3. dashboards/alerts updated if auth semantics changed
4. rollout toggle documented (`compat` vs `did_strict`)

## Phased Execution Plan

### Phase 1: Service Boundary and Contracts

Goal: stop direct DB coupling from API routes.

Tasks:

1. Complete route adapters to `LedgerService` across `backend/api/*`.
2. Add compatibility policy doc for v0->v1 evolution.
3. Keep existing endpoints stable while internal boundary moves.

Exit criteria:

- `backend/api/*` routes no longer instantiate `LedgerStoreV2` directly.

Status on 2026-02-23:

- Exit criteria met for backend route layer.
- Compatibility policy doc delivered:
  - `backend/utils/ref/compat-policy-v0-v1.md`
- Phase 1 is fully closed.

Enables:

- Independent ledger service extraction without breaking API consumers.

### Phase 2: Tenancy Enforcement

Goal: make multi-ledger isolation first-class.

Tasks:

1. Require `ledger_id` in sync ingress/egress contracts.
2. Scope all stream/storage keys by `ledger_id`.
3. Add authz scaffolding `(principal, ledger_id, action)`.

Exit criteria:

- No write/read/sync accepted without explicit ledger context.

Enables:

- Safe provisioning for multiple ledgers per tenant/user/app.

### Phase 2A: Ledger Reset + Multi-Tenant Re-Foundation (Immediate)

Goal: clear test-era state safely, then relaunch with future-facing multi-tenant ledger provisioning semantics.

Tasks:

1. Freeze + export current state before reset.
   - capture `admin/history/audit` export and retain as dated archive artifact.
   - record current backend/middleware/frontend deploy SHAs and env fingerprints.
2. Execute reset path per environment using existing Make targets.
   - soft reset path: `make fly-soft-reset ...` for logical clear.
   - hard reset path (destructive): `make fly-hard-reset CONFIRM=1 ...` for volume replacement.
3. Re-provision baseline ledgers explicitly after reset.
   - use `POST /admin/ledgers` for each seed ledger rather than implicit write-side creation.
4. Normalize provisioning contract to multi-tenant first.
   - required fields at provisioning time:
     - `ledger_id`
     - `tenant_id`
     - `owner_id` (or principal binding)
     - `policy` (retention/sync/isolation profile)
5. Enforce explicit ledger context on all writes immediately after reset.
   - require `x-ledger-id` + `x-ledger-id-h64` (or equivalent explicit payload fields).
6. Run post-reset smoke validation.
   - no legacy test entities/coordinates visible.
   - known-ledger write/read succeeds.
   - unknown-ledger write rejected with deterministic authz reason.
7. Publish reset and bootstrap runbook.
   - include rollback/archive instructions and acceptance checklist.

Progress snapshot (2026-02-25):

- Completed:
  - reset archive/export path validated and used (`admin/history/audit` snapshot captured).
  - soft + hard reset run sequence validated in cloud.
  - baseline provisioning path validated post-reset via `POST /admin/ledgers`.
  - strict bootstrap bug fixed and deployed (unknown-ledger deny no longer blocks admin bootstrap).
  - baseline ledger provisioning validated for `default` and `gate-alpha`.
- Remaining in Phase 2A:
  - formalize tenant-first bootstrap API contract (single idempotent onboarding call).
  - finalize principal/tenant mapping policy for all bootstrap flows.
  - publish operator runbook as a stable checklist artifact.

Acceptance criteria:

- reset archive exists and is retrievable (`admin/history/audit` snapshot captured with timestamp).
- backend ledger store contains only post-reset data.
- provisioning works idempotently for baseline ledgers via `/admin/ledgers`.
- tenancy/authz checks enforce known-ledger + owner/tenant constraints after reset.
- frontend/middleware model selection remains middleware-authoritative and stable after reset.
- end-to-end smoke passes:
  - backend `/health` `200`
  - middleware `smart_stream` `200`
  - frontend `smart_stream` `200`
  - no cross-tenant leakage in history/entity listing.

Future-facing storage direction (decision for this phase):

- default topology remains one multi-tenant ledger authority service on Fly (not one app per ledger).
- isolation strategy is tiered:
  - default shared multi-tenant runtime with strict logical isolation,
  - optional dedicated deployment for high-isolation tenants later.
- ledger remains source of truth; graph/search mirrors remain projections with source-event traceability.

Immediate SaaS provisioning checklist (next execution block):

1. Introduce tenant bootstrap API (`/admin/tenants`) as the canonical provisioning entrypoint.
   - idempotent create semantics.
   - provisions tenant metadata + default tenant ledger set.
2. Canonical identity model:
   - `tenant_id`: `tenant:<slug>`
   - `ledger_id`: stable tenant-scoped namespace (for example `chat-<slug>`)
   - owner/admin principal binding persisted at provision time.
3. Keep `/admin/ledgers` as a lower-level primitive.
   - used by tenant bootstrap internally and for controlled operator repair.
4. Enforce strict authz invariants after bootstrap:
   - known-ledger write/read succeeds for owner/tenant/admin.
   - unknown-ledger write rejected with deterministic `403` reason.
5. Add regression coverage:
   - tenant bootstrap idempotency
   - cross-tenant read/write rejection
   - default-ledger compatibility path where explicitly required.

### Phase 3: Sync Reliability Hardening

Goal: restart-safe, replay-safe operations.

Tasks:

1. Add persistent cursor/checkpoint records.
2. Add DLQ replay workflows.
3. Add divergence repair tooling (beyond quarantine).

Exit criteria:

- deterministic replay and recoverable sync failures.

Enables:

- production-grade local/offline -> cloud propagation.

### Phase 3B: Projection Lane (Neo4j)

Goal: fast graph queries with ledger as source of truth.

Tasks:

1. Implement projector worker from sync cursors.
2. Add checkpoint and DLQ replay records.
3. Apply idempotent Cypher upserts from taxonomy in `ledger-neo4j-projection-v0.md`.

Exit criteria:

- replay rebuild reproduces graph state deterministically from ledger events.

Enables:

- graph-native query performance without compromising ledger integrity.

### Phase 4: Middleware Memory Gateway

Goal: product surface for AI developers/integrations.

Tasks:

1. Provisioning APIs: tenant/ledger/channel profile setup.
2. Provider key refs + rotation lifecycle.
3. n8n-friendly memory endpoints.

Exit criteria:

- middleware can onboard tenants and operate memory as a service.

Enables:

- plug-in "memory node" integrations across low-code and agent platforms.

### Phase 4B: Integration Certification

Goal: certify external systems using middleware as the single integration surface.

Targets:

1. OpenClaw (local)
2. n8n
3. ChatGPT App via MCP
4. WhatsApp channel adapter

Reference:

- `backend/utils/ref/integration-certification-matrix.md`

Tasks:

1. Create integration contract fixtures per target.
2. Run certification gates:
- Contract
- Memory E2E
- Failure behavior
- Security isolation
- Soak stability
3. Add integration-specific runbooks and alert routing.

Exit criteria:

- each target passes `P0` gates in the matrix before production enablement.

Enables:

- safe expansion into external ecosystems without bypassing tenancy and sync guarantees.

### Phase 5: Migration and Cutover

Goal: safe transition from monolith-owned ledger paths.

Tasks:

1. Dual-write with consistency diff checks.
2. Cohort-based rollout and rollback switch.
3. SLOs/alerts/runbooks for sync and key failures.

Exit criteria:

- stable SLOs after reads-then-writes cutover.

Enables:

- external usage at scale with operational confidence.

### Phase 6: Platform Deployment (Vercel + Fly.io)

Goal: production topology with clear control-plane/data-plane separation.

Tasks:

1. Deploy backend/ledger authority to Fly.io.
- expose stable FastAPI hooks:
  - `GET /health`
  - `POST /sync/v0/handshake`
  - `POST /sync/v0/push`
  - `POST /sync/v0/pull`
  - `POST /sync/v0/backfill`
  - `GET /sync/v0/status`
  - existing `/ledger/*` read/write routes

2. Deploy middleware to Vercel.
- set backend base URL to Fly app.
- keep Ed25519 private signing keys in Vercel middleware env only.
- expose middleware memory gateway endpoints to frontend/channels.

3. Deploy frontend to Vercel.
- frontend talks to middleware only.
- no signing private keys in frontend env.

4. Hardening hooks.
- strict CORS allowlist for Vercel origins on backend.
- auth between middleware and backend for sync and admin routes.
- per-ledger tenant context propagation headers.

Exit criteria:

- middleware/ frontend on Vercel operate against Fly backend with signed sync, stable latency, and no cross-tenant leakage.

Enables:

- internet-facing multi-tenant operations with portable deployment model.

## Immediate Next Sprint (Concrete)

1. Expand `LedgerService` adapters from sync routes to:
- `backend/api/http.py`
- `backend/api/ledger.py`
- `backend/api/enrich.py`
  - Status: complete in current branch.

2. Add required `ledger_id` validation to:
- `/sync/v0/push`
- `/sync/v0/pull`
- `/sync/v0/backfill`
  - Status: complete in current branch (`ledger_id_h64` required and enforced).

3. Add sync checkpoint persistence schema and endpoints.
  - Status: complete in current branch.
  - Added:
    - `POST /sync/v0/checkpoint/save`
    - `POST /sync/v0/checkpoint/load`
    - `GET /sync/v0/status` now reports `checkpoints`.

4. Update middleware daemon path from legacy `/sync/push` to `/sync/v0/*`.
  - Status: complete in current local stack.
  - `ds-middleware-local/sync_daemon.py` now uses bidirectional:
    - `POST /sync/v0/pull`
    - `POST /sync/v0/push`
    - cursor-based progression per direction.
5. Add deployment lane artifacts:
- Fly backend env template
- Vercel middleware/frontend env templates
- endpoint contract checklist
  - Status: complete in current branch.
  - Added:
    - `backend/utils/ref/fly-backend.env.example`
    - `backend/utils/ref/vercel-middleware.env.example`
    - `backend/utils/ref/vercel-frontend.env.example`
    - `backend/utils/ref/deployment-endpoint-contract-checklist.md`
6. Stand up integration certification harness for OpenClaw and n8n (`P0` tests first).
  - OpenClaw: complete
  - n8n: on hold until Vercel/Fly migration gate is green
  - MCP connector path: initial middleware scaffold complete (`/mcp` + `ds.*` tool stubs) for ChatGPT demo wiring.
  - MCP OAuth path: dev OAuth discovery/registration/token flow wired for connector compatibility.
  - MCP parity path: `ds.append_event` now runs middleware `/api/chat` pipeline before emitting signed sync events, so guardian/enrich flow aligns with UI path.
  - UI history parity: frontend `__all__` history now merges sync/MCP events from `/sync/v0/pull`.
  - Local ops: middleware `launch-all`/`kill-all` now include ngrok startup/cleanup and current MCP URL export to `.mcp.url`.
  - Introspection parity via MCP: new `ds.introspect` tool proxies backend `/api/chat/introspect`, so ChatGPT connector can apply the same runtime sequencing signals used by main pipeline.
  - MCP operability: middleware startup now prints local/public MCP URL and OAuth metadata endpoints to reduce connector misconfiguration.
  - EQ6 parity hardening: `ds.append_event` now supports `auto_e6` (default on), deriving header fields (`mode/ptype/law/route/K/P/E/dW/V_q`) from adaptive governor + introspection signals before envelope signing.

7. Execute the highest-leverage backend decoupling step:
- finish `LedgerService` route adapters in:
  - `backend/api/http.py`
  - `backend/api/ledger.py`
  - `backend/api/enrich.py`
  - Status: complete (2026-02-23).
    - route boundary migration finalized across `backend/api/*` with request-scoped `LedgerService`.
8. Wire adaptive execution hooks in middleware using:
- `adaptive-execution-policy-v0.json`
- `adaptive-execution-hooks-v0.md`
  - Status: initial wiring complete for `/api/chat` in `ds-middleware-local` behind feature flags.

9. Start point now (recommended):
- Lock the new pipeline behavior with cross-repo contract + latency tests:
  - add one integration suite that asserts `meta`/`meta_patch` contract, `eq9_eval_source` transitions, and diagnostic event gating semantics.
  - include timezone-aware anchor tests for "yesterday" under multiple offsets.
  - add performance assertions for:
    - p95 overhead with diagnostics off (baseline mode)
    - bounded overhead with diagnostics on + walk downsampling.
  - publish a small ops rollup for:
  - `eq9_eval_source` distribution (`post_commit_metadata|post_commit_cache|post_commit_introspect`)
  - `meta_patch` applied vs timeout/error rates.
  - Status: complete.
    - Added middleware contract suite scaffold:
      - `ds-middleware-local/tests/test_orchestrator_stream_contract.py`
      - `ds-middleware-local/tests/test_orchestrator_eq9_source.py`
      - `ds-middleware-local/tests/test_anchor_time_window.py` (passing locally)
    - Local verification:
      - middleware contract set: `12 passed`
        - `test_orchestrator_stream_contract.py`
        - `test_orchestrator_eq9_source.py`
        - `test_orchestrator_latency_smoke.py`
      - anchor window suite: `7 passed`
        - `test_anchor_time_window.py`
    - Backend verification:
      - `PYTHONPATH=. pytest -q backend/tests/test_metrics.py` -> `13 passed`
    - Follow-up (cross-repo wiring):
      - middleware telemetry emission should include `eq9_eval_source`, `meta_patch_status`, and `meta_patch_reason` so new backend ops rollups are populated from live traffic.

### Remaining Rollout Work (Current Snapshot)

0. Phase 2 tenancy/authz baseline (audit + first implementation slice complete on 2026-02-23).
- Current coverage:
  - explicit authz calls are present in:
    - `backend/api/http.py`
    - `backend/api/sync.py`
- Gaps identified:
  - substantially reduced on 2026-02-23:
    - write paths covered in `chat`/`enrich`/`ingest`
    - read/admin/stats paths now have explicit authz hooks
- Gap shape:
  - reduced further on 2026-03-02:
    - coord walk persistence no longer accepts client namespace as storage authority.
    - middleware walk/telemetry namespace propagation now ledger-bound.
  - remaining risk surface:
    - any route still operating in optional legacy mode (`entity_compat` / hashed session entity mode) must remain disabled by default and guarded by explicit env toggles.
  - telemetry write path is now ledger-bound (2026-02-23) with explicit-context enforcement semantics aligned to route policy.
- Next implementation slice:
  - implemented on 2026-02-23 for first write-path contracts:
    - `/chat`
    - `/chat/stream`
    - `/api/chat/commit-answer`
    - `/ingest`
    - `/ingest/file`
    - `/enrich`
  - enforcement now active via:
    - explicit `ledger_id` contract fields (payload/form where applicable) or `x-ledger-id` headers
    - `authorize_or_raise(..., explicit_context=True)` on the above handlers
  - remaining Phase 2 route slice:
    - strict tenancy policy finalization:
      - decide default strictness for resolver namespace gate (`RESOLVER_NAMESPACE_GATE_MODE`)
      - define/ship policy mapping beyond `allow_all` in `services/authz.py`
      - add explicit multi-tenant ledger identity model for stats/global/admin surfaces
      - promote `LEDGER_CONTEXT_MODE` from compat -> enforce with staged rollout guardrails
    - Status update (2026-02-23):
      - `services/authz.py` now supports registry-backed policy mode (`LEDGER_AUTHZ_MODE=registry|tenant_owner|enforce|policy`) with:
        - tenant/owner/admin principal checks by action (`ledger.read|ledger.write|sync.*`)
        - admin-path principal-type gating
        - unknown-ledger policy control (`LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=allow|deny`)
      - default compatibility remains `LEDGER_AUTHZ_MODE=allow_all`.
      - test coverage added:
        - `backend/tests/test_authz_policy.py`
      - staged rollout guardrails documented:
        - env template defaults + staged toggles in `backend/utils/ref/fly-backend.env.example`
        - operational toggle/check/rollback flow in `backend/utils/ref/migration-runbook-commands.md`
    - Status update (2026-02-23):
      - telemetry contract hardening for `POST /stats/telemetry`:
        - explicit ledger context is now only accepted from payload namespace/entity or `x-ledger-id` headers.
        - session-derived namespace fallback is treated as inferred context (not explicit) for `LEDGER_CONTEXT_MODE=enforce`.
        - payload/header ledger mismatch now returns deterministic `400 ledger_scope_mismatch`.
      - test coverage added:
        - `backend/tests/test_tenancy_route_contracts.py`
    - Status update (2026-02-23):
      - provisioning baseline for new ledgers implemented in `backend/api/admin.py`:
        - new v1 registry store key: `__ledgers_v1__`
        - schema fields persisted per ledger:
          - `ledger_id`
          - `tenant_id`
          - `owner_principal_id`
          - `owner_principal_type`
          - `policy_profile`
          - `status`
          - timestamps + metadata
        - `POST /admin/ledgers` is idempotent (`created=true|false`) and dual-writes compatibility list registry.
        - `GET /admin/ledgers` now returns both:
          - `ledgers` (legacy list)
          - `ledger_records` (v1 structured records)
      - test coverage added:
        - `backend/tests/test_admin_ledger_provisioning.py`

0.1 Domain-model hardening slice (new, 2026-02-25).
- Done:
  - model review completed and formalized in this rollout doc (`ledger` vs `contributor` vs `context` vs `entry`).
  - strict unknown-ledger deny behavior is already active and validated in staging lanes.
  - idempotent baseline provisioning via `/admin/ledgers` is in place for seeded ledgers.
- Status update (2026-02-26):
  - PR-1 complete: shared `ledger_scope` resolver and deterministic `ledger_scope_mismatch` guard live across chat/enrich/ingest/feedback.
  - PR-2 complete: write namespace authority unified to canonical ledger scope by default.
  - PR-3 complete: canonical provenance persisted on write (`ledger_id`, contributor tuple, context/provider/model/session/turn fields).
  - PR-4 complete: context contract introduced (`context_id`, `x-context-id`) with enforce-mode gate (`LEDGER_CONTEXT_ID_MODE=enforce`).
  - PR-5 complete: per-ledger context binding enforcement added (`reason=context_not_allowed`) with policy mode toggle.
  - PR-6 complete: coordinate decode/feedback paths now enforce coordinate namespace vs canonical ledger scope consistency.
  - PR-7 complete: staged compatibility toggles shipped:
    - `LEDGER_SCOPE_STRICT`
    - `LEDGER_NAMESPACE_SOURCE=ledger_id|entity_compat`
    - `LEDGER_CONTEXT_ID_MODE=compat|enforce`
    - `LEDGER_CONTEXT_BINDING_MODE=compat|enforce|off`
  - PR-8 complete: tests and operator runbooks updated with strict defaults + rollback knobs.
  - PR-9 complete (temporary demo override):
    - added backend-wide demo flag `DEMO_GOD_MODE=true` for open access during demo operations.
    - implementation:
      - `backend/services/demo_mode.py`
      - `backend/services/authz.py` (allow all actions when enabled)
      - `backend/services/ledger_scope.py` (relaxed mismatch + default ledger fallback via `DEMO_GOD_DEFAULT_LEDGER`)
      - `backend/services/context_scope.py` (relaxed context mismatch)
    - default remains secure (`DEMO_GOD_MODE=false`).
  - PR-10 complete (2026-03-02): coord-walk persistence/storage authority fix.
    - `/chat/walk/write` now enforces canonical ledger scope for persisted namespace and write authz.
  - PR-11 complete (2026-03-02): middleware namespace emission containment.
    - walk writes + telemetry emission now use canonical ledger namespace (not session-hash entity namespace).
- Additions to do:
  - execute hard reset in target demo environment and re-provision only canonical demo ledgers/contexts/contributors.
  - use scripted flow:
    - `backend/utils/ref/scripts/hard_reset_demo_foundation.sh`
    - `backend/utils/ref/scripts/post_reset_acceptance.sh`
  - set/verify strict runtime defaults after re-provision:
    - `LEDGER_AUTHZ_MODE=registry`
    - `LEDGER_CONTEXT_MODE=enforce`
    - `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`
    - `LEDGER_SCOPE_STRICT=true`
    - `LEDGER_CONTEXT_ID_MODE=enforce`
    - `LEDGER_CONTEXT_BINDING_MODE=enforce`
    - `LEDGER_NAMESPACE_SOURCE=ledger_id`
  - finalize middleware/frontend propagation for `x-context-id` on every write path.
  - finalize frontend/middleware attachment+ticker stabilization in production:
    - prevent stale cross-scope attachment coordinate reuse from client cache.
    - ensure inline ticker is visible by default unless explicitly disabled.
  - prepare and run one migration fallback drill using PR-7 compatibility values before production cutover.
  - add and execute RBAC schema rollout (next section), then remove temporary `DEMO_GOD_MODE` for non-demo environments.
  - required coverage:
    - write-path tests asserting `persisted_namespace == ledger_id`.
    - mismatch rejection tests for chat/ingest/enrich/commit.
    - context binding allow/deny tests.
    - feedback path tests for known-vs-unknown ledger under strict policy.

0.3 RBAC schema rollout (next implementation block, 2026-02-26).

Goal:
- replace temporary demo-wide override (`DEMO_GOD_MODE`) with explicit role-based permissions across ledgers, contributors, contexts, and actions.

Target RBAC model:
- principal:
  - `{principal_id, principal_type}` (`user|service|model|admin`)
- role:
  - `owner`, `admin`, `writer`, `reader`, `auditor`, `context_admin`
- scope bindings:
  - ledger scope: per-ledger role grants
  - context scope: per-ledger allowlist + optional per-role context constraints
- action matrix (minimum):
  - `ledger.read`: owner/admin/reader/writer/auditor
  - `ledger.write`: owner/admin/writer
  - `ledger.feedback`: owner/admin/writer/reader (policy-tunable)
  - `ledger.pin`: owner/admin/writer
  - `sync.pull`: owner/admin/reader/writer/auditor
  - `sync.push`: owner/admin/writer
  - `admin.*`: admin only

Schema additions (registry v1 extension):
- `roles`: map of `principal -> [roles]` at ledger level
- `role_bindings`: optional explicit grants with metadata:
  - `principal_id`, `principal_type`, `roles`, `allowed_context_ids`, `status`, timestamps
- preserve compatibility with existing:
  - `tenant_id`, `owner_principal_id`, `owner_principal_type`, `policy_profile`, `metadata.allowed_context_ids`

Rollout sequence:
1. Add data model + admin APIs:
   - `POST /admin/ledgers/{ledger_id}/roles/grant`
   - `POST /admin/ledgers/{ledger_id}/roles/revoke`
   - `GET /admin/ledgers/{ledger_id}/roles`
2. Update `services/authz.py` policy evaluator to use role grants first, owner/tenant compatibility second.
3. Add context binding overlay for roles (`context_admin` + per-role `allowed_context_ids`).
4. Add migration script:
   - map current owner to `owner`
   - map admin principal types to `admin`
   - preserve existing write/read behavior for current demo principals.
5. Add tests:
   - positive/negative matrix for each action and role
   - context-bound role checks
   - unknown-ledger + unknown-role deterministic denial reasons
6. Sunset temporary override:
   - disable `DEMO_GOD_MODE`
   - keep override only for explicitly named demo environments with expiry date.

Temporary demo override governance:
- `DEMO_GOD_MODE` is now a short-term operational switch.
- must include:
  - explicit owner approval to enable
  - environment-scoped secret only (never default in code/env template)
  - sunset ticket + target disable date
  - post-demo verification that strict authz paths are re-enabled.

0.2 Implementation sequence (PR-by-PR) + hard reset execution (new, 2026-02-25).

PR sequence (backend-first, low risk):

1. PR-1: Canonical scope resolver + mismatch guard utility.
- Add shared guard used by chat/ingest/enrich/commit/feedback:
  - resolve canonical `ledger_id` from payload/header/path.
  - reject deterministic mismatch with `400 ledger_scope_mismatch`.
- Acceptance:
  - no handler performs ad-hoc scope resolution.
  - mismatch payload shape matches stats contract.

2. PR-2: Namespace authority unification for writes.
- Update write paths so persisted namespace is always canonical `ledger_id`:
  - `/chat`
  - `/chat/stream`
  - `/api/chat/commit-answer`
  - `/ingest`, `/ingest/file`, `/ingest/stream`, `/ingest/stream-file`
  - `/enrich`, `/enrich/guardian`
- Acceptance:
  - `persisted_namespace == ledger_id` for all writes.
  - no write path derives namespace from `entity` for storage key.

3. PR-3: Provenance normalization on entries.
- Persist canonical provenance fields on each write:
  - `ledger_id`
  - `contributor_id` (principal tuple)
  - `context_id` (when present)
  - `provider_id`, `model_id`, `session_id`, `turn_id` where available
- Acceptance:
  - provenance appears in entry metadata for all write classes (chat/ingest/enrich).

4. PR-4: Context identity contract.
- Introduce `context_id` + `x-context-id` support across write/read/feedback routes.
- Default mode:
  - compat: optional context capture
  - strict: context required for write paths
- Acceptance:
  - deterministic `422` when strict mode requires context and it is missing.

5. PR-5: Ledger-context binding enforcement.
- Add per-ledger allowlist/binding for contexts in registry metadata.
- Enforce on writes after authz principal checks.
- Acceptance:
  - unauthorized context -> deterministic `403` (`reason=context_not_allowed`).
  - authorized context -> write success.

6. PR-6: Feedback/resolve path consistency pass.
- Ensure coordinate-addressed paths enforce:
  - coordinate namespace == canonical `ledger_id` scope
- Acceptance:
  - feedback/read/decode reject scope mismatch deterministically.

7. PR-7: Compatibility shims + migration toggles.
- Feature flags:
  - `LEDGER_SCOPE_STRICT`
  - `LEDGER_CONTEXT_ID_MODE=compat|enforce`
  - `LEDGER_NAMESPACE_SOURCE=ledger_id|entity_compat`
- Acceptance:
  - staged rollout possible without breaking legacy clients during migration window.

8. PR-8: Test + runbook closeout.
- Add/refresh tests:
  - namespace-authority invariants
  - mismatch rejection
  - context allow/deny
  - known-vs-unknown feedback behavior in strict policy
- Update runbooks with final strict defaults and rollback.

Hard reset execution needed for demo foundation:

1. Freeze and export current state (must-do before destructive reset).
- capture:
  - `GET /admin/history/audit`
  - `GET /admin/ledgers`
  - `GET /admin/tenants`
  - deployed SHAs + env snapshot

2. Execute hard reset of backend ledger state.
- use existing destructive target:
  - `make fly-hard-reset CONFIRM=1 ...`
- objective:
  - replace/reset volume-backed ledger state to clean baseline.

3. Re-provision baseline demo state immediately after reset.
- create tenant(s) and ledger(s) explicitly:
  - `POST /admin/tenants` (preferred)
  - `POST /admin/ledgers` (operator primitive/fallback)
- seed target demo ledger(s) and bindings:
  - contributors (user/model/service identities)
  - contexts (`ctx:frontend:vercel`, `ctx:frontend:local`, `ctx:decoder`, `ctx:openclaw`, `ctx:chatgpt`)

4. Enable strict policy after provisioning.
- set/verify:
  - `LEDGER_AUTHZ_MODE=registry`
  - `LEDGER_CONTEXT_MODE=enforce`
  - `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`
  - `LEDGER_SCOPE_STRICT=true` (new flag from PR-7)
  - `LEDGER_CONTEXT_ID_MODE=enforce` (new flag from PR-7)

5. Run hard-reset acceptance checks.
- known ledger write/read/feedback: success.
- unknown ledger write/feedback: `403 unknown_ledger`.
- scope mismatch: `400 ledger_scope_mismatch`.
- disallowed context write: `403 context_not_allowed`.
- no legacy test namespaces visible in inventory/history.

Canonical execution scripts (added 2026-02-26):

- hard reset + archive + re-provision + strict flags:
  - `backend/utils/ref/scripts/hard_reset_demo_foundation.sh`
- post-reset acceptance probe:
  - `backend/utils/ref/scripts/post_reset_acceptance.sh`

1. Middleware -> backend telemetry parity for EQ9/meta-patch rollups.
- Scope: `ds-middleware-local/routes/orchestrator.py` telemetry emit payload.
- Needed fields: `eq9_eval_source`, `meta_patch_status`, `meta_patch_reason`.
- Why: backend counters/rates are implemented; this wiring fills them in production traffic.
- Status update (2026-02-24): complete.
  - middleware emit path now sends:
    - `eq9_eval_source`
    - `meta_patch_status`
    - `meta_patch_reason`
  - emit now occurs after post-introspect patch resolution so fields reflect final turn state.
  - validation:
    - `pytest -q tests/test_orchestrator_eq9_source.py tests/test_orchestrator_stream_contract.py tests/test_orchestrator_latency_smoke.py` -> `13 passed`
  - tests added/updated:
    - `ds-middleware-local/tests/test_orchestrator_eq9_source.py`
    - `ds-middleware-local/tests/test_orchestrator_latency_smoke.py`

2. Staging migration gate dry run to green.
- Scope: env contracts + connector/OAuth + sync health checks.
- Status update (2026-02-24): green.
- Completed in latest pass:
  - dry-run report:
    - `backend/utils/ref/staging-gate-dryrun-2026-02-24.md`
  - preflight artifact checks confirmed present:
    - `backend/utils/ref/fly-backend.env.example`
    - `backend/utils/ref/vercel-middleware.env.example`
    - `backend/utils/ref/vercel-frontend.env.example`
    - `backend/utils/ref/deployment-endpoint-contract-checklist.md`
  - local live probes now executing:
    - backend `GET /health` -> `200`
    - backend `POST /sync/v0/handshake` -> `200`
    - backend `GET /sync/v0/status` -> `200`
    - middleware `GET /health` -> `200`
  - auth/context contract probes confirmed:
    - `/stats/telemetry` payload/header mismatch -> `400 ledger_scope_mismatch`
    - `/stats/telemetry` header fallback -> `200`
    - `/api/ingest` missing explicit context -> `422 ledger_context_required`
    - `/api/ingest` header fallback -> `200`
  - strict-mode auth probes completed locally:
    - `LEDGER_AUTHZ_MODE=registry`
    - `LEDGER_CONTEXT_MODE=enforce`
    - `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`
    - `RESOLVER_NAMESPACE_GATE_MODE=strict`
    - results:
      - known registry ledger owner write -> `200`
      - unknown ledger write -> `403` (`reason=unknown_ledger`)
      - non-owner write (`principal_type=user`) -> `403` (`reason=write_requires_owner_or_tenant`)
      - inferred context (`/stats/telemetry` session-only) -> `422 ledger_context_required`
  - strict middleware write-path probes completed against strict backend:
    - middleware `/api/ingest/file` with default ledger `gate-alpha` -> `200`
    - middleware `/api/ingest/file` with default ledger `gate-unknown` -> `502` (backend propagated `403 Forbidden`)
    - backend strict logs confirm known ledger write `200` and unknown ledger write `403` on `/api/ingest/file`
  - CORS probes validated locally with representative origin set:
    - explicit allowlist + regex config validated:
      - `MIDDLEWARE_CORS_ORIGINS=https://ds-middleware-local.vercel.app,https://ds-frontend-local.vercel.app,https://ds-frontend-local-new.vercel.app`
      - `BACKEND_CORS_ORIGIN_REGEX=https://(ds-frontend-local|ds-frontend-local-new).*\\.vercel\\.app`
    - allowed Vercel origins -> `200` preflight + explicit `access-control-allow-origin`
    - preview domain (`https://ds-frontend-local-git-main-preview.vercel.app`) -> `200` via regex allow
    - disallowed origin -> `400` preflight without permissive allow-origin
  - middleware ledger-switch semantics probed in legacy runtime:
    - root cause isolated to non-explicit route method registration for `GET` handler on `/api/ledgers`
    - local fix applied in `ds-middleware-local/app.py`:
      - `app.route("/api/ledgers", methods=["GET"])(list_ledgers)`
      - `app.route("/api/ledgers", methods=["POST"])(create_or_switch_ledger)`
    - local re-test after fix:
      - `POST /api/ledgers {"ledger_id":"gate-alpha"}` -> `{"ledger_id":"gate-alpha"}`
      - follow-up `GET /api/ledgers` shows `active_ledger=gate-alpha`
      - `POST /api/ledgers {"ledger_id":"gate-unknown"}` -> `{"ledger_id":"gate-unknown"}`
      - follow-up `GET /api/ledgers` shows `active_ledger=gate-unknown`
  - deployed Fly CORS matrix snapshot (`https://ds-backend-new.fly.dev`) captured:
    - backend CORS secrets updated and verified live:
      - allowed (`200` preflight + explicit allow-origin): `https://ds-frontend-local.vercel.app`, `https://ds-frontend-local-new.vercel.app`, `https://ds-middleware-local.vercel.app`, `https://ds-frontend-local-git-main-preview.vercel.app`
      - denied (`400` preflight): `https://evil.example`
    - status: deployed CORS policy now aligned with intended staging matrix
  - middleware ledger-switch fix deployed to Fly (`ds-middleware-new`) and validated:
    - `POST /api/ledgers` now returns `{"ledger_id":"<target>"}` and follow-up `GET /api/ledgers` reflects updated `active_ledger`
  - strict staging auth still not effective after enabling strict backend secrets:
    - remediation applied:
      - set `DB_PATH=/app/data` on `ds-backend-new` to ensure registry/auth state persists on mounted volume
      - bootstrap provisioning flow in staging:
        1. temporary `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=allow`
        2. `POST /admin/ledgers` provision `gate-alpha`
        3. restore `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`
    - post-remediation staging probes:
      - direct backend known ledger write (`gate-alpha`) -> `200`
      - direct backend unknown ledger write (`gate-dbfix-deny-*`) -> `403` (`reason=unknown_ledger`)
      - middleware `/api/ingest/file` after switch to `gate-alpha` -> `200`
      - middleware `/api/ingest/file` after switch to unknown ledger -> `502` (backend `403` propagated)
    - status: strict staging auth lane now green
  - integrated frontend-origin lane evidence captured on deployed targets:
    - `GET https://ds-frontend-local-new.vercel.app` -> `200`
    - frontend-origin preflight:
      - backend `OPTIONS /sync/v0/status` -> `200`
      - middleware `OPTIONS /api/ingest/file` required CORS secret update, then -> `200`
    - frontend-origin write path checks:
      - known ledger (`gate-alpha`) via middleware `/api/ingest/file` -> `200`
      - unknown ledger via middleware `/api/ingest/file` -> `502` (backend `403` propagated)
    - frontend-origin chat lane smoke:
      - middleware `POST /api/chat` -> `200` with assistant reply payload
  - admin provisioning reliability fix landed:
    - logging context key collision (`created`) patched in `backend/api/logging_utils.py`
    - regression coverage added in `backend/tests/test_logging_utils.py`
- Current blockers:
  - none for staging migration gate lanes (auth/context/CORS/ledger-switch/frontend-origin smoke are green).
- Remaining blockers:
  - move to migration cutover prerequisites (checkpoint loop + dual-write/diff + projection worker readiness).
  - keep strict provisioning bootstrap as explicit operator step (`unknown=allow -> provision -> unknown=deny`).

### Next Best Actions (Ordered)

1. Automate strict gate smoke in CI/CD (deployed env checks).
- Scope:
  - CORS matrix (`allowed exact`, `allowed preview regex`, `denied origin`)
  - direct backend unknown-ledger deny check (`403`)
  - middleware deny propagation check (`502` wrapping backend `403`)
- Exit criteria:
  - single script target in runbook and CI job so regression is caught automatically before rollout changes.

2. Harden strict provisioning bootstrap into an operator-safe routine.
- Scope:
  - keep `DB_PATH=/app/data` invariant enforced in deployment config
  - formalize bootstrap (`unknown=allow -> provision -> unknown=deny`) as explicit pre-flight step
  - optionally introduce dedicated provisioning/admin allowance path to remove temporary policy toggling
- Exit criteria:
  - no manual ambiguity in strict provisioning flow; repeatable in staging/prod.

3. Start migration cutover prerequisites immediately after gate green.
- Scope:
  - checkpoint save/load operational validation
  - projection worker + checkpoint loop operational checks
  - dual-write + diff checks for representative traffic
- Exit criteria:
  - cutover readiness section has concrete pass/fail artifacts and rollback switch is prepared.

3. Migration cutover prerequisites after gate is green.
- Scope:
  - stand up projection worker + checkpoint operational loop
  - enable dual-write + diff checks
  - execute cohort cutover with rollback switch

4. E6 hardening phases not yet fully closed.
- E6-2 (single-source `BridgeAllowed`) and E6-3 (base-4 mode/packet/route gating) need explicit completion sign-off.
- E6-4/E6-5/E6-6 are partially complete and require final strictness/rollout closure.

5. Integration certifications (OpenClaw/n8n/MCP/WhatsApp).
- Status: on hold by plan until migration stabilizes on Vercel/Fly.
- Resume condition: staging gate green + migration cutover baseline stable.

### Migration-Only Execution Checklist (Fly + Vercel)

Use this checklist to complete migration before resuming integrations.
Command sheet:
- `backend/utils/ref/migration-runbook-commands.md`

1. Preflight freeze (no new integration scope)
- Confirm integration tracks remain paused in this plan.
- Freeze API contract changes to:
  - `/sync/v0/handshake`
  - `/sync/v0/push`
  - `/sync/v0/pull`
  - `/sync/v0/backfill`
  - `/sync/v0/status`
- Confirm required env templates are present:
  - `backend/utils/ref/fly-backend.env.example`
  - `backend/utils/ref/vercel-middleware.env.example`
  - `backend/utils/ref/vercel-frontend.env.example`

2. Backend deploy (Fly)
- Deploy backend as ledger/sync authority.
- Verify health + version:
  - `curl -sS https://<fly-backend>/health`
  - must include `git_sha`.
- Verify sync contract endpoints respond:
  - `curl -sS -X POST https://<fly-backend>/sync/v0/handshake`
  - `curl -sS https://<fly-backend>/sync/v0/status`

3. Middleware deploy (Vercel)
- Configure middleware to target Fly backend base URL.
- Ensure signing/auth secrets are set in Vercel middleware env only.
- Verify middleware live checks:
  - health endpoint
  - one `/api/chat/smart_stream` smoke call
  - telemetry post to `/stats/telemetry`

4. Frontend deploy (Vercel)
- Point frontend to Vercel middleware URL.
- Validate:
  - regular chat turn
  - sync-origin event visibility in `All` timeline
  - pipeline diagnostics toggles (off by default)

5. Security gate
- Validate middleware->backend auth enforcement in staging.
- Validate strict CORS allowlist for staging/prod Vercel origins on Fly backend.
- Confirm no permissive wildcard CORS in deployed env.

6. Migration data-plane gate
- Validate checkpoint persistence:
  - save/load checkpoint routes functional
  - `/sync/v0/status` reports checkpoints.
- Run dual-write + diff checks for representative traffic.
- Confirm projection worker/checkpoint loop is stable.

7. Cutover readiness criteria (must all pass)
- Staging gate report green.
- p95 latency within target envelope for baseline chat + sync pull/push.
- No unresolved auth/CORS findings.
- No critical rollup inconsistencies in stats.

8. Controlled cutover
- Enable cohort-based traffic shift with rollback switch armed.
- Observe:
  - error rate
  - latency
  - sync backlog/checkpoint drift
  - quarantine and search-invariant alerts.

9. Rollback playbook (immediate)
- Revert frontend/middleware backend targets to previous stable stack.
- Disable cohort routing to new stack.
- Preserve checkpoints and event logs for replay/diff analysis.
- Open incident note with:
  - trigger timestamp
  - scope
  - top failing checks
  - recovery ETA.

### Contract + Latency Test Matrix (Next Step Detail)

Owner split:
- Backend contracts: `ds-backend-local`
- Middleware behavior + streaming contracts: `ds-middleware-local`
- Frontend consumer/parsing contracts: `ds-frontend-local`

1. Middleware stream contract tests (`meta` + `meta_patch`)
- Target file: `ds-middleware-local/tests/test_orchestrator_stream_contract.py` (new)
- Assertions:
  - `meta` always includes: `eq9_eval`, `eq9_eval_pre_commit`, `eq9_eval_post_commit`, `eq9_eval_source`, `eq9_eval_pending`.
  - `meta_patch` appears only when post-introspect path is pending.
  - `meta_patch.status` is one of: `applied|skipped`.
  - `meta_patch.reason` is set when `status=skipped`.
  - `introspect_snapshot_post` is omitted unless `include_post_introspect_snapshot=true` (or telemetry debug mode).
- Acceptance:
  - all contract assertions green across both `include_post_introspect_snapshot=false` and `true`.
  - Status: complete in current branch.
    - Local verification:
      - `3 passed` (`ds-middleware-local/tests/test_orchestrator_stream_contract.py`)

2. EQ9 source transition tests (cache + metadata + live introspect)
- Target file: `ds-middleware-local/tests/test_orchestrator_eq9_source.py` (new)
- Assertions:
  - source path emits one of:
    - `post_commit_metadata`
    - `post_commit_cache`
    - `post_commit_introspect`
    - `pending_post_commit_introspect` (pre-patch state only)
  - cache hit path sets `post_introspect_cache_hit=true`.
  - live introspect path clears pending in `meta_patch` on `applied`.
- Acceptance:
  - deterministic source transitions under seeded fixtures.
  - Status: complete in current branch.
    - Local verification:
      - `7 passed` (`ds-middleware-local/tests/test_orchestrator_eq9_source.py`)

3. Frontend diagnostic gating + downsampling tests
- Target file: `ds-frontend-local/tests/test_orchestrator_consumer_pipeline.py` (new)
- Assertions:
  - `pipeline_events` absent when `include_pipeline_events=false`.
  - `pipeline_events` present when `include_pipeline_events=true`.
  - `walk_metric_delta` count respects `PIPELINE_WALK_METRIC_STRIDE`.
  - retained events are bounded by `MAX_PIPELINE_EVENTS`.
- Acceptance:
  - payload size and event count remain bounded under synthetic high-volume streams.
  - Status: complete in current branch.
    - Added:
      - `ds-frontend-local/tests/test_orchestrator_consumer_pipeline.py`
      - `ds-frontend-local/tests/test_pipeline_event_overhead.py`
    - Local verification:
      - `4 passed` (`test_orchestrator_consumer_pipeline.py`, `test_pipeline_event_overhead.py`)

4. Anchor timezone window tests ("yesterday")
- Target file: `ds-middleware-local/tests/test_anchor_time_window.py` (new)
- Assertions:
  - day window uses request/session timezone hints (`utc_offset`, `timezone_offset_minutes`).
  - fallback behavior uses local server tz when hints missing.
  - absolute window in payload is consistent with selected reference time.
- Acceptance:
  - pass for at least: `UTC`, `-08:00`, `+11:00`, boundary around local midnight.
  - Status: complete in current branch.
    - Local verification:
      - `7 passed` (`ds-middleware-local/tests/test_anchor_time_window.py`)

5. Grounding fallback target tests (dynamic output target)
- Target file: `backend/tests/test_chat_grounding_guard.py` (extend)
- Assertions:
  - fallback line uses `eq9_target.output_tokens_soft` when present.
  - fallback uses default only when target missing/invalid.
- Acceptance:
  - no hardcoded-target regression in fallback text.
  - Status: complete in current branch.
    - Extended with explicit cases for:
      - dynamic target from `eq9_target.output_tokens_soft`
      - default target fallback when missing
      - default target fallback when invalid type
    - Local verification:
      - `6 passed` (`backend/tests/test_chat_grounding_guard.py`)

6. Latency guardrail checks (CI smoke thresholds)
- Target files:
  - `ds-middleware-local/tests/test_orchestrator_latency_smoke.py` (new)
  - `ds-frontend-local/tests/test_pipeline_event_overhead.py` (new)
- Assertions:
  - diagnostics-off p95 does not exceed baseline budget (team-defined).
  - diagnostics-on remains within bounded overhead budget with downsampling active.
  - post-introspect cache reduces repeat-turn latency vs cold path.
- Acceptance:
  - budgets tracked in CI output and compared against baseline artifact.
  - Status: complete in current branch.
    - Frontend: complete in current branch.
      - `ds-frontend-local/tests/test_pipeline_event_overhead.py` now includes diagnostics-off vs diagnostics-on budget assertion.
      - Local verification: `4 passed` with `test_orchestrator_consumer_pipeline.py` + `test_pipeline_event_overhead.py`.
    - Middleware: complete in current branch.
      - `ds-middleware-local/tests/test_orchestrator_latency_smoke.py`
      - Local verification:
        - `2 passed` (`test_orchestrator_latency_smoke.py`)
    - CI wiring + baseline artifacts added:
      - middleware workflow:
        - `ds-middleware-local/.github/workflows/rollout-contract-latency.yml`
        - runs:
          - `tests/test_orchestrator_stream_contract.py`
          - `tests/test_orchestrator_eq9_source.py`
          - `tests/test_orchestrator_latency_smoke.py`
          - `tests/test_anchor_time_window.py`
        - publishes artifacts:
          - `artifacts/middleware-rollout-junit.xml`
          - `artifacts/middleware-latency-baseline.json`
      - frontend workflow:
        - `ds-frontend-local/.github/workflows/rollout-contract-latency.yml`
        - runs:
          - `tests/test_orchestrator_consumer_pipeline.py`
          - `tests/test_pipeline_event_overhead.py`
        - publishes artifacts:
          - `artifacts/frontend-rollout-junit.xml`
          - `artifacts/frontend-latency-baseline.json`

Suggested execution order:
1. Implement middleware contract tests (items 1, 2, 4).
2. Implement frontend gating/downsampling tests (item 3).
3. Extend backend grounding fallback tests (item 5).
4. Add latency smoke checks + baseline artifact publication (item 6).

Suggested first command set (per repo):
1. `ds-middleware-local`: `pytest -q tests/test_orchestrator_stream_contract.py tests/test_orchestrator_eq9_source.py tests/test_anchor_time_window.py`
2. `ds-frontend-local`: `pytest -q tests/test_orchestrator_consumer_pipeline.py tests/test_pipeline_event_overhead.py`
3. `ds-backend-local`: `pytest -q backend/tests/test_chat_grounding_guard.py`

## Execution Checklist (Owners + Acceptance)

### A) Decoupling Completion (Backend)

Owner: `ds-backend-local`

1. Enforce explicit ledger context beyond sync routes.
- Scope: write-capable routes in `backend/api/*` and service calls.
- Acceptance:
  - no write path implicitly defaults to wrong ledger when `ledger_id` is omitted
  - persisted namespace for every write equals canonical `ledger_id`
  - any scope mismatch returns deterministic `400 ledger_scope_mismatch`
  - integration tests cover default-ledger compatibility and explicit-ledger writes

2. Add authz hook skeleton `(principal, ledger_id, action)`.
- Scope: request boundary and `LedgerService` call sites.
- Acceptance:
  - all write paths pass through a single guard function
  - deny path returns deterministic 403 payload shape

3. Add stream-head bootstrap/repair behavior.
- Scope: sync ingest path and stream state transitions.
- Acceptance:
  - first-write bootstrap on fresh streams is deterministic
  - divergence/quarantine emits actionable reason codes and repair guidance

4. Lock API contract notes for v0->v1 compatibility.
- Scope: sync + ledger contracts.
- Acceptance:
  - compatibility doc includes required/optional fields and migration behavior
  - no breaking change introduced without compatibility gate
  - Status: complete in current branch.
  - Added:
    - `backend/utils/ref/compat-policy-v0-v1.md`

### B) Middleware Alignment (Control Plane)

Owner: `ds-middleware-local`

1. Propagate ledger context explicitly on all backend writes.
- Scope: `/api/chat`, `/api/orchestrator`, MCP write tools, daemon sync calls.
- Acceptance:
  - middleware always sends explicit ledger context headers/fields
  - no backend write call relies on backend-side default ledger inference

2. Keep walk diagnostics and EQ6 signals visible end-to-end.
- Scope: orchestrator stream meta + persisted walk payloads.
- Acceptance:
  - `flow_diagnostic` appears in walk metadata when present
  - EQ6 fields (`lawfulness_level`, `cw`, commit flags) remain in debug/meta payloads

3. Stabilize MCP operational path.
- Scope: OAuth persistence, dynamic ngrok URL handling, append/pipeline fallback behavior.
- Acceptance:
  - connector survives restart without client-registration loss
  - `ds.append_event` returns deterministic status (`committed|quarantine|duplicate|queued`)

### C) Frontend Compatibility (UX Surface)

Owner: `ds-frontend-local`

1. Verify `All` timeline parity for sync-origin writes.
- Scope: merged history from ledger + sync pull.
- Acceptance:
  - MCP-origin sync events appear in `All` for matching session/entity
  - no duplicate rendering for same event id/coord

2. Expose sync state hints in UI (minimal).
- Scope: status indicators only.
- Acceptance:
  - user can see pending/accepted/quarantine state without opening logs

### D) Migration Readiness Gates (Cloud Cutover)

Owners: `ds-backend-local`, `ds-middleware-local`, `ds-frontend-local`

1. Contract gate.
- Acceptance:
  - deployment contract checklist passes for all public routes and env vars

2. Reliability gate.
- Acceptance:
  - restart test preserves sync correctness and MCP connectivity
  - replay/duplicate/quarantine tests pass in staging

3. Security gate.
- Acceptance:
  - signing keys remain middleware-only
  - backend authz hook active for write paths in staging

4. Performance gate.
- Acceptance:
  - p95 chat and append latency captured and within target under representative load

## Next 10 Commits (Linear, Low-Risk)

1. Backend: add central authz hook interface + no-op implementation.
- Files: `backend/services/*`, `backend/api/*` wiring only.
- Output: all write routes call one guard point, behavior unchanged (allow-all).
  - Status: complete in current branch.
  - Added `backend/services/authz.py` (`authorize_or_raise`, principal extraction, allow-all mode via `LEDGER_AUTHZ_MODE`).
  - Wired into write paths:
    - `backend/api/sync.py` (`/sync/push`, `/sync/v0/push`, `/sync/v0/pull`, `/sync/v0/backfill`, checkpoint save/load)
    - `backend/api/http.py` (`/ledger/write`, `/ledger/pin`, `/ledger/unpin`, `/debug/ledger/write`)

2. Backend: enforce explicit ledger context on write routes (compat mode).
- Files: `backend/api/*` write handlers + request parsing.
- Output: explicit `ledger_id` preferred; default still allowed behind compatibility flag.
  - Status: complete in current branch.
  - Added explicit ledger-context guard mode in `backend/services/authz.py`:
    - `LEDGER_CONTEXT_MODE=compat|enforce|off` (default `compat`)
  - Wired explicit-context flags into guarded write paths in:
    - `backend/api/sync.py` (v0 push/pull/backfill/checkpoint routes)
    - `backend/api/http.py` (ledger write/pin/unpin/debug write)
  - Added compatibility tests:
    - `backend/tests/test_authz_context.py` (5 passing cases)

3. Backend: stream-head bootstrap rule for fresh stream writes.
- Files: sync ingest/service path.
- Output: deterministic acceptance for first valid event without manual seq repair.
  - Status: complete in current branch.
  - `sync /v0/push` now recovers missing `latest` stream head from highest known `seq:*` entry before chain checks (`backend/api/sync.py`).
  - This prevents false `missing_predecessor` quarantine when stream data exists but `latest` pointer is absent.
  - Added regression coverage:
    - `backend/tests/test_sync_v0.py::test_sync_v0_bootstraps_missing_latest_from_stream_head`

4. Backend: divergence/quarantine reason-code normalization.
- Files: sync response formatter + docs.
- Output: stable machine-readable reason codes (`missing_predecessor`, `divergence_seq_conflict`, etc.).
  - Status: complete in current branch.
  - `sync /v0/push` now uses centralized canonical reason codes and verifier-reason normalization:
    - known verifier reasons preserved (`bad_proof`, `bad_crc`, etc.)
    - unknown verifier reasons collapse to `verification_failed` with `reason_detail` retained in quarantine payload
  - Added tests:
    - `backend/tests/test_sync_v0.py::test_sync_v0_bad_proof_reason_is_canonical`
    - `backend/tests/test_sync_v0.py::test_sync_v0_unknown_verifier_reason_normalized`

5. Backend tests: tenancy + stream bootstrap + quarantine matrix.
- Files: `backend/tests/*`.
- Output: coverage for legacy default-ledger and explicit-ledger scenarios.
  - Status: complete in current branch.
  - Added/extended matrix coverage:
    - tenancy context enforcement (`backend/tests/test_authz_context.py`)
    - stream-head bootstrap recovery (`backend/tests/test_sync_v0.py::test_sync_v0_bootstraps_missing_latest_from_stream_head`)
    - quarantine reasons:
      - `missing_predecessor` (`test_sync_v0_missing_predecessor_quarantined`)
      - `chain_mismatch`, `divergence_seq_conflict`, `nonce_replay`, `ledger_scope_mismatch`
      - verifier normalization (`bad_proof`, `verification_failed`)
    - explicit-ledger contract:
      - `/sync/v0/push` requires `ledger_id_h64` (`test_sync_v0_push_requires_ledger_scope`)
    - backfill override path:
      - `allow_backfill` bypass for missing predecessor (`test_sync_v0_allow_backfill_accepts_missing_predecessor`)

6. Middleware: propagate explicit ledger context on every backend write.
- Files: `api/client.py`, orchestrator + MCP paths.
- Output: no write depends on backend default inference.
  - Status: complete in current branch set.
  - `ds-middleware-local/api/client.py` now injects explicit ledger headers on every request:
    - `x-ledger-id` (active ledger)
    - `x-ledger-id-h64` (stable SHA-256-derived 64-bit hex)
  - This covers orchestrator/API-client write paths that previously depended on backend default inference.
  - MCP sync paths continue to send explicit `ledger_id_h64` in payloads.

7. Middleware: finalize MCP status determinism and fallback controls.
- Files: `utils/mcp_server.py`.
- Output: consistent `committed|quarantine|duplicate|queued` semantics across failures/retries.
  - Status: complete in current branch set.
  - `ds-middleware-local/utils/mcp_server.py` now enforces deterministic status precedence:
    - `quarantine` if any quarantine signal is present
    - `committed` if accepted
    - `duplicate` if duplicate
    - `queued` for inconclusive backend responses
  - Inconclusive responses now optionally enqueue retry payloads (`queue_on_failure=true`) with reason `inconclusive_backend_result`.

8. Middleware tests: MCP + walk diagnostic propagation.
- Files: `tests/test_orchestrator_walk_alignment.py` + MCP smoke harness updates.
- Output: metadata parity assertions for `flow_diagnostic`, EQ6, and append outcomes.
  - Status: complete in current branch set.
  - Existing walk diagnostic parity test remains active:
    - `ds-middleware-local/tests/test_orchestrator_walk_alignment.py`
  - Added MCP append status unit coverage:
    - `ds-middleware-local/tests/test_mcp_status_mapping.py`
    - validates deterministic `committed|duplicate|quarantine|queued` outcomes from `ds.append_event`
  - Local run note:
    - runtime dependency `cryptography` installed; middleware rollout suites now execute (no skip blocker).

9. Frontend: `All` timeline parity polish + sync state badge.
- Files: frontend history aggregation/render path.
- Output: sync-origin events visible and deduped; minimal pending/quarantine hints shown.
  - Status: complete in current branch set.
  - `ds-frontend-local/routes/home.py`:
    - improved sync dedupe keying for `__all__` history merge:
      - prefer `event_id`, fallback `stream_key:seq`
    - sync-origin rows now carry `sync_state` metadata (defaults to `synced` when not provided)
  - `ds-frontend-local/components/chat.py`:
    - render badges for sync-origin and state hints:
      - `sync`
      - `queued|pending`
      - `quarantine`
  - Added targeted tests:
    - `ds-frontend-local/tests/test_history_sync_badges.py` (3 passing)

10. Staging migration gate pass (Vercel/Fly dry run).
- Scope: env contracts + connector/OAuth + sync health checks.
- Output: checklist green for contract, reliability, security, and baseline latency.
  - Status: in progress (latest dry run is partial, not green).
  - Dry-run report:
    - `backend/utils/ref/staging-gate-dryrun-2026-02-20.md`
  - Completed in this pass:
    - backend `/health` now includes version metadata (`git_sha`) to satisfy checklist contract
  - Remaining blockers from dry run:
    - local live probes were blocked (backend/middleware/frontend not running)
    - security gate needs explicit staging validation for middleware->backend auth enforcement
    - CORS deploy scoping should be validated/tightened for staging/prod domains

## Migration Dependencies (Order)

1. Freeze sync/envelope contracts (`v0`) and keep backward compatibility.
2. Complete service boundary extraction (`LedgerService` adapters).
3. Enforce tenant + `ledger_id` requirements.
4. Move middleware to `/sync/v0/*` signed flows.
5. Stand up projection worker and checkpointing.
6. Enable dual-write + diff checks.
7. Deploy Vercel/Fly topology.
8. Cut over cohorts with rollback switch.
9. Expand integration certifications in order: OpenClaw -> n8n -> MCP -> WhatsApp.
   - Plan note: intentionally deferred until steps 7-8 are complete and stable.

## COORD Crypto Extension Plan (Reuse-First)

Objective: extend trust to coordinate resolution without introducing a parallel cryptography stack.

Principles:
- Reuse existing E6 envelope authenticity/replay controls for provenance authority.
- Treat COORD string integrity as a signed binding concern, not a separate nonce protocol.
- Keep write-time nonce policy in sync/event layer; do not require nonce for ordinary read/decode.

Planned implementation (incremental):

1. Signed coordinate binding in existing metadata path.
- Add canonical coordinate binding fields to signed/MACed metadata:
  - `coord_canonical`
  - `coord_h64`
  - `coord_version`
- Reuse existing envelope verification and canonical reason handling in:
  - `backend/api/sync.py`
  - `backend/utils/spec/e6-envelope-v0.md`

2. Resolver enforcement using existing scope/authz services.
- Before decode/resolve, enforce:
  - canonical ledger scope consistency
  - context/authz checks
  - coordinate-binding verification when present
- Reuse existing primitives:
  - `backend/services/ledger_scope.py`
  - `backend/services/authz.py`
  - existing decode mismatch guards already added in `chat`/resolver paths.

3. Optional signed resolve token for external sharing (capability style).
- Add short-lived signed token that binds:
  - coord
  - ledger scope
  - context/audience
  - expiry (`exp`) + token id (`jti`)
- Verify token at resolve boundary; return deterministic deny reason on failure.
- Reuse current signing key management patterns and verifier reason normalization.

4. Model-facing trust hints (no new protocol).
- Ensure resolved refs carry trust metadata (verified/denied reason) so model pipelines can ignore unverified refs.
- Reuse existing metadata/event plumbing in orchestrator stream and commit metadata fields.
- Resolver/model implication with DID principals:
  - resolved COORD metadata should include principal trust context (for example signer DID/key-id or deterministic deny reason).
  - model pipelines should treat unresolved/unauthenticated principal context as non-authoritative retrieval evidence.

5. Dual coordinate binding (identity + semantic retrieval).
- Preserve two coordinate families with explicit roles:
  - canonical ledger identity: `WX/EV/ATT` (durable, append-oriented identifier path)
  - semantic retrieval coordinate: `W4-<int>` (prime-product retrieval signal)
- Add optional signed metadata fields on write when available:
  - `coord_w4`
  - `token_primes`
  - `coord_binding_version`
- Signature/provenance verification must cover canonical coord and semantic coord fields together when semantic fields are present.
- Keep semantic coordinate additive (not identity-authoritative): collisions in W4 are acceptable for retrieval but must not be used as primary identity keys.
- Historical migration/backfill should be metadata-only and idempotent:
  - compute `coord_w4`/`token_primes` for legacy entries where missing
  - mark with migration metadata (for example `coord_w4_backfilled=true`) without rewriting canonical entry identity.

Non-goals / exclusions for this phase:
- do not encode trust state via modulus rules (for example `% 336`) inside `coord_w4`.
- do not require canonical `WX` identity to be payload-hash-derived unless generation semantics are explicitly redesigned in a dedicated migration.
- do not replace existing authz/policy checks with coordinate arithmetic shortcuts.

Exit criteria:
- Coordinate tampering attempts fail deterministically at resolver boundary.
- No cross-ledger coordinate resolution succeeds without matching scope/authz.
- Existing envelope/sync contracts remain backward compatible (`v0`), with additive fields only.

## E6 Scoring Implementation Plan (from `backend/utils/ref/Scoring.md`)

Goal: implement a hard-first, non-compensatory E6 referee where contradictions cannot score as fully lawful/coherent.

Mandatory direction:
- E6 must be `always-write, selective-promotion`.
- E6 must never block raw event recording.
- E6 only controls promotion (`block|quarantine|local-commit|ledger-commit`).

### Phase E6-1: Spec-to-Code Field Parity (No Behavioral Change)

Tasks:

1. Add explicit E6 operands to turn metadata:
- Hard gates: `L_top`, `K_t`, `P_t`, `E_t`
- Soft metrics: `L_phys`, `L_t`, `H_t`, `A_corr`, `A_self`, `A_t`, `U_t`, `V_int`
- Window metrics: `V_int_mean_3`, `V_int_std_3`

2. Add env-backed thresholds and policy values:
- `E6_THETA_L`, `E6_THETA_H`, `E6_THETA_V`, `E6_THETA_SIGMA`, `E6_THETA_SELF`
- `E6_ALLOWED_DW` (default `0,1,-1`)

Touchpoints:
- `backend/api/agent_writes.py`
- `backend/fieldx_kernel/governance_engine.py`

Exit criteria:
- Every committed turn includes the E6 operands and thresholds used for its decision.

Status:
- complete in current branch.
- implemented in `backend/api/agent_writes.py` as `metadata.e6_scoring` with:
  - hard gates (`L_top`, `K_t`, `P_t`, `E_t`)
  - soft metrics (`L_phys`, `L_t`, `H_t`, `A_corr`, `A_self`, `A_t`, `U_t`, `V_int_t`)
  - 3-tick window (`V_int_mean_3`, `V_int_std_3`)
  - threshold snapshot + `allowed_dW`
  - bridge evaluation (`bridge_allowed_runtime`, `bridge_allowed_formula_eval`)

### Phase E6-2: Single-Source `BridgeAllowed(t)`

Tasks:

1. Implement strict `BridgeAllowed(t)` using hard-first product:
- `P_t=1`, `E_t=1`, `L_t>=θ_L`, `H_t>=θ_H`, `V_int_mean_3>=θ_V`, `V_int_std_3<=θ_σ`

2. Persist `bridge_allowed` and deterministic fail reasons:
- `bridge_allowed=false`
- `bridge_blockers=[...]`

Touchpoints:
- `backend/api/agent_writes.py`

Exit criteria:
- All route/packet decisions reference `bridge_allowed` rather than ad-hoc mixed checks.
- Raw event append remains unconditional.

### Phase E6-3: Base-4 Mode + Packet/Route Gating

Tasks:

1. Implement mode machine:
- `0 HALT`, `1 PROBE`, `2 STABILISE`, `3 EXPRESS`
- hard-fail immediate drop to `HALT`

2. Enforce packet policy:
- `HR` in all modes
- `PP` in `PROBE`
- `CA` in `STABILISE`
- `WU` in `EXPRESS` only

3. Enforce route policy:
- `block | quarantine | local-commit | ledger-commit` mapped to mode + bridge
- `route` semantics control promotion only, never raw write.

Touchpoints:
- `backend/api/agent_writes.py`
- `backend/fieldx_kernel/e6_packet.py` (header parity checks)

Exit criteria:
- Packet type and route always match mode policy in persisted metadata + emitted headers.
- Every tick appends raw event regardless of mode (`0..3`).

### Phase E6-4: Contradiction Hardening and Drift Semantics

Tasks:

1. Bind contradiction to Eq6 awareness floor:
- if contradiction, increment violation witness and enforce `A_self` penalty
- if `A_self < θ_self`, force non-bridge path

2. Split drift semantics:
- `drift_structural` (governance/topology/ledger)
- `drift_grounding` (resolved-vs-claimed contradiction)
- `drift = max(drift_structural, drift_grounding)` (or documented blend)

3. Align appraisal fields with E6 outputs:
- remove silent default/full-pass appraisal in contradiction cases

Touchpoints:
- `backend/api/agent_writes.py`
- `backend/fieldx_kernel/orchestrator.py`

Exit criteria:
- Contradiction cases cannot emit `law_score=1.0` and `drift=0.0`.

Status:
- partially complete in current branch with minimal linkage in `backend/api/agent_writes.py`:
  - contradiction already increments Eq6 witness count (`E6_CONTRADICTION_VIOLATIONS`)
  - contradiction now hard-gates bridge when `A_self < theta_self` (`governance_contradiction_gate`)
  - contradiction appraisal now anchors `law_score` to `A_self` when available
  - drift split now emitted as:
    - `drift_structural` (prior appraisal drift)
    - `drift_grounding` (contradiction floor)
    - `appraisal.drift` remains `max(structural, grounding)`

### Phase E6-5: HALT Semantics (`emit` vs `commit`)

Tasks:

1. In `HALT`, still append raw event and emit HR telemetry.
2. In `HALT`, set promotion route to `block` (no canonical merge).
3. Keep telemetry emission enabled for diagnosis.
4. Preserve contradiction/strain signals for triggered review, not universal reprocessing.

Touchpoints:
- `backend/api/agent_writes.py`
- `backend/fieldx_kernel/orchestrator.py`

Exit criteria:
- HALT turns are always recorded in raw diary.
- HALT turns are never promoted to canonical memory.

Status:
- partially complete in current branch (`backend/api/agent_writes.py`):
  - HALT promotion is now always `route=0` via `_promotion_decision(...)`
  - header build now force-locks HALT semantics (`mode=0`, `ptype=HR`, `route=0`) even if `route_override` is present
  - telemetry path remains active and raw-write invariant remains explicit (`always_write_raw=true`)
  - contradiction/halt now emits `review_trigger` with `mode=triggered_only`

### Phase E6-6: Tests + Rollout Flags

Tasks:

1. Add deterministic tests:
- hard gate fail -> mode `0`
- recovery climb `0->1->2->3`
- any hard fail from `3` -> `0`
- contradiction + resolved context -> lowered lawfulness + raised drift

2. Introduce staged rollout flags:
- `E6_SCORING_STRICT=0|1`
- `E6_MODE_GATING_STRICT=0|1`
- `E6_HALT_MINIMAL_COMMIT_ONLY=0|1`

3. Surface E6 diagnostics in introspect/stats:
- mode, bridge_allowed, blockers, `V_int_mean_3`, `V_int_std_3`

Touchpoints:
- `backend/tests/*`
- `backend/api/chat.py` (`/api/chat/introspect`)
- `backend/api/stats.py`

Exit criteria:
- E6 scoring path can be enabled progressively with clear observability and rollback.

Status:
- partially complete in current branch:
  - deterministic mode/packet tests extended in `backend/tests/test_e6_header_mode_packet.py`:
    - strict route ladder `0->1->2->3`
    - hard fail from `express` forces `halt`
  - rollout flags now wired/exposed using existing flow:
    - `E6_MODE_GATING_STRICT` now aliases strict route->mode/ptype mapping in `backend/api/agent_writes.py`
    - `E6_SCORING_STRICT`, `E6_MODE_GATING_STRICT`, `E6_HALT_MINIMAL_COMMIT_ONLY` emitted in `metadata.e6_rollout_flags`
  - diagnostics surfaced:
    - introspect adds `e6_diagnostics` + `e6_rollout_flags` (`backend/api/chat.py`)
    - session stats adds `e6_diagnostics` from latest chat event (`backend/api/stats.py`)
    - `e6_diagnostics` now includes `V_int_mean_3` + `V_int_std_3` from existing `e6_scoring.window` signals
  - telemetry payload extended to carry existing E6 fields + `e6_v_int_mean_3`/`e6_v_int_std_3` (`backend/metrics/telemetry.py`)
  - telemetry integrity hardening for search counters:
    - enforce `search_used <= search_requested` during telemetry rollup ingest (`backend/metrics/store.py`)
    - stats payload now surfaces `search_invariant_repairs` + `alerts.search_invariant_repair_active` (`backend/api/stats.py`)
    - alert threshold is configurable via `STATS_SEARCH_REPAIR_ALERT_THRESHOLD` (default `0`)
  - response grounding hardening for metric prompts:
    - reject unsupported numeric deltas/probabilities in metric-change answers and fallback to verified EQ9 summary (`backend/api/chat.py`)
    - log applied override in turn metadata as `grounding_override.reason=ungrounded_numeric_delta_claims`

## Set Up Now for Scale (Low-Regret Foundations)

These items should be established before cloud cutover to avoid redesign during growth:

1. Keep four hard boundaries:
- Ledger authority (truth)
- Runtime/model execution
- Middleware orchestration
- Frontend UX

2. Keep ledger service multi-tenant first, isolate later only when needed:
- avoid one-app-per-ledger by default
- preserve per-ledger authz boundaries in data + API contracts

3. Keep event model content-addressed and verifiable:
- `event_id`, `prev_event_id`, `payload_hash`, `issuer`, `stream_key`, `seq`
- preserve signature/proof evolution path without changing event identity semantics

4. Treat sync like event exchange (push/pull/checkpoint), not DB replication:
- never block user turns on sync completion
- retain quarantine + reconciliation workflows as first-class behavior

5. Keep projection stores as mirrors, never source of truth:
- every projected record must reference source `event_id`
- track projection version + watermark for deterministic rebuilds

## Risks and Mitigations

1. Risk: partial boundary extraction causes mixed write paths.
- Mitigation: migration flag + route-level adapter checklist.

2. Risk: key mismanagement leaks signer material.
- Mitigation: move private keys to vault/KMS; no frontend key storage.

3. Risk: stream divergence and silent drift.
- Mitigation: quarantine + checkpointed replay + explicit repair workflow.
