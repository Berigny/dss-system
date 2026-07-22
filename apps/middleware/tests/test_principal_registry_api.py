from __future__ import annotations

from pathlib import Path
import json
import logging
from typing import TypedDict
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import app as app_module
from utils.principal_link_challenges import PrincipalLinkChallenges
from utils.principal_registry import PrincipalRegistry
from utils.verified_id_requests import VerifiedIDRequests


class _ResolverCall(TypedDict):
    url: str
    params: dict[str, str] | None
    headers: dict[str, str] | None


def _client_with_registry(tmp_path: Path) -> TestClient:
    app_module.PRINCIPAL_REGISTRY = PrincipalRegistry(tmp_path / "principal_registry.json")
    app_module.PRINCIPAL_LINK_CHALLENGES = PrincipalLinkChallenges(tmp_path / "principal_link_challenges.json")
    app_module.VERIFIED_ID_REQUESTS = VerifiedIDRequests(tmp_path / "verified_id_requests.json")
    return TestClient(app_module.app)


def test_control_plane_registry_endpoints_proxy_backend(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    calls: list[tuple[str, str, dict | None, dict[str, str]]] = []

    async def fake_backend_fetch(request, *, method: str, path: str, payload=None):
        headers = app_module._control_plane_backend_headers(request, payload)
        calls.append((method, path, payload, headers))
        if path == "/api/control-plane/submissions":
            if method == "GET":
                return {"status": "ok", "submissions": [{"submission_ref": "cps:test", "submission_status": "submitted"}]}
            submission_payload = payload if isinstance(payload, dict) else {}
            return {
                "status": "ok",
                "execution_mode": "submitted_for_approval",
                "submission_status": "submitted",
                "submission_ref": "cps:test",
                "submitted_at": "2026-04-09T00:00:00Z",
                "submission": {"submission_ref": "cps:test", "target_path": str(submission_payload.get("target_path") or "")},
            }
        if path == "/api/control-plane/submissions/cps:test/review":
            return {
                "status": "ok",
                "execution_mode": "submitted_for_approval",
                "submission_status": "applied",
                "submission_ref": "cps:test",
                "approved_at": "2026-04-09T00:01:00Z",
                "applied_at": "2026-04-09T00:02:00Z",
                "submission": {"submission_ref": "cps:test", "submission_status": "applied"},
            }
        if path == "/api/control-plane/providers":
            return {"status": "ok", "provider": {"provider_id": "provider:openrouter:shared", "provider_type": "OpenRouter", "secret_present": True}}
        if path == "/api/control-plane/model-bindings":
            return {"status": "ok", "model_binding": {"binding_id": "binding:chat:default", "provider_type": "OpenRouter", "model_id": "openai/gpt-4o"}}
        if path == "/api/control-plane/ledgers" and method == "GET":
            return {"status": "ok", "ledgers": [{"ledger_id": "ledger:test", "canonical_subject": "did:web:testserver:ledgers:ledger-test", "row_family": "interaction", "shareability": "share-ready", "preferred_reference": {"value": "did:web:testserver:ledgers:ledger-test", "shareability": "share-ready", "kind": "ledger", "copy_role": "primary"}, "source_precedence": {"current_source": "backend_canonical_record", "order": ["backend_canonical_record", "middleware_governed_envelope", "dashboard_render_model", "display_alias"], "dashboard_inference_allowed": False}, "detail_panels": ["overview", "governance", "provenance", "payload"], "reference_aliases": [{"value": "ledger:test", "field": "ledger_id", "role": "supporting_alias", "shareability": "fallback-only"}]}]}
        if path == "/api/control-plane/surfaces" and method == "GET":
            return {"status": "ok", "surfaces": [{"surface_id": "surface:test", "row_family": "interaction", "detail_panels": ["overview", "governance", "provenance", "payload"]}]}
        if path == "/api/control-plane/relationships" and method == "GET":
            return {"status": "ok", "relationships": [{"relationship_id": "principal::did:key:z6MkPendingA::ledger::ledger:test", "row_family": "relationship", "detail_panels": ["overview", "permission_or_access", "governance", "provenance", "payload"]}]}
        if path == "/api/control-plane/principals":
            return {
                "status": "ok",
                "principal": {
                    "principal_did": "did:key:z6MkPendingA",
                    "status": "active",
                    "metadata": {"provisioning_state": "pending_provisioning", "ledger_id": "ledger:test"},
                },
            }
        if path == "/api/control-plane/entities/activate":
            return {
                "status": "ok",
                "entity_type": "principal",
                "principal": {
                    "principal_did": "did:key:z6MkPendingA",
                    "status": "active",
                    "metadata": {"provisioning_state": "active", "ledger_id": "ledger:test"},
                },
            }
        if path == "/api/control-plane/ledgers":
            return {"status": "ok", "ledger": {"ledger_id": "ledger:test", "status": "pending"}}
        if path == "/api/control-plane/surfaces":
            return {"status": "ok", "surface": {"surface_id": "surface:test", "status": "pending"}}
        return {"status": "ok", "relationship": {"relationship_id": "principal::did:key:z6MkPendingA::ledger::ledger:test", "permission_scope": "admin"}}

    monkeypatch.setattr(app_module, "_control_plane_backend_fetch", fake_backend_fetch)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")

    headers = {"x-principal-did": "did:key:z6MkOperatorA"}

    assert client.post("/api/control-plane/ledgers", headers=headers, json={"ledger_id": "ledger:test", "status": "pending", "tenant_id": "tenant:demo"}).status_code == 200
    provider_create = client.post("/api/control-plane/providers", headers=headers, json={"provider_id": "provider:openrouter:shared", "provider_type": "OpenRouter"})
    assert provider_create.status_code == 200
    assert provider_create.json()["idempotency_key"]
    binding_create = client.post("/api/control-plane/model-bindings", headers=headers, json={"binding_id": "binding:chat:default", "provider_type": "OpenRouter", "model_id": "openai/gpt-4o"})
    assert binding_create.status_code == 200
    assert binding_create.json()["idempotency_key"]
    ledger_create = client.post("/api/control-plane/ledgers", headers=headers, json={"ledger_id": "ledger:test-2", "status": "pending", "tenant_id": "tenant:demo"})
    assert ledger_create.status_code == 200
    assert ledger_create.json()["execution_mode"] == "direct_write"
    assert ledger_create.json()["submission_status"] == "applied"
    assert ledger_create.json()["idempotency_key"]
    principal_create = client.post(
        "/api/control-plane/principals",
        headers=headers,
        json={"principal_did": "did:key:z6MkPendingA", "status": "pending", "principal_type": "human", "ledger_id": "ledger:test"},
    )
    assert principal_create.status_code == 200
    assert principal_create.json()["provisioning"]["provisioning_state"] == "pending_provisioning"
    assert principal_create.json()["idempotency_key"]
    assert client.post("/api/control-plane/surfaces", headers=headers, json={"surface_id": "surface:test", "status": "pending"}).status_code == 200
    relationship_create = client.post(
        "/api/control-plane/relationships",
        headers=headers,
        json={"subject_entity_type": "principal", "subject_entity_id": "did:key:z6MkPendingA", "object_entity_type": "ledger", "object_entity_id": "ledger:test"},
    )
    assert relationship_create.status_code == 200
    assert relationship_create.json()["idempotency_key"]
    assert relationship_create.json()["execution_mode"] == "submitted_for_approval"
    assert relationship_create.json()["submitted_at"] == "2026-04-09T00:00:00Z"
    assert "applied_at" not in relationship_create.json()
    ledgers_rows = client.get("/api/control-plane/ledgers", headers=headers).json()["ledgers"]
    assert ledgers_rows[0]["preferred_reference"]["value"] == "did:web:testserver:ledgers:ledger-test"
    assert ledgers_rows[0]["source_precedence"]["current_source"] == "backend_canonical_record"
    relationships_rows = client.get("/api/control-plane/relationships", headers=headers).json()["relationships"]
    assert relationships_rows[0]["row_family"] == "relationship"
    assert relationships_rows[0]["detail_panels"][1] == "permission_or_access"
    assert client.get("/api/control-plane/ledgers", headers=headers).status_code == 200
    assert client.get("/api/control-plane/submissions", headers=headers).status_code == 200
    assert client.get("/api/control-plane/surfaces", headers=headers).status_code == 200
    assert client.get("/api/control-plane/relationships", headers=headers).status_code == 200
    activate = client.post(
        "/api/control-plane/entities/activate",
        headers=headers,
        json={"entity_type": "principal", "entity_id": "did:key:z6MkPendingA", "status": "active"},
    )
    assert activate.status_code == 200
    assert activate.json()["execution_mode"] == "submitted_for_approval"
    assert activate.json()["submission_ref"] == "cps:test"

    assert ("POST", "/api/control-plane/ledgers") == calls[0][:2]
    first_payload = calls[0][2]
    assert isinstance(first_payload, dict)
    assert first_payload["namespace"] == "ledger:test"
    assert first_payload["canonical_subject"] == "did:web:testserver:ledgers:ledger-test"
    assert first_payload["canonical_subject_source"] == "did:web:ledger"
    assert calls[0][3]["x-admin-token"] == "test-admin-token"
    assert calls[0][3]["x-principal-id"] == "did:key:z6MkOperatorA"
    surface_calls = [row for row in calls if row[0] == "POST" and row[1] == "/api/control-plane/surfaces"]
    assert surface_calls
    first_surface_payload = surface_calls[0][2]
    assert isinstance(first_surface_payload, dict)
    assert first_surface_payload["canonical_subject"] == "did:web:testserver:surfaces:surface-test"
    assert first_surface_payload["canonical_subject_source"] == "did:web:surface"
    relationship_submission_calls = [row for row in calls if row[0] == "POST" and row[1] == "/api/control-plane/submissions"]
    assert relationship_submission_calls
    relationship_payload = next(
        row[2]["payload"]
        for row in relationship_submission_calls
        if isinstance(row[2], dict) and row[2].get("target_path") == "/api/control-plane/relationships"
    )
    assert relationship_payload["canonical_subject"] == "did:web:testserver:relationships:principal-did-key-z6mkpendinga-ledger-ledger-test"
    assert relationship_payload["canonical_subject_source"] == "did:web:relationship"
    mutation_calls = [row for row in calls if row[0] == "POST" and row[1].startswith("/api/control-plane/")]
    assert all(isinstance(row[2], dict) and str(row[2].get("idempotency_key") or "").strip() for row in mutation_calls)
    assert any(row[1] == "/api/control-plane/submissions" for row in mutation_calls)


def test_control_plane_guarded_mutations_require_break_glass_for_direct_write(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)

    async def fake_backend_fetch(request, *, method: str, path: str, payload=None):
        if path == "/api/control-plane/submissions":
            return {"status": "ok", "execution_mode": "submitted_for_approval", "submission_status": "submitted", "submission_ref": "cps:test", "submitted_at": "2026-04-09T00:00:00Z"}
        return {"status": "ok"}

    monkeypatch.setattr(app_module, "_control_plane_backend_fetch", fake_backend_fetch)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")

    headers = {"x-principal-did": "did:key:z6MkOperatorA"}
    denied = client.post(
        "/api/control-plane/relationships",
        headers=headers,
        json={
            "subject_entity_type": "principal",
            "subject_entity_id": "did:key:z6MkPendingA",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:test",
            "governance_mode": "direct_write",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"] == "break_glass_required"


def test_control_plane_break_glass_direct_write_emits_audit_trace(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_backend_fetch(request, *, method: str, path: str, payload=None):
        calls.append((method, path, payload))
        if path == "/api/control-plane/entities/activate":
            return {"status": "ok", "entity_type": "principal", "principal": {"principal_did": "did:key:z6MkPendingA", "status": "active", "metadata": {"provisioning_state": "active"}}}
        return {"status": "ok"}

    monkeypatch.setattr(app_module, "_control_plane_backend_fetch", fake_backend_fetch)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")

    headers = {"x-principal-did": "did:key:z6MkOperatorA"}
    resp = client.post(
        "/api/control-plane/entities/activate",
        headers=headers,
        json={
            "entity_type": "principal",
            "entity_id": "did:key:z6MkPendingA",
            "status": "active",
            "governance_mode": "direct_write",
            "break_glass": {
                "actor": "operator:architect",
                "reason_code": "tenant_outage_recovery",
                "scope": "principal_activation",
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["policy_trace"]["policy_decision"] == "override"
    assert body["policy_trace"]["break_glass_active"] is True
    assert body["break_glass_audit"]["actor"] == "operator:architect"
    assert body["break_glass_audit"]["reason_code"] == "tenant_outage_recovery"
    assert any(row[1] == "/api/control-plane/entities/activate" for row in calls)


def test_control_plane_submission_response_carries_strict_default_policy_trace(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)

    async def fake_backend_fetch(request, *, method: str, path: str, payload=None):
        if path == "/api/control-plane/submissions":
            return {"status": "ok", "execution_mode": "submitted_for_approval", "submission_status": "submitted", "submission_ref": "cps:test", "submitted_at": "2026-04-09T00:00:00Z"}
        return {"status": "ok"}

    monkeypatch.setattr(app_module, "_control_plane_backend_fetch", fake_backend_fetch)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")

    headers = {"x-principal-did": "did:key:z6MkOperatorA"}
    resp = client.post(
        "/api/control-plane/entities/remove",
        headers=headers,
        json={"entity_type": "principal", "entity_id": "did:key:z6MkPendingA"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_mode"] == "submitted_for_approval"
    assert body["submitted_at"] == "2026-04-09T00:00:00Z"
    assert "applied_at" not in body
    assert body["policy_trace"]["policy_decision"] == "submit_for_approval"
    assert body["policy_trace"]["reason_code"] == "strict_default_submission"


def test_models_endpoint_uses_control_plane_chat_bindings(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(app_module.settings, "LLM_MODEL", "", raising=False)

    async def fake_fetch_local_models(timeout: float):
        return []

    monkeypatch.setattr(app_module, "_fetch_local_models", fake_fetch_local_models)

    async def fake_backend_fetch(request, *, method: str, path: str, payload=None):
        assert method == "GET"
        parsed = urlparse(path)
        assert parsed.path == "/api/control-plane/model-bindings"
        assert parse_qs(parsed.query).get("surface_id") == ["surface:chat:primary"]
        return {
            "status": "ok",
            "model_bindings": [
                {
                    "binding_id": "binding:chat:gemini",
                    "provider_type": "OpenRouter",
                    "model_id": "google/gemini-2.5-flash",
                    "name": "Google: Gemini 2.5 Flash",
                    "status": "active",
                    "app_surfaces": ["surface:chat:primary"],
                },
                {
                    "binding_id": "binding:chat:haiku",
                    "provider_type": "OpenRouter",
                    "model_id": "anthropic/claude-haiku-4.5",
                    "name": "Anthropic: Claude Haiku 4.5",
                    "status": "available",
                    "app_surfaces": ["surface:chat:primary"],
                },
                {
                    "binding_id": "binding:chat:other-surface",
                    "provider_type": "OpenRouter",
                    "model_id": "openai/gpt-5.1-chat",
                    "name": "OpenAI: GPT-5.1 Chat",
                    "status": "active",
                    "app_surfaces": ["surface:telegram:template"],
                },
            ],
        }

    monkeypatch.setattr(app_module, "_control_plane_backend_fetch", fake_backend_fetch)

    response = client.get("/api/models", headers={"accept": "application/json"})
    assert response.status_code == 200
    payload = response.json()
    online_models = payload.get("online_models")
    assert isinstance(online_models, list)
    assert [item.get("id") for item in online_models] == [
        "google/gemini-2.5-flash",
        "anthropic/claude-haiku-4.5",
    ]


def test_models_endpoint_excludes_telegram_template_binding(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(app_module.settings, "LLM_MODEL", "", raising=False)

    async def fake_fetch_local_models(timeout: float):
        return []

    monkeypatch.setattr(app_module, "_fetch_local_models", fake_fetch_local_models)

    async def fake_backend_fetch(request, *, method: str, path: str, payload=None):
        return {
            "status": "ok",
            "model_bindings": [
                {
                    "binding_id": "binding:chat:default",
                    "provider_type": "OpenRouter",
                    "model_id": "openai/gpt-4o",
                    "name": "OpenAI: GPT-4o",
                    "status": "active",
                    "app_surfaces": ["surface:chat:primary"],
                },
                {
                    "binding_id": "binding:telegram:template",
                    "provider_type": "OpenRouter",
                    "model_id": "binding:telegram:template",
                    "name": "Telegram Template",
                    "status": "active",
                    "app_surfaces": ["surface:chat:primary"],
                },
            ],
        }

    monkeypatch.setattr(app_module, "_control_plane_backend_fetch", fake_backend_fetch)

    response = client.get("/api/models", headers={"accept": "application/json"})
    assert response.status_code == 200
    online_models = response.json().get("online_models")
    assert [item.get("id") for item in online_models] == ["openai/gpt-4o"]


def test_principal_registry_upsert_get_list_disable_enable(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkTestA",
            "principal_key_refs": ["did:key:z6MkTestA#k1"],
            "tenant_id": "tenant:demo",
            "display_name": "Demo Principal",
            "metadata": {"source": "test", "actor_type": "service", "vc_status": "bound", "wallet_capable": False},
        },
    )
    assert create.status_code == 200
    created = create.json().get("principal")
    assert isinstance(created, dict)
    assert created.get("principal_did") == "did:key:z6MkTestA"
    assert created.get("status") == "active"
    metadata = created.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("actor_type") == "service"
    assert metadata.get("vc_status") == "bound"
    assert metadata.get("wallet_capable") is False
    assert created.get("canonical_subject") == "did:key:z6MkTestA"
    assert created.get("canonical_subject_source") == "principal_did"

    fetched = client.get("/api/principals/did:key:z6MkTestA")
    assert fetched.status_code == 200
    fetched_payload = fetched.json()
    assert fetched_payload.get("tenant_id") == "tenant:demo"

    listed = client.get("/api/principals?status=active&tenant_id=tenant:demo")
    assert listed.status_code == 200
    rows = listed.json().get("principals")
    assert isinstance(rows, list)
    assert any(row.get("principal_did") == "did:key:z6MkTestA" for row in rows)

    disabled = client.post(
        "/api/principals/did:key:z6MkTestA/disable",
        json={"reason": "incident"},
    )
    assert disabled.status_code == 200
    disabled_row = disabled.json().get("principal")
    assert isinstance(disabled_row, dict)
    assert disabled_row.get("status") == "disabled"

    enabled = client.post("/api/principals/did:key:z6MkTestA/enable", json={})
    assert enabled.status_code == 200
    enabled_row = enabled.json().get("principal")
    assert isinstance(enabled_row, dict)
    assert enabled_row.get("status") == "active"


def test_principal_registry_rejects_invalid_did(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)
    bad = client.post(
        "/api/principals",
        json={"principal_did": "user:alice", "principal_key_refs": []},
    )
    assert bad.status_code == 422


def test_principal_registry_bindings_resolve_and_conflict(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    first = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkModelA",
            "tenant_id": "tenant:demo",
            "display_name": "OpenRouter Actor",
            "metadata": {"actor_type": "model", "vc_status": "bound"},
        },
    )
    assert first.status_code == 200

    bind = client.post(
        "/api/principals/did:key:z6MkModelA/bindings",
        json={
            "binding_type": "openrouter:model",
            "binding_subject": "Anthropic/Claude-3.7-Sonnet",
            "tenant_id": "tenant:demo",
            "metadata": {
                "binding_type": "openrouter:model",
                "binding_subject": "anthropic/claude-3.7-sonnet",
                "issuer": "openrouter",
                "wallet_capable": False,
            },
        },
    )
    assert bind.status_code == 200
    principal = bind.json().get("principal")
    assert isinstance(principal, dict)
    refs = principal.get("principal_key_refs")
    assert isinstance(refs, list)
    assert "openrouter:model:anthropic/claude-3.7-sonnet" in refs
    assert principal.get("canonical_subject") == "openrouter:model:anthropic/claude-3.7-sonnet"
    assert principal.get("canonical_subject_source") == "binding:openrouter:model"
    metadata = principal.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("actor_type") == "model"
    assert metadata.get("issuer") == "openrouter"

    resolved = client.get(
        "/api/principals/resolve",
        params={"key_ref": "openrouter:model:Anthropic/Claude-3.7-Sonnet", "tenant_id": "tenant:demo"},
    )
    assert resolved.status_code == 200
    resolved_principal = resolved.json().get("principal")
    assert isinstance(resolved_principal, dict)
    assert resolved_principal.get("principal_did") == "did:key:z6MkModelA"

    second = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkModelB",
            "tenant_id": "tenant:demo",
            "display_name": "Duplicate Actor",
            "metadata": {"actor_type": "model"},
        },
    )
    assert second.status_code == 200

    conflict = client.post(
        "/api/principals/did:key:z6MkModelB/bindings",
        json={
            "binding_ref": "openrouter:model:anthropic/claude-3.7-sonnet",
            "tenant_id": "tenant:demo",
        },
    )
    assert conflict.status_code == 409
    assert conflict.text == "principal_key_ref already bound: openrouter:model:anthropic/claude-3.7-sonnet"


def test_principal_registry_resolve_returns_conflict_shape_for_duplicate_active_bindings(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)
    registry_path = tmp_path / "principal_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "principals": {
                    "did:key:z6MkConflictA": {
                        "principal_did": "did:key:z6MkConflictA",
                        "tenant_id": "tenant:demo",
                        "principal_key_refs": ["github:user:duplicate"],
                        "canonical_subject": "github:user:duplicate",
                        "canonical_subject_source": "binding:github:user",
                        "status": "active",
                        "metadata": {"actor_type": "service"},
                    },
                    "did:key:z6MkConflictB": {
                        "principal_did": "did:key:z6MkConflictB",
                        "tenant_id": "tenant:demo",
                        "principal_key_refs": ["github:user:duplicate"],
                        "canonical_subject": "github:user:duplicate:other",
                        "canonical_subject_source": "binding:github:user",
                        "status": "active",
                        "metadata": {"actor_type": "service"},
                    },
                },
                "subject_events": [],
                "standing_events": [],
                "binding_events": [],
            }
        ),
        encoding="utf-8",
    )

    resp = client.get("/api/principals/resolve", params={"key_ref": "github:user:duplicate", "tenant_id": "tenant:demo"})

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["outcome"] == "conflict"
    assert detail["canonical_principal_key_ref"] == "github:user:duplicate"
    assert [row["principal_did"] for row in detail["conflicting_principals"]] == [
        "did:key:z6MkConflictA",
        "did:key:z6MkConflictB",
    ]


def test_principal_registry_binding_events_capture_issuer_evidence_and_idempotency(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkBindAuditA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "model"},
        },
    )
    assert create.status_code == 200

    bind = client.post(
        "/api/principals/did:key:z6MkBindAuditA/bindings",
        json={
            "binding_ref": "openrouter:model:anthropic/claude-3.7-sonnet",
            "tenant_id": "tenant:demo",
            "issuer": "operator:review-board",
            "reason": "governed_activation",
            "evidence_refs": ["evidence:ticket:123", "evidence:proof:abc"],
            "idempotency_key": "bind-audit-1",
        },
    )
    assert bind.status_code == 200
    event = bind.json()["binding_event"]
    assert event["issuer"] == "operator:review-board"
    assert event["reason"] == "governed_activation"
    assert event["evidence_refs"] == ["evidence:ticket:123", "evidence:proof:abc"]
    assert event["idempotency_key"] == "bind-audit-1"

    replay = client.post(
        "/api/principals/did:key:z6MkBindAuditA/bindings",
        json={
            "binding_ref": "openrouter:model:anthropic/claude-3.7-sonnet",
            "tenant_id": "tenant:demo",
            "issuer": "operator:review-board",
            "reason": "governed_activation",
            "evidence_refs": ["evidence:ticket:123", "evidence:proof:abc"],
            "idempotency_key": "bind-audit-1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["binding_event"]["event_id"] == event["event_id"]

    listing = client.get("/api/principals/did:key:z6MkBindAuditA/bindings")
    assert listing.status_code == 200
    assert listing.json()["bindings"] == ["openrouter:model:anthropic/claude-3.7-sonnet"]
    assert [row["event_id"] for row in listing.json()["binding_events"]] == [event["event_id"]]


def test_principal_registry_canonical_subject_conflict_fails_closed(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    first = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkProviderA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "model", "wallet_capable": False},
        },
    )
    assert first.status_code == 200

    bind_first = client.post(
        "/api/principals/did:key:z6MkProviderA/bindings",
        json={
            "binding_ref": "openrouter:provider:anthropic",
            "tenant_id": "tenant:demo",
        },
    )
    assert bind_first.status_code == 200
    assert bind_first.json().get("principal", {}).get("canonical_subject") == "openrouter:provider:anthropic"

    second = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkProviderB",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "model", "wallet_capable": False},
        },
    )
    assert second.status_code == 200

    bind_second = client.post(
        "/api/principals/did:key:z6MkProviderB/bindings",
        json={
            "binding_ref": "openrouter:provider:anthropic",
            "tenant_id": "tenant:demo",
        },
    )
    assert bind_second.status_code == 409
    assert bind_second.text == "principal_key_ref already bound: openrouter:provider:anthropic"


def test_human_principal_prefers_principal_did_over_model_binding(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkHumanA",
            "principal_key_refs": ["openrouter:model:anthropic/claude-3.7-sonnet"],
            "tenant_id": "tenant:demo",
            "display_name": "Human Operator",
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
            },
        },
    )
    assert create.status_code == 200
    principal = create.json().get("principal")
    assert isinstance(principal, dict)
    refs = principal.get("principal_key_refs")
    assert isinstance(refs, list)
    assert "openrouter:model:anthropic/claude-3.7-sonnet" in refs
    assert principal.get("canonical_subject") == "did:key:z6MkHumanA"
    assert principal.get("canonical_subject_source") == "principal_did"

    bound = client.post(
        "/api/principals/did:key:z6MkHumanA/bindings",
        json={
            "binding_ref": "github:user:12345",
            "tenant_id": "tenant:demo",
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
            },
        },
    )
    assert bound.status_code == 200
    principal = bound.json().get("principal")
    assert isinstance(principal, dict)
    assert principal.get("canonical_subject") == "did:key:z6MkHumanA"
    assert principal.get("canonical_subject_source") == "principal_did"


def test_principal_authority_history_unifies_subject_and_standing_timeline(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkAuthorityTimelineA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "model"},
        },
    )
    assert create.status_code == 200

    subject = client.post(
        "/api/principals/did:key:z6MkAuthorityTimelineA/subject/events",
        json={
            "event_type": "binding_succession",
            "issuer": "operator:test",
            "reason": "subject_promoted",
            "evidence_refs": ["evidence:subject:1"],
        },
    )
    assert subject.status_code == 200
    subject_event_id = subject.json()["event"]["event_id"]

    standing = client.post(
        "/api/principals/did:key:z6MkAuthorityTimelineA/standing/events",
        json={
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "idempotency_key": "timeline-1",
            "evidence_refs": ["evidence:standing:1"],
            "delta": {"trust_class": "T0", "posture_class": "P0", "probation_status": "probation"},
            "credential_ref": "cred:demo-1",
            "standing_envelope_ref": "env:demo-1",
        },
    )
    assert standing.status_code == 200

    history = client.get("/api/principals/did:key:z6MkAuthorityTimelineA/authority/history")
    assert history.status_code == 200
    body = history.json()
    diagnostics = body["diagnostics"]
    assert diagnostics["subject_event_count"] == 2
    assert diagnostics["authority_event_count"] == 1
    assert diagnostics["timeline_count"] == 3
    assert diagnostics["materialized_from_principal_registry"] is True
    current_subject = body["current_subject"]
    current_standing = body["current_standing"]
    assert current_subject["subject_transition_event_ref"] == subject_event_id
    assert current_standing["last_reason_code"] == "eq_blocked:eq9_telos"
    timeline = body["timeline"]
    assert timeline[-1]["family"] == "authority"
    assert timeline[-1]["reason_code"] == "eq_blocked:eq9_telos"


def test_principal_registry_subject_events_and_probation(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkResetA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "model", "wallet_capable": False},
        },
    )
    assert create.status_code == 200
    principal = create.json().get("principal")
    assert isinstance(principal, dict)
    metadata = principal.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("probation_status") == "probation"
    assert metadata.get("probation_reason") == "fresh_subject_created"

    events = client.get("/api/principals/did:key:z6MkResetA/subject/events")
    assert events.status_code == 200
    event_rows = events.json().get("events")
    assert isinstance(event_rows, list)
    assert any(row.get("event_type") == "fresh_subject_created" for row in event_rows)

    reset = client.post(
        "/api/principals/did:key:z6MkResetA/subject/events",
        json={
            "event_type": "subject_reset_requested",
            "reason": "rotate identity path",
            "issuer": "operator:test",
            "evidence_refs": ["ticket:123"],
            "standing_carryover": "probation",
            "credential_carryover": "review_required",
        },
    )
    assert reset.status_code == 200
    reset_body = reset.json()
    event = reset_body.get("event")
    assert isinstance(event, dict)
    assert event.get("event_type") == "subject_reset_requested"
    assert event.get("standing_carryover") == "probation"
    assert event.get("credential_carryover") == "review_required"
    principal = reset_body.get("principal")
    assert isinstance(principal, dict)
    metadata = principal.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("probation_status") == "probation"
    assert metadata.get("probation_reason") == "subject_reset_requested"


def test_principal_registry_standing_events_idempotency_and_authority(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkStandingA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "model", "wallet_capable": False},
        },
    )
    assert create.status_code == 200

    event = client.post(
        "/api/principals/did:key:z6MkStandingA/standing/events",
        json={
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-1"],
            "idempotency_key": "evt-001",
            "credential_ref": "cred:demo-1",
            "standing_envelope_ref": "env:demo-1",
        },
    )
    assert event.status_code == 200
    body = event.json()
    created_event = body.get("event")
    assert isinstance(created_event, dict)
    assert created_event.get("event_type") == "sanction"
    assert created_event.get("credential_ref") == "cred:demo-1"
    assert created_event.get("standing_envelope_ref") == "env:demo-1"
    principal = body.get("principal")
    assert isinstance(principal, dict)
    standing_view = principal.get("standing_view")
    assert isinstance(standing_view, dict)
    assert standing_view.get("trust_class") == "T0"
    assert standing_view.get("posture_class") == "P0"
    assert standing_view.get("credential_ref") == "cred:demo-1"
    assert standing_view.get("standing_envelope_ref") == "env:demo-1"
    assert "eq_blocked:eq9_telos" in (standing_view.get("active_sanctions") or [])

    standing = client.get("/api/principals/did:key:z6MkStandingA/standing")
    assert standing.status_code == 200
    standing_payload = standing.json().get("standing")
    assert isinstance(standing_payload, dict)
    assert standing_payload.get("last_event_type") == "sanction"
    assert standing_payload.get("last_reason_code") == "eq_blocked:eq9_telos"

    listed = client.get("/api/principals/did:key:z6MkStandingA/standing/events")
    assert listed.status_code == 200
    rows = listed.json().get("events")
    assert isinstance(rows, list)
    assert any(row.get("idempotency_key") == "evt-001" for row in rows)

    duplicate = client.post(
        "/api/principals/did:key:z6MkStandingA/standing/events",
        json={
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "idempotency_key": "evt-001",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.text == "standing_event already recorded: deterministic:eq9:evt-001"

    forbidden = client.post(
        "/api/principals/did:key:z6MkStandingA/standing/events",
        json={
            "event_type": "repair",
            "issuer": "advisory:model",
            "reason_code": "repair_attempt",
            "idempotency_key": "evt-002",
        },
    )
    assert forbidden.status_code == 422

    self_issued = client.post(
        "/api/principals/did:key:z6MkStandingA/standing/events",
        json={
            "event_type": "trust_adjustment",
            "issuer": "self:model",
            "reason_code": "self_upgrade",
            "idempotency_key": "evt-003",
        },
    )
    assert self_issued.status_code == 422

    repair = client.post(
        "/api/principals/did:key:z6MkStandingA/standing/events",
        json={
            "event_type": "repair",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T1", "posture_class": "P1"},
            "idempotency_key": "evt-004",
        },
    )
    assert repair.status_code == 200
    repaired_view = repair.json().get("principal", {}).get("standing_view")
    assert isinstance(repaired_view, dict)
    assert repaired_view.get("trust_class") == "T1"
    assert repaired_view.get("posture_class") == "P1"
    assert "eq_blocked:eq9_telos" not in (repaired_view.get("active_sanctions") or [])


def test_principal_registry_rejects_invalid_actor_metadata(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)
    bad = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkInvalidActor",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "robot-overlord"},
        },
    )
    assert bad.status_code == 422


def test_principal_registry_supports_governed_actor_types_and_aliases(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    governed = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkDeviceA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "device"},
        },
    )
    assert governed.status_code == 200
    assert governed.json().get("principal", {}).get("actor_type") == "device"
    assert governed.json().get("principal", {}).get("metadata", {}).get("actor_type") == "device"

    organisation = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkOrgA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "organization"},
        },
    )
    assert organisation.status_code == 200
    assert organisation.json().get("principal", {}).get("actor_type") == "organisation"
    assert organisation.json().get("principal", {}).get("metadata", {}).get("actor_type") == "organisation"

    agent = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkAgentA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "node"},
        },
    )
    assert agent.status_code == 200
    assert agent.json().get("principal", {}).get("actor_type") == "agent"
    assert agent.json().get("principal", {}).get("metadata", {}).get("actor_type") == "agent"

    service = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkServiceA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "application"},
        },
    )
    assert service.status_code == 200
    assert service.json().get("principal", {}).get("actor_type") == "service"
    assert service.json().get("principal", {}).get("metadata", {}).get("actor_type") == "service"


def test_principal_registry_enforces_closed_binding_namespaces(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    accepted = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkClosedNs",
            "tenant_id": "tenant:demo",
            "principal_key_refs": [
                "service:url:https://OPS.EXAMPLE/v1/",
                "wallet:Portable:HolderA",
            ],
            "metadata": {"actor_type": "service"},
        },
    )
    assert accepted.status_code == 200
    principal = accepted.json().get("principal", {})
    assert principal.get("principal_key_refs") == [
        "service:url:https://ops.example/v1",
        "wallet:portable:holdera",
    ]

    rejected = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkUnknownNs",
            "tenant_id": "tenant:demo",
            "principal_key_refs": ["custom:model:alpha"],
            "metadata": {"actor_type": "service"},
        },
    )
    assert rejected.status_code == 422
    assert rejected.text == "unsupported principal_key_ref namespace"


def test_principal_authority_surface_returns_wallet_and_governance_evidence(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkAuthorityA",
            "tenant_id": "tenant:demo",
            "display_name": "Authority Principal",
            "principal_key_refs": ["openrouter:model:anthropic/claude-3.7-sonnet", "github:user:12345"],
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
                "wallet_did": "did:web:id.dualsubstrate.com:wallet:test",
                "wallet_binding_ref": "vc:msauth:test:v1",
                "issuer_did": "did:web:id.dualsubstrate.com",
                "trust_anchor_role": "operator",
            },
        },
    )
    assert create.status_code == 200

    standing = client.post(
        "/api/principals/did:key:z6MkAuthorityA/standing/events",
        json={
            "event_type": "trust_adjustment",
            "issuer": "operator:test",
            "reason_code": "wallet_binding_verified",
            "delta": {
                "trust_class": "T3",
                "posture_class": "P3",
                "operator_profile": "architect",
                "probation_status": "cleared",
            },
            "credential_ref": "cred:msauth:test:v1",
            "standing_envelope_ref": "env:architect:test",
            "idempotency_key": "evt-authority-surface-1",
        },
    )
    assert standing.status_code == 200

    authority = client.get("/api/principals/did:key:z6MkAuthorityA/authority")
    assert authority.status_code == 200
    payload = authority.json()
    surface = payload.get("authority")
    assert isinstance(surface, dict)
    assert surface.get("principal_did") == "did:key:z6MkAuthorityA"
    assert surface.get("canonical_subject") == "did:key:z6MkAuthorityA"
    assert surface.get("canonical_subject_source") == "principal_did"
    assert surface.get("actor_type") == "human"
    assert surface.get("authority_type") == "wallet_bound_operator"
    assert surface.get("wallet_capable") is True
    assert surface.get("wallet_provider") == "microsoft_authenticator"
    assert surface.get("wallet_did") == "did:web:id.dualsubstrate.com:wallet:test"
    assert surface.get("wallet_binding_ref") == "vc:msauth:test:v1"
    assert surface.get("credential_ref") == "cred:msauth:test:v1"
    assert surface.get("issuer_did") == "did:web:id.dualsubstrate.com"
    assert surface.get("trust_anchor_role") == "operator"
    assert surface.get("trust_class") == "T3"
    assert surface.get("posture_class") == "P3"
    assert surface.get("operator_profile") == "architect"
    refs = surface.get("authority_refs")
    assert isinstance(refs, list)
    assert "cred:msauth:test:v1" in refs
    assert "vc:msauth:test:v1" in refs
    verifier_summary = surface.get("verifier_summary")
    assert isinstance(verifier_summary, dict)
    assert verifier_summary.get("authority_active") is True
    assert verifier_summary.get("wallet_bound") is True
    assert verifier_summary.get("credential_bound") is True
    assert verifier_summary.get("issuer_linked") is True


def test_principal_authority_surface_supports_multiple_wallet_bound_humans(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    first = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkHumanWalletA",
            "tenant_id": "tenant:demo",
            "display_name": "Wallet Human A",
            "principal_key_refs": ["github:user:2001"],
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
                "wallet_did": "did:web:id.dualsubstrate.com:wallet:human-a",
                "wallet_binding_ref": "vc:msauth:human-a:v1",
            },
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkHumanWalletB",
            "tenant_id": "tenant:demo",
            "display_name": "Wallet Human B",
            "principal_key_refs": ["github:user:2002"],
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
                "wallet_did": "did:web:id.dualsubstrate.com:wallet:human-b",
                "wallet_binding_ref": "vc:msauth:human-b:v1",
            },
        },
    )
    assert second.status_code == 200

    for did, credential_ref, envelope_ref in [
        ("did:key:z6MkHumanWalletA", "cred:msauth:human-a:v1", "env:human:a"),
        ("did:key:z6MkHumanWalletB", "cred:msauth:human-b:v1", "env:human:b"),
    ]:
        standing = client.post(
            f"/api/principals/{did}/standing/events",
            json={
                "event_type": "trust_adjustment",
                "issuer": "operator:test",
                "reason_code": "wallet_binding_verified",
                "delta": {
                    "trust_class": "T2",
                    "posture_class": "P2",
                    "operator_profile": "member",
                    "probation_status": "cleared",
                },
                "credential_ref": credential_ref,
                "standing_envelope_ref": envelope_ref,
                "idempotency_key": f"evt-{credential_ref}",
            },
        )
        assert standing.status_code == 200

    first_authority = client.get("/api/principals/did:key:z6MkHumanWalletA/authority")
    second_authority = client.get("/api/principals/did:key:z6MkHumanWalletB/authority")
    assert first_authority.status_code == 200
    assert second_authority.status_code == 200

    first_surface = first_authority.json().get("authority")
    second_surface = second_authority.json().get("authority")
    assert isinstance(first_surface, dict)
    assert isinstance(second_surface, dict)
    assert first_surface.get("authority_type") == "wallet_bound_human"
    assert second_surface.get("authority_type") == "wallet_bound_human"
    assert first_surface.get("credential_ref") == "cred:msauth:human-a:v1"
    assert second_surface.get("credential_ref") == "cred:msauth:human-b:v1"
    assert first_surface.get("wallet_binding_ref") == "vc:msauth:human-a:v1"
    assert second_surface.get("wallet_binding_ref") == "vc:msauth:human-b:v1"
    assert first_surface.get("canonical_subject") == "did:key:z6MkHumanWalletA"
    assert second_surface.get("canonical_subject") == "did:key:z6MkHumanWalletB"


def test_principal_registry_architect_standing_event_clears_probation(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkArchitectA",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "service", "vc_status": "bound"},
        },
    )
    assert create.status_code == 200
    created = create.json().get("principal")
    assert isinstance(created, dict)
    assert created.get("metadata", {}).get("probation_status") == "probation"

    elevate = client.post(
        "/api/principals/did:key:z6MkArchitectA/standing/events",
        json={
            "event_type": "trust_adjustment",
            "issuer": "operator:architect",
            "reason_code": "architect_testing_mode",
            "delta": {
                "trust_class": "T3",
                "posture_class": "P3",
                "operator_profile": "architect",
                "probation_status": "cleared",
            },
            "idempotency_key": "evt-architect-001",
            "standing_envelope_ref": "env:architect:test",
        },
    )
    assert elevate.status_code == 200
    principal = elevate.json().get("principal")
    assert isinstance(principal, dict)
    standing_view = principal.get("standing_view")
    assert isinstance(standing_view, dict)
    assert standing_view.get("trust_class") == "T3"
    assert standing_view.get("posture_class") == "P3"
    assert standing_view.get("operator_profile") == "architect"
    assert standing_view.get("probation_status") is None
    metadata = principal.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("probation_status") is None


def test_principal_registry_upsert_preserves_existing_standing_view(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkPreserveStanding",
            "tenant_id": "tenant:demo",
            "display_name": "Original Display",
            "metadata": {"actor_type": "human", "wallet_capable": False},
        },
    )
    assert create.status_code == 200

    elevate = client.post(
        "/api/principals/did:key:z6MkPreserveStanding/standing/events",
        json={
            "event_type": "trust_adjustment",
            "issuer": "operator:architect",
            "reason_code": "architect_testing_mode",
            "delta": {
                "trust_class": "T3",
                "posture_class": "P3",
                "operator_profile": "architect",
                "probation_status": "cleared",
            },
            "credential_ref": "cred:wallet:test",
            "standing_envelope_ref": "env:architect:wallet",
            "idempotency_key": "evt-preserve-standing-001",
        },
    )
    assert elevate.status_code == 200
    elevated_principal = elevate.json().get("principal")
    assert isinstance(elevated_principal, dict)
    elevated_view = elevated_principal.get("standing_view")
    assert isinstance(elevated_view, dict)
    assert elevated_view.get("trust_class") == "T3"
    assert elevated_view.get("credential_ref") == "cred:wallet:test"

    update = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkPreserveStanding",
            "tenant_id": "tenant:demo",
            "display_name": "Updated Display",
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
                "wallet_did": "did:web:id.dualsubstrate.com:wallet:david",
            },
        },
    )
    assert update.status_code == 200
    updated_principal = update.json().get("principal")
    assert isinstance(updated_principal, dict)
    assert updated_principal.get("display_name") == "Updated Display"
    updated_view = updated_principal.get("standing_view")
    assert isinstance(updated_view, dict)
    assert updated_view.get("trust_class") == "T3"
    assert updated_view.get("posture_class") == "P3"
    assert updated_view.get("operator_profile") == "architect"
    assert updated_view.get("probation_status") is None
    assert updated_view.get("credential_ref") == "cred:wallet:test"


def test_principal_link_start_and_verify_by_email(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRINCIPAL_LINK_CODE_DEBUG", "1")
    monkeypatch.setattr(app_module, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(app_module, "PRINCIPAL_LINK_EMAIL_FROM", "DSS <david@berigny.org>")

    deliveries: list[dict] = []

    class _FakeResendClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, headers=None, json=None):
            deliveries.append({"url": url, "headers": headers or {}, "json": json or {}})

            class _Resp:
                status_code = 202

            return _Resp()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", _FakeResendClient)
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkExisting",
            "principal_key_refs": [],
            "tenant_id": "tenant:demo",
            "display_name": "Existing Principal",
            "metadata": {"email": "david@berigny.org", "phone": "+61449040846"},
        },
    )
    assert create.status_code == 200

    start = client.post(
        "/api/principals/link/github/start",
        json={
            "github_user_id": "12345",
            "github_login": "david",
            "github_email": "david@berigny.org",
            "tenant_id": "tenant:demo",
        },
    )
    assert start.status_code == 200
    start_body = start.json()
    assert start_body.get("link_state") == "verification_required"
    assert start_body.get("principal_did") == "did:key:z6MkExisting"
    assert start_body.get("debug_code")
    assert start_body.get("delivery_state") == "sent"
    assert deliveries
    assert deliveries[0]["url"] == "https://api.resend.com/emails"
    assert deliveries[0]["headers"]["Authorization"] == "Bearer re_test"
    assert deliveries[0]["json"]["to"] == ["david@berigny.org"]
    assert deliveries[0]["json"]["subject"] == "Your DSS verification code"

    verify = client.post(
        "/api/principals/link/github/verify",
        json={"challenge_id": start_body.get("challenge_id"), "code": start_body.get("debug_code")},
    )
    assert verify.status_code == 200
    verify_body = verify.json()
    principal = verify_body.get("principal")
    assert isinstance(principal, dict)
    assert principal.get("principal_did") == "did:key:z6MkExisting"
    refs = principal.get("principal_key_refs")
    assert isinstance(refs, list)
    assert "github:user:12345" in refs
    metadata = principal.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("github_link_status") == "linked"


def test_principal_link_start_requires_email_delivery_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "RESEND_API_KEY", "")
    monkeypatch.setattr(app_module, "PRINCIPAL_LINK_EMAIL_FROM", "DSS <david@berigny.org>")
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkExisting",
            "principal_key_refs": [],
            "tenant_id": "tenant:demo",
            "display_name": "Existing Principal",
            "metadata": {"email": "david@berigny.org"},
        },
    )
    assert create.status_code == 200

    start = client.post(
        "/api/principals/link/github/start",
        json={
            "github_user_id": "12345",
            "github_login": "david",
            "github_email": "david@berigny.org",
            "tenant_id": "tenant:demo",
        },
    )
    assert start.status_code == 503
    assert start.text == "email_delivery_not_configured"


def test_principal_link_start_requires_configured_sender(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(app_module, "PRINCIPAL_LINK_EMAIL_FROM", "")
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkExisting",
            "principal_key_refs": [],
            "tenant_id": "tenant:demo",
            "display_name": "Existing Principal",
            "metadata": {"email": "david@berigny.org"},
        },
    )
    assert create.status_code == 200

    start = client.post(
        "/api/principals/link/github/start",
        json={
            "github_user_id": "12345",
            "github_login": "david",
            "github_email": "david@berigny.org",
            "tenant_id": "tenant:demo",
        },
    )
    assert start.status_code == 503
    assert start.text == "email_sender_not_configured"


def test_principal_link_start_logs_resend_failure_details(tmp_path: Path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(app_module, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(app_module, "PRINCIPAL_LINK_EMAIL_FROM", "DSS <david@berigny.org>")

    class _FakeResendClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, headers=None, json=None):
            class _Resp:
                status_code = 422
                text = '{"message":"You can only send testing emails to your own email address"}'

            return _Resp()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", _FakeResendClient)
    client = _client_with_registry(tmp_path)

    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkExisting",
            "principal_key_refs": [],
            "tenant_id": "tenant:demo",
            "display_name": "Existing Principal",
            "metadata": {"email": "david@berigny.org"},
        },
    )
    assert create.status_code == 200

    with caplog.at_level(logging.ERROR):
        start = client.post(
            "/api/principals/link/github/start",
            json={
                "github_user_id": "12345",
                "github_login": "david",
                "github_email": "david@berigny.org",
                "tenant_id": "tenant:demo",
            },
        )

    assert start.status_code == 502
    assert start.text == "email_delivery_failed"
    assert "resend email delivery failed status=422" in caplog.text
    assert "from='DSS <david@berigny.org>'" in caplog.text
    assert "to='d***@berigny.org'" in caplog.text
    assert "You can only send testing emails" in caplog.text


def test_principal_link_start_returns_linked_for_existing_github_ref(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)
    create = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkExisting",
            "principal_key_refs": ["github:user:12345"],
            "tenant_id": "tenant:demo",
            "display_name": "Existing Principal",
            "metadata": {"email": "david@berigny.org"},
        },
    )
    assert create.status_code == 200

    start = client.post(
        "/api/principals/link/github/start",
        json={
            "github_user_id": "12345",
            "github_login": "david",
            "github_email": "david@berigny.org",
            "tenant_id": "tenant:demo",
        },
    )
    assert start.status_code == 200
    body = start.json()
    assert body.get("link_state") == "linked"
    principal = body.get("principal")
    assert isinstance(principal, dict)
    assert principal.get("principal_did") == "did:key:z6MkExisting"


def test_trust_anchor_status_unconfigured(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    monkeypatch.delenv("TRUST_ANCHOR_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    resp = client.get("/api/trust-anchor/status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("status") == "unconfigured"
    trust_anchor = payload.get("trust_anchor")
    assert isinstance(trust_anchor, dict)
    assert trust_anchor.get("issuer_did") == "did:web:id.dualsubstrate.com"
    assert trust_anchor.get("did_document_url") == "https://id.dualsubstrate.com/.well-known/did.json"
    checks = payload.get("checks")
    assert isinstance(checks, dict)
    assert checks.get("issuer_did_match") is False
    assert checks.get("live_resolution_verified") is False
    assert checks.get("public_did_resolves") is False
    warnings = payload.get("warnings")
    assert isinstance(warnings, list)
    assert "backend_admin_token_not_configured" in warnings
    assert "public_did_document_unavailable" in warnings


def test_trust_anchor_status_uses_backend_admin_surfaces(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    monkeypatch.setenv("TRUST_ANCHOR_ADMIN_TOKEN", "test-admin-token")

    class DummyResponse:
        def __init__(self, payload: dict):
            self._payload = payload
            self.content = json.dumps(payload).encode()
            self.status_code = 200
            self.text = json.dumps(payload)

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params=None, headers=None):
            self.calls.append((url, headers))
            if url.endswith('/admin/issuer-authorities?status=active'):
                return DummyResponse({
                    'issuers': [
                        {
                            'issuer': 'dss-v1',
                            'issuer_did': 'did:web:id.dualsubstrate.com',
                            'credential_ref': 'issuer-authority:dss-trust-anchor',
                            'identity_anchor_ref': 'https://id.dualsubstrate.com/.well-known/did.json',
                            'verification_state': 'anchored',
                            'policy_ref': 'https://id.dualsubstrate.com/.well-known/trust-anchor.json#issuer-policy',
                            'policy_verdict': 'allow',
                            'policy_scope': ['identity.assertion', 'trust.anchor.publish', 'trust_anchor'],
                            'verifier_policy_ref': 'https://id.dualsubstrate.com/.well-known/trust-anchor.json',
                            'vc_verification_status': 'verified',
                            'vc_verification_proof_ref': 'https://id.dualsubstrate.com/.well-known/did.json',
                        }
                    ]
                })
            if url.endswith('/admin/live-identity-checks?subject_type=issuer'):
                return DummyResponse({
                    'checks': [
                        {
                            'subject_ref': 'did:web:id.dualsubstrate.com',
                            'resolved_identity': 'did:web:id.dualsubstrate.com',
                            'identity_anchor_ref': 'https://id.dualsubstrate.com/.well-known/did.json',
                            'resolution_status': 'verified',
                        }
                    ]
                })
            if url == 'https://id.dualsubstrate.com/.well-known/did.json':
                return DummyResponse({
                    'id': 'did:web:id.dualsubstrate.com',
                    'service': [
                        {
                            'serviceEndpoint': 'https://id.dualsubstrate.com/api/trust-anchor/status',
                        },
                        {
                            'serviceEndpoint': 'https://id.dualsubstrate.com/.well-known/trust-anchor.json',
                        },
                    ],
                })
            raise AssertionError(f'unexpected url {url}')

    monkeypatch.setattr(app_module.httpx, 'AsyncClient', DummyAsyncClient)

    resp = client.get('/api/trust-anchor/status')

    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get('status') == 'ok'
    issuer = payload.get('issuer_authority')
    assert isinstance(issuer, dict)
    assert issuer.get('issuer_did') == 'did:web:id.dualsubstrate.com'
    live = payload.get('live_identity_check')
    assert isinstance(live, dict)
    assert live.get('resolution_status') == 'verified'
    checks = payload.get('checks')
    assert isinstance(checks, dict)
    assert checks.get('issuer_did_match') is True
    assert checks.get('issuer_anchor_match') is True
    assert checks.get('live_subject_match') is True
    assert checks.get('live_anchor_match') is True
    assert checks.get('live_resolution_verified') is True
    assert checks.get('issuer_policy_explicit') is True
    assert checks.get('issuer_policy_verifier_ref_present') is True
    assert checks.get('issuer_binding_anchored') is True
    assert checks.get('issuer_vc_verified') is True
    assert checks.get('issuer_vc_proof_ref_present') is True
    assert checks.get('public_did_resolves') is True
    assert checks.get('public_did_id_match') is True
    assert checks.get('public_service_status_present') is True
    assert checks.get('public_service_bundle_present') is True
    did_document = payload.get('did_document')
    assert isinstance(did_document, dict)
    assert did_document.get('id') == 'did:web:id.dualsubstrate.com'
    assert issuer.get('policy_verdict') == 'allow'
    assert issuer.get('verifier_policy_ref') == 'https://id.dualsubstrate.com/.well-known/trust-anchor.json'
    assert issuer.get('verification_state') == 'anchored'
    assert issuer.get('vc_verification_status') == 'verified'
    assert payload.get('warnings') == []


def test_trust_anchor_bundle_wraps_status_payload(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    monkeypatch.setenv("TRUST_ANCHOR_ADMIN_TOKEN", "test-admin-token")

    class DummyResponse:
        def __init__(self, payload: dict):
            self._payload = payload
            self.content = json.dumps(payload).encode()
            self.status_code = 200
            self.text = json.dumps(payload)

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params=None, headers=None):
            if url.endswith('/admin/issuer-authorities?status=active'):
                return DummyResponse({
                    'issuers': [
                        {
                            'issuer_did': 'did:web:id.dualsubstrate.com',
                            'credential_ref': 'issuer-authority:dss-trust-anchor',
                            'identity_anchor_ref': 'https://id.dualsubstrate.com/.well-known/did.json',
                            'verification_state': 'anchored',
                            'policy_ref': 'https://id.dualsubstrate.com/.well-known/trust-anchor.json#issuer-policy',
                            'policy_verdict': 'allow',
                            'policy_scope': ['identity.assertion', 'trust.anchor.publish', 'trust_anchor'],
                            'verifier_policy_ref': 'https://id.dualsubstrate.com/.well-known/trust-anchor.json',
                            'vc_verification_status': 'verified',
                            'vc_verification_proof_ref': 'https://id.dualsubstrate.com/.well-known/did.json',
                        }
                    ]
                })
            if url.endswith('/admin/live-identity-checks?subject_type=issuer'):
                return DummyResponse({
                    'checks': [
                        {
                            'subject_ref': 'did:web:id.dualsubstrate.com',
                            'resolved_identity': 'did:web:id.dualsubstrate.com',
                            'identity_anchor_ref': 'https://id.dualsubstrate.com/.well-known/did.json',
                            'resolution_status': 'verified',
                        }
                    ]
                })
            if url == 'https://id.dualsubstrate.com/.well-known/did.json':
                return DummyResponse({
                    'id': 'did:web:id.dualsubstrate.com',
                    'service': [
                        {
                            'serviceEndpoint': 'https://id.dualsubstrate.com/api/trust-anchor/status',
                        },
                        {
                            'serviceEndpoint': 'https://id.dualsubstrate.com/.well-known/trust-anchor.json',
                        },
                    ],
                })
            raise AssertionError(f'unexpected url {url}')

    monkeypatch.setattr(app_module.httpx, 'AsyncClient', DummyAsyncClient)

    resp = client.get('/api/trust-anchor/bundle')

    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get('issuer_did') == 'did:web:id.dualsubstrate.com'
    assert payload.get('did_document_url') == 'https://id.dualsubstrate.com/.well-known/did.json'
    status_payload = payload.get('trust_anchor_status')
    assert isinstance(status_payload, dict)
    assert status_payload.get('status') == 'ok'
    issuer = status_payload.get('issuer_authority')
    assert isinstance(issuer, dict)
    assert issuer.get('policy_verdict') == 'allow'
    assert issuer.get('verifier_policy_ref') == 'https://id.dualsubstrate.com/.well-known/trust-anchor.json'
    assert issuer.get('verification_state') == 'anchored'
    assert issuer.get('vc_verification_status') == 'verified'
    issuer_policy = payload.get('issuer_policy')
    assert isinstance(issuer_policy, dict)
    assert issuer_policy.get('policy_verdict') == 'allow'
    assert issuer_policy.get('explicit') is True
    assert issuer_policy.get('verifier_visible') is True
    issuer_evidence = payload.get('issuer_authority_evidence')
    assert isinstance(issuer_evidence, dict)
    assert issuer_evidence.get('credential_ref') == 'issuer-authority:dss-trust-anchor'
    assert issuer_evidence.get('verification_state') == 'anchored'
    assert issuer_evidence.get('vc_verification_status') == 'verified'
    assert issuer_evidence.get('binding_anchored') is True
    assert issuer_evidence.get('proof_verified') is True
    service_endpoints = payload.get('service_endpoints')
    assert isinstance(service_endpoints, dict)
    assert service_endpoints.get('trust_anchor_bundle') == 'https://id.dualsubstrate.com/.well-known/trust-anchor.json'
    assert service_endpoints.get('issuer_authority_object') == 'https://id.dualsubstrate.com/.well-known/issuer-authority.json'
    assert service_endpoints.get('issuer_authority_status_object') == 'https://id.dualsubstrate.com/.well-known/issuer-authority-status.json'
    assert service_endpoints.get('verifier_policy_object') == 'https://id.dualsubstrate.com/.well-known/verifier-policy.json'
    authority_object = payload.get('public_issuer_authority')
    assert isinstance(authority_object, dict)
    assert authority_object.get('type') == 'DssIssuerAuthority'
    assert authority_object.get('statement_type') == 'IssuerAuthorityStatement'
    assert authority_object.get('format') == 'dss-public-authority-statement-v1'
    assert authority_object.get('issuer_did') == 'did:web:id.dualsubstrate.com'
    assert authority_object.get('not_a_verifiable_credential') is True
    issuer_object = authority_object.get('issuer')
    assert isinstance(issuer_object, dict)
    assert issuer_object.get('id') == 'did:web:id.dualsubstrate.com'
    subject = authority_object.get('subject')
    assert isinstance(subject, dict)
    assert subject.get('id') == 'did:web:id.dualsubstrate.com'
    assert subject.get('type') == 'IssuerAuthoritySubject'
    assert subject.get('organisation_name') == 'Dual Substrate'
    organisation_identity = authority_object.get('organisation_identity')
    assert isinstance(organisation_identity, dict)
    assert organisation_identity.get('name') == 'Dual Substrate'
    assert organisation_identity.get('homepage') == 'https://dualsubstrate.com'
    assert organisation_identity.get('status') == 'partial'
    policy = authority_object.get('policy')
    assert isinstance(policy, dict)
    assert policy.get('policy_verdict') == 'allow'
    status = authority_object.get('status')
    assert isinstance(status, dict)
    assert status.get('binding_anchored') is True
    assert status.get('vc_verified') is True
    status_discovery = authority_object.get('status_discovery')
    assert isinstance(status_discovery, dict)
    assert status_discovery.get('authority_status_ref') == 'https://id.dualsubstrate.com/.well-known/issuer-authority-status.json'
    assert status_discovery.get('revocation_ref') == 'https://id.dualsubstrate.com/.well-known/trust-anchor.json'
    authority_status_object = payload.get('public_issuer_authority_status')
    assert isinstance(authority_status_object, dict)
    assert authority_status_object.get('type') == 'DssIssuerAuthorityStatus'
    assert authority_status_object.get('status_type') == 'IssuerAuthorityStatusStatement'
    assert authority_status_object.get('format') == 'dss-public-authority-status-v1'
    assert authority_status_object.get('not_a_verifiable_credential') is True
    assert authority_status_object.get('id') == 'https://id.dualsubstrate.com/.well-known/issuer-authority-status.json'
    authority_status = authority_status_object.get('status')
    assert isinstance(authority_status, dict)
    assert authority_status.get('binding_anchored') is True
    assert authority_status.get('vc_verified') is True
    verifier_policy_object = payload.get('public_verifier_policy')
    assert isinstance(verifier_policy_object, dict)
    assert verifier_policy_object.get('type') == 'DssVerifierPolicy'
    assert verifier_policy_object.get('policy_type') == 'VerifierPolicyStatement'
    assert verifier_policy_object.get('format') == 'dss-public-verifier-policy-v1'
    assert verifier_policy_object.get('id') == 'https://id.dualsubstrate.com/.well-known/verifier-policy.json'
    verifier_policy = verifier_policy_object.get('policy')
    assert isinstance(verifier_policy, dict)
    assert verifier_policy.get('policy_verdict') == 'allow'
    verification_expectations = verifier_policy_object.get('verification_expectations')
    assert isinstance(verification_expectations, dict)
    assert verification_expectations.get('resolve_issuer_did_first') is True
    publication_intent = payload.get('publication_intent')
    assert isinstance(publication_intent, dict)
    assert publication_intent.get('profile') == 'dss-public-trust-discovery-v1'
    assert publication_intent.get('current_publication_state') == 'partial'
    assert 'issuer_authority_statement' in (publication_intent.get('published_now') or [])
    assert 'typed_issuer_authority_credential' in (publication_intent.get('future_publication_targets') or [])
    evidence_profile = payload.get('evidence_profile')
    assert isinstance(evidence_profile, dict)
    assert evidence_profile.get('organisation_identity_status') == 'partial'
    assert evidence_profile.get('authority_statement_published') is True
    assert evidence_profile.get('authority_status_statement_published') is True
    assert evidence_profile.get('vc_evidence_published') is True
    verifier_instructions = payload.get('verifier_instructions')
    assert isinstance(verifier_instructions, dict)
    assert verifier_instructions.get('inspect_authority_object') == 'https://id.dualsubstrate.com/.well-known/issuer-authority.json'
    assert verifier_instructions.get('inspect_authority_status_object') == 'https://id.dualsubstrate.com/.well-known/issuer-authority-status.json'
    assert verifier_instructions.get('inspect_verifier_policy_object') == 'https://id.dualsubstrate.com/.well-known/verifier-policy.json'
    assert verifier_instructions.get('inspect_status') == 'https://id.dualsubstrate.com/api/trust-anchor/status'
    notes = verifier_instructions.get('notes', [])
    assert 'Use the verifier policy object as the typed policy discovery surface.' in notes
    assert 'Treat the authority object as a public authority statement, not as a full verifiable credential.' in notes
    profile = payload.get('interop_profile')
    assert isinstance(profile, dict)
    assert profile.get('untp_alignment') == 'targeted'
    profile_notes = profile.get('notes', [])
    assert 'Publishes explicit issuer policy and authority evidence summaries.' in profile_notes
    assert 'Publishes a typed public issuer authority object and verifier instructions.' in profile_notes
    assert 'Publishes a separate issuer authority status object for verifier-facing status discovery.' in profile_notes
    assert 'Publishes a typed verifier policy object for policy-specific discovery.' in profile_notes
    assert 'States the current publication boundary explicitly so verifiers can distinguish published surfaces from future credential work.' in profile_notes


def test_trust_anchor_public_documents_passthrough_backend_public_routes(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    monkeypatch.setenv("TRUST_ANCHOR_ADMIN_TOKEN", "test-admin-token")

    class DummyResponse:
        def __init__(self, payload: dict):
            self._payload = payload
            self.content = b"{}"
            self.status_code = 200
            self.text = json.dumps(payload)

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params=None, headers=None):
            if url.endswith("/public/trust-anchor/issuer-authority"):
                return DummyResponse({
                    "id": "https://id.dualsubstrate.com/.well-known/issuer-authority.json",
                    "type": "DssIssuerAuthority",
                    "credential_family": "authority",
                })
            if url.endswith("/public/trust-anchor/issuer-authority-status"):
                return DummyResponse({
                    "id": "https://id.dualsubstrate.com/.well-known/issuer-authority-status.json",
                    "type": "DssIssuerAuthorityStatus",
                    "credential_family": "status",
                    "freshness": {"checked_at": "2026-04-09T00:00:00Z", "is_fresh": True},
                })
            if url.endswith("/public/trust-anchor/verifier-policy"):
                return DummyResponse({
                    "id": "https://id.dualsubstrate.com/.well-known/verifier-policy.json",
                    "type": "DssVerifierPolicy",
                })
            if url.endswith("/public/status/status:review-board"):
                return DummyResponse({
                    "id": "https://id.dualsubstrate.com/api/trust-anchor/credential-status/status:review-board",
                    "type": "DssCredentialStatus",
                    "status": {"current": "active"},
                    "freshness": {"checked_at": "2026-04-09T00:00:00Z", "is_fresh": True},
                    "invalidation": {"is_invalidated": False, "reasons": []},
                })
            if url.endswith("/public/trust-anchor/bundle"):
                return DummyResponse({
                    "issuer_did": "did:web:id.dualsubstrate.com",
                    "publication_intent": {"current_publication_state": "minimum_live"},
                })
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    authority = client.get("/.well-known/issuer-authority.json")
    assert authority.status_code == 200
    assert authority.json()["type"] == "DssIssuerAuthority"

    authority_status = client.get("/.well-known/issuer-authority-status.json")
    assert authority_status.status_code == 200
    assert authority_status.json()["freshness"]["is_fresh"] is True

    verifier_policy = client.get("/.well-known/verifier-policy.json")
    assert verifier_policy.status_code == 200
    assert verifier_policy.json()["type"] == "DssVerifierPolicy"

    credential_status = client.get("/api/trust-anchor/credential-status/status:review-board")
    assert credential_status.status_code == 200
    assert credential_status.json()["type"] == "DssCredentialStatus"
    assert credential_status.json()["invalidation"]["reasons"] == []

    bundle = client.get("/.well-known/trust-anchor.json")
    assert bundle.status_code == 200
    assert bundle.json()["publication_intent"]["current_publication_state"] == "minimum_live"


def test_tiered_resolver_route_proxies_backend_contract(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    captured: dict[str, object] = {}

    async def fake_backend_fetch_json(*, method, path, params=None, payload=None, headers=None, timeout=None):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        captured["headers"] = headers
        return {
            "status": "ok",
            "read_tier": "verifier_full",
            "entry": {
                "key": {"namespace": "chat-team-a", "identifier": "WX-123"},
                "metadata": {"content": {"state": "withheld", "reason": "operator_only_content"}},
            },
            "redaction": {"native_coord_policy": "internal_only"},
        }

    monkeypatch.setattr(app_module, "_backend_fetch_json", fake_backend_fetch_json)

    response = client.post(
        "/api/resolve_tiered",
        json={"coordinate": "chat-team-a:WX-123", "read_tier": "verifier_full"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["read_tier"] == "verifier_full"
    assert body["redaction"]["native_coord_policy"] == "internal_only"
    assert captured["method"] == "POST"
    assert captured["path"] == "/resolve/tiered"
    assert captured["payload"] == {
        "namespace": "chat-team-a",
        "identifier": "WX-123",
        "read_tier": "verifier_full",
    }
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-ledger-id"] == "chat-team-a"


def test_public_object_routes_proxy_backend_documents(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    calls: list[_ResolverCall] = []

    class DummyResponse:
        def __init__(self, payload: dict, status_code: int = 200, headers: dict[str, str] | None = None):
            self._payload = payload
            self.content = json.dumps(payload).encode()
            self.status_code = status_code
            self.text = json.dumps(payload)
            self.headers = headers or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params=None, headers=None):
            calls.append({"url": url, "params": params, "headers": headers})
            normalized_params = params or {}
            ref = str(normalized_params.get("ref") or "").strip()
            mode = str(normalized_params.get("mode") or "").strip()
            if url.endswith("/v1/resolve") and mode == "skim":
                assert ref == "http://testserver/o/claim/obj-123"
                return DummyResponse(
                    {
                        "id": "http://testserver/o/claim/obj-123",
                        "kind": "claim",
                        "status": "active",
                        "resolverRef": "rrf_test_public_object",
                        "evidence": {
                            "resolverUrl": "http://testserver/v1/resolve?ref=http%3A%2F%2Ftestserver%2Fo%2Fclaim%2Fobj-123",
                        },
                    },
                    headers={
                        "Cache-Control": "public, max-age=300, stale-while-revalidate=60",
                        "X-Resolver-Mode": "skim",
                        "X-Public-Object-Id": "http://testserver/o/claim/obj-123",
                        "X-Resolver-Ref": "rrf_test_public_object",
                    },
                )
            if url.endswith("/v1/resolve") and mode == "full":
                assert ref == "http://testserver/o/claim/obj-123"
                return DummyResponse(
                    {
                        "detail": {
                            "outcome": "not_authorized",
                            "mode": "full",
                            "ref": ref,
                            "resolverRef": "rrf_test_public_object",
                            "nativeCoordState": "withheld",
                        }
                    },
                    status_code=403,
                    headers={
                        "Cache-Control": "no-store",
                        "Pragma": "no-cache",
                        "X-Resolver-Mode": "full",
                        "X-Resolver-Ref": "rrf_test_public_object",
                    },
                )
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(app_module.httpx, "AsyncClient", DummyAsyncClient)

    skim_v1 = client.get("/v1/resolve", params={"ref": "http://testserver/o/claim/obj-123", "mode": "skim"})
    assert skim_v1.status_code == 200
    assert skim_v1.json()["resolverRef"] == "rrf_test_public_object"
    assert skim_v1.headers["Cache-Control"] == "public, max-age=300, stale-while-revalidate=60"
    assert skim_v1.headers["X-Resolver-Mode"] == "skim"
    assert skim_v1.headers["X-Public-Object-Id"] == "http://testserver/o/claim/obj-123"
    assert skim_v1.headers["X-Resolver-Ref"] == "rrf_test_public_object"

    doc = client.get("/o/claim/obj-123")
    assert doc.status_code == 200
    assert doc.json()["resolverRef"] == "rrf_test_public_object"
    assert doc.headers["X-Resolver-Mode"] == "skim"
    assert doc.headers["Cache-Control"] == "public, max-age=300, stale-while-revalidate=60"

    status = client.get("/o/claim/obj-123/status")
    assert status.status_code == 403
    assert status.json()["detail"]["outcome"] == "not_authorized"
    assert status.json()["detail"]["nativeCoordState"] == "withheld"
    assert status.headers["Cache-Control"] == "no-store"
    assert status.headers["X-Resolver-Mode"] == "full"

    assert calls[0]["url"].endswith("/v1/resolve")
    assert calls[0]["params"] == {"ref": "http://testserver/o/claim/obj-123", "mode": "skim"}
    assert calls[1]["url"].endswith("/v1/resolve")
    assert calls[1]["params"] == {"ref": "http://testserver/o/claim/obj-123", "mode": "skim"}
    assert calls[2]["url"].endswith("/v1/resolve")
    assert calls[2]["params"] == {"ref": "http://testserver/o/claim/obj-123", "mode": "full"}


def test_verified_id_presentation_request_creation(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    monkeypatch.setenv("VERIFIED_ID_TENANT_ID", "tenant-123")
    monkeypatch.setenv("VERIFIED_ID_CLIENT_ID", "client-123")
    monkeypatch.setenv("VERIFIED_ID_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("VERIFIED_ID_AUTHORITY", "did:web:id.dualsubstrate.com")
    monkeypatch.setenv("VERIFIED_ID_CREDENTIAL_TYPE", "VerifiedCredentialExpert")
    monkeypatch.setenv("VERIFIED_ID_CALLBACK_API_KEY", "callback-secret")

    async def _fake_create(config, request_payload):
        assert config["tenant_id"] == "tenant-123"
        assert request_payload["callback"]["headers"]["api-key"] == "callback-secret"
        assert request_payload["requestedCredentials"][0]["type"] == "VerifiedCredentialExpert"
        return {
            "requestId": "req_test_123",
            "url": "openid-vc://presentation-request",
            "expiry": 1774500000,
            "qrCode": "data:image/png;base64,abc123",
        }

    monkeypatch.setattr(app_module, "_verified_id_create_presentation_request", _fake_create)

    created = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkPendingWallet",
            "tenant_id": "tenant:demo",
            "display_name": "Pending Wallet",
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
                "wallet_proof_state": "pending_verified_id",
                "pending_wallet_did": "did:web:id.dualsubstrate.com:wallet:pending-wallet",
                "pending_wallet_binding_ref": "vc:msauth:pending-wallet:v1",
                "pending_credential_ref": "cred:msauth:pending-wallet:v1",
                "profile_approval_state": "approved_pending_wallet_proof",
                "vc_status": "none",
            },
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/api/verified-id/presentation-requests",
        json={"principal_did": "did:key:z6MkPendingWallet"},
    )
    assert response.status_code == 200
    request_payload = response.json().get("request")
    assert isinstance(request_payload, dict)
    assert request_payload.get("request_id") == "req_test_123"
    assert request_payload.get("principal_did") == "did:key:z6MkPendingWallet"
    assert request_payload.get("request_url") == "openid-vc://presentation-request"
    assert str(request_payload.get("state") or "").startswith("vid_")


def test_verified_id_callback_finalizes_pending_wallet_proof(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)
    monkeypatch.setenv("VERIFIED_ID_CALLBACK_API_KEY", "callback-secret")

    created = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkPendingWalletFinalize",
            "tenant_id": "tenant:demo",
            "display_name": "Pending Wallet Finalize",
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
                "wallet_proof_state": "pending_verified_id",
                "pending_wallet_did": "did:web:id.dualsubstrate.com:wallet:pending-wallet-finalize",
                "pending_wallet_binding_ref": "vc:msauth:pending-wallet-finalize:v1",
                "pending_credential_ref": "cred:msauth:pending-wallet-finalize:v1",
                "profile_approval_state": "approved_pending_wallet_proof",
                "trust_anchor_role": "approved_pending_wallet_proof",
                "vc_status": "none",
            },
        },
    )
    assert created.status_code == 200

    app_module.VERIFIED_ID_REQUESTS.create(
        state="vid_test_state",
        request_id="req_test_final",
        principal_did="did:key:z6MkPendingWalletFinalize",
        mode="presentation",
        request_payload={"callback": {"state": "vid_test_state"}},
        response_payload={"requestId": "req_test_final", "url": "openid-vc://presentation-request"},
    )

    callback = client.post(
        "/api/webhooks/entra/verified-id",
        headers={"api-key": "callback-secret"},
        json={
            "requestId": "req_test_final",
            "requestStatus": "presentation_verified",
            "state": "vid_test_state",
            "subject": "did:web:id.dualsubstrate.com:wallet:pending-wallet-finalize",
            "verifiedCredentialsData": [{"type": ["VerifiedCredentialExpert"]}],
        },
    )
    assert callback.status_code == 200
    finalization = callback.json().get("finalization")
    assert isinstance(finalization, dict)
    assert finalization.get("wallet_proof_state") == "verified"
    assert finalization.get("wallet_binding_ref") == "vc:msauth:pending-wallet-finalize:v1"
    assert finalization.get("credential_ref") == "cred:msauth:pending-wallet-finalize:v1"

    principal = client.get("/api/principals/did:key:z6MkPendingWalletFinalize")
    assert principal.status_code == 200
    body = principal.json()
    metadata = body.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("wallet_proof_state") == "verified"
    assert metadata.get("wallet_did") == "did:web:id.dualsubstrate.com:wallet:pending-wallet-finalize"
    assert metadata.get("wallet_binding_ref") == "vc:msauth:pending-wallet-finalize:v1"
    assert metadata.get("vc_status") == "verified"
    assert metadata.get("trust_anchor_role") == "member"
    assert metadata.get("pending_wallet_did") is None
    standing_view = body.get("standing_view")
    assert isinstance(standing_view, dict)
    assert standing_view.get("credential_ref") == "cred:msauth:pending-wallet-finalize:v1"


def test_verified_id_issuance_request_creation(tmp_path: Path, monkeypatch) -> None:
    client = _client_with_registry(tmp_path)

    async def _fake_create(config, request_payload):
        assert request_payload["type"] == "DssVerifiedIdentity"
        assert request_payload["authority"] == "did:web:id.dualsubstrate.com"
        assert request_payload["claims"]["principal_did"] == "did:key:z6MkIssueA"
        return {
            "requestId": "issuance-request-1",
            "url": "openid-initiate-issuance://example",
            "qrCode": "data:image/png;base64,BBBB",
            "expiry": 1775000001,
        }

    monkeypatch.setenv("VERIFIED_ID_TENANT_ID", "tenant-id")
    monkeypatch.setenv("VERIFIED_ID_CLIENT_ID", "client-id")
    monkeypatch.setenv("VERIFIED_ID_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("VERIFIED_ID_AUTHORITY", "did:web:id.dualsubstrate.com")
    monkeypatch.setenv("VERIFIED_ID_CREDENTIAL_TYPE", "DssVerifiedIdentity")
    monkeypatch.setenv("VERIFIED_ID_MANIFEST_URL", "https://issuer.example/manifest")
    monkeypatch.setattr(app_module, "_verified_id_create_issuance_request", _fake_create)

    created = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkIssueA",
            "tenant_id": "tenant:demo",
            "display_name": "Issue Principal",
            "metadata": {
                "actor_type": "human",
                "email": "issue@example.com",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
                "wallet_proof_state": "pending_verified_id",
                "profile_approval_state": "approved_pending_wallet_proof",
            },
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/api/verified-id/issuance-requests",
        json={"principal_did": "did:key:z6MkIssueA"},
    )
    assert response.status_code == 200
    body = response.json()
    request_body = body.get("request")
    assert isinstance(request_body, dict)
    assert request_body.get("mode") == "issuance"
    assert request_body.get("request_id") == "issuance-request-1"
    assert request_body.get("request_url") == "openid-initiate-issuance://example"


def test_verified_id_callback_marks_issuance_in_wallet(tmp_path: Path) -> None:
    client = _client_with_registry(tmp_path)

    created = client.post(
        "/api/principals",
        json={
            "principal_did": "did:key:z6MkIssueB",
            "tenant_id": "tenant:demo",
            "display_name": "Issue Principal",
            "metadata": {
                "actor_type": "human",
                "wallet_capable": True,
                "wallet_provider": "microsoft_authenticator",
                "wallet_proof_state": "pending_verified_id",
                "profile_approval_state": "approved_pending_wallet_proof",
                "pending_wallet_did": "did:web:id.dualsubstrate.com:wallet:issueb",
                "pending_wallet_binding_ref": "vc:msauth:issueb:v1",
                "pending_credential_ref": "cred:msauth:issueb:v1",
            },
        },
    )
    assert created.status_code == 200

    app_module.VERIFIED_ID_REQUESTS.create(
        state="vid_issueb",
        request_id="issuance-request-2",
        principal_did="did:key:z6MkIssueB",
        mode="issuance",
        request_payload={"type": "DssVerifiedIdentity"},
        response_payload={"requestId": "issuance-request-2", "url": "openid-initiate-issuance://example"},
    )

    response = client.post(
        "/api/webhooks/entra/verified-id",
        json={
            "requestStatus": "issuance_successful",
            "state": "vid_issueb",
            "requestId": "issuance-request-2",
            "subject": "did:web:id.dualsubstrate.com:wallet:issueb",
        },
    )
    assert response.status_code == 200
    body = response.json()
    finalization = body.get("finalization")
    assert isinstance(finalization, dict)
    assert finalization.get("wallet_issuance_state") == "issued_in_wallet"

    principal = client.get("/api/principals/did:key:z6MkIssueB")
    assert principal.status_code == 200
    metadata = principal.json().get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("wallet_issuance_state") == "issued_in_wallet"
    assert metadata.get("wallet_proof_state") == "pending_presentation_verification"
