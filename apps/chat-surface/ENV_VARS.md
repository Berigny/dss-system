# Environment Variables — ds-frontend-local

Auto-generated during DSS-232 pre-migration cleanup.

| Variable | Default(s) | Source file(s) |
|---|---|---|
| `ATTACHMENT_MAX_BYTES` | str(50 * 1024 * 1024 | config/settings.py |
| `BACKEND_ADMIN_BASE` | os.getenv("DUALSUBSTRATE_BACKEND_ADMIN_BASE", "https://ds... | config/settings.py |
| `BACKEND_ADMIN_TOKEN` | "" | config/settings.py |
| `BASIC_AUTH_ENABLED` | "" | app.py |
| `BASIC_AUTH_PASSWORD` | "" | app.py |
| `BASIC_AUTH_USER` | "" | app.py |
| `CHAT_SURFACE_ID` | "surface:chat:primary" | config/settings.py |
| `CLOUD_API` | "https://ds-backend-new.fly.dev", "https://your-fly-app.fly.dev" | app.py, sync_daemon.py |
| `CONTROL_PLANE_BASE` | os.getenv("DUALSUBSTRATE_CONTROL_PLANE_BASE", "https://id... | config/settings.py |
| `DEFAULT_LEDGER_ID` | (required / no default) | config/settings.py |
| `DEFAULT_SESSION_ID` | "demo-session" | config/settings.py |
| `DEMO_BOOT_TIMEOUT` | "300" | demo_app.py |
| `DEMO_HEALTH_URL` | f"{UI_URL}/health" | demo_app.py |
| `DEMO_LAUNCH_TARGET` | "launch-all" | demo_app.py |
| `DEMO_LEDGER_ID` | (required / no default) | app.py, config/settings.py |
| `DEMO_MAKE_CMD` | "make" | demo_app.py |
| `DEMO_STACK_LOG` | "/tmp/ds-demo-stack.log" | demo_app.py |
| `DEMO_UI_URL` | "http://127.0.0.1:5050" | demo_app.py |
| `DEMO_WINDOW_TITLE` | "DS Frontend Demo" | demo_app.py |
| `DUALSUBSTRATE_API` | os.getenv("API_BASE", "https://ds-middleware-new.fly.dev" | config/settings.py |
| `DUALSUBSTRATE_API_KEY` | "" | config/settings.py |
| `DUALSUBSTRATE_API_KEY_LOCAL` | os.getenv("DUALSUBSTRATE_API_KEY", "" | config/settings.py |
| `DUALSUBSTRATE_API_LOCAL` | os.getenv("DUALSUBSTRATE_API", os.getenv("API_BASE", "htt... | config/settings.py |
| `DUALSUBSTRATE_AUTH_BASE` | (required / no default) | app.py |
| `DUALSUBSTRATE_COOKIE_DOMAIN` | (required / no default) | app.py |
| `DUALSUBSTRATE_LEDGER` | (required / no default) | config/settings.py |
| `ENABLE_LEDGER_MANAGEMENT` | "true" | config/settings.py |
| `ENABLE_LOCAL_LLM` | "false" | config/settings.py |
| `FASTHTML_SECRET_KEY` | "dev-secret" | app.py |
| `FRONTDOOR_AUTH_MODE` | "" | app.py |
| `FRONTEND_ENTITY_MODE` | "ledger" | app.py, utils/session.py |
| `FRONTEND_PRINCIPAL_ID` | os.getenv("DEMO_OWNER_ID", "demo-user" | config/settings.py |
| `FRONTEND_PRINCIPAL_TYPE` | "user" | config/settings.py |
| `FRONTEND_TENANT_ID` | os.getenv("DEMO_TENANT_ID", "tenant:demo" | config/settings.py |
| `GITHUB_OAUTH_CLIENT_ID` | (required / no default) | app.py |
| `GITHUB_OAUTH_CLIENT_SECRET` | (required / no default) | app.py |
| `GITHUB_OAUTH_REDIRECT_URI` | (required / no default) | app.py |
| `HISTORY_DISCOVERY_LIMIT` | "100" | app.py |
| `HTTP_TIMEOUT` | "10.0" | config/settings.py |
| `LEDGER_INVENTORY_DISCOVERY_TIMEOUT_SECONDS` | "4.0" | app.py |
| `LEDGER_INVENTORY_MAX_PROBE_ENTITIES` | "3" | app.py |
| `LEDGER_INVENTORY_THREAD_TIMEOUT_SECONDS` | "2.0" | app.py |
| `LLM_API_KEY` | (required / no default) | api/llm.py |
| `LLM_BASE_URL` | (required / no default) | api/llm.py, app.py |
| `LLM_FORCE_SYSTEM_SIGNALS` | "false" | api/llm.py |
| `LLM_MAX_TOKENS` | (required / no default) | config/settings.py |
| `LLM_MODEL` | "openai/gpt-4o" | api/llm.py, config/settings.py |
| `LLM_PROVIDER` | "openrouter" | config/settings.py |
| `LLM_SUPPORTS_TOOLS` | "true" | api/llm.py |
| `LOCAL_API` | "http://127.0.0.1:8080", "http://localhost:8080" | app.py, sync_daemon.py |
| `MANUAL_SYNC_MAX_ROUNDS` | str(MANUAL_SYNC_MAX_ROUNDS_DEFAULT | app.py |
| `OPENAI_COMPAT_INCLUDE_PIPELINE_EVENTS` | "" | app.py |
| `OPENAI_COMPAT_PIPELINE_ENGINE` | "middleware" | app.py |
| `OPENAI_COMPAT_S_MODE` | "s1" | app.py |
| `OPENAI_COMPAT_USE_PIPELINE` | "1" | app.py |
| `OPENROUTER_API_KEY` | "" | api/llm.py, config/settings.py |
| `OPENROUTER_APP_TITLE` | "ourIP.AI Assistant" | api/llm.py |
| `OPENROUTER_HTTP_REFERRER` | settings.API_BASE | api/llm.py |
| `OPENROUTER_MAX_TOKENS` | (required / no default) | config/settings.py |
| `PIPELINE_WALK_METRIC_STRIDE` | "2" | app.py |
| `RESOLVE_SNIPPET_DEBUG` | "" | app.py |
| `STATIC_ASSET_VERSION` | "v2" | config/settings.py |
| `SYNC_BATCH_LIMIT` | "200" | app.py |
| `SYNC_CONTEXT_ID` | os.getenv("FRONTEND_CONTEXT_ID", "ctx:frontend:local" | sync_daemon.py |
| `SYNC_INTERVAL_SECONDS` | "60" | sync_daemon.py |
| `SYNC_LEDGER_ID` | os.getenv("DEMO_LEDGER_ID", "LOAM" | sync_daemon.py |
| `SYNC_LEDGER_ID_H64` | "" | app.py |
| `SYNC_LEDGER_IDS_H64` | "" | app.py |
| `SYNC_LEDGER_LIMIT` | "500" | sync_daemon.py |
| `SYNC_PEER_ID` | "frontend-manual-sync", "frontend-sync-daemon" | app.py, sync_daemon.py |
| `SYNC_PRINCIPAL_ID` | os.getenv("DEMO_OWNER_ID", "demo-user" | sync_daemon.py |
| `SYNC_PRINCIPAL_TYPE` | "user" | sync_daemon.py |
| `TIMING_DEBUG` | "" | app.py |
| `USE_BACKEND_STREAM` | "" | config/settings.py |
| `VERCEL` | "" | app.py, config/settings.py |
| `VERCEL_ENV` | "" | app.py |
| `VERCEL_GIT_COMMIT_SHA` | (required / no default) | app.py, components/layout.py |

## Notes

- This inventory was generated by scanning `.py` files for `os.getenv`, `os.environ.get`, and `os.environ[...]` calls.
- Values shown are the literal defaults found in source; they may be removed or changed during migration.
- Review and update this file as the codebase evolves.
