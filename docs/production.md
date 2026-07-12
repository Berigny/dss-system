# Production runbook

This runbook describes how to deploy and operate the DSS production environment.

## Branch

Production deployments are produced from the `main` branch.

```bash
git checkout main
git pull origin main
# promote develop to main via a pull request or fast-forward merge
```

## What deploys automatically

Merges to `main` trigger path-filtered GitHub Actions workflows:

- Fly.io apps deploy to their production app names:
  - `dss-system-backend`
  - `dss-system-middleware`
  - `dss-system-did-issuer`
- Vercel apps deploy to production:
  - control-plane production
  - chat-surface production
  - coord-demo production

## Required secrets

Ensure these repository secrets are set in **GitHub → Settings → Secrets and variables → Actions**:

- `FLY_API_TOKEN`
- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`
- `VERCEL_PROJECT_ID_CONTROL_PLANE`
- `VERCEL_PROJECT_ID_CHAT_SURFACE`
- `VERCEL_PROJECT_ID_COORD_DEMO`

> **Current status:** The repository is still private and the GitHub Actions secrets (`FLY_API_TOKEN`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, and the three Vercel project IDs) are not set — CI/CD will not trigger until those are configured.

## Pre-deploy checks

Before promoting `develop` to `main`:

1. Verify the `ci.yml` build is green on `develop`.
2. Verify staging health endpoints return HTTP 200:
   - `https://dss-system-backend-staging.fly.dev/health`
   - `https://dss-system-middleware-staging.fly.dev/health`
   - `https://dss-system-did-issuer-staging.fly.dev/livez`
3. Smoke-test a credential offer flow and a COORD resolution through the staging control-plane preview.

## Post-deploy checks

After the `main` Actions run completes:

1. Confirm Fly.io releases succeeded for `dss-system-backend`, `dss-system-middleware`, and `dss-system-did-issuer`.
2. Confirm Vercel production deployments succeeded.
3. Verify production health endpoints:
   - `https://dss-system-backend.fly.dev/health`
   - `https://dss-system-middleware.fly.dev/health`
   - `https://dss-system-did-issuer.fly.dev/livez`
4. Spot-check the public control-plane URL and chat-surface URL.
5. Run the automated smoke test:
   ```bash
   python scripts/verify_prod.py
   ```

## Rollback

If a production deployment causes incidents:

1. Revert the merge commit on `main` and push; the workflow will redeploy the previous state.
2. Alternatively, roll back the Fly.io release directly:
   ```bash
   flyctl releases list --app dss-system-backend
   flyctl deploy --app dss-system-backend --image <previous-image>
   ```
3. For Vercel, use the project's Deployments page to promote the previous production deployment.

## Monitoring

- Fly.io: built-in metrics, logs, and alerts in the Fly.io dashboard.
- Vercel: Analytics and logs in the Vercel dashboard.
- Health endpoints (used by load balancers and orchestrators):
  - `GET /health` for backend, middleware, control-plane, chat-surface, coord-demo
  - `GET /livez` for did-issuer
