# CI/CD Alignment: Fly + Vercel

Status date: 2026-03-06

## Problem Observed

1. Deploy success did not always imply runtime version correctness.
2. `/health` `git_sha` could remain stale due to static runtime secret (`GIT_SHA`).
3. Cross-service updates (backend Fly, middleware/frontend Vercel) lacked one canonical rollout contract check.

## Baseline Fixes Added (this repo)

1. Backend Fly deploy workflow:
- `.github/workflows/fly-deploy.yml`
- On each push:
  - resolves Fly app from repo variable `FLY_APP` (fallback default)
  - preflights `flyctl status -a $FLY_APP` before deploy actions
  - stamps `GIT_SHA` Fly secret to `${{ github.sha }}`
  - deploys with `--build-arg GIT_SHA=${{ github.sha }}`
  - verifies `/health` reports expected SHA

2. Operator rollout checker:
- `backend/scripts/rollout_contract_check.py`
- Validates:
  - backend `/health`
  - middleware `/health` and backend target
  - frontend boot endpoints (`/api/wake`, `/api/models`, `/api/ingest/limits`)

3. Local Make targets:
- `make fly-deploy GIT_SHA=<sha>`
- `make rollout-contract-check [GIT_SHA=<sha>]`

## Recommended Next Changes (other repos)

1. `ds-middleware-local`
- Mirror backend deploy hardening:
  - use repo variable `FLY_APP` with sensible fallback
  - preflight app access before secret/deploy steps
  - verify `/health` `git_sha` post-deploy
- Keep `/version` endpoint with commit SHA (`GITHUB_SHA`/`FLY_IMAGE_REF` fallback).

2. `ds-frontend-local`
- Add a Vercel post-deploy contract check workflow (or external monitor) that validates:
  - `/api/wake` = 200
  - `/api/models` = 200 (no auth redirect)
  - `/api/ingest/limits` = 200
  - `/api/chat/smart_stream` response headers include upstream markers
    - `x-ds-upstream-url`
    - `x-ds-upstream-fallback`
- Add lightweight deploy info endpoint (e.g. `/api/deploy-info`) exposing:
  - `vercel_commit_sha`
  - `api_base`
  - optional middleware base

3. Cross-repo release gate
- Add a manual promotion checklist gate:
  - backend deploy complete and SHA verified
  - middleware points to expected backend URL
  - frontend points to expected middleware URL
  - rollout contract check passes before declaring production healthy

## Operational Rule

Never treat `deploy succeeded` as sufficient.  
Always require `contract check passed` across backend + middleware + frontend.
