### Fly-Hosted MCP (Production Demo Path)

Use Fly as the canonical MCP host and keep ngrok for local debugging only.

1. Pin the public MCP base URL on middleware (Fly):
   - `MCP_PUBLIC_BASE_URL=https://ds-middleware-new.fly.dev`
   - Canonical MCP endpoint for ChatGPT app: `https://ds-middleware-new.fly.dev/mcp`

2. Enable bearer token auth on middleware MCP:
   - `MCP_AUTH_REQUIRED=true`
   - `MCP_AUTH_TOKEN=<long-random-token>`
   - Optional if you want token-only during demo: `MCP_OAUTH_ENABLED=false`

3. Enable temporary backend-wide demo access (override mode) on backend app:
   - `DEMO_OVERRIDE_MODE=true`
   - `DEMO_OVERRIDE_DEFAULT_LEDGER=chat-demo`

4. Suggested Fly secret commands:

```bash
# Middleware app
fly secrets set -a ds-middleware-new \
  MCP_PUBLIC_BASE_URL="https://ds-middleware-new.fly.dev" \
  MCP_AUTH_REQUIRED="true" \
  MCP_AUTH_TOKEN="<long-random-token>" \
  MCP_OAUTH_ENABLED="false"

# Backend app (temporary demo-wide access)
fly secrets set -a ds-backend-new \
  DEMO_OVERRIDE_MODE="true" \
  DEMO_OVERRIDE_DEFAULT_LEDGER="chat-demo"
```

5. Verify deployment:
   - `GET https://ds-middleware-new.fly.dev/mcp` should return `auth_mode: "bearer_token"` and `public_url`.
   - `POST https://ds-middleware-new.fly.dev/mcp` without bearer token should return `401`.
   - `POST https://ds-middleware-new.fly.dev/mcp` with `Authorization: Bearer <MCP_AUTH_TOKEN>` should succeed.
