# DSS-EVAL operator reference

LOAD-WHEN: operating install, eval, matrix, leaderboard, or claims in this repo.

## Make targets

| Target | Purpose |
|---|---|
| `make help` | List targets |
| `make install` | Install benchmark dependencies |
| `make compile` | Compile benchmark Python files |
| `make eval-fast` | Smoke pass (under about 60 seconds) |
| `make eval` | Full deterministic suite |
| `make matrix` | Adapter x suite x seed matrix |
| `make leaderboard` | Regenerate comparison table |
| `make clean` | Remove generated reports and caches |

Targets delegate into `dss-benchmark-standalone/`.

## Important paths

| Path | Role |
|---|---|
| `dss-benchmark-standalone/` | Runnable benchmark package |
| `dss-benchmark-standalone/eval/claims_registry*.yaml` | Claims and gates |
| `dss-benchmark-standalone/eval/reports/` | Generated JSON/Markdown reports |
| `eval/` | Additional eval notes and historical reports |
| `fixtures/` | Checked-in fixtures |

## Credential safety

Prefer environment variables for any private credentials. Never commit
`.env` files or connection strings into this repository.
