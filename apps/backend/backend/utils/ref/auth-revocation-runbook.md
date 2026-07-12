# Auth Revocation Runbook (P1-06)

## Purpose
- Revoke passkey credentials.
- Revoke active session JTIs.
- Verify revoked auth is blocked immediately (within token TTL window).

## Required Env
- `ADMIN_TOKEN` or `AUTH_REVOCATION_TOKEN` must be configured on backend.

## API Endpoints
- `POST /auth/passkeys/{credential_id}/revoke`
- `POST /auth/sessions/revoke`

## Credential Revoke
```bash
curl -sS -X POST "http://127.0.0.1:8000/auth/passkeys/<credential_id>/revoke" \
  -H "Content-Type: application/json" \
  -H "x-admin-token: $ADMIN_TOKEN" \
  -d '{"reason":"incident-response"}'
```

Expected:
- HTTP `200`
- payload `status=ok`
- credential `status=revoked`

## Session Revoke (JTI)
```bash
curl -sS -X POST "http://127.0.0.1:8000/auth/sessions/revoke" \
  -H "Content-Type: application/json" \
  -H "x-admin-token: $ADMIN_TOKEN" \
  -d '{"jti":"st_xxx","reason":"incident-response"}'
```

Expected:
- HTTP `200`
- payload `status=ok`, `revoked=true`

## Fire Drill Validation
1. Mint a session token for a credential-bound principal.
2. Revoke credential or session JTI.
3. Call a write endpoint (for example `POST /api/chat/commit-answer`) with revoked bearer token.
4. Confirm HTTP `401`:
   - `detail.error = token_validation_failed`
   - `detail.reason = token_credential_revoked` or `token_revoked`

