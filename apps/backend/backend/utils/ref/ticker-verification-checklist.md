# Ticker Verification Checklist (UI + Stream)

Use this checklist to verify the "thinking ticker" and coordinate visibility are working end-to-end.

## Preconditions

- Frontend production URL: `https://ds-frontend-local-new.vercel.app`
- Middleware production URL: `https://ds-middleware-new.fly.dev`
- Backend production URL: `https://ds-backend-new.fly.dev`
- Browser DevTools open on `Network` and `Console`

## 1) API stream baseline (pass/fail)

Run:

```bash
curl -N -sS -X POST https://ds-middleware-new.fly.dev/api/chat/smart_stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"ticker baseline test","provider":"llama3.2:latest","session_id":"ticker-baseline-1","enable_ledger":true,"history":[]}' \
  | sed -n '1,80p'
```

Pass criteria:

- HTTP is successful (no transport error).
- Stream includes at least one of: `"type":"status"`, `"type":"context_meta"`, `"type":"decision_trace"`, `"type":"meta"`.

Fail criteria:

- Non-2xx or no NDJSON frames.
- Only terminal error frames.

## 2) Frontend submit flow (pass/fail)

1. Open `https://ds-frontend-local-new.vercel.app`.
2. Submit prompt: `ticker ui test`.
3. Watch request `POST /api/chat/smart_stream` in DevTools.

Pass criteria:

- Request returns `200`.
- Response stream frames include status/context/meta frame types.
- UI shows live ticker text while response is in progress.

Fail criteria:

- Request is `500`/`502`.
- Frames exist but no ticker text appears in assistant bubble.

## 3) Coordinate visibility checks (pass/fail)

While the same response streams, confirm ticker text includes at least one coordinate-like token:

- `:WX-`
- `:EV-WALK-`
- `resolved`/`queued` count text from `context_meta`

Pass criteria:

- At least one coordinate or explicit resolved/queued indicator appears.

Fail criteria:

- No coordinate or resolved/queued signal appears despite non-empty context frames.

## 4) History + ALL entity sanity (pass/fail)

Run:

```bash
BASE='https://ds-frontend-local-new.vercel.app'
COOKIE=/tmp/ticker-check.cookie
rm -f "$COOKIE"
curl -sS -c "$COOKIE" -b "$COOKIE" "$BASE/api/history/entities"
echo
curl -sS -c "$COOKIE" -b "$COOKIE" "$BASE/ui/history/__all__?limit=5" | head -n 40
```

Pass criteria:

- `/api/history/entities` returns at least one entity.
- `/ui/history/__all__` returns HTML list content (or explicit empty-state only if no history truly exists).

Fail criteria:

- Entity API errors or returns empty unexpectedly while ledger contains known chat entries.

## 5) Optional backend audit export

Run with admin token:

```bash
curl -sS "https://ds-backend-new.fly.dev/admin/history/audit?limit=100&coord_limit=5" \
  -H "x-admin-token: $BACKEND_ADMIN_TOKEN"
```

Pass criteria:

- Returns `entity_count`, `entry_count`, and `entities[*].sample_coordinates`.
- Expected historical entities are present.
