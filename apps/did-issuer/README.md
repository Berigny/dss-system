# apps/did-issuer

DSS DID issuer based on the [walt.id Issuer API](https://docs.walt.id/community-stack/issuer).

## Responsibility

Issues `DssIdentity` verifiable credentials in three formats:

- `jwt_vc_json`
- `jwt_vc_json-ld`
- `vc+sd-jwt`

The issuer is configured to use a `did:web` issuer DID and an optional Entra ID OIDC client for wallet authentication.

## Ports

- Local / Docker Compose: `8080`
- Fly.io: `8080`

## Health

The walt.id Issuer API exposes a Kubernetes-style liveness endpoint:

```
GET /livez
```

The monorepo Docker Compose and Fly.io health checks use `/livez`.

## Configuration

Deployment-specific values are driven by environment variables (see `../../.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `WEB_PORT` | `8080` | HTTP port |
| `WALT_ID_BASE_URL` | `http://localhost:8080` | Public base URL of the issuer |
| `WALT_ID_ISSUER_DID` | `did:web:id.dualsubstrate.com` | Issuer DID |
| `DSS_LOGO_URL` | `https://id.dualsubstrate.com/assets/dss-logo.png` | Logo used in credential display metadata |
| `BACKEND_URL` | `http://backend:8000` | DSS backend used for credential status checks |
| `ENTRA_OIDC_CLIENT_ID` | — | Entra ID OIDC client ID |
| `ENTRA_OIDC_CLIENT_SECRET` | — | Entra ID OIDC client secret |
| `ENTRA_OIDC_TENANT_ID` | `2f013f08-f893-436f-becc-9f82d02ca76d` | Entra ID tenant |

## Build

```bash
docker build -t dss-did-issuer ./apps/did-issuer
```

## Run locally

```bash
docker run -p 8080:8080 --env-file .env dss-did-issuer
```

## DID issuance flow

1. A wallet or frontend requests a credential offer from `POST /.well-known/openid-credential-offer`.
2. The wallet authenticates via the configured OIDC provider (Entra ID).
3. The wallet calls the token endpoint, then the credential endpoint.
4. The issuer signs a `DssIdentity` credential using the configured `WALT_ID_ISSUER_DID` and returns it to the wallet.
5. The issuer can query `BACKEND_URL` for revocation or status-list information when required.

## See also

- [Root README](../../README.md) — monorepo quick start, architecture, and CI/CD secrets
- [docs/staging.md](../../docs/staging.md) and [docs/production.md](../../docs/production.md) — deployment runbooks
