from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import backend.api.chat as chat_module
from backend.api.auth import router as auth_router
from backend.api.chat import assess_router as chat_assess_router, router as chat_router
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.services.session_tokens import (
    mint_refresh_token,
    apply_session_token_claims_or_raise,
    mint_session_token,
    mint_surface_session_bundle,
    validate_session_token,
)


def _make_commit_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}

    @app.middleware("http")
    async def _session_claim_middleware(request, call_next):
        try:
            apply_session_token_claims_or_raise(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    app.include_router(chat_assess_router)
    return TestClient(app)


def _make_auth_commit_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}

    @app.middleware("http")
    async def _session_claim_middleware(request, call_next):
        try:
            apply_session_token_claims_or_raise(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    app.include_router(auth_router)
    app.include_router(chat_assess_router)
    return TestClient(app)


def _make_stream_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}

    @app.middleware("http")
    async def _session_claim_middleware(request, call_next):
        try:
            apply_session_token_claims_or_raise(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    app.include_router(chat_router)
    return TestClient(app)


def test_session_token_mint_and_validate_round_trip() -> None:
    minted = mint_session_token(
        principal_did="did:key:z6MkTokenRoundTrip",
        principal_key_id="did:key:z6MkTokenRoundTrip#k1",
        roles=["writer"],
        allowed_context_ids=["ctx:test"],
        ledger_ids=["chat-team-b"],
        ttl_seconds=600,
    )
    claims = validate_session_token(minted["token"])
    assert claims["sub"] == "did:key:z6MkTokenRoundTrip"
    assert claims["principal_key_id"] == "did:key:z6MkTokenRoundTrip#k1"
    assert claims["roles"] == ["writer"]
    assert claims["allowed_context_ids"] == ["ctx:test"]
    assert claims["ledger_ids"] == ["chat-team-b"]
    assert claims["auth_method"] == "passkey"
    assert claims["token_use"] == "access"


def test_surface_session_bundle_mints_access_and_refresh_tokens_with_distinct_ttls() -> None:
    minted = mint_surface_session_bundle(
        principal_did="did:key:z6MkBundle",
        principal_key_id="did:key:z6MkBundle#k1",
    )
    access_claims = validate_session_token(minted["session"]["token"])
    refresh_claims = validate_session_token(minted["refresh_session"]["token"], required_token_use="refresh")
    assert access_claims["sub"] == "did:key:z6MkBundle"
    assert refresh_claims["sub"] == "did:key:z6MkBundle"
    assert access_claims["session_family_id"] == refresh_claims["session_family_id"]
    assert int(refresh_claims["exp"]) > int(access_claims["exp"])


def test_access_validator_rejects_refresh_token() -> None:
    minted = mint_refresh_token(principal_did="did:key:z6MkRefreshOnly")
    try:
        validate_session_token(minted["token"])
    except Exception as exc:
        assert getattr(exc, "reason", "") == "token_use_invalid"
        return
    raise AssertionError("Expected token_use_invalid validation error")


def test_session_token_validator_reports_expired_reason() -> None:
    minted = mint_session_token(
        principal_did="did:key:z6MkExpired",
        ttl_seconds=-1,
    )
    try:
        validate_session_token(minted["token"])
    except Exception as exc:
        assert getattr(exc, "reason", "") == "token_expired"
        return
    raise AssertionError("Expected token_expired validation error")


def test_commit_answer_accepts_bearer_session_token_and_persists_did_provenance() -> None:
    client = _make_commit_client()
    client.app.state.db[b"__ledgers_v1__"] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                "chat-team-b": {
                    "ledger_id": "chat-team-b",
                    "display_name": "Chat Team B",
                    "namespace": "chat-team-b",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-team-b",
                    "metadata": {
                        "founding_constitution": {
                            "name": "LOAM",
                            "purpose": "Carry governed support memory.",
                            "source": "control_plane_operator",
                        },
                        "ledger_alias_history": ["ledger:chat-team-b"],
                        "ledger_supersession_history": [],
                        "ledger_consolidation_history": [],
                    },
                }
            },
        }
    ).encode()
    minted = mint_session_token(
        principal_did="did:key:z6MkBearerCommit",
        principal_key_id="did:key:z6MkBearerCommit#k1",
        ttl_seconds=600,
    )
    token = minted["token"]
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "session_id": "s1",
                "turn_id": "t1",
            },
        },
        headers={
            "x-ledger-id": "chat-team-b",
            "x-principal-type": "user",
            "authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)
    response_metadata = resp.json().get("metadata") if isinstance(resp.json().get("metadata"), dict) else {}
    response_coord_meta = response_metadata.get("coord_meta") if isinstance(response_metadata.get("coord_meta"), dict) else {}
    assert response_coord_meta.get("coord") == coordinate
    assert response_coord_meta.get("runtime_namespace") == "chat-team-b"
    assert response_coord_meta.get("canonical_subject") == "did:web:id.dualsubstrate.com:ledgers:chat-team-b"

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    contributor = metadata.get("contributor") if isinstance(metadata.get("contributor"), dict) else {}
    assert contributor.get("principal_did") == "did:key:z6MkBearerCommit"
    assert contributor.get("principal_key_id") == "did:key:z6MkBearerCommit#k1"
    assert isinstance(contributor.get("session_jti"), str) and contributor.get("session_jti")
    assert metadata.get("auth_method") == "passkey"
    dual = metadata.get("provenance_dual_write") if isinstance(metadata.get("provenance_dual_write"), dict) else {}
    assert dual.get("status") == "dual_write_ok"
    assert metadata.get("retention_tier") == "Clay"
    assert metadata.get("retention_tier_reason") == "durable_ledger_write_path"
    gravity_tax = metadata.get("gravity_tax_policy") if isinstance(metadata.get("gravity_tax_policy"), dict) else {}
    assert gravity_tax.get("gravity_tax_contract_version") == "gravity-tax-v1"
    assert gravity_tax.get("explicit_retention_cost_policy") is True
    assert gravity_tax.get("retention_tier") == "Clay"
    assert gravity_tax.get("governed_promotion_required") is True
    runtime_identity = metadata.get("runtime_identity") if isinstance(metadata.get("runtime_identity"), dict) else {}
    assert runtime_identity.get("ledger_canonical_subject") == "did:web:id.dualsubstrate.com:ledgers:chat-team-b"
    library_boundary = runtime_identity.get("library_boundary") if isinstance(runtime_identity.get("library_boundary"), dict) else {}
    assert library_boundary.get("canonical_ledger_id") == "chat-team-b"
    assert library_boundary.get("registry_source") == "registered_ledger_v1"
    assert library_boundary.get("river_mutates_library_directly") is False
    assert library_boundary.get("hot_path_mode") == "summary_only"
    assert library_boundary.get("foundation_identity", {}).get("name") == "LOAM"
    assert library_boundary.get("history_continuity", {}).get("alias_aware_coord_history_lookup") is True
    assert library_boundary.get("history_continuity", {}).get("foundation_identity_available_after_consolidation") is True
    assert library_boundary.get("alias_history") == ["ledger:chat-team-b"]


def test_commit_answer_resolves_superseded_ledger_alias_to_canonical_namespace() -> None:
    client = _make_commit_client()
    client.app.state.db[b"__ledgers_v1__"] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                "chat-team-b": {
                    "ledger_id": "chat-team-b",
                    "display_name": "Chat Team B",
                    "namespace": "chat-team-b",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-team-b",
                    "metadata": {
                        "founding_constitution": {
                            "name": "LOAM",
                            "purpose": "Carry governed support memory.",
                            "source": "control_plane_operator",
                        },
                        "ledger_alias_history": ["ledger:chat-team-b", "ledger:loam-137to139"],
                        "ledger_supersession_history": ["ledger:loam-137to139"],
                        "ledger_consolidation_history": [{"event": "ledger_split_consolidated"}],
                    },
                },
                "ledger:loam-137to139": {
                    "ledger_id": "ledger:loam-137to139",
                    "display_name": "LOAM 137 to 139",
                    "namespace": "ledger:loam-137to139",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:ledger:loam-137to139",
                    "metadata": {
                        "superseded_by_ledger_id": "chat-team-b",
                        "canonical_ledger_id": "chat-team-b",
                        "ledger_alias_history": ["ledger:loam-137to139"],
                        "ledger_supersession_history": [],
                        "ledger_consolidation_history": [{"event": "superseded_by_consolidation"}],
                    },
                },
            },
        }
    ).encode()
    minted = mint_session_token(
        principal_did="did:key:z6MkAliasCommit",
        principal_key_id="did:key:z6MkAliasCommit#k1",
        ttl_seconds=600,
    )
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "ledger:loam-137to139",
            "context_id": "ctx:test",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {"session_id": "s2", "turn_id": "t2"},
        },
        headers={
            "x-ledger-id": "ledger:loam-137to139",
            "x-principal-type": "user",
            "authorization": f"Bearer {minted['token']}",
        },
    )
    assert resp.status_code == 200
    response_metadata = resp.json().get("metadata") if isinstance(resp.json().get("metadata"), dict) else {}
    response_coord_meta = response_metadata.get("coord_meta") if isinstance(response_metadata.get("coord_meta"), dict) else {}
    assert response_coord_meta.get("runtime_namespace") == "chat-team-b"

    runtime_identity = response_metadata.get("runtime_identity") if isinstance(response_metadata.get("runtime_identity"), dict) else {}
    assert runtime_identity.get("ledger_id") == "chat-team-b"
    assert runtime_identity.get("runtime_namespace") == "chat-team-b"
    library_boundary = runtime_identity.get("library_boundary") if isinstance(runtime_identity.get("library_boundary"), dict) else {}
    assert library_boundary.get("canonical_ledger_id") == "chat-team-b"
    assert library_boundary.get("requested_ledger_id") == "chat-team-b"
    assert library_boundary.get("alias_resolution_applied") is False
    assert library_boundary.get("alias_history") == ["ledger:chat-team-b", "ledger:loam-137to139"]


def test_commit_answer_persists_delegated_codex_audit_metadata() -> None:
    client = _make_commit_client()
    client.app.state.db[b"__ledgers_v1__"] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                "chat-demo": {
                    "ledger_id": "chat-demo",
                    "display_name": "Chat Demo",
                    "namespace": "chat-demo",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    "metadata": {
                        "ledger_alias_history": [],
                        "ledger_supersession_history": [],
                        "ledger_consolidation_history": [],
                    },
                }
            },
        }
    ).encode()
    client.app.state.db[b"__principals_v1__"] = json.dumps(
        {
            "version": 1,
            "principals": {
                "did:web:id.dualsubstrate.com:principals:agent:openai:codex": {
                    "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                    "display_name": "OpenAI Codex",
                    "status": "active",
                    "tenant_id": "tenant:demo",
                    "metadata": {
                        "actor_type": "agent",
                        "delegated_authority": {
                            "delegation_mode": "delegated_only",
                            "delegated_prompt_execution": "explicit_cli_request_required",
                            "revocable": True,
                            "revocation_mode": "control_plane_operator",
                            "ledger_scope": ["chat-demo"],
                            "surface_scope": ["surface:chat:primary"],
                            "delegated_by_principal_did": "did:key:z6MkOperator",
                        },
                    },
                }
            },
        }
    ).encode()
    minted = mint_session_token(
        principal_did="did:web:id.dualsubstrate.com:principals:agent:openai:codex",
        principal_key_id="openai:agent:codex",
        ttl_seconds=600,
    )
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-demo",
            "ledger_id": "chat-demo",
            "context_id": "ctx:test",
            "user_message": "Run the delegated test.",
            "assistant_reply": "Delegated audit should persist.",
            "metadata": {"session_id": "codex-s1", "turn_id": "codex-t1"},
        },
        headers={
            "x-ledger-id": "chat-demo",
            "x-principal-id": "openai:codex",
            "x-principal-type": "agent",
            "x-delegated-cli-request": "true",
            "x-delegated-by-principal-did": "did:key:z6MkOperator",
            "x-delegated-ledger-scope": "chat-demo",
            "x-delegated-surface-scope": "surface:chat:primary",
            "x-surface-id": "surface:chat:primary",
            "authorization": f"Bearer {minted['token']}",
        },
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    contributor = metadata.get("contributor") if isinstance(metadata.get("contributor"), dict) else {}
    assert contributor.get("principal_id") == "openai:codex"
    assert contributor.get("principal_type") == "agent"
    delegated = metadata.get("delegated_prompt_path") if isinstance(metadata.get("delegated_prompt_path"), dict) else {}
    assert delegated.get("active") is True
    assert delegated.get("delegation_mode") == "delegated_only"
    assert delegated.get("audit_posture") == "requested_by_operator_executed_by_delegated_principal"
    assert delegated.get("requested_by_principal_did") == "did:key:z6MkOperator"
    assert delegated.get("prompt_principal_did") == "did:web:id.dualsubstrate.com:principals:agent:openai:codex"
    assert delegated.get("prompt_principal_id") == "openai:codex"
    assert delegated.get("target_ledger_id") == "chat-demo"
    assert delegated.get("target_surface_id") == "surface:chat:primary"
    assert delegated.get("ledger_scope") == ["chat-demo"]
    assert delegated.get("surface_scope") == ["surface:chat:primary"]
    assert delegated.get("cli_request_required") is True


def test_chat_stream_accepts_bearer_session_token_and_persists_did_provenance(monkeypatch) -> None:
    async def _fake_assemble_context(**_kwargs):
        return {"recent": [], "claims": [], "retrieved": [], "assessments": {}}

    async def _fake_stream(**_kwargs):
        async def _tokens():
            for token in ("Hello", " world"):
                yield token

        fut: asyncio.Future[str] = asyncio.Future()
        fut.set_result("stop")
        return _tokens(), fut

    monkeypatch.setattr(chat_module, "assemble_context", _fake_assemble_context)
    monkeypatch.setattr(chat_module, "yield_chat_stream", _fake_stream)

    client = _make_stream_client()
    client.app.state.db[b"__ledgers_v1__"] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                "chat-team-b": {
                    "ledger_id": "chat-team-b",
                    "display_name": "Chat Team B",
                    "namespace": "chat-team-b",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-team-b",
                    "metadata": {
                        "founding_constitution": {
                            "name": "LOAM",
                            "purpose": "Carry governed support memory.",
                            "source": "control_plane_operator",
                        },
                        "ledger_alias_history": ["ledger:chat-team-b"],
                        "ledger_supersession_history": [],
                        "ledger_consolidation_history": [],
                    },
                }
            },
        }
    ).encode()
    minted = mint_session_token(
        principal_did="did:key:z6MkBearerStream",
        principal_key_id="did:key:z6MkBearerStream#k1",
        ttl_seconds=600,
    )
    token = minted["token"]
    resp = client.post(
        "/chat/stream",
        json={
            "session_id": "s1",
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
        headers={
            "x-ledger-id": "chat-team-b",
            "x-principal-type": "user",
            "authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 200
    events = [
        json.loads(line)
        for line in resp.text.splitlines()
        if line.strip()
    ]
    meta = next((event for event in events if event.get("type") == "meta"), {})
    coordinate = meta.get("coordinate")
    assert isinstance(coordinate, str) and coordinate

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    contributor = metadata.get("contributor") if isinstance(metadata.get("contributor"), dict) else {}
    assert contributor.get("principal_did") == "did:key:z6MkBearerStream"
    assert contributor.get("principal_key_id") == "did:key:z6MkBearerStream#k1"
    assert isinstance(contributor.get("session_jti"), str) and contributor.get("session_jti")
    assert metadata.get("auth_method") == "passkey"
    dual = metadata.get("provenance_dual_write") if isinstance(metadata.get("provenance_dual_write"), dict) else {}
    assert dual.get("status") == "dual_write_ok"
    assert metadata.get("retention_tier") == "Clay"
    assert metadata.get("retention_tier_reason") == "durable_ledger_write_path"
    gravity_tax = metadata.get("gravity_tax_policy") if isinstance(metadata.get("gravity_tax_policy"), dict) else {}
    assert gravity_tax.get("gravity_tax_contract_version") == "gravity-tax-v1"
    assert gravity_tax.get("explicit_retention_cost_policy") is True
    assert gravity_tax.get("retention_tier") == "Clay"
    assert gravity_tax.get("governed_promotion_required") is True
    runtime_identity = metadata.get("runtime_identity") if isinstance(metadata.get("runtime_identity"), dict) else {}
    library_boundary = runtime_identity.get("library_boundary") if isinstance(runtime_identity.get("library_boundary"), dict) else {}
    assert library_boundary.get("canonical_ledger_id") == "chat-team-b"
    assert library_boundary.get("registry_source") == "registered_ledger_v1"
    assert library_boundary.get("river_mutates_library_directly") is False


def test_commit_answer_rejects_expired_bearer_token_with_deterministic_reason() -> None:
    client = _make_commit_client()
    minted = mint_session_token(
        principal_did="did:key:z6MkBearerExpired",
        ttl_seconds=-1,
    )
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {"session_id": "s1", "turn_id": "t1"},
        },
        headers={
            "x-ledger-id": "chat-team-b",
            "x-principal-type": "user",
            "authorization": f"Bearer {minted['token']}",
        },
    )
    assert resp.status_code == 401
    payload = resp.json()
    detail = payload.get("detail") if isinstance(payload, dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("error") == "token_validation_failed"
    assert detail.get("reason") == "token_expired"


def test_revoked_credential_blocks_token_authenticated_write(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_auth_commit_client()
    principal_did = "did:key:z6MkRevokedCred"
    credential_id = "cred-revoked-1"
    client.app.state.db[b"__principals_v1__"] = (
        b'{"version":1,"principals":{"did:key:z6MkRevokedCred":{"principal_did":"did:key:z6MkRevokedCred","status":"active"}}}'
    )
    client.app.state.db[b"__passkey_bindings_v1__"] = (
        b'{"version":1,"bindings":{"cred-revoked-1":{"credential_id":"cred-revoked-1","principal_did":"did:key:z6MkRevokedCred","status":"active","public_key_pem":"pem","sign_count":2}}}'
    )

    mint_resp = client.post(
        "/auth/token",
        json={
            "principal_did": principal_did,
            "credential_id": credential_id,
            "ttl_seconds": 600,
        },
    )
    assert mint_resp.status_code == 200
    token = mint_resp.json()["session"]["token"]

    revoke_resp = client.post(
        f"/auth/passkeys/{credential_id}/revoke",
        json={"reason": "incident"},
        headers={"x-admin-token": "test-admin-token"},
    )
    assert revoke_resp.status_code == 200

    write_resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {"session_id": "s1", "turn_id": "t1"},
        },
        headers={
            "x-ledger-id": "chat-team-b",
            "x-principal-type": "user",
            "authorization": f"Bearer {token}",
        },
    )
    assert write_resp.status_code == 401
    detail = write_resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "token_validation_failed"
    assert detail.get("reason") == "token_credential_revoked"


def test_revoked_session_jti_blocks_token_authenticated_write(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_auth_commit_client()
    minted = mint_session_token(
        principal_did="did:key:z6MkRevokedSession",
        ttl_seconds=600,
    )
    token = minted["token"]
    claims = validate_session_token(token)
    revoke_resp = client.post(
        "/auth/sessions/revoke",
        json={"jti": claims["jti"], "reason": "incident"},
        headers={"x-admin-token": "test-admin-token"},
    )
    assert revoke_resp.status_code == 200

    write_resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {"session_id": "s1", "turn_id": "t1"},
        },
        headers={
            "x-ledger-id": "chat-team-b",
            "x-principal-type": "user",
            "authorization": f"Bearer {token}",
        },
    )
    assert write_resp.status_code == 401
    detail = write_resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "token_validation_failed"
    assert detail.get("reason") == "token_revoked"
