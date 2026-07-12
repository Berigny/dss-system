#!/usr/bin/env bash
set -u

# Staging/local functional smoke script with pass/fail criteria.
# Validates:
# - health + sync status
# - CORS (allowed and denied origin behavior)
# - middleware ledger switch semantics
# - strict allow/deny ingest behavior through middleware
#
# Usage:
#   bash backend/utils/ref/test-local-remote-gate.sh
#   bash backend/utils/ref/test-local-remote-gate.sh --local-only
#   bash backend/utils/ref/test-local-remote-gate.sh --remote-only
#
# Optional env overrides:
#   LOCAL_BACKEND_URL=http://127.0.0.1:8080
#   LOCAL_MIDDLEWARE_URL=http://127.0.0.1:5001
#   LOCAL_FRONTEND_ORIGIN=http://localhost:3000
#   REMOTE_BACKEND_URL=https://ds-backend-new.fly.dev
#   REMOTE_MIDDLEWARE_URL=https://ds-middleware-new.fly.dev
#   REMOTE_FRONTEND_ORIGIN=https://ds-frontend-local-new.vercel.app
#   KNOWN_LEDGER=gate-alpha
#   UPLOAD_FILE=/etc/hosts

LOCAL_BACKEND_URL="${LOCAL_BACKEND_URL:-http://127.0.0.1:8080}"
LOCAL_MIDDLEWARE_URL="${LOCAL_MIDDLEWARE_URL:-http://127.0.0.1:5001}"
LOCAL_FRONTEND_ORIGIN="${LOCAL_FRONTEND_ORIGIN:-http://localhost:3000}"

REMOTE_BACKEND_URL="${REMOTE_BACKEND_URL:-https://ds-backend-new.fly.dev}"
REMOTE_MIDDLEWARE_URL="${REMOTE_MIDDLEWARE_URL:-https://ds-middleware-new.fly.dev}"
REMOTE_FRONTEND_ORIGIN="${REMOTE_FRONTEND_ORIGIN:-https://ds-frontend-local-new.vercel.app}"

KNOWN_LEDGER="${KNOWN_LEDGER:-gate-alpha}"
UPLOAD_FILE="${UPLOAD_FILE:-/etc/hosts}"

RUN_LOCAL=1
RUN_REMOTE=1

if [[ "${1:-}" == "--local-only" ]]; then
  RUN_REMOTE=0
elif [[ "${1:-}" == "--remote-only" ]]; then
  RUN_LOCAL=0
elif [[ -n "${1:-}" ]]; then
  echo "Unknown arg: $1"
  exit 2
fi

if [[ ! -f "$UPLOAD_FILE" ]]; then
  echo "Upload file not found: $UPLOAD_FILE"
  exit 2
fi

PASS_COUNT=0
FAIL_COUNT=0

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  echo "PASS: $1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo "FAIL: $1"
}

expect_code() {
  local name="$1"
  local expected="$2"
  local actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    pass "$name (expected=$expected actual=$actual)"
  else
    fail "$name (expected=$expected actual=$actual)"
  fi
}

contains() {
  local name="$1"
  local haystack="$2"
  local needle="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    pass "$name (found '$needle')"
  else
    fail "$name (missing '$needle')"
  fi
}

run_suite() {
  local label="$1"
  local be="$2"
  local mw="$3"
  local fe_origin="$4"

  echo
  echo "=== SUITE: $label ==="
  echo "backend=$be"
  echo "middleware=$mw"
  echo "frontend_origin=$fe_origin"

  local cookie
  cookie="/tmp/${label// /_}.cookie"
  rm -f "$cookie"

  local code body allow_origin unknown_ledger

  # 1) Health + sync status
  code=$(curl --http1.1 -sS -o "/tmp/${label}_be_health.json" -w "%{http_code}" "$be/health" || echo "000")
  expect_code "$label backend /health" "200" "$code"

  code=$(curl --http1.1 -sS -o "/tmp/${label}_be_sync_status.json" -w "%{http_code}" "$be/sync/v0/status" || echo "000")
  expect_code "$label backend /sync/v0/status" "200" "$code"

  code=$(curl --http1.1 -sS -o "/tmp/${label}_mw_health.json" -w "%{http_code}" "$mw/health" || echo "000")
  expect_code "$label middleware /health" "200" "$code"

  # 2) CORS checks
  code=$(curl --http1.1 -sS -o /dev/null -w "%{http_code}" -X OPTIONS "$be/sync/v0/status" \
    -H "Origin: $fe_origin" -H "Access-Control-Request-Method: GET" || echo "000")
  expect_code "$label backend preflight allowed origin" "200" "$code"

  allow_origin=$(curl --http1.1 -sSI -X OPTIONS "$be/sync/v0/status" \
    -H "Origin: $fe_origin" -H "Access-Control-Request-Method: GET" \
    | awk -F': ' 'BEGIN{IGNORECASE=1}/^access-control-allow-origin:/{print $2}' | tr -d '\r')
  contains "$label backend allow-origin header" "$allow_origin" "$fe_origin"

  code=$(curl --http1.1 -sS -o /dev/null -w "%{http_code}" -X OPTIONS "$be/sync/v0/status" \
    -H "Origin: https://evil.example" -H "Access-Control-Request-Method: GET" || echo "000")
  expect_code "$label backend preflight denied origin" "400" "$code"

  code=$(curl --http1.1 -sS -o /dev/null -w "%{http_code}" -X OPTIONS "$mw/api/ingest/file" \
    -H "Origin: $fe_origin" -H "Access-Control-Request-Method: POST" || echo "000")
  expect_code "$label middleware preflight allowed origin" "200" "$code"

  # 3) Ledger switch semantics
  body=$(curl --http1.1 -sS -c "$cookie" -b "$cookie" \
    -H "Origin: $fe_origin" -H "Content-Type: application/json" \
    -d "{\"ledger_id\":\"$KNOWN_LEDGER\"}" "$mw/api/ledgers" || true)
  contains "$label middleware switch known ledger response" "$body" "\"ledger_id\":\"$KNOWN_LEDGER\""

  # 4) Positive strict path: known ledger write via middleware
  code=$(curl --http1.1 -sS --max-time 30 -o "/tmp/${label}_allow.json" -w "%{http_code}" \
    -c "$cookie" -b "$cookie" -H "Origin: $fe_origin" \
    -F "kind=attachment" -F "file=@$UPLOAD_FILE;type=text/plain" \
    "$mw/api/ingest/file" || echo "000")
  expect_code "$label middleware ingest allow (known ledger)" "200" "$code"

  # 5) Negative strict path: unknown ledger write via middleware should deny-propagate
  unknown_ledger="gate-deny-$(date +%s)-$RANDOM"
  body=$(curl --http1.1 -sS -c "$cookie" -b "$cookie" \
    -H "Origin: $fe_origin" -H "Content-Type: application/json" \
    -d "{\"ledger_id\":\"$unknown_ledger\"}" "$mw/api/ledgers" || true)
  contains "$label middleware switch unknown ledger response" "$body" "\"ledger_id\":\"$unknown_ledger\""

  code=$(curl --http1.1 -sS --max-time 30 -o "/tmp/${label}_deny.json" -w "%{http_code}" \
    -c "$cookie" -b "$cookie" -H "Origin: $fe_origin" \
    -F "kind=attachment" -F "file=@$UPLOAD_FILE;type=text/plain" \
    "$mw/api/ingest/file" || echo "000")
  expect_code "$label middleware ingest deny (unknown ledger -> backend 403)" "502" "$code"

  body=$(cat "/tmp/${label}_deny.json" 2>/dev/null || true)
  contains "$label middleware deny body contains backend 403" "$body" "403 Forbidden"
}

if [[ "$RUN_LOCAL" -eq 1 ]]; then
  run_suite "local" "$LOCAL_BACKEND_URL" "$LOCAL_MIDDLEWARE_URL" "$LOCAL_FRONTEND_ORIGIN"
fi

if [[ "$RUN_REMOTE" -eq 1 ]]; then
  run_suite "remote" "$REMOTE_BACKEND_URL" "$REMOTE_MIDDLEWARE_URL" "$REMOTE_FRONTEND_ORIGIN"
fi

echo
echo "=== SUMMARY ==="
echo "PASS=$PASS_COUNT"
echo "FAIL=$FAIL_COUNT"
echo
echo "PASS criteria:"
echo "- FAIL count must be 0."
echo "- Known ledger ingest must be 200 through middleware."
echo "- Unknown ledger ingest must deny as middleware 502 wrapping backend 403."
echo "- Allowed-origin CORS preflight must be 200 with explicit allow-origin header."
echo "- Denied-origin backend preflight must be 400."

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  exit 0
fi
exit 1

