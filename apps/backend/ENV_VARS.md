# Environment Variables — ds-backend-local

Auto-generated during DSS-232 pre-migration cleanup.

| Variable | Default(s) | Source file(s) |
|---|---|---|
| `ADMIN_TOKEN` | "", "test-admin-token" | backend/api/admin.py, backend/api/auth.py, backend/scripts/auth_revocation_fire_drill.py, backend/scripts/chat_delegated_smoke.py, backend/tests/test_admin_provisioning_inspection.py |
| `ASSURANCE_CHALLENGE_REQUIRED` | "0" | backend/api/chat.py |
| `ASSURANCE_ENFORCE` | "0" | backend/api/chat.py |
| `ASSURANCE_MAX_AGE_SEC` | "900" | backend/utils/assurance.py |
| `ASSURANCE_MODEL_KEYS_JSON` | (required / no default) | backend/utils/assurance.py |
| `ASSURANCE_SHARED_SECRET` | (required / no default) | backend/utils/assurance.py |
| `ATTACHMENT_CHUNK_CHARS` | "12000", "2000" | backend/api/agent_writes.py, backend/api/ingest.py |
| `ATTACHMENT_CHUNK_COST_HIGH` | "1.0" | backend/api/ingest.py |
| `ATTACHMENT_CHUNK_MAX` | "50000" | backend/api/ingest.py |
| `ATTACHMENT_CHUNK_MIN` | "4000" | backend/api/ingest.py |
| `ATTACHMENT_CHUNK_TOPIC_LIMIT` | "4" | backend/api/agent_writes.py |
| `ATTACHMENT_MAX_BYTES` | str(50 * 1024 * 1024 | backend/api/ingest.py |
| `ATTACHMENT_SUMMARY_ALWAYS` | "true" | backend/api/ingest.py |
| `ATTACHMENT_SUMMARY_COST_THRESHOLD` | "0.8" | backend/api/ingest.py |
| `AUTH_PRINCIPAL_MODE` | "compat" | backend/services/authz.py |
| `AUTH_REVOCATION_TOKEN` | "" | backend/api/auth.py |
| `AUTH_SESSION_REFRESH_TOKEN_TTL_SECONDS` | "86400" | backend/services/session_tokens.py |
| `AUTH_SESSION_TOKEN_AUDIENCE` | "ds-backend" | backend/services/session_tokens.py |
| `AUTH_SESSION_TOKEN_ISSUER` | "ds-middleware" | backend/services/session_tokens.py |
| `AUTH_SESSION_TOKEN_SECRET` | "dev-session-secret-change-me" | backend/services/session_tokens.py |
| `AUTH_SESSION_TOKEN_TTL_SECONDS` | "3600" | backend/services/session_tokens.py |
| `AUTH_WEBAUTHN_ALLOWED_ORIGINS` | _DEFAULT_ALLOWED_ORIGINS | backend/api/auth.py |
| `AUTH_WEBAUTHN_CHALLENGE_TTL_SECONDS` | "300" | backend/api/auth.py |
| `AUTH_WEBAUTHN_RP_ID` | "" | backend/api/auth.py |
| `AUTH_WEBAUTHN_RP_NAME` | "Dual Substrate" | backend/api/auth.py |
| `AUTONOMY_POLICY` | "balanced" | backend/api/chat.py |
| `BACKEND_CORS_ORIGIN_REGEX` | r"https://(ds-frontend-local-new.*\.vercel\.app|([a-z0-9-... | backend/main.py |
| `BASELINE_MODE` | (required / no default) | backend/main.py |
| `BENCHMARK_ARTEFACT_SCHEMA_VERSION` | (required / no default) | backend/services/benchmark_publication_jobs.py |
| `BENCHMARK_ARTIFACT_ROOT` | (required / no default) | backend/services/benchmark_publication_jobs.py |
| `BENCHMARK_PHASE1_MAX_AGE_HOURS` | (required / no default) | backend/services/benchmark_publication_jobs.py |
| `BENCHMARK_PUBLICATION_OPERATOR_DIDS` | (required / no default) | backend/api/admin.py |
| `BENCHMARK_PUBLICATION_OUTPUT` | (required / no default) | backend/services/benchmark_publication_jobs.py |
| `BENCHMARK_REFERENCE_ONLY_SUITES` | (required / no default) | backend/services/benchmark_publication_jobs.py |
| `BENCHMARK_TOP_K` | (required / no default) | backend/services/benchmark_publication_jobs.py |
| `CHAT_HARDENING_LEVEL` | "3" | backend/api/chat.py, backend/fieldx_kernel/orchestrator.py |
| `CHAT_MAX_TOKENS_DEFAULT` | "512", str(default_map[level] | backend/api/chat.py |
| `CHAT_MAX_TOKENS_FAST` | "320", str(fast_map[level] | backend/api/chat.py |
| `CHAT_MAX_TOKENS_MED` | "448", str(med_map[level] | backend/api/chat.py |
| `CHAT_MODEL` | DEFAULT_CHAT_MODEL | backend/api/chat.py, backend/fieldx_kernel/orchestrator.py |
| `CONTRADICTION_STREAK_ALERT_THRESHOLD` | "3" | backend/api/agent_writes.py |
| `COORD_CATALOG_LIMIT` | "4" | backend/api/chat.py, backend/fieldx_kernel/orchestrator.py |
| `COORD_DEFAULT_NAMESPACES` | "default,chat-demo-session" | backend/utils/coord.py |
| `COORD_RECENCY_HALFLIFE_MIN` | "60" | backend/api/stats.py, backend/fieldx_kernel/orchestrator.py |
| `COORD_TEST_BASE_URL` | (required / no default) | backend/scripts/coord_resolve_smoke.py |
| `COORD_TIER_L` | "0.70" | backend/api/stats.py |
| `COORD_TIER_Q` | "0.50" | backend/api/stats.py |
| `COORD_TIER_T` | "0.85" | backend/api/stats.py |
| `COORDS_ONLY_MODE` | "false" | backend/api/chat.py |
| `DB_PATH` | "./data" | backend/benchmarks/rollup_prod_telemetry_benchmarks.py, backend/main.py, backend/scripts/auth_revocation_fire_drill.py, scripts/backfill_base_foundations.py, scripts/scan_base_foundations.py |
| `DEMO_DEFAULT_LEDGER` | "", "default" | backend/api/chat.py, backend/api/ledger.py, backend/services/demo_mode.py |
| `DEMO_OVERRIDE_MODE` | "false" | backend/services/demo_mode.py |
| `DISABLE_RESPONSE_TOKEN_LIMITS` | "1" | backend/api/chat.py |
| `DSS_DETERMINISTIC` | "false" | backend/benchmarks/determinism.py, backend/config/settings.py |
| `DSS_DETERMINISTIC_SEED` | "42" | backend/benchmarks/determinism.py, backend/config/settings.py |
| `DSS_KSR_PASSWORD` | (required / no default) | backend/kernel/esoteric_stripper.py, backend/kernel/structural_integrity.py, scripts/encrypt_ksr.py, scripts/generate_kernel_constants.py |
| `DSS_KSR_PBKDF2_ITERATIONS` | "480000" | backend/kernel/ksr_crypto.py |
| `DSS_REQUIRE_BASE_FOUNDATION` | "1" | backend/services/ledger_service.py |
| `DUALSUBSTRATE_API_KEY` | (required / no default) | backend/main.py |
| `E6_ALLOWED_DW` | (required / no default) | backend/api/agent_writes.py |
| `E6_CONTRADICTION_VIOLATIONS` | "2" | backend/api/agent_writes.py |
| `E6_MODE_GATING_STRICT` | "0" | backend/api/agent_writes.py |
| `E6_MODE_PACKET_STRICT` | "0" | backend/api/agent_writes.py |
| `E6_PACKET_INGRESS_MODE` | "soft" | backend/api/enrich.py |
| `E6_PACKET_POLICY_TABLE` | "0" | backend/api/agent_writes.py |
| `E6_PROMOTION_GATE_MODE` | "runtime" | backend/api/agent_writes.py |
| `E6_SYNC_ED25519_KEYS` | "" | backend/api/sync.py |
| `E6_SYNC_HMAC_KEY` | "dev-sync-key" | backend/api/sync.py |
| `E6_SYNC_HMAC_KEYS` | "" | backend/api/sync.py |
| `E6_SYNC_ISSUER` | "prime:issuer:local" | backend/scripts/e6_sync_ed25519_smoke.py |
| `E6_SYNC_LEDGER_ID` | "ledger-local" | backend/scripts/e6_sync_ed25519_smoke.py |
| `E6_SYNC_MESSAGE` | "ed25519 smoke event" | backend/scripts/e6_sync_ed25519_smoke.py |
| `E6_SYNC_ORIGIN_NODE` | f"{socket.gethostname( | backend/scripts/e6_sync_ed25519_smoke.py |
| `E6_SYNC_ORIGIN_REPO` | "ds-backend-local" | backend/scripts/e6_sync_ed25519_smoke.py |
| `E6_SYNC_PRIVATE_KEY_HEX` | "" | backend/scripts/e6_sync_ed25519_smoke.py |
| `E6_SYNC_SUBJECT` | "prime:subject:local" | backend/scripts/e6_sync_ed25519_smoke.py |
| `E6_THETA_H` | str(base.get("theta_H", 0.70 | backend/api/agent_writes.py |
| `E6_THETA_L` | str(base.get("theta_L", 0.85 | backend/api/agent_writes.py |
| `E6_THETA_SELF` | str(base.get("theta_self", 0.6 | backend/api/agent_writes.py |
| `E6_THETA_SIGMA` | str(base.get("V_std_max", 0.1 | backend/api/agent_writes.py |
| `E6_THETA_V` | str(base.get("theta_V", 0.45 | backend/api/agent_writes.py |
| `EMBEDDING_DIM` | "1536" | backend/fieldx_kernel/orchestrator.py, backend/retrieval/fuzzy_retrieve.py |
| `EMBEDDING_MODEL` | "text-embedding-3-small" | backend/fieldx_kernel/orchestrator.py, backend/retrieval/fuzzy_retrieve.py |
| `EQ6_DRAFT_MAX_TOKENS` | "120" | backend/api/chat.py |
| `EQ6_KAPPA_SELF` | "0.5" | backend/fieldx_kernel/governance_engine.py |
| `EQ6_STRENGTH` | (required / no default) | backend/fieldx_kernel/kernel_origin_equations.py |
| `EQ6_THETA_SELF` | "0.6" | backend/fieldx_kernel/governance_engine.py |
| `EQ6_TWO_PASS` | "true" | backend/api/chat.py |
| `EQ6_WALK_MIN_LAWFULNESS` | "2" | backend/fieldx_kernel/coord_walk.py |
| `EQ6_WALK_WEIGHT` | "0.25" | backend/fieldx_kernel/coord_walk.py |
| `EQ89_TREND_WINDOW` | "8" | backend/api/agent_writes.py |
| `FASTAPI_ROOT` | (required / no default) | backend/main.py |
| `GIT_SHA` | "" | backend/api/chat.py, backend/benchmarks/rollup_prod_telemetry_benchmarks.py, backend/benchmarks/ruler_256k_benchmark.py, backend/benchmarks/run_shadow_replay_benchmark.py, backend/main.py, backend/metrics/benchmark_context.py, backend/services/benchmark_publication_jobs.py |
| `GOV_BLOCK_DRIFT_FLOOR` | "0.20" | backend/api/agent_writes.py |
| `GOV_BLOCK_GRACE_CAP` | "0.90" | backend/api/agent_writes.py |
| `GOV_BLOCK_LAW_CAP` | "0.40" | backend/api/agent_writes.py |
| `GOV_BLOCK_SCORE_CAP` | "0.80" | backend/api/agent_writes.py |
| `GOVERNANCE_H_TREND_MU` | "2.0" | backend/fieldx_kernel/governance_engine.py |
| `GOVERNANCE_PROFILE` | "strict" | backend/fieldx_kernel/governance_engine.py |
| `GOVERNANCE_REPLAY` | "0" | backend/fieldx_kernel/substrate/ledger_store_v2.py |
| `GOVERNANCE_STRICT` | "1" | backend/api/agent_writes.py |
| `GUARDIAN_ENABLED` | "1" | backend/fieldx_kernel/guardian.py |
| `GUARDIAN_INTRO_PROMPT` | "" | backend/fieldx_kernel/guardian.py |
| `GUARDIAN_MAX_TOKENS` | "256" | backend/fieldx_kernel/guardian.py |
| `GUARDIAN_MODEL` | (required / no default) | backend/fieldx_kernel/guardian.py |
| `GUARDIAN_PROVIDER` | "openrouter" | backend/fieldx_kernel/guardian.py |
| `GUARDIAN_REASONING_MAX_CHARS` | "320" | backend/fieldx_kernel/guardian.py |
| `INGEST_DEFAULT_COHERENCE` | "0.9999" | backend/api/ingest.py |
| `KERNEL_CHUNK_MAX_TOKENS` | str(DEFAULT_CHUNK_MAX_TOKENS | backend/ingestion/chunker.py |
| `KIMI_PRINCIPAL_HOST` | "" | backend/api/admin.py |
| `KNOWLEDGE_TREE_LIMIT` | "50" | backend/api/chat.py, backend/fieldx_kernel/orchestrator.py |
| `LEDGER_ADMIN_INCLUDE_DISCOVERED` | "false" | backend/api/admin.py |
| `LEDGER_AUTHZ_ADMIN_PRINCIPAL_TYPES` | "admin,service" | backend/api/admin.py, backend/services/authz.py |
| `LEDGER_AUTHZ_MODE` | "allow_all" | backend/services/authz.py |
| `LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY` | "allow" | backend/services/authz.py |
| `LEDGER_CONTEXT_BINDING_MODE` | "compat" | backend/services/authz.py |
| `LEDGER_CONTEXT_ID_MODE` | "compat" | backend/services/context_scope.py |
| `LEDGER_CONTEXT_MODE` | "compat" | backend/services/authz.py |
| `LEDGER_NAMESPACE_SOURCE` | "ledger_id" | backend/services/namespace_policy.py |
| `LEDGER_READ_VERIFY_STRICT` | "0" | backend/fieldx_kernel/substrate/ledger_store_v2.py |
| `LEDGER_SCOPE_STRICT` | "true" | backend/services/ledger_scope.py |
| `LLM_API_KEY` | "dummy", "dummy-key" | backend/fieldx_kernel/orchestrator.py, backend/retrieval/fuzzy_retrieve.py |
| `LLM_BASE_URL` | (required / no default) | backend/fieldx_kernel/orchestrator.py, backend/retrieval/fuzzy_retrieve.py |
| `LLM_MAX_TOKENS` | (required / no default) | backend/api/chat.py |
| `LLM_MODEL` | (required / no default) | backend/api/chat.py |
| `LOOP_SENSITIVITY` | "0.6" | backend/api/chat.py |
| `OPENAI_API_KEY` | (required / no default) | backend/fieldx_kernel/orchestrator.py, backend/main.py, backend/retrieval/fuzzy_retrieve.py |
| `OPENROUTER_API_KEY` | (required / no default) | backend/api/billing.py, backend/fieldx_kernel/orchestrator.py, backend/retrieval/fuzzy_retrieve.py, backend/services/model_library.py |
| `OPENROUTER_MAX_TOKENS` | (required / no default) | backend/api/chat.py |
| `P_ADIC_DISTANCE_PRIME` | "5" | backend/fieldx_kernel/orchestrator.py, backend/retrieval/fuzzy_retrieve.py |
| `PADIC_LEDGER_PRECISION` | "4" | backend/fieldx_kernel/substrate/ledger_store_v2.py |
| `PADIC_LEDGER_PRIME` | "5" | backend/fieldx_kernel/substrate/ledger_store_v2.py |
| `PADIC_RESOLVER_PRECISION` | "4" | backend/api/resolver.py |
| `PADIC_RESOLVER_PRIME` | "5" | backend/api/resolver.py |
| `PADIC_WRITE_COST_LAMBDA` | "0.0" | backend/fieldx_kernel/substrate/ledger_store_v2.py |
| `PRE_EMISSION_DENY_STRICT` | "1" | backend/api/chat.py |
| `PUBLIC_BASE_URL` | (required / no default) | backend/api/admin.py |
| `PUBLIC_STATUS_MAX_AGE_SECONDS` | (required / no default) | backend/api/admin.py |
| `QP_PRECISION_LOSS_WARNING` | "true" | backend/config/settings.py |
| `QP_PURE_ENABLED` | "true" | backend/config/settings.py |
| `QUERY_ENHANCER_MAX_TOKENS` | str(enhancer_defaults[level] | backend/fieldx_kernel/orchestrator.py |
| `RESEND_API_KEY` | (required / no default) | backend/api/wizard.py |
| `RESEND_FROM_EMAIL` | (required / no default) | backend/api/wizard.py |
| `RESOLUTION_CONTRADICTION_DRIFT_FLOOR` | "0.35" | backend/api/agent_writes.py |
| `RESOLUTION_CONTRADICTION_GRACE_CAP` | "0.85" | backend/api/agent_writes.py |
| `RESOLUTION_CONTRADICTION_LAW_CAP` | "0.55" | backend/api/agent_writes.py |
| `RESOLUTION_CONTRADICTION_SCORE_CAP` | "0.65" | backend/api/agent_writes.py |
| `RESOLVE_SCHEMA_STRICT` | "" | backend/utils/resolve_format.py |
| `SAFETY_MIN_SCORE` | "0.0" | backend/api/agent_writes.py |
| `SALIENCE_THRESHOLD` | (required / no default) | backend/main.py |
| `SEARCH_CONFIDENT_SCORE_MIN` | "0.45" | backend/fieldx_kernel/orchestrator.py |
| `SEMANTIC_WEIGHT` | (required / no default) | backend/fieldx_kernel/orchestrator.py, backend/retrieval/fuzzy_retrieve.py |
| `STATS_AUTH_OBS_RUNBOOK_URL` | default_ops | backend/api/stats.py |
| `STATS_AUTH_ROLLOUT_RUNBOOK_URL` | default_auth | backend/api/stats.py |
| `STATS_AUTH_TOKEN_VALIDATION_FAILURE_THRESHOLD` | "1" | backend/api/stats.py |
| `STATS_AUTHZ_DENY_COUNT_ALERT_THRESHOLD` | "5" | backend/api/stats.py |
| `STATS_AUTHZ_DENY_RATE_ALERT_THRESHOLD` | "0.25" | backend/api/stats.py |
| `STATS_QUARANTINE_WRITE_COUNT_ALERT_THRESHOLD` | "0" | backend/api/stats.py |
| `STATS_QUARANTINE_WRITE_RATE_ALERT_THRESHOLD` | "0.05" | backend/api/stats.py |
| `STATS_SEARCH_REPAIR_ALERT_THRESHOLD` | "0" | backend/api/stats.py |
| `SYNC_BASE_URL` | "http://127.0.0.1:8080" | backend/scripts/e6_sync_ed25519_smoke.py |
| `SYSTEM_PROMPT_DIR` | "" | backend/utils/system_prompts.py |
| `TIME_RANGE_HORIZON` | "50" | backend/fieldx_kernel/orchestrator.py |
| `TRUST_ANCHOR_ISSUER_DID` | (required / no default) | backend/api/admin.py |
| `TRUST_ANCHOR_ORGANISATION_NAME` | (required / no default) | backend/api/admin.py |
| `TRUST_ANCHOR_ORGANISATION_REGISTRATION_REF` | (required / no default) | backend/api/admin.py |
| `TRUST_ANCHOR_ORGANISATION_URI` | (required / no default) | backend/api/admin.py |
| `UNITY_DROP_ALERT_THRESHOLD` | "-0.10" | backend/api/agent_writes.py |

## Notes

- This inventory was generated by scanning `.py` files for `os.getenv`, `os.environ.get`, and `os.environ[...]` calls.
- Values shown are the literal defaults found in source; they may be removed or changed during migration.
- Review and update this file as the codebase evolves.
