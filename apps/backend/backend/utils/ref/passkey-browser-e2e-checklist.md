# Passkey Browser E2E Checklist

## Preconditions
- Backend running locally with this branch.
- Browser supports WebAuthn/passkeys (Chrome/Edge/Safari recent).
- `AUTH_WEBAUTHN_ALLOWED_ORIGINS` includes your backend page origin.
  - Example for local test page: `http://127.0.0.1:8000`

## Quick E2E Path
1. Open:
   - `http://127.0.0.1:8000/auth/dev/passkey`
2. Enter a test DID, e.g. `did:key:dev-user`.
3. Click `Register Passkey`.
4. Complete platform/browser passkey prompt.
5. Confirm page shows `REGISTER OK`.
6. Click `Login (Verify)`.
7. Complete passkey prompt again.
8. Confirm page shows `LOGIN OK` and a session token in response.

## What this validates
- Registration challenge + verify:
  - `/auth/register/challenge`
  - `/auth/register/verify`
- Login challenge + assertion verify:
  - `/auth/challenge`
  - `/auth/verify`
- Session token issuance after verify.

## Common failure reasons
- `origin_not_allowed`: add origin to `AUTH_WEBAUTHN_ALLOWED_ORIGINS`.
- `rp_id_hash_mismatch`: ensure `AUTH_WEBAUTHN_RP_ID` matches browser host.
- `challenge_flow_invalid`: do not mix register challenge with login verify.
