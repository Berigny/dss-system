# IAM/DID Backlog Issue Pack (GitHub)

Use with:
- GitHub CSV import: `backend/utils/ref/iam-did-github-issues.csv`
- Manual issue creation: copy/paste tickets below

Recommended labels:
- `area:backend`, `area:middleware`, `area:frontend`, `area:ops`
- `area:auth`, `area:authz`, `area:iam`, `area:webauthn`, `area:provenance`, `area:observability`
- `priority:P0`, `priority:P1`, `priority:P2`
- `size:S`, `size:M`, `size:L`

## P0

1. `P0-01 Auth mode flags and routing guards`
2. `P0-02 Principal claim ingestion shim`
3. `P0-03 Stream/meta auth-context diagnostics`
4. `P0-04 Middleware token pass-through envelope`
5. `P0-05 Frontend debug visibility for auth/context`
6. `P0-06 Observability baseline`

## P1

1. `P1-01 Principal registry schema and API`
2. `P1-02 Passkey/WebAuthn challenge and verify endpoints`
3. `P1-03 Session token issuer and validator`
4. `P1-04 Provenance dual-write rollout`
5. `P1-05 Frontend passkey bootstrap UX`
6. `P1-06 Revocation and emergency disable`

## P2

1. `P2-01 Enable did_strict on sensitive routes`
2. `P2-02 Context-bound authorization enforcement`
3. `P2-03 Remove legacy write authority semantics`
4. `P2-04 IAM authority hard split validation`

## Suggested Milestones

1. `Sprint A` (P0-01..P0-06)
2. `Sprint B` (P1-01..P1-04)
3. `Sprint C` (P1-05, P1-06, P2-01)
4. `Sprint D` (P2-02..P2-04)

## Next-Step Recommendation

Start with `P0-01`, `P0-02`, and `P0-03` as a single implementation slice, then run one cloud validation turn to confirm:
- auth claim parsing path is stable
- diagnostics are visible in stream/meta
- deny reasons are deterministic and operator-readable
