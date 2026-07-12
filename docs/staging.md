# Staging runbook

This runbook describes how to deploy and operate the DSS staging environment.

## Branch

Staging deployments are produced from the `develop` branch.

```bash
git checkout develop
git pull origin develop
# make changes on a feature branch, then open a PR to develop
```

## What deploys automatically

Merges to `develop` trigger path-filtered GitHub Actions workflows:

- Fly.io apps deploy to their `-staging` app names:
  - `dss-system-backend-staging`
  - `dss-system-middleware-staging`
  - `dss-system-did-issuer-staging`
- Vercel apps deploy preview deployments:
  - control-plane preview
  - chat-surface preview
  - coord-demo preview

## Required secrets

Ensure these repository secrets are set in **GitHub → Settings → Secrets and variables → Actions**:

- `FLY_API_TOKEN`
- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`
- `VERCEL_PROJECT_ID_CONTROL_PLANE`
- `VERCEL_PROJECT_ID_CHAT_SURFACE`
- `VERCEL_PROJECT_ID_COORD_DEMO`

## Smoke-test staging

After the Actions run completes:

1. Check the Fly.io dashboard for the three `-staging` apps.
2. Check the Vercel dashboard for preview deployment URLs.
3. Hit each health endpoint:
   - `https://dss-system-backend-staging.fly.dev/health`
   - `https://dss-system-middleware-staging.fly.dev/health`
   - `https://dss-system-did-issuer-staging.fly.dev/livez`
4. Open the control-plane preview URL and verify login / trust-anchor status loads.

## Rollback

If a staging deployment is bad, redeploy the last known good image or revert the merge commit on `develop`. Fly.io keeps release history in each app; use `flyctl releases list --app dss-system-backend-staging` and `flyctl deploy --app dss-system-backend-staging --image <image>` to roll back.

## Local staging parity

To reproduce the staging configuration locally:

```bash
cp .env.example .env
# set public URLs to staging values, e.g. PUBLIC_BASE_URL=https://dss-system-backend-staging.fly.dev
make dev
```

> Note: the local Docker Compose stack does not run CI/CD; it is for development and smoke-testing only.
