# Staging Migration Gate Dry Run

Run timestamp (UTC): `2026-02-20T11:34:32Z`  
Scope: rollout item #10 (`Vercel/Fly` dry run)

## Summary

- Result: `PARTIAL` (static contract checks pass/mostly pass; live endpoint checks blocked because services were not running on localhost).
- Immediate blockers:
  - Backend not reachable at `127.0.0.1:8080`
  - Middleware not reachable at `127.0.0.1:5001`
  - Frontend not reachable at `127.0.0.1:5000`

## Checklist Results

### Contract + Code Checks (Static)

- `PASS` Backend `/health` now includes version metadata (`git_sha`) in response shape.
  - File: `backend/main.py`
- `PASS` Sync v0 ledger context contracts are enforced in backend routes (`ledger_id_h64` required on push/pull/backfill/checkpoint).
  - File: `backend/api/sync.py`
- `PASS` Middleware CORS origins are env-driven (`MIDDLEWARE_CORS_ORIGINS`).
  - File: `ds-middleware-local/app.py`
- `PASS` Frontend is configured to consume middleware base (`DUALSUBSTRATE_API`/`API_BASE` wiring via settings).
  - File: `ds-frontend-local/config/settings.py`
- `PASS` Middleware->backend ledger context propagation is explicit via request headers:
  - `x-ledger-id`
  - `x-ledger-id-h64`
  - File: `ds-middleware-local/api/client.py`
- `RISK` Backend CORS allowlist is still hardcoded and includes localhost + regex, not env-scoped per deploy.
  - File: `backend/main.py`
- `RISK` No explicit backend middleware-auth enforcement gate was verified in this run (`DUALSUBSTRATE_API_KEY` usage exists but not an obvious global write-route guard).
  - File(s): `backend/main.py`, route-level checks to be validated in live gate.

### Live Endpoint Checks (Local)

- `BLOCKED` `GET http://127.0.0.1:8080/health` (connection refused)
- `BLOCKED` `GET http://127.0.0.1:8080/sync/v0/status` (connection refused)
- `BLOCKED` `POST http://127.0.0.1:8080/sync/v0/handshake` (connection refused)
- `BLOCKED` `GET http://127.0.0.1:5001/health` (connection refused)
- `BLOCKED` `GET http://127.0.0.1:5001/mcp` (connection refused)
- `BLOCKED` `GET http://127.0.0.1:5001/.well-known/oauth-authorization-server` (connection refused)
- `BLOCKED` `GET http://127.0.0.1:5001/.well-known/oauth-protected-resource` (connection refused)
- `BLOCKED` `GET http://127.0.0.1:5000/health` (connection refused)

## Next Run Commands (When Stack Is Up)

1. Backend:
   - `curl -sS http://127.0.0.1:8080/health`
   - `curl -sS http://127.0.0.1:8080/sync/v0/status`
   - `curl -sS -X POST http://127.0.0.1:8080/sync/v0/handshake -H 'Content-Type: application/json' -d '{"peer_id":"dryrun","protocol_versions":[0],"envelope_versions":[0],"alg_ids":[1,2]}'`
2. Middleware:
   - `curl -sS http://127.0.0.1:5001/health`
   - `curl -sS http://127.0.0.1:5001/mcp`
   - `curl -sS http://127.0.0.1:5001/.well-known/oauth-authorization-server`
   - `curl -sS http://127.0.0.1:5001/.well-known/oauth-protected-resource`
3. Frontend:
   - `curl -sS http://127.0.0.1:5000/health`

## Gate Decision

- `NOT GREEN YET`
- Move to green when:
  - all live endpoint checks pass,
  - security gate confirms backend write auth between middleware/backend is enforced in deployed config,
  - CORS allowlists are deploy-scoped (not broad local defaults) for staging/prod.
