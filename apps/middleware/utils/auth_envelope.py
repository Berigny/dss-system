"""Helpers for forwarding frontend auth context to backend requests."""

from __future__ import annotations

import json
import os
from typing import Any

from starlette.requests import Request


BACKEND_SESSION_TOKEN_COOKIE = "ds_backend_session_token"


# Fine-grained Qp authority scopes (DS-REVIEW-194 P3-06).
QP_SCOPES: frozenset[str] = frozenset(
    {
        "p_adic_ball_read",
        "p_adic_ball_write",
        "prime_lattice_read",
        "qp_retrieval",
        "circulation_read",
        "dual_sync_read",
    }
)


def _intersect_qp_scopes(values: list[str]) -> set[str]:
    """Return the known Qp scopes present in ``values``."""
    return {str(item).strip().lower() for item in values if str(item).strip().lower() in QP_SCOPES}


def required_qp_scopes(body: dict[str, Any]) -> set[str]:
    """Return the Qp scopes required by the request payload."""
    required: set[str] = set()
    if _truthy(body.get("qp_pure")):
        required.add("qp_retrieval")
        required.add("p_adic_ball_read")
    if isinstance(body.get("query_primes"), list):
        required.add("prime_lattice_read")
    if _truthy(body.get("include_padic_diagnostics")):
        required.add("circulation_read")
    return required


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _payload_dict(payload: Any, key: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get(key)
    if isinstance(value, dict):
        return value
    return {}


def _claim_from_payload(payload: dict[str, Any], *keys: str) -> str | None:
    nested_sources = (
        _payload_dict(payload, "auth"),
        _payload_dict(payload, "auth_claims"),
        _payload_dict(payload, "auth_context"),
        _payload_dict(payload, "claims"),
    )
    for key in keys:
        direct = _clean_str(payload.get(key))
        if direct:
            return direct
        for source in nested_sources:
            nested = _clean_str(source.get(key))
            if nested:
                return nested
    return None


def _delegated_principal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    import sys
    sys.stdout.flush()
    print("DEBUG _delegated_principal_payload keys:", list(payload.keys()), flush=True)
    nested_sources = (
        payload,
        _payload_dict(payload, "auth"),
        _payload_dict(payload, "auth_claims"),
        _payload_dict(payload, "auth_context"),
        _payload_dict(payload, "claims"),
    )
    for source in nested_sources:
        delegated = source.get("delegated_principal") if isinstance(source, dict) else None
        if isinstance(delegated, dict):
            return dict(delegated)

    # Fallback: chat-surface may request a delegated agent principal by mode
    # without sending a full delegated_principal object.
    prompt_mode = str(_claim_from_payload(payload, "prompt_principal_mode") or "").strip().lower()
    if prompt_mode == "codex":
        ledger_scope: list[str] = []
        for key in ("ledger_id", "entity"):
            value = str(payload.get(key) or "").strip()
            if value:
                ledger_scope.append(value)
                break
        surface_scope: list[str] = []
        surface_id = str(payload.get("surface_id") or "").strip()
        if surface_id:
            surface_scope.append(surface_id)
        return {
            "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            "principal_key_id": "openai:agent:codex",
            "principal_id": "openai:codex",
            "principal_type": "agent",
            "explicit_cli_request": True,
            "delegation_mode": "delegated_only",
            "ledger_scope": ledger_scope,
            "surface_scope": surface_scope or ["surface:chat:primary"],
        }

    return {}


def _scope_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def build_backend_auth_envelope(
    *,
    request: Request,
    payload: dict[str, Any] | None = None,
    default_context_id: str | None = None,
) -> dict[str, Any]:
    """Return normalized headers and claim payload for backend forwarding.

    Compat behavior is default: absent claims/tokens are tolerated, and legacy
    principal headers pass through when present.
    """

    body = payload if isinstance(payload, dict) else {}
    print("DEBUG build_backend_auth_envelope body keys:", list(body.keys()), flush=True)
    mode = str(os.getenv("MIDDLEWARE_AUTH_ENVELOPE_MODE", "compat") or "compat").strip().lower()
    if mode not in {"compat", "did_strict"}:
        mode = "compat"

    outbound_headers: dict[str, str] = {}
    outbound_claims: dict[str, str] = {}

    auth_header = _clean_str(request.headers.get("authorization"))
    header_session_token = _clean_str(request.headers.get("x-session-token"))
    cookie_session_token = _clean_str(request.cookies.get(BACKEND_SESSION_TOKEN_COOKIE))
    token_present = False
    token_type = "none"
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if token:
            token_present = True
            token_type = "bearer"
            outbound_headers["authorization"] = f"Bearer {token}"
            outbound_headers["x-session-token"] = token
    elif header_session_token:
        token_present = True
        token_type = "bearer"
        outbound_headers["authorization"] = f"Bearer {header_session_token}"
        outbound_headers["x-session-token"] = header_session_token
    elif cookie_session_token:
        token_present = True
        token_type = "bearer"
        outbound_headers["authorization"] = f"Bearer {cookie_session_token}"
        outbound_headers["x-session-token"] = cookie_session_token

    principal_did = _claim_from_payload(body, "principal_did") or _clean_str(
        request.headers.get("x-principal-did") or request.headers.get("x-did")
    )
    principal_key_id = _claim_from_payload(body, "principal_key_id") or _clean_str(
        request.headers.get("x-principal-key-id") or request.headers.get("x-key-id")
    )
    session_jti = _claim_from_payload(body, "session_jti") or _clean_str(
        request.headers.get("x-session-jti") or request.headers.get("x-auth-jti")
    )
    context_id = (
        _claim_from_payload(body, "context_id")
        or _clean_str(request.headers.get("x-context-id"))
        or _clean_str(default_context_id)
    )
    auth_method = _claim_from_payload(body, "auth_method") or _clean_str(request.headers.get("x-auth-method"))

    principal_id = _clean_str(
        request.headers.get("x-principal-id")
        or request.headers.get("x-user-id")
    )
    principal_type = _clean_str(request.headers.get("x-principal-type"))

    operator_principal_did = principal_did
    operator_principal_id = principal_id
    delegated = _delegated_principal_payload(body)
    delegated_principal_did = _clean_str(
        delegated.get("principal_did") or delegated.get("did")
    )
    if delegated_principal_did and _truthy(
        delegated.get("explicit_cli_request")
        or delegated.get("delegated_cli_request")
        or delegated.get("cli_request")
    ):
        principal_did = delegated_principal_did
        delegated_key_id = _clean_str(
            delegated.get("principal_key_id")
            or delegated.get("key_id")
            or delegated.get("principal_key_ref")
        )
        if delegated_key_id:
            principal_key_id = delegated_key_id
        delegated_principal_id = _clean_str(
            delegated.get("principal_id")
            or delegated.get("agent_id")
            or delegated.get("display_name")
        )
        if delegated_principal_id:
            principal_id = delegated_principal_id
        principal_type = _clean_str(delegated.get("principal_type")) or "agent"
        auth_method = "delegated_cli_request"
        outbound_headers["x-delegated-cli-request"] = "true"
        outbound_headers["x-delegation-mode"] = (
            _clean_str(delegated.get("delegation_mode")) or "delegated_only"
        )
        delegated_by_principal_did = _clean_str(
            delegated.get("delegated_by_principal_did") or operator_principal_did
        )
        delegated_by_principal_id = _clean_str(
            delegated.get("delegated_by_principal_id") or operator_principal_id
        )
        if delegated_by_principal_did:
            outbound_headers["x-delegated-by-principal-did"] = delegated_by_principal_did
        if delegated_by_principal_id:
            outbound_headers["x-delegated-by-principal-id"] = delegated_by_principal_id
        ledger_scope = _scope_values(
            delegated.get("ledger_scope") or delegated.get("ledger_ids")
        )
        if ledger_scope:
            outbound_headers["x-delegated-ledger-scope"] = ",".join(ledger_scope)
        surface_scope = _scope_values(
            delegated.get("surface_scope") or delegated.get("surface_ids")
        )
        if surface_scope:
            outbound_headers["x-delegated-surface-scope"] = ",".join(surface_scope)
        surface_id = _clean_str(
            delegated.get("surface_id")
            or delegated.get("target_surface_id")
        )
        if surface_id:
            outbound_headers["x-surface-id"] = surface_id
        expires_at = _clean_str(
            delegated.get("expires_at") or delegated.get("delegation_expires_at")
        )
        if expires_at:
            outbound_headers["x-delegation-expires-at"] = expires_at

    if principal_did:
        outbound_headers["x-principal-did"] = principal_did
        outbound_claims["principal_did"] = principal_did
    if principal_key_id:
        outbound_headers["x-principal-key-id"] = principal_key_id
        outbound_claims["principal_key_id"] = principal_key_id
    if session_jti:
        outbound_headers["x-session-jti"] = session_jti
        outbound_claims["session_jti"] = session_jti
    if context_id:
        outbound_headers["x-context-id"] = context_id
        outbound_claims["context_id"] = context_id
    if auth_method:
        outbound_headers["x-auth-method"] = auth_method

    # DSS-189 / DS-REVIEW-194 P3-06: advertise fine-grained Qp authority scopes.
    operator_scope_header = _scope_values(request.headers.get("x-p-adic-scope"))
    operator_scope_claim = _scope_values(
        _claim_from_payload(body, "p_adic_scope") or body.get("p_adic_scope")
    )
    operator_scopes = _intersect_qp_scopes(operator_scope_header + operator_scope_claim)

    delegated_scope: set[str] = set()
    delegated_scope_exceeds_operator = False
    if delegated:
        delegated_header = _scope_values(request.headers.get("x-delegated-p-adic-scope"))
        delegated_claim = _scope_values(
            delegated.get("p_adic_scope")
            or delegated.get("delegated_p_adic_scope")
            or body.get("delegated_p_adic_scope")
        )
        delegated_scope = _intersect_qp_scopes(delegated_header + delegated_claim)
        if delegated_scope and not delegated_scope.issubset(operator_scopes):
            delegated_scope_exceeds_operator = True

    if operator_scopes:
        outbound_headers["x-p-adic-scope"] = ",".join(sorted(operator_scopes))
        outbound_claims["p_adic_scope"] = ",".join(sorted(operator_scopes))
    if delegated and delegated_scope:
        outbound_headers["x-delegated-p-adic-scope"] = ",".join(sorted(delegated_scope))
        outbound_claims["delegated_p_adic_scope"] = ",".join(sorted(delegated_scope))

    hardening_level = body.get("hardening_level")
    if hardening_level is not None:
        try:
            outbound_claims["p_adic_hardening_level"] = str(int(hardening_level))
        except (TypeError, ValueError):
            pass

    required = required_qp_scopes(body)
    missing_scopes = required - operator_scopes

    # Preserve legacy tuple for compat rollouts where DID claims are optional.
    if principal_id:
        outbound_headers["x-principal-id"] = principal_id
    if principal_type:
        outbound_headers["x-principal-type"] = principal_type

    return {
        "mode": mode,
        "token_present": token_present,
        "token_type": token_type,
        "headers": outbound_headers,
        "claims": outbound_claims,
        "qp_scope_check": {
            "required": sorted(required),
            "granted": sorted(operator_scopes),
            "delegated_granted": sorted(delegated_scope),
            "missing": sorted(missing_scopes),
            "delegation_exceeds_operator": delegated_scope_exceeds_operator,
        },
    }


__all__ = ["build_backend_auth_envelope"]
