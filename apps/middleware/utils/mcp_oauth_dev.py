from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from starlette.responses import JSONResponse


@dataclass
class OAuthValidation:
    ok: bool
    error: str | None = None
    scopes: set[str] | None = None


class DevOAuthProvider:
    """Lightweight OAuth 2.1 provider for MCP connector development.

    This is intended for local/dev integration and connector bring-up, not production.
    """

    def __init__(self) -> None:
        self.enabled = str(os.getenv("MCP_OAUTH_ENABLED", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        scopes_raw = str(os.getenv("MCP_OAUTH_SCOPES", "ds:read ds:write")).strip()
        self.supported_scopes = [s for s in scopes_raw.split() if s]
        self.default_scope = str(os.getenv("MCP_OAUTH_DEFAULT_SCOPE", "ds:read ds:write")).strip()
        self.code_ttl_s = int(str(os.getenv("MCP_OAUTH_CODE_TTL_S", "300")))
        self.token_ttl_s = int(str(os.getenv("MCP_OAUTH_TOKEN_TTL_S", "3600")))
        self.clients_path = Path(os.getenv("MCP_OAUTH_CLIENTS_PATH", ".mcp_oauth_clients.json"))

        self._clients: dict[str, dict[str, Any]] = {}
        self._auth_codes: dict[str, dict[str, Any]] = {}
        self._tokens: dict[str, dict[str, Any]] = {}
        self._load_clients()

    def _load_clients(self) -> None:
        if not self.clients_path.exists():
            return
        try:
            raw = json.loads(self.clients_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        clients_raw = raw.get("clients")
        if not isinstance(clients_raw, dict):
            return
        loaded: dict[str, dict[str, Any]] = {}
        for client_id, row in clients_raw.items():
            if not isinstance(client_id, str) or not isinstance(row, dict):
                continue
            secret = row.get("client_secret")
            uris = row.get("redirect_uris")
            if not isinstance(secret, str) or not secret:
                continue
            if not isinstance(uris, list) or not all(isinstance(uri, str) and uri for uri in uris):
                continue
            loaded[client_id] = {
                "client_id": client_id,
                "client_secret": secret,
                "redirect_uris": uris,
                "scope": str(row.get("scope") or self.default_scope),
                "created_at": int(row.get("created_at") or int(time.time())),
            }
        self._clients = loaded

    def _save_clients(self) -> None:
        self.clients_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"clients": self._clients}
        self.clients_path.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _b64url_sha256(value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _append_query(url: str, **params: str) -> str:
        parsed = urlparse(url)
        q = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for k, v in params.items():
            q[k] = v
        new_query = urlencode(q)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    def _issuer(self, base_url: str) -> str:
        return base_url.rstrip("/")

    def _metadata_urls(self, base_url: str) -> dict[str, str]:
        b = base_url.rstrip("/")
        return {
            "issuer": self._issuer(base_url),
            "authorization_endpoint": f"{b}/oauth/authorize",
            "token_endpoint": f"{b}/oauth/token",
            "registration_endpoint": f"{b}/oauth/register",
            "protected_resource_metadata": f"{b}/.well-known/oauth-protected-resource",
        }

    def authorization_server_metadata(self, base_url: str) -> dict[str, Any]:
        urls = self._metadata_urls(base_url)
        return {
            "issuer": urls["issuer"],
            "authorization_endpoint": urls["authorization_endpoint"],
            "token_endpoint": urls["token_endpoint"],
            "registration_endpoint": urls["registration_endpoint"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": self.supported_scopes,
        }

    def protected_resource_metadata(self, base_url: str, resource_url: str) -> dict[str, Any]:
        urls = self._metadata_urls(base_url)
        return {
            "resource": resource_url,
            "authorization_servers": [urls["issuer"]],
            "scopes_supported": self.supported_scopes,
            "bearer_methods_supported": ["header"],
        }

    def register_client(self, body: dict[str, Any]) -> dict[str, Any]:
        redirect_uris = body.get("redirect_uris") if isinstance(body.get("redirect_uris"), list) else []
        uris = [str(uri) for uri in redirect_uris if str(uri).strip()]
        if not uris:
            raise ValueError("redirect_uris is required")

        client_id = f"dsmcp-{secrets.token_hex(12)}"
        client_secret = secrets.token_urlsafe(24)
        now = int(time.time())
        self._clients[client_id] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": uris,
            "scope": str(body.get("scope") or self.default_scope),
            "created_at": now,
        }
        self._save_clients()
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": now,
            "client_secret_expires_at": 0,
            "redirect_uris": uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "client_secret_post",
        }

    def authorize(self, params: dict[str, str]) -> str:
        response_type = str(params.get("response_type") or "")
        client_id = str(params.get("client_id") or "")
        redirect_uri = str(params.get("redirect_uri") or "")
        state = str(params.get("state") or "")
        code_challenge = str(params.get("code_challenge") or "")
        code_challenge_method = str(params.get("code_challenge_method") or "")
        scope = str(params.get("scope") or self.default_scope)
        resource = str(params.get("resource") or "")

        if response_type != "code":
            raise ValueError("response_type must be code")
        if not client_id or client_id not in self._clients:
            raise ValueError("unknown client_id")
        client = self._clients[client_id]
        if redirect_uri not in set(client.get("redirect_uris") or []):
            raise ValueError("redirect_uri not registered")
        if code_challenge_method != "S256" or not code_challenge:
            raise ValueError("PKCE S256 code_challenge is required")

        code = secrets.token_urlsafe(24)
        now = int(time.time())
        self._auth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "resource": resource,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "exp": now + self.code_ttl_s,
            "used": False,
        }

        kwargs = {"code": code}
        if state:
            kwargs["state"] = state
        return self._append_query(redirect_uri, **kwargs)

    def _extract_client(self, form: dict[str, str], auth_header: str | None) -> tuple[str, dict[str, Any]]:
        client_id = str(form.get("client_id") or "")
        client_secret = str(form.get("client_secret") or "")

        if auth_header and auth_header.lower().startswith("basic "):
            token = auth_header.split(" ", 1)[1].strip()
            raw = base64.b64decode(token).decode("utf-8")
            cid, _, csecret = raw.partition(":")
            client_id = cid
            client_secret = csecret

        if not client_id or client_id not in self._clients:
            raise ValueError("invalid_client")
        client = self._clients[client_id]
        if client_secret and client_secret != client.get("client_secret"):
            raise ValueError("invalid_client")
        return client_id, client

    def exchange_token(self, form: dict[str, str], auth_header: str | None = None) -> dict[str, Any]:
        grant_type = str(form.get("grant_type") or "")
        if grant_type != "authorization_code":
            raise ValueError("unsupported_grant_type")

        client_id, _client = self._extract_client(form, auth_header)
        code = str(form.get("code") or "")
        redirect_uri = str(form.get("redirect_uri") or "")
        code_verifier = str(form.get("code_verifier") or "")
        if not code or not redirect_uri or not code_verifier:
            raise ValueError("invalid_request")

        row = self._auth_codes.get(code)
        if not row:
            raise ValueError("invalid_grant")
        now = int(time.time())
        if bool(row.get("used")) or now > int(row.get("exp") or 0):
            raise ValueError("invalid_grant")
        if str(row.get("client_id")) != client_id:
            raise ValueError("invalid_grant")
        if str(row.get("redirect_uri")) != redirect_uri:
            raise ValueError("invalid_grant")

        expected = str(row.get("code_challenge") or "")
        actual = self._b64url_sha256(code_verifier)
        if actual != expected:
            raise ValueError("invalid_grant")

        row["used"] = True

        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        scope = str(row.get("scope") or self.default_scope).strip() or self.default_scope
        resource = str(row.get("resource") or "")
        exp = now + self.token_ttl_s
        self._tokens[access_token] = {
            "client_id": client_id,
            "scope": scope,
            "resource": resource,
            "exp": exp,
        }

        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": self.token_ttl_s,
            "scope": scope,
            "refresh_token": refresh_token,
        }

    def validate_bearer(self, authorization_header: str | None, required_scopes: set[str]) -> OAuthValidation:
        if not authorization_header:
            return OAuthValidation(ok=False, error="missing_token")
        if not authorization_header.lower().startswith("bearer "):
            return OAuthValidation(ok=False, error="invalid_token")

        token = authorization_header.split(" ", 1)[1].strip()
        row = self._tokens.get(token)
        if not row:
            return OAuthValidation(ok=False, error="invalid_token")
        now = int(time.time())
        if now > int(row.get("exp") or 0):
            return OAuthValidation(ok=False, error="invalid_token")

        granted = set(str(row.get("scope") or "").split())
        if required_scopes and not required_scopes.issubset(granted):
            return OAuthValidation(ok=False, error="insufficient_scope", scopes=granted)
        return OAuthValidation(ok=True, scopes=granted)

    def unauthorized_response(self, *, base_url: str, required_scopes: set[str], error: str) -> JSONResponse:
        meta = self._metadata_urls(base_url)
        scope_text = " ".join(sorted(required_scopes)) if required_scopes else ""
        challenge = (
            f'Bearer resource_metadata="{meta["protected_resource_metadata"]}", '
            f'scope="{scope_text}", error="{error}"'
        )
        return JSONResponse(
            {
                "error": error,
                "error_description": "OAuth bearer token required for this MCP operation.",
            },
            status_code=401,
            headers={"WWW-Authenticate": challenge},
        )
