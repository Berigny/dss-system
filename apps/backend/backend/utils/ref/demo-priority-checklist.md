# COORD Precision Plan (Top Priority)

## Current Status (as of March 3, 2026)

### PR Status
- `PR 1` Canonical candidate contract + adapters: `Partial`
  - Canonical handling is in place in core paths, but not fully normalized across all legacy boundaries.
- `PR 2` Unified scorer + numeric tiering only: `Partial`
  - Numeric tiering is active in main flow, but some scorer/tiering duplication still exists in `orchestrator.py`.
- `PR 3` Option 2 autonomy decision policy: `Mostly Done`
  - `AUTONOMY_POLICY` (`balanced|legacy`) and `autonomy_decision` are wired; replay-stability acceptance still needs full fixture run.
- `PR 4` Contradiction detector + single retry: `Done`
  - Post-answer contradiction check + one structured retry + `consistency_check` metadata/event implemented in `backend/api/chat.py`.
- `PR 5` Stream diagnostics + frontend visibility: `Done`
  - Backend now enforces canonical diagnostics in one path (`_apply_turn_diagnostics` + `_diagnostics_snapshot`) to keep stream/meta/response parity.
  - Candidate trace shape is stable and includes `payload_loaded` alias for UI compatibility.
  - Added backend tests for diagnostics normalization and untrusted metadata override protection.
  - Frontend + middleware panels refresh session/global stats correctly, show dynamic session spend, and include performance telemetry.
  - Final production verification remains a rollout task (not a PR5 code gap).
- `PR 6` Legacy path removal: `Done`
  - Backend sync/stream duplication in `chat.py` was consolidated and `/chat/coord/walk` is explicitly marked deprecated legacy fallback.
  - Prompt assembly now consumes canonical retrieved `relevance_score/tier_rank` first (no duplicate re-scoring for canonical candidates).
  - Query-empty dedupe path now uses canonical candidate keys instead of mixed legacy key-shape handling.
  - Autonomy policy now has a single active mode (`balanced`); legacy policy requests are ignored and logged.

### Demo Status
- `Demo 1` Resolve COORD from external prompt: `Done` (working)
- `Demo 2` Threadless model stickiness: `Done` (selection now persists across refresh/list reload)
- `Demo 3` Human-in-the-loop rating persistence: `Partial`
  - Backend decode now backfills `meta.feedback_rollup` via store fallback when metadata rollup is missing.
  - Streamlit end-to-end context/header path still requires live verification.
- `Demo 4` MCP auto-rating from ChatGPT choice: `Mostly Done`
  - Backend endpoint delivered: `POST /ledger/feedback/auto/{entry_id}` supports model-driven rating/reason with default source `mcp_auto_rate`.
  - Middleware MCP tool `ds.auto_rate_coord` now resolves + posts auto feedback to backend.
  - Remaining: live ChatGPT MCP smoke and deployment verification.
- `Demo 5` Offline/online metrics reliability: `Mostly Done`
  - Unit economics blanks fixed via session+global merge and better fallbacks.
  - Session spend now updates per turn from stream meta and billing refresh.
  - Performance row added (`latency`, `tok/s`, `resolved/turn`).
  - Needs final production smoke check.

### Quality Gates
- `Gate A` explicit COORD in top-3: `Open` (needs regression measurement)
- `Gate B` contradiction rate <2% when resolved context exists: `Partial` (mechanism implemented; threshold not yet measured)
- `Gate C` retrieval/prompt tier-score parity: `Partial` (improved, not fully certified)
- `Gate D` stream/final resolve_summary parity: `Partial` (implemented, needs full validation pass)
- `Gate E` autonomy action stability (>=95%): `Open` (replay fixture run pending)
- `Gate F` suppress priors on resolved tier-3 candidate: `Partial` (policy implemented; acceptance run pending)

## Objective
- Make COORD relevance/resolve behavior deterministic, transparent, and smooth for demos without hard-wiring answers.

## Design Principle
- Use a single canonical candidate contract from retrieval to prompt to generation checks.
- Keep model autonomy; enforce consistency through ranking, propagation, and soft governance signals.

## Autonomy Policy (Option 2: Balanced, Recommended)
- Decision policy order: `resolve from top-K canonical candidates` -> `reuse prior walk/cache path` -> `model priors`.
- Agent chooses action by utility, not hard wiring:
  - `U = w1*relevance + w2*resolve_confidence + w3*continuity(path/cache) - w4*contradiction_risk - w5*latency_cost`
  - Action with highest utility wins: `resolve`, `reuse_path`, `answer_from_priors`.
- Guardrails (soft, demo-safe):
  - If explicit COORD is tier `3` and resolved payload exists, answers must not claim inability to access.
  - If model answers from priors while high-confidence resolved candidates exist, emit contradiction signal + one retry.
- Required telemetry event:
  - `autonomy_decision`: `{action, top_k, chosen_coord?, utility, reason, retry?}`.

## 1. Canonical COORD candidate schema (single source of truth)
- Fields: `coord`, `namespace`, `identifier`, `relevance_score`, `tier_rank (0-3)`, `recency_score`, `semantic_score`, `explicit_mention`, `resolved_payload_present`, `source`.
- Rule: all pipeline stages read/write this shape only; adapters convert legacy shapes once at boundaries.
- Output contract: every turn emits `resolve_summary` and `candidate_trace` with top-K ordered list.

## 2. Unified scoring function (reused everywhere)
- Implement one scorer used by retrieval assembly and prompt assembly; remove duplicate tier logic.
- Keep soft weights configurable by env; include explicit first-mentioned COORD prior as a boost, not a mandate.
- Target behavior: first explicit COORD should usually become tier 3 when it is latest and semantically matched.
- Tier mapping: numeric only (`0,1,2,3`) with one threshold set.

## 3. Propagation fixes across turn pipeline
- In `assemble_context`, produce canonical candidates only.
- In `build_chat_messages`, consume canonical candidates directly; do not re-score from ad hoc metadata.
- In `chat.py`, build `required_coords`, `resolved_coords_set`, and `resolve_summary` from canonical `coord` only.
- Preserve `relevance_score/tier_rank` in `knowledge_tree` and metadata without shape loss.
- Add `autonomy_decision` to stream/meta so action selection is visible and testable.

## 4. Resolve-awareness without hard wiring
- Add pre-answer soft check: if `resolved_count > 0`, model prompt states resolved context is available.
- Add post-answer contradiction detector: if answer claims “cannot resolve/access” while resolved context exists, mark contradiction and request regeneration once.
- Do not hard-block; apply soft penalty and structured retry reason.

## 5. Precision for first-provided COORD
- Extract explicit COORD mentions with stable order index.
- Add `position_boost` to first mention and `explicit_mention=true`.
- Include this candidate in top-K unless score is below a configurable floor; still no forced citation.

## 6. Telemetry and debuggability (demo-critical)
- Emit `candidate_trace` event in stream: top 10 candidates with rank reasons.
- Emit `resolve_summary`: requested/resolved/unresolved counts plus lists.
- Emit `autonomy_decision`: chosen action (`resolve|reuse_path|answer_from_priors`) and utility basis.
- Emit `consistency_check`: `ok/contradiction`, reason, and whether retry occurred.
- Frontend panel: show top 3 selected COORDs, tier, score, and whether payload was loaded.

## 7. Quality gates and acceptance criteria
- Gate A: explicit COORD query yields that COORD in top 3 candidates in >=95% of regression set.
- Gate B: when `resolved_count > 0`, “cannot access/resolve” contradiction rate <2%.
- Gate C: tier/relevance values identical between retrieval stage and prompt stage for same candidate.
- Gate D: stream diagnostics and final metadata report same `resolve_summary`.
- Gate E: for replay fixtures, `autonomy_decision.action` is stable (>=95%) with fixed inputs and config.
- Gate F: when top candidate is resolved + tier 3, `answer_from_priors` action rate <5%.

## 8. Test plan
- Unit tests: normalization adapters, scorer determinism, tier mapping, explicit-mention boost.
- Integration tests: `assemble_context -> build_chat_messages -> chat response` propagation of same `coord/relevance/tier`.
- Behavior tests: prompts with first-mentioned COORD, multiple COORDs, no COORDs, stale COORDs.
- Golden demo tests: fixed fixtures matching demo scenarios with expected rank order and outputs.

## 9. Rollout plan
- Phase 1: add canonical schema + adapters + telemetry in shadow mode.
- Phase 2: switch ranking consumers to canonical scorer; keep old fields mirrored.
- Phase 3: enable Option 2 autonomy decision policy + `autonomy_decision` event.
- Phase 4: enable soft contradiction retry.
- Phase 5: remove deprecated tier/scoring/walk paths and lock tests.

## 11. Legacy Cleanup (reduce future uncertainty)
- Goal: one obvious implementation path for relevance + walk/autonomy; no duplicate scoring or shadow logic.
- Remove/deprecate:
  - duplicate tier systems (`S/Q/L/T` vs numeric `0..3`), keep numeric only.
  - duplicate relevance re-scoring in prompt assembly when canonical candidates already scored.
  - legacy key-shape handling that bypasses canonical `coord` (`coordinate`/partial forms) after adapter boundary.
  - deterministic `coord_walk` top-1 path where agent/autonomy path should be used for demos (retain only as explicit fallback endpoint).
- Consolidate:
  - one scorer module reused by retrieval + prompt + post-answer checks.
  - one candidate normalization adapter at ingress/egress.
  - one walk decision policy (Option 2) used by stream orchestration and persisted walk traces.
- Code map to clean first:
  - `backend/fieldx_kernel/orchestrator.py`: candidate scoring + tiering duplication.
  - `backend/api/chat.py`: `required_coords` / `resolved_coords_set` shape and propagation.
  - `backend/fieldx_kernel/coord_walk.py`: mark as fallback legacy deterministic walk path.
- Safety:
  - keep feature flag for rollback (`AUTONOMY_POLICY=balanced|legacy`).
  - delete dead paths only after Gate A-F pass on regression fixtures.

## 12. Implementation Checklist (PR-sized)

### PR 1: Canonical candidate contract + adapters
- Goal: one candidate shape end-to-end.
- Files:
  - `backend/fieldx_kernel/orchestrator.py`
  - `backend/api/chat.py`
  - `backend/utils/resolve_format.py` (if needed for adapter helpers)
- Tasks:
  - Add/centralize candidate adapter: normalize incoming shapes to canonical fields.
  - Ensure `assemble_context` emits canonical candidates only.
  - Ensure `chat.py` consumes canonical `coord` and does not rely on mixed `coordinate/coord` forms.
  - Preserve `relevance_score` + `tier_rank (0..3)` in metadata and knowledge tree without remap loss.
- Verification:
  - Unit tests for adapter normalization and shape preservation.
  - Integration check that retrieval and final meta carry identical top-K ordering.

### PR 2: Unified scorer + numeric tiering only
- Goal: remove scoring/tier drift between retrieval and prompt stages.
- Files:
  - `backend/fieldx_kernel/orchestrator.py`
  - `backend/api/chat.py`
- Tasks:
  - Extract single scorer helper used by both candidate assembly and prompt context assembly.
  - Remove legacy `S/Q/L/T` tier path; keep numeric `0..3` only.
  - Add explicit first-mentioned COORD boost (`position_boost`) as soft prior.
- Verification:
  - Unit tests for deterministic scoring and tier thresholds.
  - Gate C pass (same coord -> same score/tier across pipeline stages).

### PR 3: Option 2 autonomy decision policy
- Goal: explicit and observable action selection.
- Files:
  - `backend/api/chat.py`
  - `backend/fieldx_kernel/orchestrator.py`
  - `backend/fieldx_kernel/coord_walk.py` (fallback labeling/guard only)
- Tasks:
  - Implement `AUTONOMY_POLICY=balanced|legacy` feature flag (default `balanced` in demo env).
  - Compute utility and choose one action: `resolve`, `reuse_path`, `answer_from_priors`.
  - Prefer prior walk/cache reuse when utility exceeds fresh resolve.
  - Keep deterministic `/chat/coord/walk` path as explicit fallback only.
- Verification:
  - Replay fixtures for stable action choice (Gate E).
  - Top resolved tier-3 candidate suppresses priors fallback (Gate F).

### PR 4: Contradiction detector + single retry
- Goal: stop “cannot resolve” responses when resolved context exists.
- Files:
  - `backend/api/chat.py`
- Tasks:
  - Add post-answer contradiction check (`resolved_count > 0` vs claim inability).
  - Trigger one structured retry with reason; do not hard-block beyond one retry.
  - Persist `consistency_check` result in stream + final metadata.
- Verification:
  - Behavior tests for contradiction prompts.
  - Gate B pass (<2% contradiction rate when resolved context exists).

### PR 5: Stream diagnostics and frontend visibility
- Goal: transparent demo diagnostics.
- Files:
  - `backend/api/chat.py`
  - `ds-frontend-local/static/js/app.js` (tracked in frontend repo)
- Tasks:
  - Emit `candidate_trace`, `resolve_summary`, `autonomy_decision`, `consistency_check` in stream/meta.
  - Cap payload sizes and keep event schema stable.
  - Frontend panel shows top-3 candidates with tier/score/payload-loaded and chosen action.
- Verification:
  - Gate D pass (stream vs final metadata parity).
  - Manual demo smoke for 5 scripted prompts.

### PR 6: Legacy path removal (final cleanup)
- Goal: reduce future ambiguity and maintenance risk.
- Files:
  - `backend/fieldx_kernel/orchestrator.py`
  - `backend/api/chat.py`
  - `backend/fieldx_kernel/coord_walk.py`
  - related tests under `backend/tests/`
- Tasks:
  - Remove deprecated duplicate scoring/tier code paths.
  - Remove dead shape handling after adapter boundary.
  - Mark/retain deterministic walk endpoint as legacy fallback or remove if unused.
  - Update tests/docs to reflect single policy path.
- Verification:
  - Full regression + demo golden fixtures.
  - Gates A-F all passing.

## 13. Execution Order (for tomorrow demo hardening)
1. PR 1
2. PR 2
3. PR 3
4. PR 5
5. PR 4
6. PR 6

Rationale:
- lock data shape and scoring first, then autonomy behavior, then diagnostics;
- contradiction retry after diagnostics so failures are observable during tuning.

## 10. Demo runbook
- Pre-demo smoke: run 5 scripted COORD prompts and verify top-3 + resolve summary.
- Live overlay: show candidate trace and resolved payload indicators.
- Recovery path: if mismatch appears, display contradiction event and auto-regenerated grounded answer.

# Demo Priority Checklist

## 1. Demo 3: Human-in-the-loop rating persistence
- Why first: highest visible trust issue for live demo.
- Likely issue: Streamlit feedback calls not consistently carrying full tenancy/context scope.
- Likely issue: feedback saved but rollup not always surfaced clearly in resolved JSON.
- Target fix: pass `x-context-id`/`context_id` + ledger-consistent headers in decoder feedback/decode calls.
- Target fix: surface `meta.feedback_rollup` prominently after submit + re-resolve.

## 2. Demo 5: Offline/online metrics panel reliability
- Why second: visibly broken metrics (`—`, static spend) reduces demo confidence.
- Likely issue: `Session Spend` not updated on each turn.
- Likely issue: unit-economics tiles hide values when missing/zero instead of deriving fallback values.
- Target fix: update spend from stream/meta on every turn.
- Target fix: add stronger fallback derivation for `chat cost/turn` and `cost/1M tokens`.
- Target fix: add a simple “Performance” metric (latency + tokens/sec + resolved coords/turn).

## 3. Demo 2: Threadless model swap + preference stickiness
- Why third: core narrative, partly working already.
- Likely issue: “Add more” path exists, but persistence is cookie/session scoped only.
- Likely issue: selected model can reset after model-list refresh.
- Target fix: persist preferred model server-side (session profile + cookie).
- Target fix: keep user-selected OpenRouter model pinned in selector after refresh.

## 4. Demo 4: MCP auto-rating from ChatGPT choice
- Why fourth: strong add-on, not a blocker if 1/2/3 are stable.
- Target fix: add API endpoint for “auto-rate coord” with model-provided rating/reason.
- Target fix: wire this as optional action in MCP tool flow.

## 5. Demo 1: Resolve COORD from prompt on external system
- Status: working fine.
