# Migration Runbook Command Sheet (Fly + Vercel)

This command sheet is migration-first (integrations paused) and assumes:
- Backend deploy target: Fly.io
- Middleware deploy target: Fly.io
- Frontend deploy target: Vercel

## 1) Environment Setup

Set these once per shell session before deploy/verification.

```bash
# Required domains
export FLY_BACKEND_URL="https://<fly-app>.fly.dev"
export FLY_MIDDLEWARE_URL="https://<middleware-fly-app>.fly.dev"
export VERCEL_FRONTEND_URL="https://<frontend-vercel-domain>"

# Optional auth (if enforced in your deployment)
export BACKEND_ADMIN_TOKEN="<admin-token>"
export BACKEND_API_KEY="<middleware-to-backend-api-key>"
```

Optional header helpers:

```bash
export AUTH_ADMIN_HEADER="Authorization: Bearer ${BACKEND_ADMIN_TOKEN}"
export AUTH_API_HEADER="x-api-key: ${BACKEND_API_KEY}"
```

Backend persistence invariant (required for strict registry mode):

```bash
# Ensure RocksDB uses mounted Fly volume path
flyctl secrets set -a <backend-app> DB_PATH=/app/data
```

## 1B) Staged Authz/Tenancy Rollout (Recommended)

Use this sequence to avoid hard cutovers:

### Stage A: Compatibility Baseline

Backend env:
- `LEDGER_AUTHZ_MODE=allow_all`
- `LEDGER_CONTEXT_MODE=compat`
- `RESOLVER_NAMESPACE_GATE_MODE=compat`

Expected behavior:
- existing traffic remains compatible
- explicit ledger context is accepted but not strictly required everywhere

### Stage B: Registry Policy (Observe)

Backend env:
- `LEDGER_AUTHZ_MODE=registry`
- `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=allow`
- `LEDGER_CONTEXT_MODE=compat`
- `RESOLVER_NAMESPACE_GATE_MODE=compat`

Smoke checks:

```bash
# Create/provision ledger record (idempotent)
curl -sS -X POST "${FLY_BACKEND_URL}/admin/ledgers" \
  -H "Content-Type: application/json" \
  -H "${AUTH_ADMIN_HEADER}" \
  -H "x-principal-id: ops" \
  -H "x-principal-type: admin" \
  -H "x-tenant-id: tenant-acme" \
  -d '{"namespace":"chat-acme","name":"Acme","policy_profile":"standard"}'

# Verify structured registry visibility
curl -sS "${FLY_BACKEND_URL}/admin/ledgers" \
  -H "${AUTH_ADMIN_HEADER}" \
  -H "x-principal-id: ops" \
  -H "x-principal-type: admin"
```

### Stage C: Context Enforcement

Backend env:
- `LEDGER_CONTEXT_MODE=enforce`

Smoke checks:

```bash
# Missing explicit ledger context should fail with 422
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "${FLY_BACKEND_URL}/chat" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"ctx-check","entity":"chat-acme","message":"ping","provider":"openai","history":[]}'

# Header fallback should pass context check (final status may vary by provider/runtime)
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "${FLY_BACKEND_URL}/enrich" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: chat-acme" \
  -d '{"entity":"chat-acme","role":"user","content":"ping","kind":"text","metadata":{}}'
```

### Stage D: Unknown Ledger Deny + Strict Resolver Gate

Backend env:
- `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny`
- `RESOLVER_NAMESPACE_GATE_MODE=strict`

If provisioning a new ledger while strict deny is active, use bootstrap:

```bash
# Temporarily allow unknown for provisioning
flyctl secrets set -a <backend-app> LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=allow

# Provision required ledger(s)
curl -sS -X POST "${FLY_BACKEND_URL}/admin/ledgers" \
  -H "${AUTH_ADMIN_HEADER}" \
  -H "Content-Type: application/json" \
  -d '{"ledger_id":"gate-alpha","name":"gate-alpha","namespace":"gate-alpha"}'

# Restore strict deny
flyctl secrets set -a <backend-app> LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny
```

Smoke checks:

```bash
# Unknown ledger should now deny
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "${FLY_BACKEND_URL}/enrich" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: chat-missing" \
  -d '{"entity":"chat-missing","role":"user","content":"ping","kind":"text","metadata":{}}'

# Known ledger owner should pass
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "${FLY_BACKEND_URL}/api/ingest" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: chat-acme" \
  -H "x-principal-id: ops" \
  -H "x-principal-type: admin" \
  -d '{"entity":"chat-acme","session_id":"strict-check","turn_id":"t1","raw_text":"ping","kind":"text","metadata":{}}'

# Non-owner/non-tenant should deny (force non-admin principal type)
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "${FLY_BACKEND_URL}/api/ingest" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: chat-acme" \
  -H "x-principal-id: outsider" \
  -H "x-principal-type: user" \
  -d '{"entity":"chat-acme","session_id":"strict-check","turn_id":"t2","raw_text":"ping","kind":"text","metadata":{}}'
```

Note:
- default `LEDGER_AUTHZ_ADMIN_PRINCIPAL_TYPES` includes `admin,service`; use `x-principal-type: user` for explicit deny probes.

### Stage E: Context Contract Enforcement

Backend env:
- `LEDGER_CONTEXT_ID_MODE=enforce`

Smoke checks:

```bash
# Missing context_id should now fail for write paths
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "${FLY_BACKEND_URL}/chat" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: chat-acme" \
  -d '{"session_id":"ctx-check","entity":"chat-acme","ledger_id":"chat-acme","message":"ping","provider":"openai","history":[]}'

# Header context_id should satisfy enforce mode
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "${FLY_BACKEND_URL}/enrich" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: chat-acme" \
  -H "x-context-id: ctx:frontend:vercel" \
  -d '{"entity":"chat-acme","ledger_id":"chat-acme","role":"user","content":"ping","kind":"text","metadata":{}}'
```

### Stage F: Strict Scope + Context Binding + Canonical Namespace

Backend env:
- `LEDGER_SCOPE_STRICT=true`
- `LEDGER_CONTEXT_BINDING_MODE=enforce`
- `LEDGER_NAMESPACE_SOURCE=ledger_id`

Smoke checks:

```bash
# Scope mismatch should fail deterministically
curl -sS -X POST "${FLY_BACKEND_URL}/web4/decode" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: chat-team-b" \
  -d '{"coordinate":"chat-team-a:WX-1","ledger_id":"chat-team-a"}'

# Disallowed context should fail with context_not_allowed once ledger binding is configured
curl -sS -X POST "${FLY_BACKEND_URL}/ledger/feedback/chat-acme:WX-1" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: chat-acme" \
  -H "x-principal-id: <owner-or-tenant>" \
  -H "x-principal-type: user" \
  -H "x-context-id: ctx:decoder" \
  -d '{"actor_id":"human:ops","actor_type":"human","rating":3,"reason":"check","source":"smoke"}'
```

### Hard Reset + Post-Reset Acceptance (Scripted)

From backend repo (`ds-backend-local`):

```bash
export BACKEND_URL="${FLY_BACKEND_URL}"
export BACKEND_ADMIN_TOKEN="${BACKEND_ADMIN_TOKEN}"
export APP="<fly-backend-app>"
export DEMO_TENANT_ID="tenant:demo"
export DEMO_LEDGER_ID="chat-demo"
export DEMO_OWNER_ID="demo-user"
export ALLOWED_CONTEXT_IDS="ctx:frontend:vercel,ctx:frontend:local,ctx:decoder,ctx:openclaw,ctx:chatgpt"

# 1) Archive + hard reset + baseline re-provision + strict flag deploy
./backend/utils/ref/scripts/hard_reset_demo_foundation.sh

# 2) Post-reset acceptance assertions
GOOD_CONTEXT_ID="ctx:frontend:vercel" \
BAD_CONTEXT_ID="ctx:unbound" \
./backend/utils/ref/scripts/post_reset_acceptance.sh
```

Rollback for any stage:
- reset to Stage A values and redeploy
- re-run baseline smoke checks in sections 2-7
- PR-7/8 rollback values:
  - `LEDGER_SCOPE_STRICT=false`
  - `LEDGER_CONTEXT_ID_MODE=compat`
  - `LEDGER_CONTEXT_BINDING_MODE=compat`
  - `LEDGER_NAMESPACE_SOURCE=entity_compat`

## 2) Fly Backend Deploy

From backend repo (`ds-backend-local`):

```bash
# Verify fly target/app
flyctl status

# Deploy
flyctl deploy
```

Backend smoke checks:

```bash
# Health must include git_sha
curl -sS "${FLY_BACKEND_URL}/health"

# Sync v0 availability
curl -sS -X POST "${FLY_BACKEND_URL}/sync/v0/handshake" \
  -H "Content-Type: application/json" \
  -d '{}'

curl -sS "${FLY_BACKEND_URL}/sync/v0/status"
```

If your deployment enforces auth on status/sync:

```bash
curl -sS "${FLY_BACKEND_URL}/sync/v0/status" -H "${AUTH_ADMIN_HEADER}"
```

## 3) Fly Middleware Deploy

From middleware repo (`ds-middleware-local`):

```bash
# Verify fly target/app
flyctl status

# Deploy
flyctl deploy
```

Middleware env values to verify on Fly:
- `DUALSUBSTRATE_API=${FLY_BACKEND_URL}`
- `MIDDLEWARE_CORS_ORIGINS=${VERCEL_FRONTEND_URL}`
- `OPENAI_COMPAT_USE_PIPELINE=1`
- `OPENAI_COMPAT_PIPELINE_ENGINE=middleware`

Middleware smoke checks:

```bash
curl -N -sS -X POST "${FLY_MIDDLEWARE_URL}/api/chat/smart_stream" \
  -H "Content-Type: application/json" \
  -d '{"message":"migration smoke","provider":"llama3.2:latest","session_id":"migration-smoke","enable_ledger":true,"history":[]}'
```

## 4) Vercel Frontend Deploy

From frontend repo (`ds-frontend-local`):

```bash
vercel pull --yes --environment=production
vercel deploy --prod
```

Frontend env value to verify in Vercel:
- `VITE_MIDDLEWARE_BASE_URL=${FLY_MIDDLEWARE_URL}`

Quick UI validation:
- load app
- send one chat turn
- confirm response streams
- confirm no direct backend URL usage in browser network tab

## 5) Security Gate Commands

Check CORS behavior from allowed origin:

```bash
curl -sSI "${FLY_BACKEND_URL}/health" \
  -H "Origin: ${FLY_MIDDLEWARE_URL}" | rg -i "access-control-allow-origin|access-control-allow-credentials"
```

Check CORS behavior from disallowed origin (should not allow wildcard/open origin):

```bash
curl -sSI "${FLY_BACKEND_URL}/health" \
  -H "Origin: https://evil.example" | rg -i "access-control-allow-origin|access-control-allow-credentials"
```

Run a concrete CORS matrix (allowed exact, allowed preview via regex, denied origin):

```bash
for ORIGIN in \
  "https://ds-frontend-local.vercel.app" \
  "https://ds-frontend-local-new.vercel.app" \
  "https://ds-middleware-local.vercel.app" \
  "https://ds-frontend-local-git-main-preview.vercel.app" \
  "https://evil.example"
do
  CODE=$(curl -sS -o /dev/null -w "%{http_code}" -X OPTIONS "${FLY_BACKEND_URL}/sync/v0/status" \
    -H "Origin: ${ORIGIN}" -H "Access-Control-Request-Method: GET")
  ALLOW=$(curl -sSI -X OPTIONS "${FLY_BACKEND_URL}/sync/v0/status" \
    -H "Origin: ${ORIGIN}" -H "Access-Control-Request-Method: GET" \
    | awk -F': ' 'BEGIN{IGNORECASE=1}/^access-control-allow-origin:/{print $2}' | tr -d '\r')
  echo "origin=${ORIGIN} code=${CODE} allow_origin=${ALLOW:-<none>}"
done
```

If auth is expected between middleware and backend, verify unauthenticated requests fail:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" "${FLY_BACKEND_URL}/sync/v0/status"
```

Middleware ledger switch operator note (legacy runtime):
- `POST /api/ledgers` may behave read-only in some runtime paths and return the same payload as `GET /api/ledgers`.
- For strict auth drills, use deterministic process defaults instead of session switching:
  - `DEFAULT_LEDGER_ID=<ledger>`
  - `DUALSUBSTRATE_LEDGER=<ledger>`

## 6) Data Plane Gate Commands

Sync status and checkpoint visibility:

```bash
curl -sS "${FLY_BACKEND_URL}/sync/v0/status"
```

Checkpoint save/load smoke (adjust payload fields if your schema differs):

```bash
curl -sS -X POST "${FLY_BACKEND_URL}/sync/v0/checkpoint/save" \
  -H "Content-Type: application/json" \
  -d '{"peer_id":"migration-check","ledger_id_h64":"0000000000000000","cursor":{"seq":1}}'

curl -sS -X POST "${FLY_BACKEND_URL}/sync/v0/checkpoint/load" \
  -H "Content-Type: application/json" \
  -d '{"peer_id":"migration-check","ledger_id_h64":"0000000000000000"}'
```

## 7) Cutover Readiness Quick Checks

Run and record:

```bash
# Backend health
curl -sS "${FLY_BACKEND_URL}/health"

# Middleware stream smoke
curl -sS -o /dev/null -w "%{http_code}\n" \
  -X POST "${FLY_MIDDLEWARE_URL}/api/chat/smart_stream" \
  -H "Content-Type: application/json" \
  -d '{"message":"readiness check","provider":"llama3.2:latest","session_id":"readiness","enable_ledger":true,"history":[]}'

# Stats/global snapshot from backend (if exposed)
curl -sS "${FLY_BACKEND_URL}/stats/global"
```

## 8) Rollback Commands (Fast Path)

Use Fly (backend+middleware) and Vercel (frontend) to revert to last known-good release.

```bash
# Fly: rollback backend to previous release
flyctl releases
flyctl releases rollback
```

```bash
# Fly: rollback middleware to previous release
flyctl releases
flyctl releases rollback
```

```bash
# Vercel: frontend rollback (interactive selection)
vercel ls
vercel rollback
```

After rollback:

```bash
curl -sS "${FLY_BACKEND_URL}/health"
curl -sS -o /dev/null -w "%{http_code}\n" "${FLY_MIDDLEWARE_URL}"
```

## 9) Variable Mapping (From Existing Templates)

Backend template source: `backend/utils/ref/fly-backend.env.example`
- `FASTAPI_ROOT` -> `FLY_BACKEND_URL`
- `MIDDLEWARE_CORS_ORIGINS` -> include both middleware + frontend Vercel domains

Middleware template source: `backend/utils/ref/vercel-middleware.env.example`
- `DUALSUBSTRATE_API` -> `FLY_BACKEND_URL`
- `MIDDLEWARE_CORS_ORIGINS` -> `VERCEL_FRONTEND_URL`

Frontend template source: `backend/utils/ref/vercel-frontend.env.example`
- `VITE_MIDDLEWARE_BASE_URL` -> `FLY_MIDDLEWARE_URL`
