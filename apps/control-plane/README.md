# DSS Control Plane

The DSS Control Plane is a Starlette/FastHTML dashboard for managing DualSubstrate
identity, trust anchors, governance, benchmarks, and surface integrations.

This component was migrated from `DSS-Dashboard` into the `dss-system` monorepo as
`apps/control-plane/`.

## Local development

From the monorepo root:

```bash
cp .env.example .env
# edit .env with your values
make dev
```

The `control-plane` service will be available at http://localhost:3000.

You can also run it directly with Docker:

```bash
cd apps/control-plane
docker build -t dss-control-plane .
docker run -p 3000:3000 --env-file ../../.env dss-control-plane
```

## Running tests

Tests live in `tests/` and use `pytest`.  Run them locally:

```bash
cd apps/control-plane
pytest -q
```

Or inside the container:

```bash
docker compose exec control-plane pytest -q
```

## Deployment

### Vercel

The included `vercel.json` points to `src/main.py` as the Python entrypoint.

```bash
cd apps/control-plane
vercel --prod
```

Required Vercel environment variables (see root `.env.example`):

- `PUBLIC_BASE_URL`
- `ISSUER_DID`
- `MIDDLEWARE_BASE_URL` (or `MIDDLEWARE_URL`)
- `BACKEND_BASE_URL`
- `CHAT_BASE_URL`
- `BENCHMARK_DECODER_BASE_URL`
- `OPENROUTER_API_KEY`
- `FASTHTML_SECRET_KEY`

### Docker Compose

The root `docker-compose.yml` already defines the `control-plane` service on
port `3000` and depends on `middleware` and `backend`.

```bash
docker compose up control-plane
```

## Environment variables

All backend, middleware, and linked surface URLs are read from environment
variables.  The legacy `MIDDLEWARE_BASE_URL` name is still supported; the
alias `MIDDLEWARE_URL` is also accepted.

The runtime port defaults to `3000` and can be overridden with `PORT`.

## Entrypoint

- `src/main.py` imports and re-exports the Starlette application defined in
  `app.py`.  This keeps the migration non-invasive while giving Vercel and
  Docker a stable entrypoint path.

## See also

- [Root README](../../README.md) — monorepo quick start, architecture, and CI/CD secrets
- [docs/staging.md](../../docs/staging.md) and [docs/production.md](../../docs/production.md) — deployment runbooks
