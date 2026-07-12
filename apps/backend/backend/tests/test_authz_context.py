from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request

from backend.api.admin import PRINCIPAL_REGISTRY_V1_KEY
from backend.services.authz import authz_diagnostics_from_request, authorize_or_raise


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    query: str = "",
    db: dict[bytes, bytes] | None = None,
) -> Request:
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("utf-8"), str(value).encode("utf-8")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": query.encode("utf-8"),
        "headers": raw_headers,
        "app": SimpleNamespace(state=SimpleNamespace(db=db or {})),
    }
    return Request(scope)


def test_enforce_mode_blocks_non_default_without_explicit_context(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_CONTEXT_MODE", "enforce")
    req = _make_request()
    try:
        authorize_or_raise(req, ledger_id="chat-team-a", action="ledger.write", explicit_context=False)
    except HTTPException as exc:
        assert exc.status_code == 422
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        assert detail.get("error") == "ledger_context_required"
        return
    raise AssertionError("Expected HTTPException(422) for missing explicit ledger context")


def test_enforce_mode_allows_non_default_with_payload_context(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_CONTEXT_MODE", "enforce")
    req = _make_request()
    authorize_or_raise(req, ledger_id="chat-team-a", action="ledger.write", explicit_context=True)


def test_enforce_mode_allows_non_default_with_header_context(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_CONTEXT_MODE", "enforce")
    req = _make_request(headers={"x-ledger-id": "chat-team-a"})
    authorize_or_raise(req, ledger_id="chat-team-a", action="ledger.write", explicit_context=False)


def test_enforce_mode_allows_default_without_explicit_context(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_CONTEXT_MODE", "enforce")
    req = _make_request()
    authorize_or_raise(req, ledger_id="default", action="ledger.write", explicit_context=False)


def test_compat_mode_allows_non_default_without_explicit_context(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_CONTEXT_MODE", "compat")
    req = _make_request()
    authorize_or_raise(req, ledger_id="chat-team-a", action="ledger.write", explicit_context=False)


def test_authz_diagnostics_from_request_exposes_compact_fields(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_PRINCIPAL_MODE", "did_strict")
    req = _make_request(
        headers={
            "x-context-id": "ctx:frontend",
            "x-principal-did": "did:key:z6Mkexample",
            "x-principal-key-id": "did:key:z6Mkexample#k1",
            "x-session-jti": "jti-123",
            "x-principal-registry-status": "active",
            "x-principal-registry-source": "middleware",
        }
    )
    authorize_or_raise(req, ledger_id="default", action="ledger.read", explicit_context=False)
    diagnostics = authz_diagnostics_from_request(req)
    assert diagnostics["principal_mode"] == "did_strict"
    assert diagnostics["principal_did_present"] is True
    assert diagnostics["principal_key_id_present"] is True
    assert diagnostics["session_jti_present"] is True
    assert diagnostics["principal_registry_status"] == "active"
    assert diagnostics["principal_registry_source"] == "middleware"
    assert diagnostics["context_id"] == "ctx:frontend"
    assert isinstance(diagnostics["authz_reason"], str)


def test_delegated_codex_contract_requires_scope_and_cli_request(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "allow_all")
    codex_did = "did:web:id.dualsubstrate.com:principals:agent:openai:codex"
    registry = {
        "version": 1,
        "principals": {
            codex_did: {
                "principal_did": codex_did,
                "status": "active",
                "metadata": {
                    "actor_type": "agent",
                    "delegated_authority": {
                        "delegation_mode": "delegated_only",
                        "delegated_by_principal_did": "did:key:z6MkOperator",
                        "ledger_scope": ["chat-demo"],
                        "surface_scope": ["surface:chat:primary"],
                    },
                },
            }
        },
    }
    db = {PRINCIPAL_REGISTRY_V1_KEY: json.dumps(registry).encode("utf-8")}
    req = _make_request(
        headers={
            "x-principal-did": codex_did,
            "x-principal-type": "agent",
            "x-delegated-cli-request": "true",
            "x-delegated-by-principal-did": "did:key:z6MkOperator",
            "x-delegated-ledger-scope": "chat-demo",
            "x-delegated-surface-scope": "surface:chat:primary",
            "x-surface-id": "surface:chat:primary",
        },
        db=db,
    )
    authorize_or_raise(req, ledger_id="chat-demo", action="ledger.write", explicit_context=True)
    diagnostics = authz_diagnostics_from_request(req)
    assert diagnostics["delegated_prompt_path_active"] is True
    assert diagnostics["delegated_cli_request"] is True
    assert diagnostics["delegated_by_principal_did"] == "did:key:z6MkOperator"
    assert diagnostics["delegated_surface_id"] == "surface:chat:primary"
    assert diagnostics["principal_registry_status"] == "active"
    assert diagnostics["principal_registry_source"] == "backend_registry"


def test_delegated_codex_contract_rejects_expired_or_mismatched_scope(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "allow_all")
    codex_did = "did:web:id.dualsubstrate.com:principals:agent:openai:codex"
    registry = {
        "version": 1,
        "principals": {
            codex_did: {
                "principal_did": codex_did,
                "status": "active",
                "metadata": {
                    "actor_type": "agent",
                    "delegated_authority": {
                        "delegation_mode": "delegated_only",
                        "delegated_by_principal_did": "did:key:z6MkOperator",
                        "ledger_scope": ["chat-demo"],
                        "surface_scope": ["surface:chat:primary"],
                    },
                },
            }
        },
    }
    db = {PRINCIPAL_REGISTRY_V1_KEY: json.dumps(registry).encode("utf-8")}
    req = _make_request(
        headers={
            "x-principal-did": codex_did,
            "x-principal-type": "agent",
            "x-delegated-cli-request": "true",
            "x-delegated-by-principal-did": "did:key:z6MkOperator",
            "x-surface-id": "surface:chat:wrong",
            "x-delegation-expires-at": "2020-01-01T00:00:00Z",
        },
        db=db,
    )
    try:
        authorize_or_raise(req, ledger_id="chat-demo", action="ledger.write", explicit_context=True)
    except HTTPException as exc:
        assert exc.status_code == 403
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        assert detail.get("reason") in {
            "delegated_surface_scope_mismatch",
            "delegation_expired",
        }
        return
    raise AssertionError("Expected delegated Codex contract failure for expired or mismatched scope")


def test_delegated_codex_contract_canonicalizes_principal_by_key_ref(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "allow_all")
    canonical_did = "did:web:id.dualsubstrate.com:principals:agent:openai:codex"
    runtime_did = "did:web:ds-backend-new.fly.dev:principals:agent:openai:codex"
    registry = {
        "version": 1,
        "principals": {
            canonical_did: {
                "principal_did": canonical_did,
                "status": "active",
                "principal_key_refs": ["openai:agent:codex"],
                "metadata": {
                    "actor_type": "agent",
                    "delegated_authority": {
                        "delegation_mode": "delegated_only",
                        "delegated_by_principal_did": "did:key:z6MkOperator",
                        "ledger_scope": ["chat-demo"],
                        "surface_scope": ["surface:chat:primary"],
                    },
                },
            }
        },
    }
    db = {PRINCIPAL_REGISTRY_V1_KEY: json.dumps(registry).encode("utf-8")}
    req = _make_request(
        headers={
            "x-principal-did": runtime_did,
            "x-principal-key-id": "openai:agent:codex",
            "x-principal-type": "agent",
            "x-delegated-cli-request": "true",
            "x-delegated-by-principal-did": "did:key:z6MkOperator",
            "x-delegated-ledger-scope": "chat-demo",
            "x-delegated-surface-scope": "surface:chat:primary",
            "x-surface-id": "surface:chat:primary",
        },
        db=db,
    )
    authorize_or_raise(req, ledger_id="chat-demo", action="ledger.write", explicit_context=True)
    assert getattr(req.state, "auth_claim_principal_did", None) == canonical_did
    diagnostics = authz_diagnostics_from_request(req)
    assert diagnostics["principal_registry_status"] == "active"
    assert diagnostics["principal_registry_source"] == "backend_registry"
