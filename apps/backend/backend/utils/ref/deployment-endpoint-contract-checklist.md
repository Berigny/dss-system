# Deployment Endpoint Contract Checklist

## Backend (Fly)
- `GET /health` returns 200 with version metadata.
- `POST /sync/v0/handshake` advertises `alg_id=1` when `E6_SYNC_ED25519_KEYS` is configured.
- `POST /sync/v0/push` requires `ledger_id_h64` and rejects scope mismatches.
- `POST /sync/v0/pull` requires `ledger_id_h64` and enforces stream scope.
- `POST /sync/v0/backfill` requires `ledger_id_h64` and stream scope.
- `POST /sync/v0/checkpoint/save` persists peer/ledger cursor state.
- `POST /sync/v0/checkpoint/load` retrieves peer/ledger cursor state.
- `GET /sync/v0/status` reports `events`, `streams`, `nonces`, `quarantine`, `checkpoints`.

## Middleware (Vercel)
- Middleware points to Fly backend via `DUALSUBSTRATE_API`.
- No private signing keys in frontend runtime.
- OpenAI-compatible routes map to middleware orchestration path.
- If daemon enabled, it uses `/sync/v0/pull` + `/sync/v0/push` only.

## Frontend (Vercel)
- Frontend calls middleware base URL only.
- No direct backend or signer key exposure.

## Security Gates
- CORS allowlist is restricted to deployed frontend/middleware domains.
- Auth token between middleware and backend is enforced.
- Tenant/ledger context propagation headers are present on cross-service calls.
- Quarantine and checkpoint metrics are visible in operational dashboards.
