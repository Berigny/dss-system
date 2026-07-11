# dss-system

Monorepo home for the Dual Substrate Stack (DSS).

This repository is the migration target for the six legacy repos:

| Legacy repo | Monorepo path | Host | Framework |
|-------------|---------------|------|-----------|
| `ds-backend-local` | `apps/backend/` | fly.io | Python/FastAPI |
| `ds-middleware-local` | `apps/middleware/` | fly.io | Python/FastHTML |
| `ds-walt-id-issuer` (from `ds-review`) | `apps/did-issuer/` | fly.io | Java / Python |
| `DSS-Dashboard` | `apps/control-plane/` | Vercel | FastHTML |
| `ds-frontend-local` | `apps/chat-surface/` | Vercel | FastHTML |
| `Web4-Coordinate-Decode` | `apps/coord-demo/` | Vercel | FastHTML |

> **Status:** scaffold only. Apps are populated in later DSS tickets.

## Quick start

```bash
cp .env.example .env
# edit .env with strong secrets and correct hostnames
make dev
```

## Make targets

- `make dev` — build and start all services in Docker Compose
- `make down` — stop all services
- `make logs` — follow Docker Compose logs
- `make test` — run test suites inside containers
- `make lint` — placeholder for linting (TBD)

## Structure

```
.
├── apps/              # One directory per service
├── packages/          # Shared packages (e.g. shared-types)
├── infra/             # Terraform / Pulumi / Fly / Vercel configs
├── docs/              # Architecture and runbook docs
├── docker-compose.yml
├── Makefile
└── .env.example
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
