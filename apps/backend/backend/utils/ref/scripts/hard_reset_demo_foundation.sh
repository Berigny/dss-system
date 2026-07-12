#!/usr/bin/env bash
set -euo pipefail

# Hard reset + baseline demo reprovisioning for ds-backend-local.
# Requires:
# - flyctl authenticated
# - make targets available from repo root
# - BACKEND_URL and BACKEND_ADMIN_TOKEN exported

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
ARTIFACT_ROOT="${ROOT_DIR}/backend/utils/ref/artifacts/reset"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_DIR="${ARTIFACT_ROOT}/${TS}"

BACKEND_URL="${BACKEND_URL:-${FLY_BACKEND_URL:-}}"
BACKEND_ADMIN_TOKEN="${BACKEND_ADMIN_TOKEN:-${ADMIN_TOKEN:-}}"
APP="${APP:-ds-backend-new}"
REGION="${REGION:-syd}"
VOLUME="${VOLUME:-ledger_volume}"
VOLUME_ID="${VOLUME_ID:-}"

DEMO_TENANT_ID="${DEMO_TENANT_ID:-tenant:demo}"
DEMO_LEDGER_ID="${DEMO_LEDGER_ID:-chat-demo}"
DEMO_OWNER_ID="${DEMO_OWNER_ID:-demo-user}"
DEMO_OWNER_TYPE="${DEMO_OWNER_TYPE:-user}"
DEMO_POLICY_PROFILE="${DEMO_POLICY_PROFILE:-standard}"

# CSV lists to keep script portable.
ALLOWED_CONTEXT_IDS="${ALLOWED_CONTEXT_IDS:-ctx:frontend:vercel,ctx:frontend:local,ctx:decoder,ctx:openclaw,ctx:chatgpt}"
CONTRIBUTOR_IDS="${CONTRIBUTOR_IDS:-user:${DEMO_OWNER_ID},model:openrouter:auto,model:ollama:llama3.2,agent:openclaw,service:openrouter,service:ollama,service:openai}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_env() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "Missing required environment variable: $name" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd make
require_cmd flyctl
require_cmd python3
require_cmd git
require_env BACKEND_URL "$BACKEND_URL"
require_env BACKEND_ADMIN_TOKEN "$BACKEND_ADMIN_TOKEN"

mkdir -p "$ARCHIVE_DIR"

echo "[1/6] Capturing pre-reset archive into ${ARCHIVE_DIR}"
curl -fsS "${BACKEND_URL}/admin/history/audit?limit=1000&coord_limit=20" \
  -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}" > "${ARCHIVE_DIR}/admin-history-audit.json"
curl -fsS "${BACKEND_URL}/admin/ledgers" \
  -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}" > "${ARCHIVE_DIR}/admin-ledgers.json"
curl -fsS "${BACKEND_URL}/admin/tenants" \
  -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}" > "${ARCHIVE_DIR}/admin-tenants.json"
curl -fsS "${BACKEND_URL}/health" > "${ARCHIVE_DIR}/health-pre-reset.json"

{
  echo "timestamp_utc=${TS}"
  echo "backend_url=${BACKEND_URL}"
  echo "app=${APP}"
  echo "region=${REGION}"
  echo "volume=${VOLUME}"
  echo "volume_id=${VOLUME_ID}"
  echo "git_sha=$(git -C "${ROOT_DIR}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "git_branch=$(git -C "${ROOT_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
} > "${ARCHIVE_DIR}/snapshot.env"

if flyctl releases list -a "${APP}" > "${ARCHIVE_DIR}/fly-releases-pre-reset.txt" 2>/dev/null; then
  true
else
  flyctl releases -a "${APP}" > "${ARCHIVE_DIR}/fly-releases-pre-reset.txt" || true
fi
flyctl volumes list -a "${APP}" > "${ARCHIVE_DIR}/fly-volumes-pre-reset.txt" || true

echo "[2/6] Executing destructive hard reset via Make target"
if [[ -n "${VOLUME_ID}" ]]; then
  make -C "${ROOT_DIR}" fly-hard-reset CONFIRM=1 APP="${APP}" REGION="${REGION}" VOLUME="${VOLUME}" VOLUME_ID="${VOLUME_ID}"
else
  make -C "${ROOT_DIR}" fly-hard-reset CONFIRM=1 APP="${APP}" REGION="${REGION}" VOLUME="${VOLUME}"
fi

echo "[3/6] Waiting for backend health"
for i in {1..30}; do
  code="$(curl -sS -o /dev/null -w "%{http_code}" "${BACKEND_URL}/health" || true)"
  if [[ "$code" == "200" ]]; then
    break
  fi
  sleep 2
done
curl -fsS "${BACKEND_URL}/health" > "${ARCHIVE_DIR}/health-post-reset.json"

echo "[4/6] Re-provision tenant and baseline ledger"
tenant_payload="${ARCHIVE_DIR}/tenant-provision-payload.json"
ledger_payload="${ARCHIVE_DIR}/ledger-provision-payload.json"
allowed_contexts_json="$(python3 - <<'PY' "$ALLOWED_CONTEXT_IDS"
import json
import sys
items = [s.strip() for s in (sys.argv[1] if len(sys.argv) > 1 else "").split(",") if s.strip()]
print(json.dumps(items))
PY
)"
contributors_json="$(python3 - <<'PY' "$CONTRIBUTOR_IDS"
import json
import sys
items = [s.strip() for s in (sys.argv[1] if len(sys.argv) > 1 else "").split(",") if s.strip()]
print(json.dumps(items))
PY
)"

cat > "${tenant_payload}" <<JSON
{
  "tenant_id": "${DEMO_TENANT_ID}",
  "owner_principal_id": "${DEMO_OWNER_ID}",
  "owner_principal_type": "${DEMO_OWNER_TYPE}",
  "policy_profile": "${DEMO_POLICY_PROFILE}",
  "ledger_ids": ["${DEMO_LEDGER_ID}"],
  "metadata": {
    "allowed_context_ids": ${allowed_contexts_json},
    "contributor_ids": ${contributors_json},
    "services": ["openrouter","ollama","openai"],
    "provisioning": {
      "seed": "hard_reset_demo_foundation",
      "model": "ledger_has_contributors_and_contexts"
    }
  }
}
JSON

cat > "${ledger_payload}" <<JSON
{
  "name": "${DEMO_LEDGER_ID}",
  "namespace": "${DEMO_LEDGER_ID}",
  "tenant_id": "${DEMO_TENANT_ID}",
  "owner_principal_id": "${DEMO_OWNER_ID}",
  "owner_principal_type": "${DEMO_OWNER_TYPE}",
  "policy_profile": "${DEMO_POLICY_PROFILE}",
  "metadata": {
    "allowed_context_ids": ${allowed_contexts_json},
    "contributor_ids": ${contributors_json},
    "services": ["openrouter","ollama","openai"],
    "provisioning": {
      "seed": "hard_reset_demo_foundation",
      "model": "ledger_has_contributors_and_contexts"
    }
  }
}
JSON

tenant_status=""
for attempt in {1..8}; do
  tenant_status="$(curl -sS -o "${ARCHIVE_DIR}/provisioning-tenant-response.json" -w "%{http_code}" \
    -X POST "${BACKEND_URL}/admin/tenants" \
    -H "Content-Type: application/json" \
    -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}" \
    -H "x-principal-id: ${DEMO_OWNER_ID}" \
    -H "x-principal-type: admin" \
    -H "x-tenant-id: ${DEMO_TENANT_ID}" \
    --data-binary @"${tenant_payload}")"
  if [[ "${tenant_status}" == "200" ]]; then
    break
  fi
  sleep 3
done
if [[ "${tenant_status}" != "200" ]]; then
  echo "Tenant provisioning failed with status ${tenant_status}" >&2
  cat "${ARCHIVE_DIR}/provisioning-tenant-response.json" >&2 || true
  exit 1
fi

ledger_status="$(curl -sS -o "${ARCHIVE_DIR}/provisioning-ledger-response.json" -w "%{http_code}" \
  -X POST "${BACKEND_URL}/admin/ledgers" \
  -H "Content-Type: application/json" \
  -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}" \
  -H "x-principal-id: ${DEMO_OWNER_ID}" \
  -H "x-principal-type: admin" \
  -H "x-tenant-id: ${DEMO_TENANT_ID}" \
  --data-binary @"${ledger_payload}")"
if [[ "${ledger_status}" != "200" ]]; then
  echo "Ledger provisioning failed with status ${ledger_status}" >&2
  cat "${ARCHIVE_DIR}/provisioning-ledger-response.json" >&2 || true
  exit 1
fi

curl -fsS "${BACKEND_URL}/admin/tenants" \
  -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}" > "${ARCHIVE_DIR}/post-provision-admin-tenants.json"
curl -fsS "${BACKEND_URL}/admin/ledgers" \
  -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}" > "${ARCHIVE_DIR}/post-provision-admin-ledgers.json"

echo "[5/6] Enabling strict runtime policy flags"
flyctl secrets set -a "${APP}" \
  LEDGER_AUTHZ_MODE=registry \
  LEDGER_CONTEXT_MODE=enforce \
  LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY=deny \
  LEDGER_SCOPE_STRICT=true \
  LEDGER_CONTEXT_ID_MODE=enforce \
  LEDGER_CONTEXT_BINDING_MODE=enforce \
  LEDGER_NAMESPACE_SOURCE=ledger_id
flyctl deploy -a "${APP}"

echo "[6/6] Writing run manifest"
cat > "${ARCHIVE_DIR}/manifest.txt" <<MANIFEST
timestamp_utc=${TS}
backend_url=${BACKEND_URL}
app=${APP}
tenant_id=${DEMO_TENANT_ID}
ledger_id=${DEMO_LEDGER_ID}
owner_principal_id=${DEMO_OWNER_ID}
allowed_context_ids=${ALLOWED_CONTEXT_IDS}
contributor_ids=${CONTRIBUTOR_IDS}
MANIFEST

echo "Hard reset + reprovision completed."
echo "Archive: ${ARCHIVE_DIR}"
echo "Next: run backend/utils/ref/scripts/post_reset_acceptance.sh with matching env vars."
