# Environment Variables — ds-middleware-local

Auto-generated during DSS-232 pre-migration cleanup.

| Variable | Default(s) | Source file(s) |
|---|---|---|
| `ADAPTIVE_EXECUTION_ENABLED` | "1", "false" | config/settings.py, utils/mcp_server.py |
| `ADAPTIVE_EXECUTION_FORCE_PROFILE` | "" | config/settings.py, utils/mcp_server.py |
| `ADAPTIVE_EXECUTION_LOCAL_PROVIDER_MARKERS` | "ollama,llama,local" | utils/mcp_server.py |
| `ADMIN_TOKEN` | (required / no default) | api/client.py, app.py |
| `ASSURANCE_MODEL_KEYS_JSON` | (required / no default) | utils/assurance.py |
| `ASSURANCE_SHARED_SECRET` | (required / no default) | utils/assurance.py |
| `ATTACHMENT_MAX_BYTES` | str(50 * 1024 * 1024 | config/settings.py |
| `AUTH_WEBAUTHN_RP_ID` | (required / no default) | app.py |
| `AUTONOMY_POLICY` | "balanced" | routes/orchestrator.py |
| `BASIC_AUTH_PASSWORD` | "" | app.py |
| `BASIC_AUTH_USER` | "" | app.py |
| `CHAT_HARDENING_LEVEL` | "0" | config/settings.py |
| `CHAT_SURFACE_ID` | "surface:chat:primary" | config/settings.py |
| `CLOUD_API` | "https://ds-backend-new.fly.dev", "https://your-fly-app.fly.dev" | app.py, sync_daemon.py |
| `CONTROL_PLANE_REGISTRY_PATH` | "./data/control_plane_registry.json" | app.py |
| `DEFAULT_LEDGER_ID` | "default" | config/settings.py, utils/mcp_server.py |
| `DEFAULT_SESSION_ID` | "demo-session" | config/settings.py |
| `DEMO_LEDGER_ID` | (required / no default) | config/settings.py |
| `DEMO_OVERRIDE_DEFAULT_LEDGER` | "s2" | config/settings.py |
| `DISABLE_RESPONSE_TOKEN_LIMITS` | "1" | api/llm.py |
| `DUALSUBSTRATE_API` | os.getenv("API_BASE", "https://ds-backend-new.fly.dev" | config/settings.py |
| `DUALSUBSTRATE_API_KEY` | "" | config/settings.py |
| `DUALSUBSTRATE_COOKIE_DOMAIN` | (required / no default) | app.py |
| `DUALSUBSTRATE_LEDGER` | (required / no default) | config/settings.py |
| `E6_SYNC_ISSUER` | "prime:issuer:mcp", "prime:issuer:middleware", "prime:issuer:openclaw" | tests/integrations/openclaw_p0_harness.py, utils/e6_sync_ed25519_push_smoke.py, utils/mcp_server.py |
| `E6_SYNC_KEY_ID` | "1" | tests/integrations/openclaw_p0_harness.py, utils/mcp_server.py |
| `E6_SYNC_LEDGER_ID` | "ledger-local" | tests/integrations/openclaw_p0_harness.py, utils/e6_sync_ed25519_push_smoke.py |
| `E6_SYNC_MESSAGE` | "middleware ed25519 smoke" | utils/e6_sync_ed25519_push_smoke.py |
| `E6_SYNC_ORIGIN_NODE` | f"mcp-{secrets.token_hex(4, f"openclaw-{socket.gethostname(, f"{socket.gethostname( | tests/integrations/openclaw_p0_harness.py, utils/e6_sync_ed25519_push_smoke.py, utils/mcp_server.py |
| `E6_SYNC_ORIGIN_REPO` | "ds-middleware-local", "openclaw-local" | tests/integrations/openclaw_p0_harness.py, utils/e6_sync_ed25519_push_smoke.py, utils/mcp_server.py |
| `E6_SYNC_PRIVATE_KEY_HEX` | "" | tests/integrations/openclaw_p0_harness.py, utils/e6_sync_ed25519_push_smoke.py, utils/mcp_server.py |
| `E6_SYNC_SUBJECT` | "prime:subject:mcp", "prime:subject:middleware", "prime:subject:openclaw" | tests/integrations/openclaw_p0_harness.py, utils/e6_sync_ed25519_push_smoke.py, utils/mcp_server.py |
| `ENABLE_INTROSPECT` | "1" | routes/orchestrator.py |
| `ENABLE_LEDGER_MANAGEMENT` | "true" | config/settings.py |
| `ENABLE_LOCAL_LLM` | "false" | config/settings.py |
| `ENTRA_OIDC_CLIENT_ID` | "" | app.py |
| `ENTRA_OIDC_CLIENT_SECRET` | "" | app.py |
| `ENTRA_OIDC_REDIRECT_URI` | "" | app.py |
| `ENTRA_OIDC_TENANT_ID` | "2f013f08-f893-436f-becc-9f82d02ca76d" | app.py |
| `EQ9_CONTROL_DIAL` | EQ9_CONTROL_DIAL_DEFAULT | routes/orchestrator.py |
| `EQ9_TARGET_DRIFT_MAX` | (required / no default) | routes/orchestrator.py |
| `EQ9_TARGET_LAW_MIN` | (required / no default) | routes/orchestrator.py |
| `EQ9_TARGET_MEANING_PER_TOKEN_MIN` | (required / no default) | routes/orchestrator.py |
| `EQ9_TARGET_OUTPUT_TOKENS_SOFT` | (required / no default) | routes/orchestrator.py |
| `EQ9_TARGET_SCORE_MIN` | (required / no default) | routes/orchestrator.py |
| `FASTHTML_SECRET_KEY` | "dev-secret" | app.py |
| `FRONTDOOR_AUTH_MODE` | "" | app.py |
| `FRONTEND_CONTEXT_ID` | "ctx:frontend:vercel" | app.py |
| `GIT_SHA` | "" | app.py |
| `HARDENING_PROFILE` | (required / no default) | config/settings.py |
| `HTTP_TIMEOUT` | "10.0" | config/settings.py |
| `LLM_API_KEY` | (required / no default) | api/llm.py |
| `LLM_BASE_URL` | (required / no default) | api/llm.py, app.py |
| `LLM_FORCE_SYSTEM_SIGNALS` | "false" | api/llm.py |
| `LLM_MAX_TOKENS` | (required / no default) | config/settings.py |
| `LLM_MODEL` | "openai/gpt-4o" | api/llm.py, config/settings.py |
| `LLM_PROVIDER` | "openrouter" | config/settings.py |
| `LLM_SUPPORTS_TOOLS` | "true" | api/llm.py |
| `LOCAL_API` | "http://127.0.0.1:8080", "http://localhost:8080" | app.py, sync_daemon.py |
| `MANUAL_SYNC_MAX_ROUNDS` | str(MANUAL_SYNC_MAX_ROUNDS_DEFAULT | app.py |
| `MCP_APPEND_PIPELINE` | "true" | utils/mcp_server.py |
| `MCP_AUTH_REQUIRED` | "true" | config/settings.py |
| `MCP_AUTH_TOKEN` | "" | config/settings.py |
| `MCP_AUTO_E6` | "true" | utils/mcp_server.py |
| `MCP_CONTEXT_ID` | "ctx:mcp" | utils/mcp_server.py |
| `MCP_MIDDLEWARE_BASE_URL` | "" | utils/mcp_server.py |
| `MCP_OAUTH_CLIENTS_PATH` | ".mcp_oauth_clients.json" | utils/mcp_oauth_dev.py |
| `MCP_OAUTH_CODE_TTL_S` | "300" | utils/mcp_oauth_dev.py |
| `MCP_OAUTH_DEFAULT_SCOPE` | "ds:read ds:write" | utils/mcp_oauth_dev.py |
| `MCP_OAUTH_ENABLED` | "true" | utils/mcp_oauth_dev.py |
| `MCP_OAUTH_SCOPES` | "ds:read ds:write" | utils/mcp_oauth_dev.py |
| `MCP_OAUTH_TOKEN_TTL_S` | "3600" | utils/mcp_oauth_dev.py |
| `MCP_P0_BACKEND_BASE` | "http://127.0.0.1:8080" | tests/integrations/mcp_p0_harness.py |
| `MCP_P0_LEDGER_ID` | os.getenv("DEFAULT_LEDGER_ID", "default" | tests/integrations/mcp_p0_harness.py |
| `MCP_P0_LEDGER_ID_H64` | "" | tests/integrations/mcp_p0_harness.py |
| `MCP_P0_MCP_URL` | f"{MIDDLEWARE_BASE}/mcp" | tests/integrations/mcp_p0_harness.py |
| `MCP_P0_MIDDLEWARE_BASE` | "http://127.0.0.1:5001" | tests/integrations/mcp_p0_harness.py |
| `MCP_P0_OFFLINE_MODE` | "manual_stop_backend" | tests/integrations/mcp_p0_harness.py |
| `MCP_P0_PEER_ID` | os.getenv("MCP_SYNC_PEER_ID", "mcp-p0-harness" | tests/integrations/mcp_p0_harness.py |
| `MCP_P0_TENANT_ID` | "demo-tenant" | tests/integrations/mcp_p0_harness.py |
| `MCP_PIPELINE_TIMEOUT_S` | "90" | utils/mcp_server.py |
| `MCP_PUBLIC_BASE_URL` | "" | app.py, config/settings.py |
| `MCP_QUEUE_PATH` | ".mcp_sync_queue.jsonl" | utils/mcp_server.py |
| `MCP_STREAM_STATE_PATH` | ".mcp_stream_state.json" | utils/mcp_server.py |
| `MCP_SYNC_PEER_ID` | "chatgpt-mcp" | utils/mcp_server.py |
| `MIDDLEWARE_AUTH_ENVELOPE_MODE` | "compat" | utils/auth_envelope.py |
| `MIDDLEWARE_ENABLE_UI` | "0" | app.py |
| `MIDDLEWARE_ENTITY_MODE` | "ledger" | utils/session.py |
| `MIDDLEWARE_PUBLIC_BASE_URL` | (required / no default) | app.py, routes/orchestrator.py |
| `OPENAI_COMPAT_PIPELINE_ENGINE` | "middleware" | app.py |
| `OPENAI_COMPAT_POLICY_ALLOW_CLIENT_OVERRIDES` | "0" | app.py |
| `OPENAI_COMPAT_S_MODE` | "s1" | app.py |
| `OPENAI_COMPAT_USE_PIPELINE` | "1" | app.py |
| `OPENROUTER_API_KEY` | "" | api/llm.py, app.py, config/settings.py, tests/test_openrouter_key.py |
| `OPENROUTER_APP_TITLE` | "ourIP.AI Assistant" | api/llm.py |
| `OPENROUTER_HTTP_REFERRER` | settings.API_BASE | api/llm.py |
| `OPENROUTER_MAX_TOKENS` | (required / no default) | config/settings.py |
| `PIPELINE_S_MODE` | "s2" | routes/orchestrator.py |
| `PRINCIPAL_LINK_CHALLENGES_PATH` | "./data/principal_link_challenges.json" | app.py |
| `PRINCIPAL_LINK_CODE_DEBUG` | "0" | app.py |
| `PRINCIPAL_LINK_EMAIL_FROM` | "" | app.py |
| `PRINCIPAL_REGISTRY_PATH` | "./data/principal_registry.json" | app.py |
| `QP_PURE_ENABLED` | "true" | config/settings.py |
| `RESEND_API_KEY` | "" | app.py |
| `RESOLVE_SNIPPET_DEBUG` | "" | app.py |
| `STATIC_ASSET_VERSION` | "v2" | config/settings.py |
| `SYNC_BASE_URL` | "http://127.0.0.1:8080" | tests/integrations/openclaw_p0_harness.py, utils/e6_sync_ed25519_push_smoke.py |
| `SYNC_BATCH_LIMIT` | "200" | app.py, sync_daemon.py |
| `SYNC_INTERVAL_SECONDS` | "60" | sync_daemon.py |
| `SYNC_LEDGER_ID_H64` | "" | app.py, sync_daemon.py |
| `SYNC_LEDGER_IDS_H64` | "" | app.py |
| `SYNC_PEER_ID` | "frontend-manual-sync", "middleware-sync-daemon" | app.py, sync_daemon.py |
| `TIMING_DEBUG` | "" | app.py |
| `TRUST_ANCHOR_ADMIN_PRINCIPAL_ID` | (required / no default) | app.py |
| `TRUST_ANCHOR_ADMIN_PRINCIPAL_TYPE` | (required / no default) | app.py |
| `TRUST_ANCHOR_ADMIN_TOKEN` | (required / no default) | api/client.py, app.py |
| `TRUST_ANCHOR_CONTEXT_ID` | (required / no default) | app.py |
| `TRUST_ANCHOR_ISSUER_DID` | (required / no default) | app.py |
| `TRUST_ANCHOR_LEDGER_ID` | (required / no default) | app.py |
| `TRUST_ANCHOR_ORGANISATION_NAME` | (required / no default) | app.py |
| `TRUST_ANCHOR_ORGANISATION_REGISTRATION_REF` | (required / no default) | app.py |
| `TRUST_ANCHOR_ORGANISATION_URI` | (required / no default) | app.py |
| `TRUST_ANCHOR_PUBLIC_BASE_URL` | "https://id.dualsubstrate.com" | app.py |
| `USE_BACKEND_STREAM` | "" | config/settings.py |
| `VERIFIED_ID_ACCEPTED_ISSUERS` | "" | app.py |
| `VERIFIED_ID_AUTHORITY` | "" | app.py |
| `VERIFIED_ID_CALLBACK_API_KEY` | "" | app.py |
| `VERIFIED_ID_CALLBACK_BASE_URL` | (required / no default) | app.py |
| `VERIFIED_ID_CLIENT_ID` | "" | app.py |
| `VERIFIED_ID_CLIENT_NAME` | "Dual Substrate Identity" | app.py |
| `VERIFIED_ID_CLIENT_SECRET` | "" | app.py |
| `VERIFIED_ID_CREDENTIAL_TYPE` | "" | app.py |
| `VERIFIED_ID_DEFAULT_COMPANY` | (required / no default) | app.py |
| `VERIFIED_ID_ISSUANCE_PIN_LENGTH` | "0" | app.py |
| `VERIFIED_ID_ISSUANCE_PURPOSE` | "Issue your DSS identity credential into Microsoft Authen... | app.py |
| `VERIFIED_ID_MANIFEST_URL` | "" | app.py |
| `VERIFIED_ID_PURPOSE` | "Verify your identity before wallet authority is activated." | app.py |
| `VERIFIED_ID_REQUESTS_PATH` | "./data/verified_id_requests.json" | app.py |
| `VERIFIED_ID_TENANT_ID` | "" | app.py |
| `WALT_ID_CALLBACK_API_KEY` | "" | app.py |
| `WALT_ID_CREDENTIAL_CONFIGURATION_ID` | "DssIdentity_jwt_vc_json" | app.py |
| `WALT_ID_ISSUER_DID` | "did:web:id.dualsubstrate.com" | app.py |
| `WALT_ID_ISSUER_KEY_JWK` | "" | app.py |
| `WALT_ID_ISSUER_URL` | "https://ds-walt-id-issuer.fly.dev" | app.py |

## Notes

- This inventory was generated by scanning `.py` files for `os.getenv`, `os.environ.get`, and `os.environ[...]` calls.
- Values shown are the literal defaults found in source; they may be removed or changed during migration.
- Review and update this file as the codebase evolves.
