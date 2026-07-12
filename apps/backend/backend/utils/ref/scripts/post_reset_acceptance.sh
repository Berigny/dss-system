#!/usr/bin/env bash
set -euo pipefail

# Post-reset acceptance checks for strict ledger/context model.
# Verifies:
# - known ledger write/read/feedback success
# - unknown ledger deny
# - scope mismatch reject
# - disallowed context reject

BACKEND_URL="${BACKEND_URL:-${FLY_BACKEND_URL:-}}"
BACKEND_ADMIN_TOKEN="${BACKEND_ADMIN_TOKEN:-${ADMIN_TOKEN:-}}"
DEMO_LEDGER_ID="${DEMO_LEDGER_ID:-chat-demo}"
DEMO_OWNER_ID="${DEMO_OWNER_ID:-demo-user}"
DEMO_OWNER_TYPE="${DEMO_OWNER_TYPE:-user}"
GOOD_CONTEXT_ID="${GOOD_CONTEXT_ID:-ctx:frontend:vercel}"
BAD_CONTEXT_ID="${BAD_CONTEXT_ID:-ctx:unbound}"
UNKNOWN_LEDGER_ID="${UNKNOWN_LEDGER_ID:-chat-missing}"

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

http_with_retry() {
  local method="$1"
  local url="$2"
  local out_file="$3"
  local attempts="${4:-6}"
  local sleep_secs="${5:-2}"
  local status=""
  local i=1
  while [[ "$i" -le "$attempts" ]]; do
    status="$(curl -sS -o "$out_file" -w "%{http_code}" -X "$method" "$url" \
      -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}")"
    if [[ "$status" == "200" ]]; then
      echo "$status"
      return 0
    fi
    if [[ "$status" != "502" && "$status" != "503" && "$status" != "504" ]]; then
      echo "$status"
      return 0
    fi
    sleep "$sleep_secs"
    i=$((i + 1))
  done
  echo "$status"
}

extract_json_field() {
  local file="$1"
  local field="$2"
  python3 - "$file" "$field" <<'PY'
import json
import sys

path, field = sys.argv[1:]
with open(path, "r", encoding="utf-8") as f:
    payload = json.load(f)

value = payload
for part in field.split("."):
    if isinstance(value, dict):
        value = value.get(part)
    else:
        value = None
        break

if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(str(value))
PY
}

assert_status() {
  local got="$1"
  local expected="$2"
  local label="$3"
  local body_file="${4:-}"
  if [[ "$got" != "$expected" ]]; then
    echo "FAIL: ${label} (expected ${expected}, got ${got})" >&2
    if [[ -n "$body_file" && -f "$body_file" ]]; then
      echo "Response body:" >&2
      cat "$body_file" >&2
      echo >&2
    fi
    return 1
  fi
  echo "PASS: ${label} -> ${got}"
}

require_cmd curl
require_cmd python3
require_env BACKEND_URL "$BACKEND_URL"
require_env BACKEND_ADMIN_TOKEN "$BACKEND_ADMIN_TOKEN"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "[1/7] Health check"
health_code="$(curl -sS -o "$tmpdir/health.json" -w "%{http_code}" "${BACKEND_URL}/health")"
assert_status "$health_code" "200" "GET /health"

echo "[2/7] Pre-write audit snapshot"
audit_code="$(http_with_retry "GET" "${BACKEND_URL}/admin/history/audit?limit=1000&coord_limit=20" "$tmpdir/audit-pre.json" 8 3)"
assert_status "$audit_code" "200" "GET /admin/history/audit"

echo "[2.5/7] Verify target ledger exists in registry"
ledger_list_code="$(curl -sS -o "$tmpdir/ledgers.json" -w "%{http_code}" "${BACKEND_URL}/admin/ledgers" -H "x-admin-token: ${BACKEND_ADMIN_TOKEN}")"
assert_status "$ledger_list_code" "200" "GET /admin/ledgers" "$tmpdir/ledgers.json"
if ! python3 - "$tmpdir/ledgers.json" "$DEMO_LEDGER_ID" <<'PY'
import json
import sys

path, ledger_id = sys.argv[1:]
with open(path, "r", encoding="utf-8") as f:
    payload = json.load(f)
ledgers = payload.get("ledgers") or []
if ledger_id in ledgers:
    raise SystemExit(0)
raise SystemExit(1)
PY
then
  echo "FAIL: target ledger '${DEMO_LEDGER_ID}' not found in /admin/ledgers; provisioning step likely failed." >&2
  cat "$tmpdir/ledgers.json" >&2
  echo >&2
  exit 1
fi

echo "[3/7] Known ledger write succeeds"
cat > "$tmpdir/known-ingest.json" <<JSON
{"entity":"${DEMO_LEDGER_ID}","ledger_id":"${DEMO_LEDGER_ID}","context_id":"${GOOD_CONTEXT_ID}","session_id":"reset-acceptance","turn_id":"t1","raw_text":"post reset known ledger write","kind":"text","metadata":{"provider":"smoke","model":"smoke"}}
JSON
known_status="$(curl -sS -o "$tmpdir/known-ingest-response.json" -w "%{http_code}" -X POST "${BACKEND_URL}/api/ingest" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: ${DEMO_LEDGER_ID}" \
  -H "x-context-id: ${GOOD_CONTEXT_ID}" \
  -H "x-principal-id: ${DEMO_OWNER_ID}" \
  -H "x-principal-type: ${DEMO_OWNER_TYPE}" \
  --data-binary "@$tmpdir/known-ingest.json")"
assert_status "$known_status" "200" "POST /api/ingest known ledger" "$tmpdir/known-ingest-response.json"
coord="$(extract_json_field "$tmpdir/known-ingest-response.json" "coordinate")"
if [[ -z "$coord" ]]; then
  echo "FAIL: known ingest response missing coordinate" >&2
  exit 1
fi
echo "PASS: coordinate=${coord}"

echo "[4/7] Known ledger feedback succeeds"
cat > "$tmpdir/feedback.json" <<JSON
{"actor_id":"human:${DEMO_OWNER_ID}","actor_type":"human","context_id":"${GOOD_CONTEXT_ID}","rating":3,"reason":"post-reset-check","source":"acceptance"}
JSON
feedback_status="$(curl -sS -o "$tmpdir/feedback-response.json" -w "%{http_code}" -X POST "${BACKEND_URL}/ledger/feedback/${coord}" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: ${DEMO_LEDGER_ID}" \
  -H "x-context-id: ${GOOD_CONTEXT_ID}" \
  -H "x-principal-id: ${DEMO_OWNER_ID}" \
  -H "x-principal-type: ${DEMO_OWNER_TYPE}" \
  --data-binary "@$tmpdir/feedback.json")"
assert_status "$feedback_status" "200" "POST /ledger/feedback known ledger" "$tmpdir/feedback-response.json"

echo "[5/7] Unknown ledger denied"
cat > "$tmpdir/unknown-ingest.json" <<JSON
{"entity":"${UNKNOWN_LEDGER_ID}","ledger_id":"${UNKNOWN_LEDGER_ID}","context_id":"${GOOD_CONTEXT_ID}","session_id":"reset-acceptance","turn_id":"t2","raw_text":"unknown ledger probe","kind":"text","metadata":{}}
JSON
unknown_status="$(curl -sS -o "$tmpdir/unknown-response.json" -w "%{http_code}" -X POST "${BACKEND_URL}/api/ingest" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: ${UNKNOWN_LEDGER_ID}" \
  -H "x-context-id: ${GOOD_CONTEXT_ID}" \
  -H "x-principal-id: outsider" \
  -H "x-principal-type: user" \
  --data-binary "@$tmpdir/unknown-ingest.json")"
assert_status "$unknown_status" "403" "POST /api/ingest unknown ledger" "$tmpdir/unknown-response.json"
unknown_reason="$(extract_json_field "$tmpdir/unknown-response.json" "detail.reason")"
if [[ "$unknown_reason" != "unknown_ledger" ]]; then
  echo "FAIL: expected reason unknown_ledger, got ${unknown_reason}" >&2
  exit 1
fi
echo "PASS: unknown ledger reason=${unknown_reason}"

echo "[6/7] Scope mismatch rejected"
cat > "$tmpdir/decode-mismatch.json" <<JSON
{"coordinate":"${DEMO_LEDGER_ID}:WX-1","ledger_id":"${UNKNOWN_LEDGER_ID}"}
JSON
mismatch_status="$(curl -sS -o "$tmpdir/decode-mismatch-response.json" -w "%{http_code}" -X POST "${BACKEND_URL}/web4/decode" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: ${UNKNOWN_LEDGER_ID}" \
  --data-binary "@$tmpdir/decode-mismatch.json")"
assert_status "$mismatch_status" "400" "POST /web4/decode scope mismatch" "$tmpdir/decode-mismatch-response.json"
mismatch_error="$(extract_json_field "$tmpdir/decode-mismatch-response.json" "detail.error")"
if [[ "$mismatch_error" != "ledger_scope_mismatch" ]]; then
  echo "FAIL: expected error ledger_scope_mismatch, got ${mismatch_error}" >&2
  exit 1
fi
echo "PASS: mismatch error=${mismatch_error}"

echo "[7/7] Disallowed context rejected"
cat > "$tmpdir/bad-context-ingest.json" <<JSON
{"entity":"${DEMO_LEDGER_ID}","ledger_id":"${DEMO_LEDGER_ID}","context_id":"${BAD_CONTEXT_ID}","session_id":"reset-acceptance","turn_id":"t3","raw_text":"bad context probe","kind":"text","metadata":{}}
JSON
bad_ctx_status="$(curl -sS -o "$tmpdir/bad-context-response.json" -w "%{http_code}" -X POST "${BACKEND_URL}/api/ingest" \
  -H "Content-Type: application/json" \
  -H "x-ledger-id: ${DEMO_LEDGER_ID}" \
  -H "x-context-id: ${BAD_CONTEXT_ID}" \
  -H "x-principal-id: ${DEMO_OWNER_ID}" \
  -H "x-principal-type: ${DEMO_OWNER_TYPE}" \
  --data-binary "@$tmpdir/bad-context-ingest.json")"
assert_status "$bad_ctx_status" "403" "POST /api/ingest disallowed context" "$tmpdir/bad-context-response.json"
bad_ctx_reason="$(extract_json_field "$tmpdir/bad-context-response.json" "detail.reason")"
if [[ "$bad_ctx_reason" != "context_not_allowed" ]]; then
  echo "FAIL: expected reason context_not_allowed, got ${bad_ctx_reason}" >&2
  exit 1
fi
echo "PASS: disallowed context reason=${bad_ctx_reason}"

echo "Acceptance checks passed."
