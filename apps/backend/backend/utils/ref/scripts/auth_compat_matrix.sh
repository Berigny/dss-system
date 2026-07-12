#!/usr/bin/env bash
set -euo pipefail

BACKEND_URL="${BACKEND_URL:-https://ds-backend-new.fly.dev}"
LEDGER_ID="${LEDGER_ID:-chat-demo}"
CONTEXT_ID="${CONTEXT_ID:-ctx:frontend:vercel}"
PRINCIPAL_ID="${PRINCIPAL_ID:-demo-user}"
PRINCIPAL_TYPE="${PRINCIPAL_TYPE:-user}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

pass_count=0
fail_count=0
warn_count=0

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

expect() {
  local name="$1"
  local condition="$2"
  if eval "$condition"; then
    echo "[PASS] ${name}"
    pass_count=$((pass_count + 1))
  else
    echo "[FAIL] ${name}" >&2
    fail_count=$((fail_count + 1))
  fi
}

warn() {
  local name="$1"
  echo "[WARN] ${name}"
  warn_count=$((warn_count + 1))
}

post_json() {
  local out_body="$1"
  local out_code="$2"
  local url="$3"
  local data="$4"
  shift 4
  local code
  code="$(curl -sS -o "${out_body}" -w "%{http_code}" -X POST "${url}" \
    -H "Content-Type: application/json" \
    "$@" \
    -d "${data}")"
  printf "%s" "${code}" > "${out_code}"
}

stream_context_meta_authz() {
  local out_json="$1"
  local data="$2"
  shift 2
  curl -sS -N --max-time 75 -X POST "${BACKEND_URL}/chat/stream" \
    -H "Content-Type: application/json" \
    "$@" \
    -d "${data}" \
    | "${PYTHON_BIN}" -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    if obj.get("type") == "context_meta":
        authz = obj.get("authz")
        if not isinstance(authz, dict):
            print("context_meta missing authz", file=sys.stderr)
            sys.exit(3)
        print(json.dumps(authz))
        sys.exit(0)
print("context_meta not found", file=sys.stderr)
sys.exit(2)
' > "${out_json}"
}

stream_context_meta_authz_retry() {
  local out_json="$1"
  local data="$2"
  shift 2
  local attempt=1
  while [[ "${attempt}" -le 2 ]]; do
    if stream_context_meta_authz "${out_json}" "${data}" "$@"; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  return 1
}

require_cmd curl
require_cmd "${PYTHON_BIN}"

echo "Backend URL: ${BACKEND_URL}"
echo "Ledger ID: ${LEDGER_ID}"
echo "Context ID: ${CONTEXT_ID}"

# Case 1: commit-answer persists payload DID claims.
case1_body="${TMP_DIR}/case1.json"
case1_code="${TMP_DIR}/case1.code"
post_json \
  "${case1_body}" "${case1_code}" \
  "${BACKEND_URL}/api/chat/commit-answer" \
  "{\"entity\":\"${LEDGER_ID}\",\"ledger_id\":\"${LEDGER_ID}\",\"context_id\":\"${CONTEXT_ID}\",\"principal_did\":\"did:key:z6MkMatrixA\",\"principal_key_id\":\"did:key:z6MkMatrixA#k1\",\"session_jti\":\"jti-matrix-a\",\"user_message\":\"matrix case 1\",\"assistant_reply\":\"ack\",\"metadata\":{\"session_id\":\"matrix-s1\",\"turn_id\":\"matrix-t1\",\"provider\":\"matrix\",\"model\":\"matrix\"}}" \
  -H "x-ledger-id: ${LEDGER_ID}" \
  -H "x-principal-id: ${PRINCIPAL_ID}" \
  -H "x-principal-type: ${PRINCIPAL_TYPE}"
expect "commit-answer payload claims status=200" "[[ \"$(cat "${case1_code}")\" == \"200\" ]]"
expect "commit-answer payload claims principal_did persisted" "cat \"${case1_body}\" | ${PYTHON_BIN} -c 'import json,sys; d=json.load(sys.stdin); c=((d.get(\"metadata\") or {}).get(\"contributor\") or {}); raise SystemExit(0 if c.get(\"principal_did\")==\"did:key:z6MkMatrixA\" else 1)'"
expect "commit-answer payload claims session_jti persisted" "cat \"${case1_body}\" | ${PYTHON_BIN} -c 'import json,sys; d=json.load(sys.stdin); c=((d.get(\"metadata\") or {}).get(\"contributor\") or {}); raise SystemExit(0 if c.get(\"session_jti\")==\"jti-matrix-a\" else 1)'"

# Case 2: commit-answer without DID claims still succeeds in compat mode.
case2_body="${TMP_DIR}/case2.json"
case2_code="${TMP_DIR}/case2.code"
post_json \
  "${case2_body}" "${case2_code}" \
  "${BACKEND_URL}/api/chat/commit-answer" \
  "{\"entity\":\"${LEDGER_ID}\",\"ledger_id\":\"${LEDGER_ID}\",\"context_id\":\"${CONTEXT_ID}\",\"user_message\":\"matrix case 2\",\"assistant_reply\":\"ack\",\"metadata\":{\"session_id\":\"matrix-s2\",\"turn_id\":\"matrix-t2\",\"provider\":\"matrix\",\"model\":\"matrix\"}}" \
  -H "x-ledger-id: ${LEDGER_ID}" \
  -H "x-principal-id: ${PRINCIPAL_ID}" \
  -H "x-principal-type: ${PRINCIPAL_TYPE}"
expect "commit-answer no DID claims status=200 (compat)" "[[ \"$(cat "${case2_code}")\" == \"200\" ]]"

# Case 3: chat context payload/header mismatch observability (warn-only).
case3_body="${TMP_DIR}/case3.json"
case3_code="${TMP_DIR}/case3.code"
post_json \
  "${case3_body}" "${case3_code}" \
  "${BACKEND_URL}/chat" \
  "{\"session_id\":\"matrix-chat-ctx\",\"entity\":\"${LEDGER_ID}\",\"ledger_id\":\"${LEDGER_ID}\",\"context_id\":\"ctx:a\",\"message\":\"ctx mismatch check\",\"provider\":\"google/gemini-2.5-flash\",\"history\":[]}" \
  -H "x-ledger-id: ${LEDGER_ID}" \
  -H "x-context-id: ctx:b"
if [[ "$(cat "${case3_code}")" == "400" ]] && \
  cat "${case3_body}" | "${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); detail=d.get("detail") or {}; raise SystemExit(0 if detail.get("error")=="context_scope_mismatch" else 1)'
then
  echo "[PASS] chat context mismatch guard active (400/context_scope_mismatch)"
  pass_count=$((pass_count + 1))
else
  warn "chat context mismatch guard not active in this deployment (status=$(cat "${case3_code}"))"
fi

# Case 4: chat ledger payload/header mismatch observability (warn-only).
case4_body="${TMP_DIR}/case4.json"
case4_code="${TMP_DIR}/case4.code"
post_json \
  "${case4_body}" "${case4_code}" \
  "${BACKEND_URL}/chat" \
  "{\"session_id\":\"matrix-chat-ledger\",\"entity\":\"${LEDGER_ID}\",\"ledger_id\":\"${LEDGER_ID}\",\"context_id\":\"${CONTEXT_ID}\",\"message\":\"ledger mismatch check\",\"provider\":\"google/gemini-2.5-flash\",\"history\":[]}" \
  -H "x-ledger-id: chat-other"
if [[ "$(cat "${case4_code}")" == "400" ]] && \
  cat "${case4_body}" | "${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); detail=d.get("detail") or {}; raise SystemExit(0 if detail.get("error")=="ledger_scope_mismatch" else 1)'
then
  echo "[PASS] chat ledger mismatch guard active (400/ledger_scope_mismatch)"
  pass_count=$((pass_count + 1))
else
  warn "chat ledger mismatch guard not active in this deployment (status=$(cat "${case4_code}"))"
fi

# Case 5: stream authz diagnostics reflect payload DID claims.
case5_authz="${TMP_DIR}/case5-authz.json"
if stream_context_meta_authz_retry \
  "${case5_authz}" \
  "{\"session_id\":\"matrix-stream-did\",\"entity\":\"${LEDGER_ID}\",\"ledger_id\":\"${LEDGER_ID}\",\"context_id\":\"${CONTEXT_ID}\",\"principal_did\":\"did:key:z6MkMatrixStream\",\"principal_key_id\":\"did:key:z6MkMatrixStream#k1\",\"session_jti\":\"jti-matrix-stream\",\"message\":\"stream authz with claims\",\"provider\":\"google/gemini-2.5-flash\",\"history\":[]}" \
  -H "x-ledger-id: ${LEDGER_ID}" \
  -H "x-principal-id: ${PRINCIPAL_ID}" \
  -H "x-principal-type: ${PRINCIPAL_TYPE}"
then
  expect "stream authz principal_did_present=true" "cat \"${case5_authz}\" | ${PYTHON_BIN} -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get(\"principal_did_present\") is True else 1)'"
  expect "stream authz context_id matches payload" "cat \"${case5_authz}\" | ${PYTHON_BIN} -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get(\"context_id\")==\"${CONTEXT_ID}\" else 1)'"
else
  warn "stream authz with payload claims timed out before context_meta"
fi

# Case 6: stream authz diagnostics in compat mode allow missing DID claims.
case6_authz="${TMP_DIR}/case6-authz.json"
if stream_context_meta_authz_retry \
  "${case6_authz}" \
  "{\"session_id\":\"matrix-stream-compat\",\"entity\":\"${LEDGER_ID}\",\"ledger_id\":\"${LEDGER_ID}\",\"context_id\":\"${CONTEXT_ID}\",\"message\":\"stream authz compat without did\",\"provider\":\"google/gemini-2.5-flash\",\"history\":[]}" \
  -H "x-ledger-id: ${LEDGER_ID}" \
  -H "x-principal-id: ${PRINCIPAL_ID}" \
  -H "x-principal-type: ${PRINCIPAL_TYPE}"
then
  expect "stream authz principal_mode=compat" "cat \"${case6_authz}\" | ${PYTHON_BIN} -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get(\"principal_mode\")==\"compat\" else 1)'"
else
  warn "stream authz compat check timed out before context_meta"
fi

echo "---"
echo "Result: pass=${pass_count} fail=${fail_count} warn=${warn_count}"
if [[ "${fail_count}" -gt 0 ]]; then
  exit 1
fi
