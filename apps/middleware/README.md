# DSS Middleware

The DSS Middleware is a FastAPI/FastHTML gateway that sits between the
DualSubstrate frontends (control-plane, chat-surface, coord-demo) and the
backend services (backend API, did-issuer).

This component was migrated from `ds-middleware-local` into the `dss-system`
monorepo as `apps/middleware/`.

## Local development

From the monorepo root:

```bash
cp .env.example .env
# edit .env with your values
make dev
```

The `middleware` service will be available at http://localhost:8001.

You can also run it directly with Docker:

```bash
cd apps/middleware
docker build -t dss-middleware .
docker run -p 8001:8001 --env-file ../../.env dss-middleware
```

## Running tests

Tests live in `tests/` and use `pytest`.  Run them locally:

```bash
cd apps/middleware
pytest -q
```

Or inside the container:

```bash
docker compose exec middleware pytest -q
```

## Deployment

### Fly.io

The included `fly.toml` configures the `dss-middleware` app on port 8001 with a
`/health` check and a `middleware_data` volume mounted at `/app/data`.

```bash
cd apps/middleware
fly deploy
```

### Vercel

The included `vercel.json` points to `src/main.py` as the Python entrypoint.

```bash
cd apps/middleware
vercel --prod
```

## Environment variables

Key variables (see root `.env.example` for the full set):

- `DUALSUBSTRATE_API` or `API_BASE` — backend API base URL.
- `DUALSUBSTRATE_API_KEY` — backend API key.
- `MIDDLEWARE_CORS_ORIGINS` — comma-separated list of allowed frontend origins
  (e.g., Vercel deployments for control-plane, chat-surface, coord-demo).
- `OPENROUTER_API_KEY`, `LLM_MODEL`, `LLM_PROVIDER` — LLM routing.
- `WALT_ID_ISSUER_URL`, `WALT_ID_ISSUER_DID` — DID issuer integration.
- `ENTRA_OIDC_CLIENT_ID`, `ENTRA_OIDC_CLIENT_SECRET`, `ENTRA_OIDC_TENANT_ID` —
  Microsoft Entra OIDC.
- `MCP_PUBLIC_BASE_URL`, `MCP_AUTH_TOKEN` — MCP surface.

## Entrypoint

- `src/main.py` imports and re-exports the FastAPI application defined in
  `fastapi_app.py` for a stable Vercel/Docker/Fly entrypoint path.
- `GET /health` returns `{"status": "ok"}` for healthchecks.
