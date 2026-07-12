Phase 0: Foundations (1 week)

  1. Define core entities

  - tenant
  - ledger
  - agent_profile
  - provider_secret_ref
  - channel_integration
  - sync_peer

  2. Freeze API conventions

  - Every request scoped by tenant_id and ledger_id where applicable.
  - Idempotency keys for create/write endpoints.
  - Audit event envelope for all mutations.

  3. Security baseline

  - Secrets only via vault refs (or encrypted-at-rest temp fallback).
  - RBAC roles: owner, builder, operator, viewer.

  ———

  Phase 1: Provision + Memory Core (2 weeks)

  1. Tenant/Ledger provisioning APIs

  - POST /v1/tenants
  - POST /v1/ledgers
  - GET /v1/ledgers/{ledger_id}
  - POST /v1/ledgers/{ledger_id}/archive

  2. Memory APIs (middleware facade over ledger service)

  - POST /v1/memory/append
  - POST /v1/memory/query
  - POST /v1/memory/checkpoint
  - GET /v1/memory/streams/{ledger_id}/status

  3. Acceptance

  - New tenant can create ledger and append/query signed events end-to-end.

  ———

  Phase 2: Agent Profiles + Keys (1-2 weeks)

  1. Provider key management

  - POST /v1/secrets/providers (store ref/enc blob)
  - GET /v1/secrets/providers (metadata only)
  - POST /v1/secrets/providers/{id}/rotate

  2. Agent profile APIs

  - POST /v1/agents/profiles
  - PATCH /v1/agents/profiles/{id}
  - POST /v1/agents/profiles/{id}/test
  - Fields: preferred models, fallback chain, budget caps, safety mode, tool policy.

  3. Acceptance

  - Profile-bound chat/memory flow runs with selected provider key refs.

  ———

  Phase 3: Integration Channels (n8n-first) (2 weeks)

  1. Channel registry

  - POST /v1/channels
  - PATCH /v1/channels/{id}
  - POST /v1/channels/{id}/enable|disable

  2. n8n node-compatible endpoints

  - POST /v1/channels/n8n/trigger
  - POST /v1/channels/n8n/memory/append
  - POST /v1/channels/n8n/memory/query
  - Webhook signature verification + replay protection.

  3. Acceptance

  - n8n flow can provision/select ledger, run agent call, persist/retrieve memory.

  ———

  Phase 4: Sync + Federation Controls (2 weeks)

  1. Sync orchestration APIs

  - POST /v1/sync/peers
  - POST /v1/sync/policies
  - POST /v1/sync/run
  - GET /v1/sync/status

  2. Policy engine

  - allowlist by event type/channel/ledger
  - sensitive-class deny rules
  - one-way local->cloud default, bidirectional opt-in

  3. Acceptance

  - Local/offline ledger syncs to cloud with policy enforcement + audit.

  ———

  Phase 5: Ops, Billing, Multi-tenant Hardening (ongoing)

  1. Metering and quotas

  - per-tenant token usage, sync volume, storage, channel invocations.

  2. Observability

  - SLOs for append/query/sync lag.
  - Alerts for divergence, replay spikes, key failures.

  3. Admin APIs

  - tenant export/import
  - key revocation blast-radius tooling
  - ledger repair workflows

  ———

  Minimal API surface to start implementing now

  1. POST /v1/tenants
  2. POST /v1/ledgers
  3. POST /v1/secrets/providers
  4. POST /v1/agents/profiles
  5. POST /v1/memory/append
  6. POST /v1/memory/query
  7. POST /v1/channels/n8n/memory/append
  8. POST /v1/channels/n8n/memory/query

  If you want, I can next draft exact JSON schemas for Phase 1+2 endpoints so you can implement directly.

   Achieved So Far

  1. Defined and documented the core protocol stack

  - backend/utils/spec/e6-header-v0.md
  - backend/utils/spec/e6-envelope-v0.md
  - backend/utils/spec/e6-sync-v0.md

  2. Implemented envelope crypto layer in backend

  - backend/fieldx_kernel/e6_envelope.py
  - Supports:
      - alg_id=2 HMAC verification
      - alg_id=1 Ed25519 verification (native via cryptography when available)

  3. Implemented sync v0 API surface in backend

  - backend/api/sync.py
  - Endpoints:
      - /sync/v0/handshake
      - /sync/v0/push
      - /sync/v0/pull
      - /sync/v0/backfill
      - /sync/v0/status
  - Includes dedupe, nonce replay checks, chain continuity checks, and divergence quarantine.

  4. Added tests

  - backend/tests/test_e6_envelope.py
  - backend/tests/test_sync_v0.py
  - Existing E6 packet/ingress tests still pass in targeted runs.

  5. Enabled Ed25519 runtime configuration

  - requirements.txt now includes cryptography
  - Backend env configured with E6_SYNC_ED25519_KEYS=...
  - Handshake confirmed alg_ids: [1,2].

  6. Middleware signing path validated

  - Added middleware smoke signer:
      - ds-middleware-local/utils/e6_sync_ed25519_push_smoke.py
  - Middleware requirements updated to include cryptography.
  - Verified end-to-end signed pushes accepted, including seq-chain continuity on same stream.

  7. Drafted Neo4j projection architecture

  - backend/utils/ref/ledger-neo4j-projection-v0.md
  - Defines event taxonomy, checkpoint/replay model, and idempotent Cypher patterns.

  Basic Current-State Architecture

  1. Frontend (ds-frontend-local)

  - UI/client only.
  - Should not hold signing private keys.

  2. Middleware (ds-middleware-local)

  - Orchestration/integration layer.
  - Signs envelopes (Ed25519) and pushes to backend sync API.
  - Candidate “memory gateway” surface for developer workflows/channels.

  3. Backend (ds-backend-local)

  - Ledger authority + sync verifier.
  - Verifies envelope authenticity/integrity.
  - Maintains stream ordering checks, nonce replay detection, quarantine controls.

  4. Ledger model

  - Header v0 (fast route/gating) + Envelope v0 (identity/proof).
  - Sync v0 supports pull/push/backfill/status for multi-ledger evolution.

  Next Steps

  1. Extract ledger service boundary in backend

  - Move ledger operations behind a service module.
  - Enables clean decoupling from orchestration code.

  2. Enforce mandatory ledger_id tenancy everywhere

  - Read/write/sync all scoped by tenant+ledger.
  - Enables true multi-ledger provisioning and isolation.

  3. Upgrade middleware sync daemon to /sync/v0/*

  - Replace legacy raw /sync/push replication.
  - Enables production-grade signed propagation.

  4. Add key lifecycle controls

  - Rotation, revocation, and key metadata endpoints.
  - Enables safer long-lived operations.

  5. Add projector worker (ledger -> Neo4j)

  - Implement checkpointed projection and DLQ replay.
  - Enables fast graph queries without compromising ledger integrity.

  6. Add dual-write + consistency diff mode

  - Compare old and new paths during migration.
  - Enables low-risk cutover.

  7. Add observability and runbooks

  - Lag/divergence/replay alerts and operator procedures.
  - Enables reliable operations at scale.

  What These Next Steps Enable

  - Multi-tenant memory platform (not just one backend ledger)
  - Offline/local + cloud sync with cryptographic trust
  - Middleware-as-node integration model (n8n and similar)
  - High-performance graph intelligence via Neo4j while ledger remains truth
  - Safer production rollout path for broader external users