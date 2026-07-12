# Ledger -> Neo4j Projection v0 (Draft)

Purpose: keep the ledger authoritative and use Neo4j as a derived graph index.

## 1) Event Taxonomy for Graph Projection

Projection worker consumes verified E6 envelopes and maps payloads to graph mutations.

Required envelope metadata:

- `event_id` (u64 hex for v0)
- `ledger_id_h64`
- `origin_repo_h64`
- `origin_node_h64`
- `seq`
- `created_at`
- `payload_hash_h64`

Recommended payload event types:

1. `entity.created`
- Subject/object creation with labels.
- Example payload fields: `entity_id`, `entity_type`, `labels`, `props`.

2. `entity.updated`
- Partial property update.
- Example fields: `entity_id`, `set`, `unset`.

3. `relation.upserted`
- Create/update relationship edge.
- Example fields: `from_id`, `to_id`, `rel_type`, `props`.

4. `relation.deleted`
- Soft delete edge or valid-to close.
- Example fields: `from_id`, `to_id`, `rel_type`, `reason`.

5. `claim.asserted`
- Provenance/credential-style assertions as graph nodes + `ASSERTS` edges.
- Example fields: `claim_id`, `subject_id`, `issuer_id`, `claim_type`, `value`.

6. `checkpoint.emitted`
- Optional synthetic event for replay boundary and audit anchoring.

Canonical mapping rule:

- `graph_key = ledger_id_h64 + ":" + logical_id`
- Never use raw logical ids globally without ledger scoping.

## 2) Checkpoint + Replay Contract

State tables (in ledger service metadata store, not Neo4j):

1. `projection_checkpoint`
- key: `(projector_id, ledger_id_h64, stream_key)`
- fields:
  - `last_seq`
  - `last_event_id`
  - `last_event_hash` (optional)
  - `updated_at`

2. `projection_dlq`
- key: `dlq_id`
- fields:
  - `event_id`
  - `stream_key`
  - `seq`
  - `reason`
  - `attempts`
  - `next_retry_at`
  - `payload`

Processing contract:

1. Read events in stream order after checkpoint.
2. Apply Neo4j mutation transaction.
3. If tx commit succeeds, atomically advance checkpoint.
4. If tx fails, push to DLQ and stop stream or continue by policy.
5. Replayer reprocesses from checkpoint or from explicit `from_seq`.

Idempotency contract:

- Each projection write MUST include `last_event_id` on touched nodes/edges.
- Re-applying same `event_id` MUST be no-op.

Divergence contract:

- If ledger marks divergence/quarantine, projector MUST skip quarantined branch by default.
- Repair mode may project alternate branch into a separate graph namespace.

## 3) Initial Neo4j Schema + Idempotent Cypher

### Labels and Keys

1. `LedgerEntity`
- keys: `ledger_id`, `entity_id`

2. `LedgerClaim`
- keys: `ledger_id`, `claim_id`

3. `LedgerEvent` (optional audit layer in Neo4j)
- keys: `ledger_id`, `event_id`

### Constraints (run once)

```cypher
CREATE CONSTRAINT ledger_entity_key IF NOT EXISTS
FOR (n:LedgerEntity) REQUIRE (n.ledger_id, n.entity_id) IS UNIQUE;

CREATE CONSTRAINT ledger_claim_key IF NOT EXISTS
FOR (n:LedgerClaim) REQUIRE (n.ledger_id, n.claim_id) IS UNIQUE;

CREATE CONSTRAINT ledger_event_key IF NOT EXISTS
FOR (n:LedgerEvent) REQUIRE (n.ledger_id, n.event_id) IS UNIQUE;
```

### Upsert Entity (idempotent by `event_id`)

```cypher
MERGE (n:LedgerEntity {ledger_id: $ledger_id, entity_id: $entity_id})
ON CREATE SET
  n.created_at = $created_at,
  n.first_event_id = $event_id
WITH n
WHERE coalesce(n.last_event_id, '') <> $event_id
SET
  n += $set_props,
  n.last_event_id = $event_id,
  n.updated_at = $created_at;
```

### Upsert Relationship (dynamic type via APOC recommended)

If APOC available:

```cypher
MATCH (a:LedgerEntity {ledger_id: $ledger_id, entity_id: $from_id})
MATCH (b:LedgerEntity {ledger_id: $ledger_id, entity_id: $to_id})
CALL apoc.merge.relationship(a, $rel_type, {ledger_id: $ledger_id}, $set_props, b)
YIELD rel
WITH rel
WHERE coalesce(rel.last_event_id, '') <> $event_id
SET
  rel += $set_props,
  rel.last_event_id = $event_id,
  rel.updated_at = $created_at;
```

Without APOC: use fixed relationship types per event family.

### Soft Delete Relationship

```cypher
MATCH (a:LedgerEntity {ledger_id: $ledger_id, entity_id: $from_id})-[r]->(b:LedgerEntity {ledger_id: $ledger_id, entity_id: $to_id})
WHERE type(r) = $rel_type
  AND coalesce(r.deleted, false) = false
SET
  r.deleted = true,
  r.deleted_at = $created_at,
  r.last_event_id = $event_id;
```

### Claim Projection

```cypher
MERGE (c:LedgerClaim {ledger_id: $ledger_id, claim_id: $claim_id})
ON CREATE SET c.created_at = $created_at
SET
  c.claim_type = $claim_type,
  c.value = $value,
  c.issuer_id = $issuer_id,
  c.subject_id = $subject_id,
  c.last_event_id = $event_id,
  c.updated_at = $created_at;

MERGE (s:LedgerEntity {ledger_id: $ledger_id, entity_id: $subject_id})
MERGE (i:LedgerEntity {ledger_id: $ledger_id, entity_id: $issuer_id})
MERGE (i)-[:ASSERTED {ledger_id: $ledger_id}]->(c)
MERGE (c)-[:ABOUT {ledger_id: $ledger_id}]->(s);
```

## 4) Minimal Projector Execution Loop

1. Poll/read next events from ledger sync cursor.
2. Validate schema version and event type.
3. Map event -> Cypher + params.
4. Execute transaction.
5. Commit checkpoint.
6. Emit projector metrics:
- `projected_ok_total`
- `projected_dlq_total`
- `projector_lag_events`
- `projector_last_seq`

## 5) Rollout Sequence

1. Build projector in shadow mode (write to staging Neo4j).
2. Compare random query samples vs ledger-derived expectations.
3. Enable production projection read-only consumers.
4. Move graph-heavy endpoints to Neo4j, keep ledger replay fallback.

