from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import (
    _annotate_control_plane_row,
    CONTROL_PLANE_MUTATION_REGISTRY_V1_KEY,
    CONTROL_PLANE_SUBMISSION_REGISTRY_V1_KEY,
    LEDGER_REGISTRY_V1_KEY,
    MODEL_BINDING_REGISTRY_V1_KEY,
    PRINCIPAL_REGISTRY_V1_KEY,
    PROVIDER_CREDENTIAL_REGISTRY_V1_KEY,
    RELATIONSHIP_REGISTRY_V1_KEY,
    SURFACE_REGISTRY_V1_KEY,
    control_plane_router,
)


def _make_client(*, base_url: str = "http://testserver") -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(control_plane_router)
    return TestClient(app, base_url=base_url)


def test_control_plane_routes_persist_and_activate_entities(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    ledger = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "ledger:test",
            "name": "Test Ledger",
            "tenant_id": "tenant:demo",
            "status": "pending",
            "provisioning_source": "control_plane",
            "idempotency_key": "ledger-create-1",
            "metadata": {"env": "test"},
            "founding_constitution_name": "LOAM",
            "founding_constitution_personality": "Deliberate, layered, patient with complexity.",
            "founding_constitution_purpose": "Hold governed memory and continuity for this ledger.",
        },
        headers=headers,
    )
    assert ledger.status_code == 200
    assert ledger.json()["ledger"]["ledger_id"] == "test"
    assert ledger.json()["ledger"]["status"] == "pending"
    constitution = ledger.json()["ledger"]["metadata"]["founding_constitution"]
    assert constitution["name"] == "LOAM"
    assert constitution["personality"] == "Deliberate, layered, patient with complexity."
    assert constitution["purpose"] == "Hold governed memory and continuity for this ledger."
    assert constitution["source"] == "control_plane_operator"
    assert ledger.json()["execution_mode"] == "direct_write"
    assert ledger.json()["submission_status"] == "applied"

    principal = client.post(
        "/api/control-plane/principals",
        json={
            "principal_did": "did:key:z6MkPendingBackend",
            "tenant_id": "tenant:demo",
            "display_name": "Pending Backend Principal",
            "status": "pending",
            "provisioning_source": "control_plane",
            "idempotency_key": "principal-create-1",
            "metadata": {"actor_type": "human", "ledger_id": "ledger:test"},
        },
        headers=headers,
    )
    assert principal.status_code == 200
    assert principal.json()["principal"]["principal_did"] == "did:key:z6MkPendingBackend"
    assert principal.json()["principal"]["actor_type"] == "human"
    assert principal.json()["principal"]["metadata"]["provisioning_state"] == "pending_provisioning"
    assert principal.json()["principal"]["status"] == "pending"

    surface = client.post(
        "/api/control-plane/surfaces",
        json={
            "surface_id": "surface:test",
            "display_name": "Test Surface",
            "surface_type": "chat",
            "status": "pending",
            "idempotency_key": "surface-create-1",
            "ledger_id": "ledger:test",
            "principal_did": "did:key:z6MkPendingBackend",
        },
        headers=headers,
    )
    assert surface.status_code == 200
    assert surface.json()["surface"]["surface_id"] == "surface:test"
    assert surface.json()["surface"]["status"] == "pending"

    relationship = client.post(
        "/api/control-plane/relationships",
        json={
            "subject_entity_type": "principal",
            "subject_entity_id": "did:key:z6MkPendingBackend",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:test",
            "relationship_type": "governs",
            "permission_scope": "full",
            "enabled_state": "enabled",
            "idempotency_key": "rel-create-1",
        },
        headers=headers,
    )
    assert relationship.status_code == 200
    assert relationship.json()["relationship"]["relationship_id"] == "principal::did:key:z6MkPendingBackend::ledger::test"
    assert relationship.json()["relationship"]["created_by_principal_id"] == "ops-admin"

    ledgers_list = client.get("/api/control-plane/ledgers", headers=headers)
    principals_list = client.get("/api/control-plane/principals", headers=headers)
    surfaces_list = client.get("/api/control-plane/surfaces", headers=headers)
    relationships_list = client.get("/api/control-plane/relationships", headers=headers)
    assert ledgers_list.status_code == 200
    assert principals_list.status_code == 200
    assert surfaces_list.status_code == 200
    assert relationships_list.status_code == 200
    ledger_row = ledgers_list.json()["ledgers"][0]
    principal_row = principals_list.json()["principals"][0]
    surface_row = surfaces_list.json()["surfaces"][0]
    relationship_row = relationships_list.json()["relationships"][0]
    assert ledger_row["ledger_id"] == "test"
    assert ledger_row["row_family"] == "interaction"
    assert ledger_row["preferred_reference"]["value"] == ledger_row["canonical_subject"]
    assert ledger_row["shareability"] == "share-ready"
    assert ledger_row["source_precedence"]["current_source"] == "backend_canonical_record"
    assert ledger_row["memory_tier_classification"]["retention_tier"] == "Clay"
    assert ledger_row["memory_tier_classification"]["contract"]["ledger_record_tier"] == "Clay"
    assert "ledger_alias_history" in ledger_row["memory_tier_classification"]["contract"]["durable_classes"]
    self_description = ledger_row["ledger_self_description"]
    assert self_description["seed_identity"]["name"] == "LOAM"
    assert self_description["seed_identity"]["personality"] == "Deliberate, layered, patient with complexity."
    assert self_description["seed_identity"]["purpose"] == "Hold governed memory and continuity for this ledger."
    assert self_description["resolved_constitution_context"]["present"] is False
    assert self_description["resolved_constitution_context"]["coord_resolved_access_is_not_runtime_foundation_identity"] is True
    assert self_description["runtime_foundation_identity"]["available"] is True
    assert self_description["runtime_foundation_identity"]["fields"]["name"] == "LOAM"
    verified_traits = {item["trait"]: item for item in self_description["verified_ledger_traits"]}
    assert verified_traits["lifecycle_status"]["evidence"][0]["field"] == "status"
    assert verified_traits["provisioning_state"]["evidence"][0]["field"] == "metadata.provisioning_state"
    assert "ledger:test" in ledger_row["metadata"]["ledger_alias_history"]
    assert principal_row["principal_did"] == "did:key:z6MkPendingBackend"
    assert principal_row["row_family"] == "identity"
    assert principal_row["preferred_reference"]["value"] == principal_row["canonical_subject"]
    assert any(alias["field"] in {"principal_did", "display_name"} for alias in principal_row["reference_aliases"])
    assert surface_row["surface_id"] == "surface:test"
    assert surface_row["detail_panels"] == ["overview", "governance", "provenance", "payload"]
    assert relationship_row["relationship_id"] == "principal::did:key:z6MkPendingBackend::ledger::test"
    assert relationship_row["row_family"] == "relationship"
    assert relationship_row["detail_panels"][1] == "permission_or_access"

    activate_ledger = client.post(
        "/api/control-plane/entities/activate",
        json={"entity_type": "ledger", "entity_id": "test", "status": "active", "idempotency_key": "ledger-activate-1"},
        headers=headers,
    )
    activate_principal = client.post(
        "/api/control-plane/entities/activate",
        json={"entity_type": "principal", "entity_id": "did:key:z6MkPendingBackend", "status": "active", "idempotency_key": "principal-activate-1"},
        headers=headers,
    )
    activate_surface = client.post(
        "/api/control-plane/entities/activate",
        json={"entity_type": "surface", "entity_id": "surface:test", "status": "active", "idempotency_key": "surface-activate-1"},
        headers=headers,
    )
    assert activate_ledger.status_code == 200
    assert activate_principal.status_code == 200
    assert activate_surface.status_code == 200


def test_control_plane_ledger_defaults_constitution_name_from_display_name(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    ledger = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "ledger:test-fallback",
            "name": "LOAM Runtime",
            "tenant_id": "tenant:demo",
            "status": "pending",
            "provisioning_source": "control_plane",
            "idempotency_key": "ledger-create-fallback-1",
            "founding_constitution_personality": "Deliberate and patient.",
            "founding_constitution_purpose": "Hold governed memory and continuity for this ledger.",
        },
        headers=headers,
    )
    assert ledger.status_code == 200
    constitution = ledger.json()["ledger"]["metadata"]["founding_constitution"]
    assert constitution["name"] == "LOAM Runtime"
    assert constitution["personality"] == "Deliberate and patient."
    assert constitution["purpose"] == "Hold governed memory and continuity for this ledger."
    assert constitution["source"] == "control_plane_operator"


def test_control_plane_upsert_prefixed_ledger_alias_collapses_to_existing_canonical_row(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    first = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "chat-demo",
            "name": "Chat Demo",
            "status": "active",
            "founding_constitution_name": "LOAM",
        },
        headers=headers,
    )
    assert first.status_code == 200

    second = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "ledger:chat-demo",
            "name": "Chat Demo Alias",
            "status": "active",
        },
        headers=headers,
    )
    assert second.status_code == 200
    payload = second.json()["ledger"]
    assert payload["ledger_id"] == "chat-demo"
    assert payload["canonical_subject"] == "did:web:testserver:ledgers:chat-demo"
    assert "ledger:chat-demo" in payload["metadata"]["ledger_alias_history"]

    ledgers = client.get("/api/control-plane/ledgers", headers=headers).json()["ledgers"]
    assert len(ledgers) == 1
    assert ledgers[0]["ledger_id"] == "chat-demo"
    assert "ledger:chat-demo" in ledgers[0]["metadata"]["ledger_alias_history"]


def test_control_plane_ledger_create_requires_explicit_canonical_id_or_namespace(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    response = client.post(
        "/api/control-plane/ledgers",
        json={
            "name": "Duplicate Display Name Risk",
            "status": "pending",
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "ledger_id is required"


def test_control_plane_ledger_visible_rename_appends_alias_history_without_changing_canonical_identity(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    created = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "chat-demo",
            "name": "Chat Demo",
            "status": "active",
            "founding_constitution_name": "LOAM",
        },
        headers=headers,
    )
    assert created.status_code == 200
    original_subject = created.json()["ledger"]["canonical_subject"]

    renamed = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "chat-demo",
            "name": "LOAM",
            "status": "active",
            "founding_constitution_name": "LOAM Runtime",
        },
        headers=headers,
    )
    assert renamed.status_code == 200
    payload = renamed.json()["ledger"]
    assert payload["ledger_id"] == "chat-demo"
    assert payload["namespace"] == "chat-demo"
    assert payload["canonical_subject"] == original_subject
    assert payload["display_name"] == "LOAM"
    aliases = payload["metadata"]["ledger_alias_history"]
    assert "Chat Demo" in aliases
    assert "LOAM" in aliases
    assert "LOAM Runtime" in aliases
    assert "ledger:chat-demo" in aliases


def test_annotate_control_plane_ledger_row_exposes_memory_tier_classification() -> None:
    row = _annotate_control_plane_row(
        {
            "ledger_id": "test",
            "display_name": "Test Ledger",
            "namespace": "test",
            "status": "active",
            "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:test",
            "policy_profile": "standard",
            "metadata": {
                "ledger_alias_history": ["ledger:test"],
                "founding_constitution": {
                    "name": "LOAM",
                    "personality": "Deliberate.",
                    "purpose": "Hold governed memory.",
                    "source": "control_plane_operator",
                },
                "retention_tier": "Clay",
                "memory_tier_contract": {
                    "ledger_record_tier": "Clay",
                    "durable_classes": [
                        "founding_constitution",
                        "ledger_alias_history",
                        "ledger_supersession_history",
                        "ledger_consolidation_history",
                    ],
                    "future_silt_classes": ["active_continuity_state", "working_profile_state"],
                    "future_sand_classes": ["multimodal_stream_ingress", "surface_recognition_windows"],
                    "s1_s2_topology_acknowledged": True,
                },
                "ledger_alias_history": [],
                "ledger_supersession_history": [],
                "ledger_consolidation_history": [],
            },
        },
        kind="ledger",
    )

    assert row["memory_tier_classification"]["retention_tier"] == "Clay"
    contract = row["memory_tier_classification"]["contract"]
    assert contract["ledger_record_tier"] == "Clay"
    assert "founding_constitution" in contract["durable_classes"]
    assert "multimodal_stream_ingress" in contract["future_sand_classes"]
    assert contract["s1_s2_topology_acknowledged"] is True


def test_control_plane_consolidate_ledgers_rebinds_entities_and_preserves_history(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "chat-demo",
            "name": "Chat Demo",
            "status": "active",
            "founding_constitution_name": "LOAM",
            "founding_constitution_purpose": "Hold governed memory.",
        },
        headers=headers,
    )
    client.app.state.db[LEDGER_REGISTRY_V1_KEY] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                "chat-demo": {
                    "ledger_id": "chat-demo",
                    "display_name": "Chat Demo",
                    "namespace": "chat-demo",
                    "status": "active",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    "metadata": {
                        "founding_constitution": {
                            "name": "LOAM",
                            "purpose": "Hold governed memory.",
                            "source": "control_plane_operator",
                        }
                    },
                },
                "ledger:loam-137to139": {
                    "ledger_id": "ledger:loam-137to139",
                    "display_name": "LOAM 137 to 139",
                    "namespace": "ledger:loam-137to139",
                    "status": "active",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:ledger-loam-137to139",
                    "metadata": {
                        "founding_constitution": {
                            "name": "LOAM",
                            "purpose": "Hold governed memory.",
                            "source": "control_plane_operator",
                        }
                    },
                },
            },
        }
    ).encode()
    client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY] = json.dumps(
        {
            "version": 1,
            "principals": {
                "did:key:z6MkSplitPrincipal": {
                    "principal_did": "did:key:z6MkSplitPrincipal",
                    "display_name": "Split Principal",
                    "status": "active",
                    "metadata": {"actor_type": "human", "ledger_id": "ledger:loam-137to139"},
                }
            },
        }
    ).encode()
    client.post(
        "/api/control-plane/surfaces",
        json={
            "surface_id": "surface:chat:primary",
            "display_name": "Primary Chat",
            "surface_type": "chat",
            "status": "active",
            "ledger_id": "ledger:loam-137to139",
            "principal_did": "did:key:z6MkSplitPrincipal",
        },
        headers=headers,
    )
    client.post(
        "/api/control-plane/relationships",
        json={
            "subject_entity_type": "surface",
            "subject_entity_id": "surface:chat:primary",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:loam-137to139",
            "relationship_type": "member_of",
            "permission_scope": "full",
            "enabled_state": "enabled",
            "status": "active",
        },
        headers=headers,
    )

    response = client.post(
        "/api/control-plane/ledgers/consolidate",
        json={
            "canonical_ledger_id": "chat-demo",
            "superseded_ledger_ids": ["ledger:loam-137to139"],
            "reason": "rename_split_cleanup",
            "idempotency_key": "ledger-consolidate-1",
        },
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()["consolidation"]
    assert payload["canonical_ledger_id"] == "chat-demo"
    assert payload["superseded_ledger_ids"] == ["loam-137to139"]
    assert payload["preserve_history"] is True
    assert payload["silent_destructive_merge_forbidden"] is True
    assert payload["rebind_counts"]["surfaces"] == 1
    assert payload["rebind_counts"]["principals"] == 1
    assert payload["rebind_counts"]["relationships"] >= 1
    assert payload["runtime_continuity"]["alias_aware_coord_history_lookup"] is True
    assert payload["runtime_continuity"]["surviving_governed_memory_boundary"] == "chat-demo"
    assert payload["runtime_continuity"]["full_available_history_visible_across_aliases"] is True
    assert payload["runtime_continuity"]["foundation_identity_available_after_consolidation"] is True
    canonical = payload["ledger"]
    assert canonical["ledger_id"] == "chat-demo"
    assert "ledger:loam-137to139" in canonical["metadata"]["ledger_alias_history"]
    assert "ledger:loam-137to139" in canonical["metadata"]["ledger_supersession_history"]
    verified_traits = {item["trait"] for item in canonical["ledger_self_description"]["verified_ledger_traits"]}
    assert "alias_and_supersession_history" in verified_traits
    assert "consolidation_history" in verified_traits

    superseded = payload["superseded_ledgers"][0]
    assert superseded["ledger_id"] == "loam-137to139"
    assert superseded["status"] == "superseded"
    assert superseded["metadata"]["superseded_by_ledger_id"] == "chat-demo"
    assert superseded["metadata"]["canonical_ledger_id"] == "chat-demo"

    surfaces = json.loads(client.app.state.db[SURFACE_REGISTRY_V1_KEY].decode())["surfaces"]
    principals = json.loads(client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY].decode())["principals"]
    relationships = json.loads(client.app.state.db[RELATIONSHIP_REGISTRY_V1_KEY].decode())["relationships"]
    assert surfaces["surface:chat:primary"]["ledger_id"] == "chat-demo"
    assert principals["did:key:z6MkSplitPrincipal"]["metadata"]["ledger_id"] == "chat-demo"
    assert "surface::surface:chat:primary::ledger::chat-demo" in relationships
    assert "surface::surface:chat:primary::ledger::loam-137to139" not in relationships


def test_control_plane_relationship_idempotency_and_validation(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    first = client.post(
        "/api/control-plane/relationships",
        json={
            "subject_entity_type": "principal",
            "subject_entity_id": "did:key:z6MkPendingBackend",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:test",
            "relationship_type": "member_of_ledger",
            "status": "pending",
            "permission_scope": "custom",
            "permission_payload": {"allow": ["read"]},
            "idempotency_key": "rel-idem-1",
        },
        headers=headers,
    )
    assert first.status_code == 200

    replay = client.post(
        "/api/control-plane/relationships",
        json={
            "subject_entity_type": "principal",
            "subject_entity_id": "did:key:z6MkPendingBackend",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:test",
            "relationship_type": "member_of_ledger",
            "status": "pending",
            "permission_scope": "custom",
            "permission_payload": {"allow": ["read"]},
            "idempotency_key": "rel-idem-1",
        },
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["mutation_ref"] == first.json()["mutation_ref"]

    conflict = client.post(
        "/api/control-plane/relationships",
        json={
            "subject_entity_type": "principal",
            "subject_entity_id": "did:key:z6MkPendingBackend",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:test",
            "relationship_type": "member_of_ledger",
            "status": "active",
            "permission_scope": "full",
            "idempotency_key": "rel-idem-1",
        },
        headers=headers,
    )
    assert conflict.status_code == 409

    invalid = client.post(
        "/api/control-plane/relationships",
        json={
            "subject_entity_type": "principal",
            "subject_entity_id": "did:key:z6MkPendingOther",
            "object_entity_type": "ledger",
            "object_entity_id": "ledger:test",
            "relationship_type": "member_of_ledger",
            "status": "active",
            "permission_scope": "custom",
            "permission_payload": {},
        },
        headers=headers,
    )
    assert invalid.status_code == 422


def test_control_plane_provider_and_model_binding_persist_with_secret_redaction(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    provider = client.post(
        "/api/control-plane/providers",
        json={
            "provider_id": "provider:openrouter:shared",
            "provider_type": "OpenRouter",
            "credential_ref": "credref:openrouter:shared:v1",
            "secret_ref": "OPENROUTER_API_KEY",
            "secret_material": "super-secret-token",
            "status": "configured",
            "idempotency_key": "provider-1",
        },
        headers=headers,
    )
    assert provider.status_code == 200
    provider_record = provider.json()["provider"]
    assert provider_record["provider_id"] == "provider:openrouter:shared"
    assert provider_record["secret_present"] is True
    assert "secret_material" not in provider_record
    assert provider_record["canonical_subject"] == "did:web:id.dualsubstrate.com:providers:provider-openrouter-shared"
    assert provider_record["canonical_subject_source"] == "did:web:provider"

    provider_list = client.get("/api/control-plane/providers", headers=headers)
    assert provider_list.status_code == 200
    assert provider_list.json()["providers"][0]["secret_present"] is True

    binding = client.post(
        "/api/control-plane/model-bindings",
        json={
            "binding_id": "binding:chat:default",
            "name": "Chat default binding",
            "provider_id": "provider:openrouter:shared",
            "provider_ref": "provider:openrouter:shared",
            "credential_ref": "credref:openrouter:shared:v1",
            "provider_type": "OpenRouter",
            "model_id": "openai/gpt-4o",
            "app_surfaces": ["surface:chat:primary"],
            "status": "configured",
            "idempotency_key": "binding-1",
        },
        headers=headers,
    )
    assert binding.status_code == 200
    binding_record = binding.json()["model_binding"]
    assert binding_record["binding_id"] == "binding:chat:default"
    assert binding_record["credential_ref"] == "credref:openrouter:shared:v1"
    assert binding_record["canonical_subject"] == "did:web:id.dualsubstrate.com:bindings:binding-chat-default"
    assert binding_record["canonical_subject_source"] == "did:web:binding"

    raw_providers = client.app.state.db[PROVIDER_CREDENTIAL_REGISTRY_V1_KEY]
    raw_bindings = client.app.state.db[MODEL_BINDING_REGISTRY_V1_KEY]
    persisted_providers = json.loads(raw_providers.decode())["providers"]
    persisted_bindings = json.loads(raw_bindings.decode())["model_bindings"]
    assert persisted_providers["provider:openrouter:shared"]["secret_material"] == "super-secret-token"
    assert persisted_providers["provider:openrouter:shared"]["canonical_subject"] == "did:web:id.dualsubstrate.com:providers:provider-openrouter-shared"
    assert "binding:chat:default" in persisted_bindings
    assert persisted_bindings["binding:chat:default"]["canonical_subject"] == "did:web:id.dualsubstrate.com:bindings:binding-chat-default"


def test_control_plane_rejects_duplicate_canonical_subjects_for_non_principal_entities(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    provider = client.post(
        "/api/control-plane/providers",
        json={
            "provider_id": "provider:openrouter:shared",
            "provider_type": "OpenRouter",
            "status": "configured",
            "idempotency_key": "provider-canonical-1",
        },
        headers=headers,
    )
    assert provider.status_code == 200

    provider_conflict = client.post(
        "/api/control-plane/providers",
        json={
            "provider_id": "provider:openrouter:secondary",
            "provider_type": "OpenRouter",
            "canonical_subject": "did:web:id.dualsubstrate.com:providers:provider-openrouter-shared",
            "canonical_subject_source": "did:web:provider",
            "status": "configured",
            "idempotency_key": "provider-canonical-2",
        },
        headers=headers,
    )
    assert provider_conflict.status_code == 409
    assert "canonical_subject already bound" in provider_conflict.json()["detail"]

    binding = client.post(
        "/api/control-plane/model-bindings",
        json={
            "binding_id": "binding:chat:default",
            "name": "Chat default binding",
            "provider_type": "OpenRouter",
            "model_id": "google/gemini-2.5-flash",
            "app_surfaces": ["surface:chat:primary"],
            "status": "configured",
            "idempotency_key": "binding-canonical-1",
        },
        headers=headers,
    )
    assert binding.status_code == 200

    binding_conflict = client.post(
        "/api/control-plane/model-bindings",
        json={
            "binding_id": "binding:chat:alternate",
            "name": "Alternate chat binding",
            "provider_type": "OpenRouter",
            "model_id": "google/gemini-2.5-flash",
            "canonical_subject": "did:web:id.dualsubstrate.com:bindings:binding-chat-default",
            "canonical_subject_source": "did:web:binding",
            "status": "configured",
            "idempotency_key": "binding-canonical-2",
        },
        headers=headers,
    )
    assert binding_conflict.status_code == 409
    assert "canonical_subject already bound" in binding_conflict.json()["detail"]

    surface = client.post(
        "/api/control-plane/surfaces",
        json={
            "surface_id": "surface:chat:primary",
            "display_name": "Primary Chat",
            "surface_type": "chat",
            "status": "active",
            "idempotency_key": "surface-canonical-1",
        },
        headers=headers,
    )
    assert surface.status_code == 200
    assert surface.json()["surface"]["canonical_subject"] == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"

    surface_conflict = client.post(
        "/api/control-plane/surfaces",
        json={
            "surface_id": "surface:chat:shadow",
            "display_name": "Shadow Chat",
            "surface_type": "chat",
            "canonical_subject": "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary",
            "canonical_subject_source": "did:web:surface",
            "status": "active",
            "idempotency_key": "surface-canonical-2",
        },
        headers=headers,
    )
    assert surface_conflict.status_code == 409
    assert "canonical_subject already bound" in surface_conflict.json()["detail"]


def test_control_plane_model_binding_upserts_stable_model_principal(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    first = client.post(
        "/api/control-plane/model-bindings",
        json={
            "binding_id": "binding:chat:gemini-primary",
            "name": "Primary Gemini binding",
            "provider_type": "OpenRouter",
            "model_id": "google/gemini-2.5-flash",
            "app_surfaces": ["surface:chat:primary"],
            "status": "configured",
            "idempotency_key": "binding-principal-1",
        },
        headers=headers,
    )
    assert first.status_code == 200
    first_binding = first.json()["model_binding"]
    stable_did = "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash"
    assert first_binding["linked_model_principal"] == stable_did

    second = client.post(
        "/api/control-plane/model-bindings",
        json={
            "binding_id": "binding:chat:gemini-secondary",
            "name": "Secondary Gemini binding",
            "provider_type": "OpenRouter",
            "model_id": "google/gemini-2.5-flash",
            "app_surfaces": ["surface:custom:main-chat"],
            "status": "configured",
            "idempotency_key": "binding-principal-2",
        },
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["model_binding"]["linked_model_principal"] == stable_did

    raw_principals = client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY]
    principals = json.loads(raw_principals.decode())["principals"]
    assert stable_did in principals
    principal = principals[stable_did]
    assert principal["principal_key_refs"] == ["openrouter:model:google/gemini-2.5-flash"]
    assert principal["canonical_subject"] == stable_did
    assert principal["canonical_subject_source"] == "did:web:model-principal"
    assert principal["status"] == "active"
    assert principal["actor_type"] == "model"
    assert principal["metadata"]["actor_type"] == "model"


def test_control_plane_model_binding_collapses_legacy_model_principal_to_stable_did(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    legacy = client.post(
        "/api/control-plane/principals",
        json={
            "principal_did": "did:key:z6MkLegacyGemini",
            "tenant_id": "tenant:unknown",
            "display_name": "Legacy Gemini Principal",
            "principal_key_refs": ["openrouter:model:google/gemini-2.5-flash"],
            "metadata": {"actor_type": "model", "wallet_capable": False},
            "status": "active",
            "idempotency_key": "legacy-principal-1",
        },
        headers=headers,
    )
    assert legacy.status_code == 200

    binding = client.post(
        "/api/control-plane/model-bindings",
        json={
            "binding_id": "binding:chat:gemini-migrated",
            "name": "Migrated Gemini binding",
            "provider_type": "OpenRouter",
            "model_id": "google/gemini-2.5-flash",
            "app_surfaces": ["surface:chat:primary"],
            "status": "configured",
            "idempotency_key": "binding-principal-migrate-1",
        },
        headers=headers,
    )
    assert binding.status_code == 200
    stable_did = "did:web:id.dualsubstrate.com:principals:model:openrouter:google-gemini-2-5-flash"
    assert binding.json()["model_binding"]["linked_model_principal"] == stable_did

    raw_principals = client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY]
    principals = json.loads(raw_principals.decode())["principals"]
    assert stable_did in principals
    assert "did:key:z6MkLegacyGemini" not in principals
    assert principals[stable_did]["display_name"] == "google/gemini-2.5-flash"
    assert principals[stable_did]["principal_key_refs"] == ["openrouter:model:google/gemini-2.5-flash"]


def test_control_plane_surface_list_rewrites_legacy_canonical_host(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    response = client.post(
        "/api/control-plane/surfaces",
        json={
            "surface_id": "surface:chat:primary",
            "display_name": "Primary Chat",
            "surface_type": "chat",
            "canonical_subject": "did:web:legacy.local:surfaces:surface-chat-primary",
            "canonical_subject_source": "did:web:surface",
            "status": "active",
            "idempotency_key": "surface-legacy-host-1",
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["surface"]["canonical_subject"] == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"

    listing = client.get("/api/control-plane/surfaces", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["surfaces"][0]["canonical_subject"] == "did:web:id.dualsubstrate.com:surfaces:surface-chat-primary"


def test_control_plane_provision_codex_principal_freezes_stable_agent_identity(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    response = client.post(
        "/api/control-plane/principals/codex/provision",
        json={
            "tenant_id": "tenant:demo",
            "ledger_id": "chat-demo",
            "surface_ids": ["surface:chat:primary"],
            "idempotency_key": "codex-principal-1",
        },
        headers=headers,
    )
    assert response.status_code == 200
    principal = response.json()["principal"]
    assert principal["principal_did"] == "did:web:id.dualsubstrate.com:principals:agent:openai:codex"
    assert principal["canonical_subject"] == "did:web:id.dualsubstrate.com:principals:agent:openai:codex"
    assert principal["actor_type"] == "agent"
    assert principal["status"] == "active"
    assert principal["display_name"] == "OpenAI Codex"
    assert principal["metadata"]["actor_type"] == "agent"
    assert principal["metadata"]["provider_type"] == "openai"
    assert principal["metadata"]["agent_id"] == "codex"
    assert principal["metadata"]["agent_runtime"] == "external_cli"
    assert principal["metadata"]["ledger_id"] == "chat-demo"
    delegated = principal["metadata"]["delegated_authority"]
    assert delegated["delegation_mode"] == "delegated_only"
    assert delegated["delegated_prompt_execution"] == "explicit_cli_request_required"
    assert delegated["hidden_operator_alias"] is False
    assert delegated["revocable"] is True
    assert delegated["revocation_mode"] == "control_plane_operator"
    assert delegated["ledger_scope"] == ["chat-demo"]
    assert delegated["surface_scope"] == ["surface:chat:primary"]
    assert delegated["delegated_by_principal_id"] == "ops-admin"
    assert principal["principal_key_refs"] == ["openai:agent:codex"]

    list_response = client.get("/api/control-plane/principals", headers=headers)
    assert list_response.status_code == 200
    principals = list_response.json()["principals"]
    assert principals[0]["principal_did"] == "did:web:id.dualsubstrate.com:principals:agent:openai:codex"


def test_control_plane_provision_codex_principal_uses_public_identity_domain_without_env(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)

    client = _make_client(base_url="https://ds-backend-new.fly.dev")
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    response = client.post(
        "/api/control-plane/principals/codex/provision",
        json={
            "tenant_id": "tenant:demo",
            "ledger_id": "chat-demo",
            "surface_ids": ["surface:chat:primary"],
            "idempotency_key": "codex-principal-no-env",
        },
        headers=headers,
    )
    assert response.status_code == 200
    principal = response.json()["principal"]
    assert principal["principal_did"] == "did:web:id.dualsubstrate.com:principals:agent:openai:codex"


def test_control_plane_provision_kimi_principal_freezes_stable_agent_identity(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")
    monkeypatch.setenv("KIMI_PRINCIPAL_HOST", "chat.dualsubstrate.com")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    response = client.post(
        "/api/control-plane/principals/kimi/provision",
        json={
            "tenant_id": "tenant:demo",
            "ledger_id": "chat-demo",
            "surface_ids": ["surface:chat:primary"],
            "idempotency_key": "kimi-principal-1",
        },
        headers=headers,
    )
    assert response.status_code == 200
    principal = response.json()["principal"]
    assert principal["principal_did"] == "did:web:chat.dualsubstrate.com:principals:agent:moonshot:kimi-code"
    assert principal["canonical_subject"] == "did:web:chat.dualsubstrate.com:principals:agent:moonshot:kimi-code"
    assert principal["actor_type"] == "agent"
    assert principal["status"] == "active"
    assert principal["display_name"] == "Moonshot: Kimi-code"
    assert principal["metadata"]["actor_type"] == "agent"
    assert principal["metadata"]["provider_type"] == "moonshot"
    assert principal["metadata"]["agent_id"] == "kimi-code"
    assert principal["metadata"]["agent_runtime"] == "external_cli"
    assert principal["metadata"]["ledger_id"] == "chat-demo"
    delegated = principal["metadata"]["delegated_authority"]
    assert delegated["delegation_mode"] == "delegated_only"
    assert delegated["delegated_prompt_execution"] == "explicit_cli_request_required"
    assert delegated["hidden_operator_alias"] is False
    assert delegated["revocable"] is True
    assert delegated["revocation_mode"] == "control_plane_operator"
    assert delegated["ledger_scope"] == ["chat-demo"]
    assert delegated["surface_scope"] == ["surface:chat:primary"]
    assert delegated["delegated_by_principal_id"] == "ops-admin"
    assert principal["principal_key_refs"] == ["moonshot:agent:kimi-code"]

    list_response = client.get("/api/control-plane/principals", headers=headers)
    assert list_response.status_code == 200
    principals = list_response.json()["principals"]
    assert any(p["principal_did"] == "did:web:chat.dualsubstrate.com:principals:agent:moonshot:kimi-code" for p in principals)


def test_control_plane_provision_kimi_principal_uses_request_host_without_env(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("KIMI_PRINCIPAL_HOST", raising=False)

    client = _make_client(base_url="https://chat.example.com")
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    response = client.post(
        "/api/control-plane/principals/kimi/provision",
        json={
            "tenant_id": "tenant:demo",
            "ledger_id": "chat-demo",
            "surface_ids": ["surface:chat:primary"],
            "idempotency_key": "kimi-principal-no-env",
        },
        headers=headers,
    )
    assert response.status_code == 200
    principal = response.json()["principal"]
    assert principal["principal_did"] == "did:web:chat.example.com:principals:agent:moonshot:kimi-code"


def test_control_plane_relationships_include_derived_material_connections(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    principal = client.post(
        "/api/control-plane/principals",
        json={
            "principal_did": "did:key:z6MkDavidBerigny",
            "tenant_id": "tenant:unknown",
            "display_name": "David Berigny",
            "metadata": {"actor_type": "human", "wallet_capable": True},
            "status": "active",
            "idempotency_key": "material-principal-1",
        },
        headers=headers,
    )
    assert principal.status_code == 200

    ledger = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "chat-demo",
            "name": "chat-demo",
            "tenant_id": "tenant:unknown",
            "status": "active",
            "idempotency_key": "material-ledger-1",
        },
        headers=headers,
    )
    assert ledger.status_code == 200

    surface = client.post(
        "/api/control-plane/surfaces",
        json={
            "surface_id": "surface:chat:primary",
            "display_name": "chat.dualsubstrate.com",
            "surface_type": "chat",
            "status": "active",
            "ledger_id": "chat-demo",
            "principal_did": "did:key:z6MkDavidBerigny",
            "idempotency_key": "material-surface-1",
        },
        headers=headers,
    )
    assert surface.status_code == 200

    first_binding = client.post(
        "/api/control-plane/model-bindings",
        json={
            "binding_id": "binding:chat:haiku",
            "name": "Anthropic Claude Haiku 4.5",
            "provider_type": "OpenRouter",
            "model_id": "anthropic/claude-haiku-4.5",
            "app_surfaces": ["surface:chat:primary"],
            "status": "configured",
            "idempotency_key": "material-binding-1",
        },
        headers=headers,
    )
    assert first_binding.status_code == 200
    model_1 = first_binding.json()["model_binding"]["linked_model_principal"]

    second_binding = client.post(
        "/api/control-plane/model-bindings",
        json={
            "binding_id": "binding:chat:gemini",
            "name": "Google Gemini 2.5 Flash",
            "provider_type": "OpenRouter",
            "model_id": "google/gemini-2.5-flash",
            "app_surfaces": ["surface:chat:primary"],
            "status": "configured",
            "idempotency_key": "material-binding-2",
        },
        headers=headers,
    )
    assert second_binding.status_code == 200
    model_2 = second_binding.json()["model_binding"]["linked_model_principal"]

    relationships = client.get("/api/control-plane/relationships", headers=headers)
    assert relationships.status_code == 200
    rows = relationships.json()["relationships"]
    relationship_lookup = {
        row["relationship_id"]: row
        for row in rows
    }

    assert relationship_lookup["surface::surface:chat:primary::ledger::chat-demo"]["relationship_type"] == "surface_bound_to_ledger"
    assert relationship_lookup["principal::did:key:z6MkDavidBerigny::surface::surface:chat:primary"]["relationship_type"] == "can_access_surface"
    assert relationship_lookup[f"principal::{model_1}::surface::surface:chat:primary"]["relationship_type"] == "can_access_surface"
    assert relationship_lookup[f"principal::{model_1}::ledger::chat-demo"]["relationship_type"] == "writes_to_ledger"
    assert relationship_lookup[f"principal::{model_1}::principal::did:key:z6MkDavidBerigny"]["relationship_type"] == "administered_by"

    assert f"principal::{model_1}::principal::{model_2}" not in relationship_lookup
    assert all(
        not (
            row["subject_entity_id"] == model_1
            and row["object_entity_id"] == model_2
        )
        for row in rows
    )


def test_control_plane_submission_registry_persists_governed_mutations(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    submit = client.post(
        "/api/control-plane/submissions",
        json={
            "mutation_kind": "relationships",
            "target_path": "/api/control-plane/relationships",
            "target_entity_type": "relationship",
            "target_entity_id": "principal::did:key:z6MkPendingBackend::ledger::test",
            "payload": {
                "subject_entity_type": "principal",
                "subject_entity_id": "did:key:z6MkPendingBackend",
                "object_entity_type": "ledger",
                "object_entity_id": "ledger:test",
            },
            "evidence_refs": ["ev:review:test"],
            "idempotency_key": "submission-1",
        },
        headers=headers,
    )
    assert submit.status_code == 200
    assert submit.json()["execution_mode"] == "submitted_for_approval"
    assert submit.json()["submission_status"] == "submitted"
    assert submit.json()["submission"]["target_path"] == "/api/control-plane/relationships"
    assert submit.json()["submission"]["submitted_by"] == "ops-admin"
    assert submit.json()["submission"]["evidence_refs"] == ["ev:review:test"]
    assert submit.json()["submission"]["row_family"] == "governance"
    assert submit.json()["submission"]["preferred_reference"]["value"] == submit.json()["submission_ref"]
    assert submit.json()["submission"]["shareability"] == "internal-only"
    assert [row["status"] for row in submit.json()["submission"]["lifecycle"]] == ["submitted"]
    assert "applied_at" not in submit.json()
    assert submit.json()["submitted_at"]

    raw_submissions = client.app.state.db[CONTROL_PLANE_SUBMISSION_REGISTRY_V1_KEY]
    stored = json.loads(raw_submissions.decode())["submissions"]
    assert len(stored) == 1

    review = client.post(
        f"/api/control-plane/submissions/{submit.json()['submission_ref']}/review",
        json={"action": "approve", "reviewer_note": "looks good"},
        headers=headers,
    )
    assert review.status_code == 200
    assert review.json()["submission_status"] == "applied"
    assert review.json()["approved_at"]
    assert review.json()["result"]["relationship"]["relationship_id"] == "principal::did:key:z6MkPendingBackend::ledger::test"
    assert [row["status"] for row in review.json()["submission"]["lifecycle"]] == ["submitted", "approved", "applied"]


def test_control_plane_submission_review_persists_failed_state(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    submit = client.post(
        "/api/control-plane/submissions",
        json={
            "mutation_kind": "remove",
            "target_path": "/api/control-plane/entities/remove",
            "target_entity_type": "principal",
            "target_entity_id": "did:key:z6MkMissingBackend",
            "payload": {"entity_type": "principal", "entity_id": "did:key:z6MkMissingBackend"},
            "idempotency_key": "submission-fail-1",
        },
        headers=headers,
    )
    assert submit.status_code == 200

    review = client.post(
        f"/api/control-plane/submissions/{submit.json()['submission_ref']}/review",
        json={"action": "approve"},
        headers=headers,
    )
    assert review.status_code == 422
    body = review.json()
    assert body["submission_status"] == "failed"
    assert body["failure"]["failure_class"] == "validation_failure"
    assert body["approved_at"]
    assert body["failed_at"]
    assert [row["status"] for row in body["submission"]["lifecycle"]] == ["submitted", "approved", "failed"]


def test_public_object_row_semantics_are_lifecycle_aware() -> None:
    superseded = _annotate_control_plane_row(
        {
            "public_object_id": "https://id.example/o/claim/2026/obj-v1",
            "current_public_object_id": "https://id.example/o/claim/2026/obj-v2",
            "status_ref": "https://id.example/o/claim/2026/obj-v1/status",
            "object_id": "obj-122",
            "lifecycle_state": "superseded",
            "shareability": "share-ready",
        },
        kind="public_object",
    )
    assert superseded["row_family"] == "public_object"
    assert superseded["preferred_reference"]["value"] == "https://id.example/o/claim/2026/obj-v2"
    assert superseded["shareability"] == "fallback-only"
    assert any(alias["field"] == "public_object_id" for alias in superseded["reference_aliases"])
    assert superseded["detail_panels"][1] == "lifecycle"

    revoked = _annotate_control_plane_row(
        {
            "public_object_id": "https://id.example/o/claim/2026/obj-v0",
            "status_ref": "https://id.example/o/claim/2026/obj-v0/status",
            "object_id": "obj-121",
            "lifecycle_state": "revoked",
            "shareability": "share-ready",
        },
        kind="public_object",
    )
    assert revoked["preferred_reference"]["value"] == "https://id.example/o/claim/2026/obj-v0"
    assert revoked["shareability"] == "not-shareable"


def test_control_plane_relationships_derive_from_ledger_owner_and_principal_metadata(monkeypatch) -> None:
    """A ledger should surface a relationship for its owner and for principals whose metadata points to it."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "did:key:z6MkOperator", "x-principal-type": "admin"}

    ledger = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "ledger:derived",
            "name": "Derived Relationship Ledger",
            "tenant_id": "tenant:demo",
            "status": "active",
            "owner_principal_id": "did:key:z6MkOperator",
            "provisioning_source": "control_plane",
            "idempotency_key": "ledger-derived-1",
        },
        headers=headers,
    )
    assert ledger.status_code == 200

    principal = client.post(
        "/api/control-plane/principals",
        json={
            "principal_did": "did:key:z6MkMember",
            "tenant_id": "tenant:demo",
            "display_name": "Member Principal",
            "status": "active",
            "metadata": {"actor_type": "human", "ledger_id": "ledger:derived"},
            "provisioning_source": "control_plane",
            "idempotency_key": "principal-derived-1",
        },
        headers=headers,
    )
    assert principal.status_code == 200

    relationships = client.get("/api/control-plane/relationships", headers=headers)
    assert relationships.status_code == 200
    ids = {r["relationship_id"] for r in relationships.json()["relationships"]}

    assert "principal::did:key:z6MkOperator::ledger::derived" in ids
    assert "principal::did:key:z6MkMember::ledger::derived" in ids

    # No surface exists, so there should be no surface-bound relationship.
    assert not any(r.startswith("surface::") for r in ids)



def test_control_plane_ledger_canonicalization_lowercases_id_and_namespace(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    response = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "ledger:LOAM",
            "namespace": "LOAM",
            "name": "Loam Root 01",
            "status": "active",
            "idempotency_key": "ledger-loam-uppercase",
        },
        headers=headers,
    )
    assert response.status_code == 200
    ledger = response.json()["ledger"]
    assert ledger["ledger_id"] == "loam"
    assert ledger["namespace"] == "loam"
    assert "ledgers:loam" in ledger["canonical_subject"]

    ledgers = client.get("/api/control-plane/ledgers", headers=headers).json()["ledgers"]
    assert len(ledgers) == 1
    assert ledgers[0]["ledger_id"] == "loam"


def test_control_plane_ledger_list_discovers_runtime_namespaces(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    client.app.state.db[b"loam:WX-123"] = b"{}"
    client.app.state.db[b"entity:chat-demo:body"] = b"{}"
    client.app.state.db[b"metrics:events:loam:2026-01-01T00:00:00Z"] = b"{}"
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    ledgers = client.get("/api/control-plane/ledgers", headers=headers).json()["ledgers"]
    ids = {item["ledger_id"] for item in ledgers}
    assert "loam" in ids
    loam = next(item for item in ledgers if item["ledger_id"] == "loam")
    assert loam.get("provisioning_source") == "runtime_discovered"
    assert "ledgers:loam" in loam.get("canonical_subject", "")
    assert "entity" not in ids
    assert "metrics" not in ids


def test_control_plane_ledger_list_merges_registered_and_runtime_ledgers(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    registered = client.post(
        "/api/control-plane/ledgers",
        json={
            "ledger_id": "loam-root-01",
            "name": "Loam Root 01",
            "status": "active",
            "idempotency_key": "ledger-loam-root-01",
        },
        headers=headers,
    )
    assert registered.status_code == 200

    client.app.state.db[b"loam:WX-456"] = b"{}"

    ledgers = client.get("/api/control-plane/ledgers", headers=headers).json()["ledgers"]
    ids = {item["ledger_id"] for item in ledgers}
    assert "loam" in ids
    assert "loam-root-01" in ids

    # Consolidate the stale registered ledger into the runtime canonical ledger.
    consolidate = client.post(
        "/api/control-plane/ledgers/consolidate",
        json={
            "canonical_ledger_id": "loam",
            "superseded_ledger_ids": ["loam-root-01"],
            "reason": "stale_root_alias",
            "idempotency_key": "ledger-consolidate-loam",
        },
        headers=headers,
    )
    assert consolidate.status_code == 200
    consolidation = consolidate.json()["consolidation"]
    assert consolidation["canonical_ledger_id"] == "loam"
    assert "loam-root-01" in consolidation["superseded_ledger_ids"]

    ledgers = client.get("/api/control-plane/ledgers", headers=headers).json()["ledgers"]
    ids = {item["ledger_id"] for item in ledgers}
    assert "loam" in ids
    loam = next(item for item in ledgers if item["ledger_id"] == "loam")
    assert "loam-root-01" in loam.get("metadata", {}).get("ledger_alias_history", [])



def test_control_plane_list_orphan_ledgers_excludes_referenced_records(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    client.post(
        "/api/control-plane/ledgers",
        json={"ledger_id": "linked-ledger", "name": "Linked", "status": "active", "idempotency_key": "ledger-linked"},
        headers=headers,
    )
    client.post(
        "/api/control-plane/ledgers",
        json={"ledger_id": "orphan-ledger", "name": "Orphan", "status": "active", "idempotency_key": "ledger-orphan"},
        headers=headers,
    )
    client.post(
        "/api/control-plane/surfaces",
        json={
            "surface_id": "surface:linked",
            "display_name": "Linked Surface",
            "surface_type": "chat",
            "status": "active",
            "ledger_id": "linked-ledger",
            "idempotency_key": "surface-linked",
        },
        headers=headers,
    )

    response = client.get("/api/control-plane/ledgers/orphans", headers=headers)
    assert response.status_code == 200
    ids = {item["ledger_id"] for item in response.json()["ledgers"]}
    assert "orphan-ledger" in ids
    assert "linked-ledger" not in ids


def test_control_plane_remove_entity_ledger_rejects_non_orphan_when_orphan_only(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    client.post(
        "/api/control-plane/ledgers",
        json={"ledger_id": "linked-ledger", "name": "Linked", "status": "active", "idempotency_key": "ledger-linked-2"},
        headers=headers,
    )
    client.post(
        "/api/control-plane/surfaces",
        json={
            "surface_id": "surface:linked",
            "display_name": "Linked Surface",
            "surface_type": "chat",
            "status": "active",
            "ledger_id": "linked-ledger",
            "idempotency_key": "surface-linked-2",
        },
        headers=headers,
    )

    response = client.post(
        "/api/control-plane/entities/remove",
        json={"entity_type": "ledger", "entity_id": "linked-ledger", "orphan_only": True},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "ledger_not_orphan"


def test_control_plane_remove_entity_ledger_allows_orphan(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    client.post(
        "/api/control-plane/ledgers",
        json={"ledger_id": "orphan-ledger", "name": "Orphan", "status": "active", "idempotency_key": "ledger-orphan-3"},
        headers=headers,
    )
    client.post(
        "/api/control-plane/ledgers",
        json={"ledger_id": "keeper-ledger", "name": "Keeper", "status": "active", "idempotency_key": "ledger-keeper"},
        headers=headers,
    )

    response = client.post(
        "/api/control-plane/entities/remove",
        json={
            "entity_type": "ledger",
            "entity_id": "orphan-ledger",
            "orphan_only": True,
            "reason": "housekeeping",
            "idempotency_key": "remove-orphan-3",
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["removed"] is True
    assert body["orphan"] is True
    assert body["reason"] == "housekeeping"

    ledgers = client.get("/api/control-plane/ledgers", headers=headers).json()["ledgers"]
    assert "orphan-ledger" not in {item["ledger_id"] for item in ledgers}
    assert "keeper-ledger" in {item["ledger_id"] for item in ledgers}


def test_control_plane_remove_entity_ledger_blocks_last_active_ledger(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {"x-principal-id": "ops-admin", "x-principal-type": "admin"}

    client.post(
        "/api/control-plane/ledgers",
        json={"ledger_id": "only-ledger", "name": "Only", "status": "active", "idempotency_key": "ledger-only"},
        headers=headers,
    )

    response = client.post(
        "/api/control-plane/entities/remove",
        json={"entity_type": "ledger", "entity_id": "only-ledger", "orphan_only": True},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "last_active_ledger"

    response = client.post(
        "/api/control-plane/entities/remove",
        json={
            "entity_type": "ledger",
            "entity_id": "only-ledger",
            "orphan_only": True,
            "allow_last_active_removal": True,
            "idempotency_key": "remove-only-override",
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["removed"] is True
