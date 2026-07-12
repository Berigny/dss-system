# E6 Sync v0 (Normative Draft)

Status: Draft v0  
Scope: multi-ledger, multi-repo synchronization for offline/online operation.

This protocol transports Envelope v0 events between peers while preserving
append-only integrity and E6 gating semantics.

## 1. Topology and Roles

- `local` ledger: device-owned, offline-first, accepts local commits.
- `cloud` ledger: shared durable ledger for cross-device/cross-repo access.
- `peer`: any sync endpoint that can push/pull envelopes.

Each event belongs to one logical stream:

```text
stream_key = (ledger_id_h64, origin_repo_h64, origin_node_h64)
```

## 2. Consistency Model

- Local append is authoritative for local device operation.
- Cross-peer propagation is eventual.
- Ordering is strict per `stream_key` via `(seq, prev_event_h64)`.
- No global total ordering is assumed.

## 3. Required State per Peer

Each sync participant MUST track:

- `cursor_by_stream`: last accepted `(seq, event_id)` per stream.
- `latest_hash_by_stream`: latest accepted chain hash (`event_id` or commit hash).
- `nonce_cache` for replay defense.
- `quarantine_queue` for invalid/unknown-schema events.

## 4. Protocol Operations

### 4.1 Handshake

Request includes:

- supported protocol versions
- supported envelope/header versions
- supported alg ids
- requested ledgers (hashed or canonical ids)

Response includes accepted intersection and policy constraints.

### 4.2 Pull

Client requests:

- target `ledger_id`
- per-stream cursor or checkpoint token
- max batch size

Server returns ordered envelopes and next cursor token.

### 4.3 Push

Client sends envelope batch.

Server returns per-item status:

- `accepted`
- `duplicate`
- `quarantine`
- `rejected`

with reason codes.

## 5. Ingest Rules

For each incoming envelope, receiver MUST:

1. Validate envelope and proof (`e6-envelope-v0.md`).
2. Check authorization policy for `(issuer, ledger, ptype, route)`.
3. Check schema compatibility.
4. Dedupe by `event_id`.
5. If chain predecessor missing, place in pending buffer and request backfill.
6. Commit atomically to local store and update cursor/checkpoint.

## 6. Conflict and Divergence

Append-only event streams:

- same `event_id` => duplicate, ignore.
- same `(stream_key, seq)` with different hash => divergence.

Divergence handling v0:

1. Quarantine conflicting branch.
2. Keep first accepted canonical branch for that peer.
3. Emit `DIVERGENCE_DETECTED` audit event.
4. Require explicit repair workflow (manual or policy engine).

## 7. Checkpoints and Snapshotting

Checkpoint format:

```text
checkpoint = {
  ledger_id_h64,
  stream_cursors[],
  merkle_root_h128,
  checkpoint_ts_ms,
  signer_id_h64,
  signature
}
```

Rules:

- Checkpoints SHOULD be emitted periodically by cloud ledgers.
- Edge peers MAY prune old events after storing verified checkpoint + retention
  policy permits.

## 8. Security and Policy

- Sync ACLs MUST be ledger-scoped.
- Propagation policy MUST be explicit allowlist by packet or claim type.
- Sensitive classes MAY be excluded from cloud push but retained locally.
- Every push/pull SHOULD produce audit records with peer id and counts.

## 9. Failure Semantics

- Network failure during push: client retries idempotently with same envelopes.
- Partial batch failure: retry only non-accepted items.
- Missing predecessor timeout: request backfill range for affected stream.
- Persistent invalid proof: move to dead-letter queue.

## 10. Minimal HTTP Mapping (Suggested)

- `POST /sync/v0/handshake`
- `POST /sync/v0/pull`
- `POST /sync/v0/push`
- `POST /sync/v0/backfill`
- `GET /sync/v0/status`

All endpoints SHOULD support compressed binary payloads for edge efficiency.

## 11. Migration Plan from Monolithic Backend Ledger

1. Introduce `ledger_id` and `origin_repo_id` on all writes.
2. Emit Envelope v0 alongside current writes.
3. Run shadow sync path local->cloud with diff checks.
4. Enable cloud ingest as source for selected consumers.
5. Cut over by ledger cohort, keep rollback switch.

## 12. Known v0 Limits

- u24 `seq` wraps; implementations MUST rely on chain hash continuity too.
- 64-bit hashed ids are compact but not ideal for adversarial collision domains.
- Divergence repair is intentionally conservative/manual in v0.
