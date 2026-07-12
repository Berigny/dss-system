# DSS COORD Demo

A minimal FastHTML proof-of-portability for the DualSubstrate COORD resolver.

This component replaces the legacy Streamlit `Web4-Coordinate-Decode` app and
lives in the `dss-system` monorepo as `apps/coord-demo/`.

## Local development

From the monorepo root:

```bash
cp .env.example .env
# edit .env with your values
make dev
```

The `coord-demo` service will be available at http://localhost:3002.

You can also run it directly with Docker:

```bash
cd apps/coord-demo
docker build -t dss-coord-demo .
docker run -p 3002:3002 --env-file ../../.env dss-coord-demo
```

## Running tests

```bash
cd apps/coord-demo
pytest -q
```

Or inside the container:

```bash
docker compose exec coord-demo pytest -q
```

## Deployment

### Vercel

The included `vercel.json` points to `src/main.py` as the Python entrypoint.

```bash
cd apps/coord-demo
vercel --prod
```

Required environment variables (see root `.env.example`):

- `MIDDLEWARE_URL` or `MIDDLEWARE_BASE_URL` or `API_BASE`
- `FASTHTML_SECRET_KEY`

### Docker Compose

The root `docker-compose.yml` already defines the `coord-demo` service on port
`3002` and depends on `middleware` and `backend`.

```bash
docker compose up coord-demo
```

## Endpoints

- `GET /` — UI with a textarea for COORD JSON input.
- `POST /resolve` — accepts a `coord` form field, parses it as JSON, forwards it
  to `{MIDDLEWARE_URL}/resolve`, and renders the upstream JSON response.
- `GET /health` — returns `{"status": "ok"}` for healthchecks.

## Entrypoint

- `src/main.py` imports and re-exports the FastHTML application defined in
  `app.py` for a stable Vercel/Docker entrypoint path.
