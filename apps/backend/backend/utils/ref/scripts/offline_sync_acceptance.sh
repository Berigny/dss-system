#!/usr/bin/env bash
set -euo pipefail

# Offline sync acceptance helper.
#
# Modes:
#   full    : capture local marker + pull sync items + push to cloud + verify marker in cloud
#   capture : capture local marker + pull sync items into artifact only
#   replay  : push previously captured items to cloud + verify marker in cloud
#
# Required env (defaults shown):
#   LOCAL_BACKEND_URL=http://127.0.0.1:8080
#   CLOUD_BACKEND_URL=https://ds-backend-new.fly.dev
#   DEMO_LEDGER_ID=chat-demo
#   DEMO_CONTEXT_ID=ctx:frontend:local
#   DEMO_OWNER_ID=demo-user
#   DEMO_PRINCIPAL_TYPE=user
#   OFFLINE_SYNC_MODE=full
#   OFFLINE_SYNC_PEER=offline-acceptance
#   OFFLINE_SYNC_LIMIT=500
#   OFFLINE_SYNC_ARTIFACT_ROOT=backend/utils/ref/artifacts/offline_sync
#   OFFLINE_SYNC_ARTIFACT_FILE=<path>   # required for replay if you don't want latest auto-pick

LOCAL_BACKEND_URL="${LOCAL_BACKEND_URL:-http://127.0.0.1:8080}"
CLOUD_BACKEND_URL="${CLOUD_BACKEND_URL:-https://ds-backend-new.fly.dev}"
DEMO_LEDGER_ID="${DEMO_LEDGER_ID:-chat-demo}"
DEMO_CONTEXT_ID="${DEMO_CONTEXT_ID:-ctx:frontend:local}"
DEMO_OWNER_ID="${DEMO_OWNER_ID:-demo-user}"
DEMO_PRINCIPAL_TYPE="${DEMO_PRINCIPAL_TYPE:-user}"
OFFLINE_SYNC_MODE="${OFFLINE_SYNC_MODE:-full}"
OFFLINE_SYNC_PEER="${OFFLINE_SYNC_PEER:-offline-acceptance}"
OFFLINE_SYNC_LIMIT="${OFFLINE_SYNC_LIMIT:-500}"
OFFLINE_SYNC_ARTIFACT_ROOT="${OFFLINE_SYNC_ARTIFACT_ROOT:-backend/utils/ref/artifacts/offline_sync}"
OFFLINE_SYNC_ARTIFACT_FILE="${OFFLINE_SYNC_ARTIFACT_FILE:-}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../" && pwd)"
ARTIFACT_ROOT_ABS="${ROOT_DIR}/${OFFLINE_SYNC_ARTIFACT_ROOT}"
mkdir -p "${ARTIFACT_ROOT_ABS}"

ts_utc() { date -u +"%Y%m%dT%H%M%SZ"; }

req() {
  local method="$1"
  local url="$2"
  local data_file="${3:-}"
  local out_file="$4"
  local code_file="$5"
  if [[ -n "${data_file}" ]]; then
    curl -sS -X "${method}" "${url}" \
      -H "Content-Type: application/json" \
      -H "x-ledger-id: ${DEMO_LEDGER_ID}" \
      -H "x-context-id: ${DEMO_CONTEXT_ID}" \
      -H "x-principal-id: ${DEMO_OWNER_ID}" \
      -H "x-principal-type: ${DEMO_PRINCIPAL_TYPE}" \
      -o "${out_file}" -w "%{http_code}" \
      --data-binary "@${data_file}" > "${code_file}"
  else
    curl -sS -X "${method}" "${url}" \
      -H "x-ledger-id: ${DEMO_LEDGER_ID}" \
      -H "x-context-id: ${DEMO_CONTEXT_ID}" \
      -H "x-principal-id: ${DEMO_OWNER_ID}" \
      -H "x-principal-type: ${DEMO_PRINCIPAL_TYPE}" \
      -o "${out_file}" -w "%{http_code}" > "${code_file}"
  fi
}

expect_200() {
  local step="$1"
  local code_file="$2"
  local out_file="$3"
  local code
  code="$(cat "${code_file}")"
  if [[ "${code}" != "200" ]]; then
    echo "FAIL: ${step} (HTTP ${code})" >&2
    sed -n '1,200p' "${out_file}" >&2 || true
    exit 1
  fi
}

ledger_h64() {
  python - "$1" <<'PY'
import hashlib, sys
ledger = sys.argv[1]
print(hashlib.blake2b(ledger.encode("utf-8"), digest_size=8).hexdigest())
PY
}

health_check() {
  local base="$1"
  local tmpdir="$2"
  req GET "${base}/health" "" "${tmpdir}/health.json" "${tmpdir}/health.code"
  expect_200 "GET ${base}/health" "${tmpdir}/health.code" "${tmpdir}/health.json"
}

capture_phase() {
  local workdir="$1"
  local h64="$2"
  local marker="$3"

  echo "[capture] writing marker to local ledger: ${marker}"
  cat > "${workdir}/ingest.json" <<JSON
{"entity":"${DEMO_LEDGER_ID}","ledger_id":"${DEMO_LEDGER_ID}","context_id":"${DEMO_CONTEXT_ID}","session_id":"offline-acceptance","turn_id":"$(ts_utc)","raw_text":"${marker}","kind":"text","metadata":{"source":"offline-sync-acceptance","mode":"capture"}}
JSON
  req POST "${LOCAL_BACKEND_URL}/api/ingest" "${workdir}/ingest.json" "${workdir}/ingest.out.json" "${workdir}/ingest.code"
  expect_200 "POST local /api/ingest" "${workdir}/ingest.code" "${workdir}/ingest.out.json"

  echo "[capture] pulling sync batch from local"
  cat > "${workdir}/pull.req.json" <<JSON
{"peer_id":"${OFFLINE_SYNC_PEER}","ledger_id_h64":"${h64}","cursors":{},"limit":${OFFLINE_SYNC_LIMIT}}
JSON
  req POST "${LOCAL_BACKEND_URL}/sync/v0/pull" "${workdir}/pull.req.json" "${workdir}/pull.out.json" "${workdir}/pull.code"
  expect_200 "POST local /sync/v0/pull" "${workdir}/pull.code" "${workdir}/pull.out.json"

  python - "${workdir}/pull.out.json" <<'PY'
import json, sys
obj = json.load(open(sys.argv[1]))
count = int(obj.get("count") or 0)
items = obj.get("items") or []
if not isinstance(items, list):
    print("FAIL: pull response items not a list", file=sys.stderr)
    sys.exit(1)
print(f"[capture] pulled items: {count}")
if count <= 0:
    print("FAIL: pull returned 0 items; nothing to replay", file=sys.stderr)
    sys.exit(1)
PY
}

replay_phase() {
  local workdir="$1"
  local h64="$2"
  local marker="$3"

  echo "[replay] pushing captured sync batch to cloud"
  python - "${workdir}/pull.out.json" "${workdir}/push.req.json" "${OFFLINE_SYNC_PEER}" "${h64}" <<'PY'
import json, sys
pull = json.load(open(sys.argv[1]))
peer = sys.argv[3]
h64 = sys.argv[4]
items = pull.get("items") or []
payload = {
    "peer_id": peer,
    "ledger_id_h64": h64,
    "items": [{"envelope_hex": str(i.get("envelope_hex") or ""), "allow_backfill": False} for i in items if i.get("envelope_hex")]
}
json.dump(payload, open(sys.argv[2], "w"))
PY
  req POST "${CLOUD_BACKEND_URL}/sync/v0/push" "${workdir}/push.req.json" "${workdir}/push.out.json" "${workdir}/push.code"
  expect_200 "POST cloud /sync/v0/push" "${workdir}/push.code" "${workdir}/push.out.json"

  python - "${workdir}/push.out.json" <<'PY'
import json, sys
obj = json.load(open(sys.argv[1]))
acc = int(obj.get("accepted") or 0)
dup = int(obj.get("duplicate") or 0)
q = int(obj.get("quarantine") or 0)
print(f"[replay] push accepted={acc} duplicate={dup} quarantine={q}")
if acc <= 0 and dup <= 0:
    print("FAIL: push accepted=0 and duplicate=0", file=sys.stderr)
    sys.exit(1)
PY

  echo "[verify] checking marker in cloud history"
  req GET "${CLOUD_BACKEND_URL}/ledger/history/${DEMO_LEDGER_ID}?limit=500" "" "${workdir}/cloud.history.json" "${workdir}/cloud.history.code"
  expect_200 "GET cloud /ledger/history/${DEMO_LEDGER_ID}" "${workdir}/cloud.history.code" "${workdir}/cloud.history.json"

  python - "${workdir}/cloud.history.json" "${marker}" <<'PY'
import json, sys
history = json.load(open(sys.argv[1]))
marker = sys.argv[2]
if isinstance(history, dict):
    history = history.get("history") or history.get("messages") or []
if not isinstance(history, list):
    print("FAIL: unexpected history payload", file=sys.stderr)
    sys.exit(1)
blob = json.dumps(history, ensure_ascii=False)
if marker not in blob:
    print("FAIL: marker not found in cloud history", file=sys.stderr)
    sys.exit(1)
print("[verify] marker found in cloud history")
PY
}

main() {
  local now workdir h64 marker
  now="$(ts_utc)"
  workdir="${ARTIFACT_ROOT_ABS}/${now}"
  mkdir -p "${workdir}"
  h64="$(ledger_h64 "${DEMO_LEDGER_ID}")"
  marker="offline-sync-marker:${now}"

  echo "mode=${OFFLINE_SYNC_MODE}"
  echo "local=${LOCAL_BACKEND_URL}"
  echo "cloud=${CLOUD_BACKEND_URL}"
  echo "ledger=${DEMO_LEDGER_ID}"
  echo "ledger_h64=${h64}"
  echo "artifact=${workdir}"

  case "${OFFLINE_SYNC_MODE}" in
    full)
      health_check "${LOCAL_BACKEND_URL}" "${workdir}"
      health_check "${CLOUD_BACKEND_URL}" "${workdir}"
      capture_phase "${workdir}" "${h64}" "${marker}"
      replay_phase "${workdir}" "${h64}" "${marker}"
      ;;
    capture)
      health_check "${LOCAL_BACKEND_URL}" "${workdir}"
      capture_phase "${workdir}" "${h64}" "${marker}"
      ;;
    replay)
      health_check "${CLOUD_BACKEND_URL}" "${workdir}"
      if [[ -z "${OFFLINE_SYNC_ARTIFACT_FILE}" ]]; then
        local latest
        latest="$(ls -1dt "${ARTIFACT_ROOT_ABS}"/* 2>/dev/null | head -n 1 || true)"
        if [[ -z "${latest}" ]]; then
          echo "FAIL: no capture artifact found; set OFFLINE_SYNC_ARTIFACT_FILE" >&2
          exit 1
        fi
        OFFLINE_SYNC_ARTIFACT_FILE="${latest}/pull.out.json"
      fi
      if [[ ! -f "${OFFLINE_SYNC_ARTIFACT_FILE}" ]]; then
        echo "FAIL: OFFLINE_SYNC_ARTIFACT_FILE not found: ${OFFLINE_SYNC_ARTIFACT_FILE}" >&2
        exit 1
      fi
      cp "${OFFLINE_SYNC_ARTIFACT_FILE}" "${workdir}/pull.out.json"
      # Best-effort marker extraction from capture ingest file if present.
      marker="$(python - "${OFFLINE_SYNC_ARTIFACT_FILE%/pull.out.json}/ingest.json" <<'PY'
import json, os, sys
p = sys.argv[1]
if os.path.exists(p):
    try:
        obj = json.load(open(p))
        print(str(obj.get("raw_text") or ""))
    except Exception:
        pass
PY
)"
      if [[ -z "${marker}" ]]; then
        marker="offline-sync-marker:"
      fi
      replay_phase "${workdir}" "${h64}" "${marker}"
      ;;
    *)
      echo "FAIL: unsupported OFFLINE_SYNC_MODE=${OFFLINE_SYNC_MODE}" >&2
      exit 1
      ;;
  esac

  echo "PASS: offline sync acceptance (${OFFLINE_SYNC_MODE})"
  echo "artifact=${workdir}"
}

main "$@"
