# Investor Demo Checklist (Cloud + Offline)

Status date: 2026-02-25  
Goal: demonstrate threadless history, model-agnostic behavior, cost/stats visibility, attachment flow, external COORD resolution, human-in-the-loop feedback, and offline continuity.

## 0A. Two-Minute Talk Track (live narration)

Use this script while clicking through the steps:

1. `We start in cloud mode. New session, clean history, and model-agnostic routing.`
2. `I send the same prompt across two different models to show provider/model independence.`
3. `Turn and global stats update in real time, including cost/usage telemetry.`
4. `Now I attach a file, prompt on it, and get a response anchored to a generated coordinate.`
5. `That coordinate is externally resolvable, outside this UI, proving portability and verifiability.`
6. `A human reviewer can submit feedback on that coordinate, and the rollup score updates.`
7. `Finally, we switch to offline/local mode with Ollama and run the same flow successfully.`
8. `Net: same UX pattern online/offline, append-only ledger traceability, and external verification.`

## 0. Preflight

Set these once in terminal:

```bash
export FRONTEND_URL='https://ds-frontend-local-new.vercel.app'
export MIDDLEWARE_URL='https://ds-middleware-new.fly.dev'
export BACKEND_URL='https://ds-backend-new.fly.dev'
export BACKEND_ADMIN_TOKEN='<admin token>'
```

Smoke gates:

```bash
curl -sS "$BACKEND_URL/health"
curl -sS "$MIDDLEWARE_URL/health"
curl -sS "$FRONTEND_URL/api/wake"
```

Pass criteria:
- all return HTTP `200`
- backend health includes `"status":"ok"`

## 1. Cloud UX: threadless history + model agnostic + stats

Open:
- `https://ds-frontend-local-new.vercel.app`

Actions:
1. Show initial history panel state (new/empty thread state).
2. Prompt 1: `Give me one line on why append-only ledgers matter.`
3. Switch model from model picker.
4. Prompt 2 (same prompt): `Give me one line on why append-only ledgers matter.`
5. Open stats panel/section.

Pass criteria:
- both prompts return successfully
- model switch is reflected in the second turn metadata/stream
- stats visibly refresh (turn/global cost/usage indicators update)

## 2. Attachment + prompt

Actions:
1. Upload a small text/markdown file.
2. Prompt: `Summarize the attachment in 3 bullets and cite the coordinate used.`

Pass criteria:
- upload completes without error
- response includes attachment-derived summary
- response includes a resolvable turn coordinate (COORD)

## 3. External COORD resolution (both paths)

Use the COORD from step 2.

### 3A. Decoder app path

Run:

```bash
streamlit run /Users/davidberigny/Documents/GitHub/Web4-Coordinate-Decode/decoder_app.py
```

Then paste COORD into the app resolver.

Pass criteria:
- resolver returns decoded metadata/content (not not-found/error)

### 3B. Direct API path (MCP-compatible proof)

```bash
COORD='<paste_coord_here>'
curl -sS -X POST "$BACKEND_URL/web4/decode" \
  -H 'Content-Type: application/json' \
  -d "{\"coordinate\":\"$COORD\"}"
```

Pass criteria:
- JSON payload returns successful decode with coordinate-linked content/metadata

## 4. Human-in-the-loop feedback (rating)

Submit approval score:

```bash
COORD='<paste_coord_here>'
curl -sS -X POST "$BACKEND_URL/ledger/feedback/$COORD" \
  -H "x-admin-token: $BACKEND_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"actor_id":"human:demo","actor_type":"human","rating":3,"reason":"approved in investor demo","source":"demo-hitl"}'
```

Read back feedback rollup:

```bash
curl -sS "$BACKEND_URL/ledger/feedback/$COORD" \
  -H "x-admin-token: $BACKEND_ADMIN_TOKEN"
```

Pass criteria:
- submit returns `status: ok`
- readback returns feedback rollup with updated score/actors

## 5. Optional explicit relevance delta proof

Pin/unpin compatibility endpoints (maps to feedback internally):

```bash
curl -sS -X POST "$BACKEND_URL/ledger/pin/$COORD" \
  -H "x-admin-token: $BACKEND_ADMIN_TOKEN"

curl -sS "$BACKEND_URL/ledger/feedback/$COORD" \
  -H "x-admin-token: $BACKEND_ADMIN_TOKEN"
```

Pass criteria:
- pinned state / rollup reflects updated human signal

## 6. Offline continuity (local + Ollama)

Actions:
1. Start local stack (backend + middleware + frontend local flow).
2. Open local app URL.
3. Confirm model list shows Ollama models.
4. Run one normal prompt and one attachment prompt.

Pass criteria:
- local prompts complete without cloud dependency
- selected model is Ollama/local
- history and COORD generation still work

## 7. Fast scriptable checks (optional)

Repo smoke script:

```bash
bash backend/utils/ref/ux-smoke.sh --remote-only
```

Pass criteria:
- summary ends with `FAIL=0`

## 8. Demo fallback lines (if something degrades)

- If cloud stream degrades: demonstrate direct middleware stream:

```bash
curl -N -sS -X POST "$MIDDLEWARE_URL/api/chat/smart_stream" \
  -H 'Content-Type: application/json' \
  -d '{"message":"demo fallback check","provider":"llama3.2:latest","session_id":"investor-fallback","enable_ledger":true,"history":[]}'
```

- If UI decode panel fails: use section 3B direct decode curl.
- If UI rating control is unavailable: use section 4 feedback endpoint.

## 9. Final investor-ready acceptance criteria

Must all be true:
- Cloud chat works and model switch works.
- Attachment ingest + summarize works.
- At least one COORD resolves externally via decoder app and API.
- Human rating round-trip visible in feedback rollup.
- Offline local mode works with Ollama models.
