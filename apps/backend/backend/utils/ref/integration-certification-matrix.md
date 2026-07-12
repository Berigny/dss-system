# Integration Certification Matrix (v0)

Purpose: certify external integrations that use middleware as the memory/control gateway.

Scope:

- OpenClaw (local)
- n8n
- ChatGPT App via MCP
- WhatsApp channel adapter

## Common Certification Gates

Each integration must pass all gates before production enablement.

1. Contract Gate
- Request/response schema compatibility.
- Auth and signature validation (if webhook-driven).
- Error normalization and retry semantics.

2. Memory E2E Gate
- Inbound event reaches middleware route.
- Middleware appends/query recalls through backend ledger/sync.
- Signed envelope accepted by `/sync/v0/push`.

3. Failure Gate
- Backend down, timeout, and partial failure behavior.
- Nonce replay / signature mismatch handling.
- DLQ or quarantine behavior validated.

4. Security Gate
- Tenant isolation and `ledger_id` enforcement.
- Secret handling and redaction in logs.
- Replay protection and webhook authenticity checks.

5. Soak Gate
- Long-running stability (1h minimum in v0).
- Duplicate and out-of-order message handling.
- Connection/reconnect resilience.

## Test Case Matrix

Legend:
- `P0`: mandatory before production
- `P1`: mandatory before broad rollout
- `P2`: recommended hardening

### OpenClaw (Local)

1. `P0` handshake compatibility
- Trigger middleware route from OpenClaw local workflow.
- Expect 2xx and stable schema mapping.

2. `P0` signed memory append
- Send one event through OpenClaw.
- Expect `/sync/v0/push` `accepted >= 1`.

3. `P0` chain continuity
- Send `seq=1` then `seq=2` same stream with `prev_event_h64`.
- Expect both accepted, no quarantine.

4. `P1` replay duplicate handling
- Re-send same envelope.
- Expect duplicate path, no corruption.

5. `P1` restart recovery
- Restart middleware/backend between events.
- Expect checkpointed continuation without divergence.

### n8n

1. `P0` webhook auth and replay defense
- Validate signature/token and timestamp window.

2. `P0` memory append/query node behavior
- n8n flow writes memory then reads context.
- Expect deterministic results and tenant scoping.

3. `P0` transient failure retries
- Inject backend timeout.
- Expect retry/backoff and eventual success or explicit failure.

4. `P1` concurrent workflow isolation
- Parallel runs with different `tenant_id`/`ledger_id`.
- Expect no cross-run contamination.

5. `P2` burst traffic soak
- Sustained workflow triggers.
- Expect bounded latency and stable acceptance rate.

### ChatGPT App via MCP

1. `P0` MCP tool contract alignment
- Validate tool input/output schemas.

2. `P0` memory tool E2E
- Tool call writes to middleware memory endpoint.
- Expect ledger append and query recall.

3. `P1` auth context propagation
- Verify user/session context maps to tenant/ledger safely.

4. `P1` malformed tool payload handling
- Send invalid payload.
- Expect safe validation error and no side effects.

5. `P2` multi-turn continuity
- Repeated tool calls preserve chain and cursor progression.

### WhatsApp (Channel Adapter)

1. `P0` webhook authenticity
- Validate provider signature and timestamp checks.

2. `P0` inbound/outbound correlation
- Incoming message persisted and reply traceable.

3. `P1` delivery retries and dedupe
- Simulate repeated webhook deliveries.
- Expect idempotent handling.

4. `P1` media/text mixed payloads
- Validate normalization and policy checks.

5. `P2` regional latency + outage drills
- Simulate delayed provider callbacks and reconnects.

## Required Metrics per Integration

- `integration_requests_total{integration,status}`
- `integration_latency_ms{integration,route}`
- `sync_push_accepted_total{integration,ledger}`
- `sync_push_quarantine_total{integration,reason}`
- `sync_push_duplicate_total{integration}`
- `integration_replay_blocked_total{integration}`

## Required Artifacts Before Go-Live

1. Contract fixture set (sample payloads and expected mappings).
2. E2E test script(s) runnable in CI/local.
3. Failure injection checklist.
4. On-call runbook:
- common alarms
- manual replay steps
- rollback switch path

## Recommended Execution Order

1. OpenClaw (already closest and local).
2. n8n (workflow ecosystem target).
3. MCP (ChatGPT app integration surface).
4. WhatsApp (higher operational/compliance complexity).
