# Staging Gate Dry Run - 2026-02-24

Scope: migration gate progression for Fly backend + Vercel middleware/frontend.

## Checks Run

1. Preflight artifact presence:
- `backend/utils/ref/fly-backend.env.example` -> present
- `backend/utils/ref/vercel-middleware.env.example` -> present
- `backend/utils/ref/vercel-frontend.env.example` -> present
- `backend/utils/ref/deployment-endpoint-contract-checklist.md` -> present

2. Local live probes (after bringing up backend/middleware):
- `GET http://127.0.0.1:8080/health` -> `200` (`{"status":"ok","git_sha":"unknown"}`)
- `POST http://127.0.0.1:8080/sync/v0/handshake` -> `200` (`status=ok`, `accepted=true`)
- `GET http://127.0.0.1:8080/sync/v0/status` -> `200`
- `POST http://127.0.0.1:8080/sync/v0/status` -> `405` (method mismatch; use `GET`)
- `GET http://127.0.0.1:5001/health` -> `200` (`{"status":"ok", ...}`)

3. Auth/context probes:
- `POST /stats/telemetry` with payload/header ledger mismatch -> `400 ledger_scope_mismatch`
- `POST /stats/telemetry` with `x-ledger-id` fallback -> `200`
- `POST /api/ingest` without explicit ledger context -> `422 ledger_context_required`
- `POST /api/ingest` with `x-ledger-id` fallback -> `200`

4. CORS probes:
- backend started with:
  - `MIDDLEWARE_CORS_ORIGINS=https://ds-middleware-local.vercel.app,https://ds-frontend-local.vercel.app,https://ds-frontend-local-new.vercel.app`
  - `BACKEND_CORS_ORIGIN_REGEX=https://(ds-frontend-local|ds-frontend-local-new).*\\.vercel\\.app`
- `OPTIONS /sync/v0/status` with `Origin: https://ds-frontend-local.vercel.app` -> `200` with explicit `access-control-allow-origin`
- `OPTIONS /sync/v0/status` with `Origin: https://ds-frontend-local-new.vercel.app` -> `200` with explicit `access-control-allow-origin`
- `OPTIONS /sync/v0/status` with `Origin: https://ds-middleware-local.vercel.app` -> `200` with explicit `access-control-allow-origin`
- `OPTIONS /sync/v0/status` with `Origin: https://ds-frontend-local-git-main-preview.vercel.app` -> `200` with explicit `access-control-allow-origin` (regex allow)
- `OPTIONS /sync/v0/status` with `Origin: https://evil.example` -> `400` (no permissive `allow-origin` observed)
- `GET /health` with `Origin: https://evil.example` -> `200` without permissive CORS header

5. Strict policy probes (`LEDGER_AUTHZ_MODE=registry`, `LEDGER_CONTEXT_MODE=enforce`, `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`, `RESOLVER_NAMESPACE_GATE_MODE=strict`):
- known registry ledger write as owner (`gate-alpha`, `x-principal-id: gate-user`) -> `200`
- unknown ledger write (`gate-unknown`) -> `403 forbidden` (`reason=unknown_ledger`)
- known ledger write by non-owner (`x-principal-type: user`) -> `403 forbidden` (`reason=write_requires_owner_or_tenant`)
- inferred session-only telemetry context -> `422 ledger_context_required`
- middleware strict write-path probes against strict backend (`http://127.0.0.1:8080`):
  - middleware `/api/ingest/file` with default ledger `gate-alpha` -> `200`
  - middleware `/api/ingest/file` with default ledger `gate-unknown` -> `502` (propagated backend `403 Forbidden` for `/api/ingest/file`)
  - backend access log confirms strict outcomes on write path:
    - known ledger write -> `200`
    - unknown ledger write -> `403`
- note: legacy middleware `POST /api/ledgers` path in this runtime returned list payload (`{"active_ledger":...}`), so deterministic ledger selection for probe used `DEFAULT_LEDGER_ID` at process start.

6. Middleware ledger-switch semantics probe (legacy runtime):
- `GET /api/ledgers` -> `{"active_ledger":"default","ledgers":["default"]}`
- `POST /api/ledgers` with `{"ledger_id":"gate-alpha"}` -> unchanged payload (`{"active_ledger":"default","ledgers":["default"]}`)
- `POST /api/ledgers` with `{"ledger_id":"gate-unknown"}` -> unchanged payload (`{"active_ledger":"default","ledgers":["default"]}`)
- conclusion: route currently behaves as read-only in this runtime; do not rely on it for strict gate drills.
- operator-safe workaround for strict drills:
  - launch middleware with `DEFAULT_LEDGER_ID=<ledger>` / `DUALSUBSTRATE_LEDGER=<ledger>`
  - run allow/deny write probes per process profile.

7. Deployed Fly CORS matrix snapshot (`https://ds-backend-new.fly.dev`):
- CORS secrets updated on Fly backend:
  - `MIDDLEWARE_CORS_ORIGINS=https://ds-middleware-local.vercel.app,https://ds-frontend-local.vercel.app,https://ds-frontend-local-new.vercel.app`
  - `BACKEND_CORS_ORIGIN_REGEX=https://(ds-frontend-local|ds-frontend-local-new).*\\.vercel\\.app`
- post-update results:
  - allowed (`200` + explicit allow-origin):
    - `https://ds-frontend-local.vercel.app`
    - `https://ds-frontend-local-new.vercel.app`
    - `https://ds-middleware-local.vercel.app`
    - `https://ds-frontend-local-git-main-preview.vercel.app`
  - denied (`400`, no allow-origin): `https://evil.example`
- conclusion: deployed backend CORS matrix now matches intended staging policy.

8. Middleware ledger-switch route fix (local code + live re-test):
- fix applied in `ds-middleware-local/app.py` route registration:
  - `app.route("/api/ledgers", methods=["GET"])(list_ledgers)`
  - `app.route("/api/ledgers", methods=["POST"])(create_or_switch_ledger)`
- live probe after fix:
  - `GET /api/ledgers` -> `{"active_ledger":"default",...}`
  - `POST /api/ledgers {"ledger_id":"gate-alpha"}` -> `{"ledger_id":"gate-alpha"}`
  - follow-up `GET /api/ledgers` -> `{"active_ledger":"gate-alpha",...}`
  - `POST /api/ledgers {"ledger_id":"gate-unknown"}` -> `{"ledger_id":"gate-unknown"}`
  - follow-up `GET /api/ledgers` -> `{"active_ledger":"gate-unknown",...}`

9. Middleware deploy + staging ledger-switch verification:
- deployed app: `https://ds-middleware-new.fly.dev`
- `GET /api/ledgers` returned current active ledger.
- `POST /api/ledgers {"ledger_id":"gate-alpha"}` -> `200 {"ledger_id":"gate-alpha"}`
- follow-up `GET /api/ledgers` reflected `active_ledger:"gate-alpha"`.
- `POST /api/ledgers {"ledger_id":"gate-unknown"}` -> `200 {"ledger_id":"gate-unknown"}`
- follow-up `GET /api/ledgers` reflected `active_ledger:"gate-unknown"`.

10. Strict staging policy probe status (after setting strict backend secrets):
- strict keys confirmed in Fly secrets and machine env:
  - `LEDGER_AUTHZ_MODE=registry`
  - `LEDGER_CONTEXT_MODE=enforce`
  - `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`
  - `RESOLVER_NAMESPACE_GATE_MODE=strict`
- observed behavior remains permissive in staging:
  - middleware `/api/ingest/stream-file` with `ledger_id=gate-deny-z99` -> `200`
  - direct backend `/api/ingest` on fresh unknown ledger (`gate-deny-<timestamp>-<rand>`) -> `200`
- conclusion: strict auth policy is not effectively enforced on deployed backend yet; requires deeper runtime diagnosis before gate can be marked green.

11. Strict staging policy remediation + re-validation (completed):
- foundational runtime fix:
  - set backend `DB_PATH=/app/data` on Fly (`ds-backend-new`) so RocksDB uses mounted persistent volume.
  - without this, provisioning/auth state could be lost across machine restarts.
- provisioning bootstrap for strict mode:
  - temporary `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=allow`
  - provision `gate-alpha` via `POST /admin/ledgers`
  - restore `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`
- post-remediation strict probes:
  - direct backend known ledger write (`gate-alpha`, owner `anonymous/service`) -> `200`
  - direct backend unknown ledger write (`gate-dbfix-deny-...`) -> `403` (`reason=unknown_ledger`)
  - middleware write path:
    - switch ledger `gate-alpha` -> `/api/ingest/file` -> `200`
    - switch ledger `gate-mw-deny-dbfix` -> `/api/ingest/file` -> `502` wrapping backend `403`
- conclusion: strict deny/allow behavior is now functioning in deployed staging path as expected.

12. Integrated frontend traffic lane smoke (deployed):
- frontend reachability:
  - `GET https://ds-frontend-local-new.vercel.app` -> `200`
- frontend-origin lane probes (`Origin: https://ds-frontend-local-new.vercel.app`):
  - backend preflight `OPTIONS /sync/v0/status` -> `200`
  - middleware preflight `OPTIONS /api/ingest/file` initially `400`; fixed by setting middleware secret:
    - `MIDDLEWARE_CORS_ORIGINS=https://ds-frontend-local-new.vercel.app,https://ds-frontend-local.vercel.app,http://127.0.0.1:5000,http://localhost:5000`
    - post-fix middleware preflight -> `200` with explicit allow-origin
  - positive write path:
    - switch middleware ledger `gate-alpha` -> `/api/ingest/file` -> `200`
  - negative write path:
    - switch middleware ledger `gate-int-ui-deny-*` -> `/api/ingest/file` -> `502` wrapping backend `403`
  - chat lane smoke:
    - `POST /api/chat` -> `200` with assistant reply payload

13. Admin provisioning endpoint reliability check:
- `POST /admin/ledgers` returned `500` due logging context key collision (`created`).
- root cause patched in `backend/api/logging_utils.py` by reserving `LogRecord` keys.
- regression test added: `backend/tests/test_logging_utils.py`

## Status

- Gate state: green (staging migration gate checks passed for auth/context/CORS/ledger-switch lanes).
- Progress: strict staging policy and integrated frontend-origin lane are now validated on deployed targets.
- Residual operator note:
  - keep bootstrap step explicit in runbook (provision ledgers before strict `unknown_ledger=deny`).

## Next Actions

1. Move to migration cutover prerequisites (checkpoint loop + dual-write/diff validation).
2. Add CI smoke for deployed strict gate probes (CORS matrix + unknown-ledger deny + middleware deny propagation).
3. Keep strict provisioning bootstrap documented and rehearsed for staging/prod.
