#!/usr/bin/env bash
set -u

# UX functionality smoke script (local + remote).
#
# Validates for each target:
# - health endpoint
# - ledgers list + switch API
# - ingest limits endpoint
# - chat endpoint
# - smart stream endpoint
#
# Usage:
#   bash backend/utils/ref/ux-smoke.sh
#   bash backend/utils/ref/ux-smoke.sh --local-only
#   bash backend/utils/ref/ux-smoke.sh --remote-only
#
# Optional env overrides:
#   LOCAL_MIDDLEWARE_URL=http://127.0.0.1:5001
#   REMOTE_MIDDLEWARE_URL=https://ds-middleware-new.fly.dev
#   LOCAL_ORIGIN=http://localhost:3000
#   REMOTE_ORIGIN=https://ds-frontend-local-new.vercel.app
#   KNOWN_LEDGER=gate-alpha

LOCAL_MIDDLEWARE_URL="${LOCAL_MIDDLEWARE_URL:-http://127.0.0.1:5001}"
REMOTE_MIDDLEWARE_URL="${REMOTE_MIDDLEWARE_URL:-https://ds-middleware-new.fly.dev}"
LOCAL_ORIGIN="${LOCAL_ORIGIN:-http://localhost:3000}"
REMOTE_ORIGIN="${REMOTE_ORIGIN:-https://ds-frontend-local-new.vercel.app}"
KNOWN_LEDGER="${KNOWN_LEDGER:-gate-alpha}"

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

PASS=0
FAIL=0

ok() {
  PASS=$((PASS + 1))
  echo "PASS: $1"
}

bad() {
  FAIL=$((FAIL + 1))
  echo "FAIL: $1"
}

check_code() {
  local name="$1"
  local expected="$2"
  local actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    ok "$name (expected=$expected actual=$actual)"
  else
    bad "$name (expected=$expected actual=$actual)"
  fi
}

check_contains() {
  local name="$1"
  local text="$2"
  local needle="$3"
  if [[ "$text" == *"$needle"* ]]; then
    ok "$name (contains '$needle')"
  else
    bad "$name (missing '$needle')"
  fi
}

run_suite() {
  local label="$1"
  local base="$2"
  local origin="$3"
  local cookie="/tmp/${label}_ux_smoke.cookie"
  rm -f "$cookie"

  echo
  echo "=== UX SMOKE: $label ==="
  echo "base=$base"
  echo "origin=$origin"

  local code body

  run_request() {
    local outfile="$1"
    shift
    : > "$outfile"
    code=$(curl --http1.1 -sS -o "$outfile" -w "%{http_code}" "$@" || true)
    code="$(normalize_code "$code")"
  }

  normalize_code() {
    local raw="$1"
    if [[ "$raw" =~ ^[0-9]{3}$ ]]; then
      printf "%s" "$raw"
      return
    fi
    # curl can emit extra text on transport timeout while still appending status.
    local tail3="${raw: -3}"
    if [[ "$tail3" =~ ^[0-9]{3}$ ]]; then
      printf "%s" "$tail3"
      return
    fi
    printf "000"
  }

  run_request "/tmp/${label}_ux_health.json" "$base/health"
  check_code "$label /health" "200" "$code"

  run_request "/tmp/${label}_ux_ledgers_get.json" -c "$cookie" -b "$cookie" "$base/api/ledgers"
  check_code "$label GET /api/ledgers" "200" "$code"

  run_request "/tmp/${label}_ux_ledgers_post.json" -c "$cookie" -b "$cookie" \
    -H "Origin: $origin" -H "Content-Type: application/json" \
    -d "{\"ledger_id\":\"$KNOWN_LEDGER\"}" "$base/api/ledgers"
  check_code "$label POST /api/ledgers" "200" "$code"
  if [[ "$code" == "200" ]]; then
    body=$(cat "/tmp/${label}_ux_ledgers_post.json" 2>/dev/null || true)
    check_contains "$label switch response" "$body" "\"ledger_id\":\"$KNOWN_LEDGER\""
  else
    bad "$label switch response (skipped due to non-200 POST /api/ledgers)"
  fi

  run_request "/tmp/${label}_ux_ingest_limits.json" "$base/api/ingest/limits"
  check_code "$label /api/ingest/limits" "200" "$code"

  : > "/tmp/${label}_ux_chat.json"
  code=$(curl --http1.1 -sS --max-time 45 -o "/tmp/${label}_ux_chat.json" -w "%{http_code}" -c "$cookie" -b "$cookie" \
    -H "Origin: $origin" -H "Content-Type: application/json" \
    -d '{"message":"ux smoke: one line status","provider":"llama3.2:latest","session_id":"ux-smoke-script","enable_ledger":true,"history":[]}' \
    "$base/api/chat" || true)
  code="$(normalize_code "$code")"
  check_code "$label POST /api/chat" "200" "$code"

  if [[ "$code" == "200" ]]; then
    body=$(cat "/tmp/${label}_ux_chat.json" 2>/dev/null || true)
    check_contains "$label chat payload" "$body" "\"reply\""
  else
    bad "$label chat payload (skipped due to non-200 POST /api/chat)"
  fi

  : > "/tmp/${label}_ux_stream.txt"
  code=$(curl --http1.1 -sS --max-time 45 -o "/tmp/${label}_ux_stream.txt" -w "%{http_code}" -c "$cookie" -b "$cookie" \
    -H "Origin: $origin" -H "Content-Type: application/json" \
    -d '{"message":"ux stream smoke","provider":"llama3.2:latest","session_id":"ux-stream-script","enable_ledger":true,"history":[]}' \
    "$base/api/chat/smart_stream" || true)
  code="$(normalize_code "$code")"
  check_code "$label POST /api/chat/smart_stream" "200" "$code"

  if [[ "$code" == "200" ]]; then
    body=$(head -n 1 "/tmp/${label}_ux_stream.txt" 2>/dev/null || true)
    check_contains "$label stream first frame" "$body" "\"type\""
  else
    bad "$label stream first frame (skipped due to non-200 POST /api/chat/smart_stream)"
  fi
}

if [[ "$RUN_LOCAL" -eq 1 ]]; then
  run_suite "local" "$LOCAL_MIDDLEWARE_URL" "$LOCAL_ORIGIN"
fi

if [[ "$RUN_REMOTE" -eq 1 ]]; then
  run_suite "remote" "$REMOTE_MIDDLEWARE_URL" "$REMOTE_ORIGIN"
fi

echo
echo "=== UX SUMMARY ==="
echo "PASS=$PASS"
echo "FAIL=$FAIL"
echo
echo "Pass criteria:"
echo "- FAIL must be 0"
echo "- chat and smart_stream must both return 200"
echo "- ledger switch response must include requested ledger_id"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
fi
exit 1
