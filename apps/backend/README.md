# DSS Backend

The DSS Backend is the core FastAPI service that provides ledger storage,
coordinate resolution, retrieval, ingestion, governance, and administrative
APIs for the DualSubstrate system.

This component was migrated from `ds-backend-local` into the `dss-system`
monorepo as `apps/backend/`.

## Local development

From the monorepo root:

```bash
cp .env.example .env
# edit .env with your values
make dev
```

The `backend` service will be available at http://localhost:8000.

You can also run it directly with Docker:

```bash
cd apps/backend
docker build -t dss-backend .
docker run -p 8000:8000 --env-file ../../.env dss-backend
```

## Running tests

Tests live in `backend/tests/` and `dss_ledger/tests/` and use `pytest`.  Run
them locally:

```bash
cd apps/backend
pytest -q
```

Or inside the container:

```bash
docker compose exec backend pytest -q
```

Some tests may require external API keys (e.g., `OPENAI_API_KEY`); set those
in `.env` or skip the relevant tests.

## Deployment

### Fly.io

The included `fly.toml` configures the `dss-backend` app on port 8000 with a
`/health` check and a `backend_data` volume mounted at `/data`.

```bash
cd apps/backend
fly deploy
```

### Docker Compose

The root `docker-compose.yml` already defines the `backend` service on port
`8000`, with a `/data` volume for the RocksDB ledger store.

```bash
docker compose up backend
```

## Environment variables

Key variables (see root `.env.example` for the full set):

- `DB_PATH` — directory for the RocksDB ledger database.  Set to `/data` in
  Docker; defaults to `./data` for local runs.
- `ADMIN_TOKEN` / `TRUST_ANCHOR_ADMIN_TOKEN` — admin authentication.
- `OPENAI_API_KEY` — used by some model/embedding paths.
- `BACKEND_CORS_ORIGIN_REGEX` — CORS allow regex for frontend origins.
- `QP_PURE_ENABLED` — enable Qp-pure coordinate filtering.
- `SALIENCE_THRESHOLD`, `BASELINE_MODE` — retrieval behaviour tuning.
- `BENCHMARK_ARTIFACT_ROOT` — path for benchmark artifact output.

## Entrypoint

- `backend/main.py` defines the FastAPI application.
- `src/main.py` re-exports it for a stable Docker/Fly entrypoint path.
- `GET /health` returns `{"status": "ok", "git_sha": "..."}` for healthchecks.

## Database path

Inside the container `DB_PATH` is set to `/data`, matching the Docker Compose
volume mount.  Outside Docker the default `./data` is used, giving a local
file-based database fallback.

## See also

- [Root README](../../README.md) — monorepo quick start, architecture, and CI/CD secrets
- [docs/staging.md](../../docs/staging.md) and [docs/production.md](../../docs/production.md) — deployment runbooks
