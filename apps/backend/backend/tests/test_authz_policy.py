from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request

from backend.services.authz import apply_auth_claim_overrides, authorize_or_raise, principal_from_request


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    path: str = "/ledger/read",
    db: dict[bytes, bytes] | None = None,
) -> Request:
    raw_headers: list[tuple[bytes, bytes]] = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("utf-8"), str(value).encode("utf-8")))
    app = SimpleNamespace(state=SimpleNamespace(db=(db if db is not None else {})))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "app": app,
    }
    return Request(scope)


def _registry_v1_payload() -> dict[bytes, bytes]:
    registry = {
        "version": 1,
        "ledgers": {
            "chat-acme": {
                "ledger_id": "chat-acme",
                "tenant_id": "tenant-acme",
                "owner_principal_id": "alice",
                "owner_principal_type": "user",
                "policy_profile": "standard",
                "status": "active",
                "metadata": {},
            }
        },
    }
    return {b"__ledgers_v1__": json.dumps(registry).encode("utf-8")}


def _registry_v1_payload_with_contexts(*contexts: str) -> dict[bytes, bytes]:
    payload = _registry_v1_payload()
    decoded = json.loads(payload[b"__ledgers_v1__"].decode("utf-8"))
    decoded["ledgers"]["chat-acme"]["metadata"] = {
        "allowed_context_ids": [ctx for ctx in contexts if ctx],
    }
    return {b"__ledgers_v1__": json.dumps(decoded).encode("utf-8")}


def test_registry_mode_allows_owner_write(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    req = _make_request(
        headers={"x-principal-id": "alice", "x-principal-type": "user"},
        db=_registry_v1_payload(),
    )
    authorize_or_raise(req, ledger_id="chat-acme", action="ledger.write", explicit_context=True)


def test_registry_mode_allows_tenant_write(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    req = _make_request(
        headers={
            "x-principal-id": "bob",
            "x-principal-type": "user",
            "x-tenant-id": "tenant-acme",
        },
        db=_registry_v1_payload(),
    )
    authorize_or_raise(req, ledger_id="chat-acme", action="ledger.write", explicit_context=True)


def test_registry_mode_allows_owner_feedback(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    req = _make_request(
        headers={"x-principal-id": "alice", "x-principal-type": "user"},
        db=_registry_v1_payload(),
    )
    authorize_or_raise(req, ledger_id="chat-acme", action="ledger.feedback", explicit_context=True)


def test_registry_mode_blocks_write_for_other_tenant(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    req = _make_request(
        headers={
            "x-principal-id": "carol",
            "x-principal-type": "user",
            "x-tenant-id": "tenant-other",
        },
        db=_registry_v1_payload(),
    )
    try:
        authorize_or_raise(req, ledger_id="chat-acme", action="ledger.write", explicit_context=True)
    except HTTPException as exc:
        assert exc.status_code == 403
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        assert detail.get("reason") == "write_requires_owner_or_tenant"
        return
    raise AssertionError("Expected 403 for tenant mismatch write")


def test_registry_mode_admin_path_requires_admin_principal(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    req = _make_request(
        headers={
            "x-principal-id": "alice",
            "x-principal-type": "user",
            "x-tenant-id": "tenant-acme",
        },
        path="/admin/ledgers",
        db=_registry_v1_payload(),
    )
    try:
        authorize_or_raise(req, ledger_id="chat-acme", action="ledger.read", explicit_context=True)
    except HTTPException as exc:
        assert exc.status_code == 403
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        assert detail.get("reason") == "admin_principal_required"
        return
    raise AssertionError("Expected 403 for non-admin principal on admin path")


def test_registry_mode_admin_path_allows_admin_principal(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    req = _make_request(
        headers={
            "x-principal-id": "ops",
            "x-principal-type": "admin",
            "x-tenant-id": "tenant-ops",
        },
        path="/admin/ledgers",
        db=_registry_v1_payload(),
    )
    authorize_or_raise(req, ledger_id="chat-acme", action="ledger.read", explicit_context=True)


def test_registry_mode_unknown_ledger_can_deny(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    req = _make_request(
        headers={"x-principal-id": "alice", "x-principal-type": "user"},
        db=_registry_v1_payload(),
    )
    try:
        authorize_or_raise(req, ledger_id="chat-missing", action="ledger.read", explicit_context=True)
    except HTTPException as exc:
        assert exc.status_code == 403
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        assert detail.get("reason") == "unknown_ledger"
        return
    raise AssertionError("Expected 403 for unknown ledger when policy=deny")


def test_registry_mode_blocks_write_when_context_not_allowed(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    req = _make_request(
        headers={
            "x-principal-id": "alice",
            "x-principal-type": "user",
            "x-context-id": "ctx:decoder",
        },
        db=_registry_v1_payload_with_contexts("ctx:frontend"),
    )
    try:
        authorize_or_raise(req, ledger_id="chat-acme", action="ledger.write", explicit_context=True)
    except HTTPException as exc:
        assert exc.status_code == 403
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        assert detail.get("reason") == "context_not_allowed"
        return
    raise AssertionError("Expected 403 for disallowed context")


def test_registry_mode_allows_write_when_context_allowed(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    req = _make_request(
        headers={
            "x-principal-id": "alice",
            "x-principal-type": "user",
            "x-context-id": "ctx:frontend",
        },
        db=_registry_v1_payload_with_contexts("ctx:frontend", "ctx:decoder"),
    )
    authorize_or_raise(req, ledger_id="chat-acme", action="ledger.write", explicit_context=True)


def test_did_strict_blocks_when_principal_did_missing(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("AUTH_PRINCIPAL_MODE", "did_strict")
    req = _make_request(
        headers={"x-principal-id": "alice", "x-principal-type": "user"},
        db=_registry_v1_payload(),
    )
    try:
        authorize_or_raise(req, ledger_id="chat-acme", action="ledger.write", explicit_context=True)
    except HTTPException as exc:
        assert exc.status_code == 403
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        assert detail.get("reason") == "did_principal_required"
        assert detail.get("principal_mode") == "did_strict"
        return
    raise AssertionError("Expected 403 when did_strict mode lacks principal DID")


def test_did_strict_allows_when_principal_did_present(monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("AUTH_PRINCIPAL_MODE", "did_strict")
    req = _make_request(
        headers={
            "x-principal-did": "did:key:z6Mkexample",
            "x-principal-id": "alice",
            "x-principal-type": "user",
        },
        db=_registry_v1_payload(),
    )
    authorize_or_raise(req, ledger_id="chat-acme", action="ledger.write", explicit_context=True)


def test_request_state_claim_overrides_feed_principal_resolution() -> None:
    req = _make_request(
        headers={"x-principal-id": "alice", "x-principal-type": "user"},
        db=_registry_v1_payload(),
    )
    apply_auth_claim_overrides(
        req,
        principal_did="did:key:z6Mkoverride",
        principal_key_id="did:key:z6Mkoverride#k1",
        session_jti="jti-override",
    )
    principal = principal_from_request(req)
    assert principal.principal_id == "alice"
    assert principal.principal_did == "did:key:z6Mkoverride"
    assert principal.principal_key_id == "did:key:z6Mkoverride#k1"
    assert principal.session_jti == "jti-override"
    assert principal.source == "did_header"
