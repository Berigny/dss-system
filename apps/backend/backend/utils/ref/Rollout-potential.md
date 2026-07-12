If you keep the ledger as the “truth machine” (append-only, content-addressed, verifiable), everything else can scale and swap around it.

Here’s a clean way to shape it so you don’t paint yourself into a corner.

## 1) Split the system into 4 hard boundaries

**A. Ledger service (Fly.io)**

* Only cares about: `append`, `fetch`, `verify`, `sync`.
* Stateless API + durable storage.
* Treat it like a database with opinions (hash-chain / audit log), not like an app.

**B. Runtime / model service**

* Local (Ollama) now; later remote runners if needed.
* Produces events and consumes events, but does *not* own persistence.

**C. Middleware / orchestration (Vercel)**

* Stateless request handling: chat turns, routing, prompt assembly, tool dispatch.
* Should be disposable. If it dies, nothing important is lost because the ledger has it.

**D. Frontend(s)**

* Web chat (Vercel).
* Mac app (offline-first) with a local ledger + local model runner.

Keep those boundaries strict and your many-to-many future becomes easy.

## 2) Ledger provisioning on Fly: avoid “one app per ledger” unless you truly need isolation

If you want “many ledgers”, you’ll be tempted to spin up a new Fly app per ledger. That’s operationally heavy.

A simpler pattern:

* **One ledger API service**, multi-tenant.
* Storage options:

  * **Postgres** (single cluster): easiest ops, strong queries, row-level security possible.
  * **SQLite per ledger file** (on volumes): neat for append-only logs, but provisioning + backup + replication needs more plumbing.

If you need *hard isolation* later, you can still move “high value” ledgers into dedicated instances. Start multi-tenant, graduate to isolated.

## 3) Make the ledger format content-addressed from day one

Even if you don’t finish full crypto verification yet, design so you can.

Event header essentials:

* `event_id = hash(header + payload + prev_event_id)`
* `prev_event_id`
* `seq` (optional but helpful)
* `timestamp`
* `issuer` (DID)
* `subject` / stream key
* `signature` (later)

Then:

* **Verify** can be: presence + chain continuity now, signature later.
* Offline merge becomes: detect branches, reconcile by policy (keep both, or pick canonical).

Your earlier “quarantine divergence_seq_conflict” is exactly what you’ll keep seeing until you formalise branch handling. Embrace it: it’s the normal offline story.

## 4) Sync: treat it like Git, not like a database replication

For offline Mac + online Fly:

* Local ledger accepts appends immediately.
* Sync agent does:

  * `push` missing events (by stream_key, from last known checkpoint)
  * `pull` missing events
  * resolves divergence: keep multiple heads, or define a canonical head

KISS rule: **never block user interaction on sync**. Chat continues locally; sync is a background “event courier”.

## 5) Auth as capability, not “log in and hope”

For a VC/DID vibe that won’t collapse under many-to-many:

**Principals**

* Users: `did:...`
* Models/runners: `did:...` (yes, give model runners identities too)
* Services: `did:...`

**Authorisation**

* Use **capabilities** (“may append to ledger X, subject Y, for N hours”) rather than global roles.
* Short-lived proofs (JWT or similar) now; upgrade the proof to VC/ZCAP later without changing the ledger API much.

**Critical detail:** every event should carry:

* `issuer` (DID)
* `auth_proof_ref` or embedded proof (depending on size/security)
* Optional `delegation` chain later

That makes auditing and multi-party ledgers sane.

## 6) Many-to-many mapping: make it explicit in the data model

Don’t imply relationships; store them as first-class objects/events:

* `membership`: user ↔ ledger
* `runner_binding`: model_runner ↔ ledger (or ↔ user)
* `policy`: who can read/append/verify which subjects
* `consent`: user grants model runner permission scope

Because it’s a ledger, these can be events too. That gives you historical traceability “for free”.

## 7) Practical deployment notes for your timeout headache

Once Vercel is in the middle:

* Keep Vercel requests *fast*: do not wait on slow ledger writes if you can avoid it.
* Pattern:

  * Middleware writes to ledger with **queue_on_failure**
  * If write doesn’t confirm quickly, return “accepted” and let a job flush/retry
* Add endpoints like `sync_status` and “last checkpoint” so clients can display “synced / pending” cleanly.

## A sensible build order (min regret)

1. Finalise ledger API + event format + branch semantics.
2. Deploy ledger service on Fly (multi-tenant).
3. Build Mac sync agent (push/pull + checkpointing).
4. Move middleware + web UI to Vercel (stateless).
5. Introduce DID identities + signed requests.
6. Introduce capability grants (JWT → VC later).
7. Expand to multi-user / multi-runner / multi-ledger policies.

---

A good offline-first trick. Think of your local ledger as a **hot cache + full integrity spine**, not a full archive.

A clean pattern is:

## 1) Two layers locally: Spine + Hot payloads

**Spine (always kept)**

* Minimal event record: `event_id`, `prev_event_id`, `stream_key`, `seq`, `timestamp`, `issuer`, plus your **COORD(s)** / pointers.
* Enough to:

  * verify chain continuity
  * reference anything you don’t store locally
  * rehydrate later

**Hot payload store (optional/evictable)**

* Full payload blobs for “recent / frequently used / pinned / required for current work”.
* Can be dropped without breaking the ledger spine.

That gives you long offline operation with a small disk footprint.

## 2) Make COORDs content-addressed (so they’re trustworthy)

If COORDs point to Fly, avoid “just a URL”. Use a pointer that includes:

* remote `event_id` (or `payload_hash`)
* remote `ledger_id_h64`
* location hints (region/node) optional

So local can say: “I don’t have it, but I know exactly what it should hash to when fetched.”

## 3) Eviction policy that won’t surprise you

Use a simple score and make it deterministic:

Score inputs:

* **recency** (last accessed)
* **frequency** (access count)
* **pin/protect** (never evict: system prompts, identity creds, current project context)
* **dependency depth** (keep a window around the current head so reconstruction is fast)

Rule of thumb:

* Always keep last **N turns per active stream** (chat feels instant).
* Always keep anything referenced by pinned “context packs”.
* Everything else can degrade to spine-only.

## 4) Offline “working set” bundles

To stay productive for long periods, add a concept like a **pack**:

* A pack is a list of event_ids + payloads you promise to keep locally.
* Packs can be “Project X”, “Current customer”, “Personal knowledge”.

That’s more human-friendly than pure LRU.

## 5) Sync strategy: pull-on-demand + prefetch

When you’re online:

* **Pull-on-demand**: if the UI needs a payload and only has COORD, fetch it.
* **Prefetch**: when you open a thread/project, fetch the next likely-needed payloads (last 200 events, referenced docs, etc.).

When you’re offline:

* Everything still works against the hot set.
* Missing payloads show as “stubbed” but still linkable.

## 6) Beware one gotcha: verification vs missing payload

If your chain hash includes full payload bytes, and you drop payloads locally, you can still verify if you keep:

* `payload_hash` in the spine
* `event_id` derived from hashes (or signature over payload hash)

So: **store hashes, not payloads**, in the permanent layer.

## 7) Suggested local record shapes (KISS)

**Spine entry**

* `event_id`
* `prev_event_id`
* `payload_hash`
* `stream_key`, `seq`, `ts`
* `issuer`
* `coords[]` (remote pointers)
* `flags` (pinned, has_payload, etc.)

**Payload blob (separate)**

* `event_id -> bytes`

This makes compaction and eviction trivial.

---

A super practical next step: decide whether your `event_id` is computed from *payload bytes* or from *payload_hash*. If it’s payload bytes, switch to payload_hash now so the “spine-only” design stays verifiable even when payloads are missing locally.



A strong pattern: **ledger = truth**, **graph(s) = fast, clever mirrors**.

Here’s how to do it without the mirror ever “becoming the truth” by accident.

## Keep the mirror as a projection, not a peer

Treat each graph DB as a **materialised view** built from the ledger’s event stream:

* Ledger stores: immutable events (facts + provenance).
* Graph stores: extracted *meaning* (entities, relations, topics, embeddings), optimised for search and traversal.
* Every node/edge in the graph must point back to ledger evidence.

Practical rule: **no graph write is accepted unless it includes `source_event_id` (and usually `payload_hash`).**

## Make re-indexing cheap and deterministic

You’ll want to rebuild mirrors often (new extraction model, new ontology, bug fix).

So each mirror should keep:

* `source_event_id` (or a range)
* `projection_version` (schema + extractor version)
* `watermark_event_id` (how far through the ledger this mirror has processed)

Then you can:

* replay from genesis
* or replay from a checkpoint
* or run two projection versions side-by-side and swap

## Two mirrors is usually better than one

Deep semantic search usually wants *both*:

**1) Vector index (semantic similarity)**

* Embeddings keyed by `event_id` or by extracted “chunk ids”.
* Fast “find me stuff like this”.

**2) Graph index (structure and reasoning)**

* Entities, relationships, timelines, ownership, causality, “who said what”, “depends on”, “belongs to”.
* Fast “walk the world model”.

Flow that works well:

1. Vector search to get candidate events.
2. Graph expansion (1–3 hops) to pull in related context.
3. Optional rerank with a small model.
4. Return results with ledger-backed citations (`event_id`, `payload_hash`, `as_of` watermark).

## Don’t lose authorisation when you mirror

This bites people.

Options:

* **Enforce ACL at query time**: graph returns candidate `event_id`s, middleware filters by ledger-authorised access.
* Or **stamp ACL attributes into the mirror** (tenant, subject, capability tags) and enforce in the graph too.

Safer early on: **filter by ledger permissions in middleware** (less chance of leakage), then optimise later.

## Handling contradictions and updates

Ledger is append-only, so your mirror must support “new facts overwrite old meaning” without deleting history.

Easy approach:

* In graph, store relationships with `valid_from_event_id` and `superseded_by_event_id` (or a boolean “active”).
* Queries default to “latest active”, but you can time-travel.

## What to return from semantic search (keep it honest)

Every result should include:

* `event_id`
* `payload_hash` (or signature ref later)
* `ledger_id_h64`
* `mirror_watermark_event_id` (so you can say “this search is accurate up to here”)

That way, even if the mirror is stale, you can prove what it’s based on.

## Minimal build path

1. Start with one projection worker reading the ledger stream.
2. Write:

   * embeddings to a vector store
   * entity/edge triples to a graph store
3. Make all graph objects traceable to ledger events.
4. Add rebuild/replay controls and watermark tracking.
5. Only then crank up “deep semantic” features.

If you tell me which graph tech you’re leaning toward (property graph like Neo4j vs RDF/quad store, or even Postgres-as-graph), I’ll sketch a simple schema for nodes/edges plus the exact “source pointers” that keep the ledger as the unquestioned truth.
