# DSS Chat Surface

The DSS Chat Surface is a FastHTML chat UI for interacting with the DualSubstrate
middleware, orchestrator, and model library.

This component was migrated from `ds-frontend-local` into the `dss-system`
monorepo as `apps/chat-surface/`.

## Local development

From the monorepo root:

```bash
cp .env.example .env
# edit .env with your values
make dev
```

The `chat-surface` service will be available at http://localhost:3001.

You can also run it directly with Docker:

```bash
cd apps/chat-surface
docker build -t dss-chat-surface .
docker run -p 3001:3001 --env-file ../../.env dss-chat-surface
```

## Running tests

Tests live in `tests/` and use `pytest`.  Run them locally:

```bash
cd apps/chat-surface
pytest -q
```

Or inside the container:

```bash
docker compose exec chat-surface pytest -q
```

## Deployment

### Vercel

The included `vercel.json` points to `src/main.py` as the Python entrypoint.

```bash
cd apps/chat-surface
vercel --prod
```

Required Vercel environment variables (see root `.env.example`):

- `DUALSUBSTRATE_API` or `API_BASE` (or `MIDDLEWARE_URL` / `MIDDLEWARE_BASE_URL`)
- `DUALSUBSTRATE_API_KEY`
- `CONTROL_PLANE_BASE`
- `BACKEND_ADMIN_BASE`
- `OPENROUTER_API_KEY`
- `FASTHTML_SECRET_KEY`

### Docker Compose

The root `docker-compose.yml` already defines the `chat-surface` service on
port `3001` and depends on `middleware` and `backend`.

```bash
docker compose up chat-surface
```

## Environment variables

All backend and middleware URLs are read from environment variables.  The
legacy `DUALSUBSTRATE_API` / `API_BASE` names are supported, and the middleware
aliases `MIDDLEWARE_URL` and `MIDDLEWARE_BASE_URL` are also accepted.

The runtime port defaults to `3001` and can be overridden with `PORT`.

## Entrypoint

- `src/main.py` imports and re-exports the FastHTML application defined in
  `app.py`.  This keeps the migration non-invasive while giving Vercel and
  Docker a stable entrypoint path.
