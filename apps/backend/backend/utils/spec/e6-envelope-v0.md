# E6 Envelope v0 (Normative Draft)

Status: Draft v0  
Scope: Identity/provenance/authenticated wrapper around E6 header and payload.

This spec extends `e6-header-v0.md` to support PrimeID-based trust, local/offline
operation, and later DID/VC interoperability.

## 1. Design Goals

- Keep hot-path routing on the 16-byte header.
- Carry identity, replay protection, and proof in a compact envelope.
- Support both constrained edge devices and cloud verification.

## 2. PrimeID Mapping

Prime-factored coordinates act as stable identifiers:

- `prime_subject_id`: canonical id of the subject (agent/user/device/ledger role)
- `prime_issuer_id`: id of signer/attester

For compact transport, v0 carries 64-bit hashes:

- `subject_id_h64 = H(canonical_prime_subject_id)`
- `issuer_id_h64 = H(canonical_prime_issuer_id)`

Canonical full PrimeID strings MUST be resolvable out-of-band from local cache or
registry when required for audits/recovery.

## 3. Envelope Structure

Binary framing:

```text
EnvelopeV0 := Header16 || PayloadN || TrailerV0
```

- `Header16`: exact bytes from E6 header v0.
- `PayloadN`: packet-type specific body (WU/HR/PP/CA).
- `TrailerV0`: identity/proof trailer below.

Trailer v0 fields:

| Field | Size | Type | Description |
| --- | ---: | --- | --- |
| `trailer_ver` | 1 | u8 | MUST be `0` |
| `alg_id` | 1 | u8 | signature/MAC algorithm id |
| `key_id` | 4 | u32 | signing key identifier |
| `ledger_id_h64` | 8 | u64 | hash of canonical ledger id |
| `origin_repo_h64` | 8 | u64 | hash of canonical origin repo id |
| `origin_node_h64` | 8 | u64 | hash of canonical node/device id |
| `subject_id_h64` | 8 | u64 | hash of subject PrimeID |
| `issuer_id_h64` | 8 | u64 | hash of issuer PrimeID |
| `nonce64` | 8 | u64 | anti-replay unique value |
| `prev_event_h64` | 8 | u64 | prior committed event hash in same stream |
| `payload_hash_h64` | 8 | u64 | hash of `PayloadN` |
| `proof_len` | 1 | u8 | proof bytes length |
| `proof` | var | bytes | signature or MAC |

Minimum trailer size excluding proof is 71 bytes.

## 4. Canonical Signing Input

The authenticated message for v0 MUST be:

```text
signing_input =
  Header16
  || payload_hash_h64
  || ledger_id_h64
  || origin_repo_h64
  || origin_node_h64
  || subject_id_h64
  || issuer_id_h64
  || nonce64
  || prev_event_h64
  || key_id
  || alg_id
```

All integer fields are big-endian.

## 5. Algorithms (v0)

Recommended `alg_id` assignments:

- `1`: Ed25519 signature (preferred for cross-repo/cloud trust)
- `2`: HMAC-SHA256 truncated to 16 bytes (shared-secret constrained edge mode)

Rules:

- Cross-repo synchronization MUST use asymmetric signatures (`alg_id=1`).
- Local-only operation MAY use `alg_id=2`.
- Verifiers MUST reject unknown `alg_id`.

## 6. Event Identity

Global event identity for dedupe and replay:

```text
event_id = H(
  ledger_id_h64
  || origin_repo_h64
  || origin_node_h64
  || seq_u24
  || prev_event_h64
  || payload_hash_h64
)
```

`seq_u24` is taken from header.

## 7. Verification Procedure

Receiver MUST perform, in order:

1. Parse and validate header CRC and magic.
2. Recompute and compare `payload_hash_h64`.
3. Verify `nonce64` freshness per `(issuer_id_h64, ledger_id_h64)` policy.
4. Verify signature/MAC over canonical signing input.
5. Validate chain link:
   - `prev_event_h64` equals latest accepted hash for source stream, or
   - packet is marked as replay/backfill with explicit policy allowance.
6. Enforce E6 gating policy from header flags (`K/P/E/valid`, `route`, `law`).

Packets failing any step MUST be rejected or quarantined.

## 8. Nonce Policy

- Nonce MUST be unique per `(issuer_id_h64, ledger_id_h64)` within retention window.
- Receivers SHOULD keep sliding-window nonce cache.
- On constrained devices, nonce cache MAY be probabilistic (Bloom filter) with
  fallback strict checks at cloud ingest.

## 9. Relationship to VC/DID

This is VC-lite:

- PrimeID maps to DID-like subject/issuer identifiers.
- Envelope proof maps to VC proof.
- Envelope payload claims map to credential subject claims.

Interop path:

- Keep canonical ids stable.
- Add adapter that maps PrimeID -> `did:prime:<id>` and EnvelopeV0 -> VC/VP.

## 10. Security Notes

- 64-bit hashes are compact but collision-prone for high-scale adversarial use.
- Cloud-grade deployments SHOULD move critical identity/hash fields to 128+ bits in
  Envelope v1 while preserving Header v0.
