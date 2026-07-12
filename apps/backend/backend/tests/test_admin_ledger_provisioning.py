from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import (
    LEDGER_REGISTRY_KEY,
    LEDGER_REGISTRY_V1_KEY,
    TENANT_REGISTRY_V1_KEY,
    router as admin_router,
)
from backend.services.ledger_service import LedgerService


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(admin_router)
    return TestClient(app)


def test_create_ledger_is_idempotent_and_persists_v1_registry(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "alice",
        "x-principal-type": "user",
        "x-tenant-id": "tenant-acme",
    }

    payload = {
        "namespace": "chat-acme-support",
        "name": "Acme Support",
        "policy_profile": "strict",
        "metadata": {"env": "staging"},
        "founding_constitution_name": "LOAM",
        "founding_constitution_personality": "Steady and deliberate.",
        "founding_constitution_purpose": "Carry governed support memory.",
    }
    create_1 = client.post("/admin/ledgers", json=payload, headers=headers)
    assert create_1.status_code == 200
    body_1 = create_1.json()
    assert body_1["status"] == "ok"
    assert body_1["ledger"] == "chat-acme-support"
    assert body_1["created"] is True
    record_1 = body_1["ledger_record"]
    assert record_1["owner_principal_id"] == "alice"
    assert record_1["owner_principal_type"] == "user"
    assert record_1["tenant_id"] == "tenant-acme"
    assert record_1["policy_profile"] == "strict"
    assert record_1["provisioning_source"] == "admin_api_v1"
    assert record_1["metadata"]["founding_constitution"]["name"] == "LOAM"
    assert record_1["metadata"]["founding_constitution"]["personality"] == "Steady and deliberate."
    assert record_1["metadata"]["founding_constitution"]["purpose"] == "Carry governed support memory."
    assert record_1["metadata"]["retention_tier"] == "Clay"
    assert record_1["metadata"]["memory_tier_contract"]["ledger_record_tier"] == "Clay"
    assert "founding_constitution" in record_1["metadata"]["memory_tier_contract"]["durable_classes"]
    assert record_1["metadata"]["ledger_alias_history"] == ["ledger:chat-acme-support"]
    assert record_1["metadata"]["ledger_supersession_history"] == []
    assert record_1["metadata"]["ledger_consolidation_history"] == []

    create_2 = client.post("/admin/ledgers", json=payload, headers=headers)
    assert create_2.status_code == 200
    body_2 = create_2.json()
    assert body_2["created"] is False
    assert body_2["ledger_record"]["created_at"] == record_1["created_at"]

    raw_v1 = client.app.state.db.get(LEDGER_REGISTRY_V1_KEY)
    assert raw_v1 is not None
    parsed_v1 = json.loads(raw_v1.decode() if isinstance(raw_v1, (bytes, bytearray)) else raw_v1)
    assert parsed_v1.get("version") == 1
    assert "chat-acme-support" in (parsed_v1.get("ledgers") or {})

    raw_legacy = client.app.state.db.get(LEDGER_REGISTRY_KEY)
    assert raw_legacy is not None
    parsed_legacy = json.loads(
        raw_legacy.decode() if isinstance(raw_legacy, (bytes, bytearray)) else raw_legacy
    )
    assert "chat-acme-support" in parsed_legacy


def test_list_ledgers_includes_migrated_legacy_records(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_client()
    client.app.state.db[LEDGER_REGISTRY_KEY] = json.dumps(["chat-legacy"]).encode()

    resp = client.get("/admin/ledgers", headers={"x-admin-token": "test-admin-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert "chat-legacy" in body["ledgers"]

    by_id = {
        record.get("ledger_id"): record for record in body.get("ledger_records", []) if isinstance(record, dict)
    }
    assert "chat-legacy" in by_id
    assert by_id["chat-legacy"]["provisioning_source"] == "legacy_registry_migration"
    assert by_id["chat-legacy"]["metadata"]["retention_tier"] == "Clay"
    assert by_id["chat-legacy"]["metadata"]["memory_tier_contract"]["ledger_record_tier"] == "Clay"


def test_ledger_library_boundary_rehydrates_foundation_identity_with_fallbacks() -> None:
    db: dict[bytes, bytes] = {}
    db[LEDGER_REGISTRY_V1_KEY] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                "chat-loam": {
                    "ledger_id": "chat-loam",
                    "display_name": "LOAM Runtime",
                    "metadata": {
                        "founding_constitution": {
                            "purpose": "Hold governed memory.",
                            "personality": "Deliberate and patient.",
                        }
                    },
                },
                "chat-display-only": {
                    "ledger_id": "chat-display-only",
                    "display_name": "Display Only Ledger",
                    "metadata": {},
                },
            },
        }
    ).encode()

    service = LedgerService(db)

    rehydrated = service.get_ledger_library_boundary("chat-loam")
    assert rehydrated["foundation_identity"]["name"] == "LOAM Runtime"
    assert rehydrated["foundation_identity"]["personality"] == "Deliberate and patient."
    assert rehydrated["foundation_identity"]["purpose"] == "Hold governed memory."
    assert rehydrated["foundation_identity"]["source"] == "control_plane_operator"
    assert rehydrated["foundation_identity"]["rehydration_mode"] == "founding_constitution"
    assert rehydrated["foundation_identity"]["constitution_present"] is True
    assert rehydrated["foundation_identity"]["foundation_identity_ref"] == "ledger:chat-loam:foundation_identity"
    assert rehydrated["identity_continuity_witness"]["canonical_ledger_id"] == "chat-loam"
    assert rehydrated["identity_continuity_witness"]["foundation_identity_available"] is True
    assert "foundation_identity.name" in rehydrated["identity_continuity_witness"]["basis"]
    assert rehydrated["ledger_rename_log"] == []

    fallback = service.get_ledger_library_boundary("chat-display-only")
    assert fallback["foundation_identity"]["name"] == "Display Only Ledger"
    assert fallback["foundation_identity"]["personality"] is None
    assert fallback["foundation_identity"]["purpose"] is None
    assert fallback["foundation_identity"]["source"] is None
    assert fallback["foundation_identity"]["rehydration_mode"] == "display_name_fallback"
    assert fallback["foundation_identity"]["constitution_present"] is False
    assert fallback["foundation_identity"]["foundation_identity_ref"] is None


def test_ledger_library_boundary_exposes_identity_continuity_witness_and_rename_log() -> None:
    db: dict[bytes, bytes] = {}
    db[LEDGER_REGISTRY_V1_KEY] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                "chat-demo": {
                    "ledger_id": "chat-demo",
                    "display_name": "LOAM",
                    "metadata": {
                        "founding_constitution": {
                            "name": "LOAM",
                            "purpose": "Hold governed memory.",
                            "source": "control_plane_operator",
                        },
                        "ledger_alias_history": ["ledger:loam-137to139", "loam-137to139", "chat-demo"],
                        "ledger_supersession_history": ["ledger:loam-137to139"],
                        "ledger_consolidation_history": [
                            {
                                "event": "ledger_split_consolidated",
                                "superseded_ledger_ids": ["loam-137to139"],
                                "timestamp": "2026-05-04T01:00:13.645664+00:00",
                                "reason": "rename_split_cleanup",
                                "operator_principal_id": "ops-admin",
                            }
                        ],
                    },
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-demo",
                    "updated_at": "2026-05-04T01:00:13.645664+00:00",
                }
            },
        }
    ).encode()

    service = LedgerService(db)
    boundary = service.get_ledger_library_boundary("chat-demo")

    assert boundary["foundation_identity"]["foundation_identity_ref"] == "ledger:chat-demo:foundation_identity"
    assert boundary["ledger_rename_log"] == ["ledger:loam-137to139", "loam-137to139"]
    witness = boundary["identity_continuity_witness"]
    assert witness["canonical_ledger_id"] == "chat-demo"
    assert witness["alias_history_count"] == 2
    assert witness["supersession_history_count"] == 1
    assert witness["consolidation_history_count"] == 1
    assert "ledger_alias_history" in witness["basis"]
    assert "ledger_supersession_history" in witness["basis"]
    assert "ledger_consolidation_history" in witness["basis"]
    assert boundary["latest_consolidation_event"]["event"] == "ledger_split_consolidated"
    assert boundary["latest_consolidation_event"]["reason"] == "rename_split_cleanup"
    assert boundary["latest_consolidation_event_id"] == "chat-demo:ledger_split_consolidated:2026-05-04T01:00:13.645664+00:00"
    assert boundary["continuity_checkpoint"]["ledger_version"] == 2
    assert boundary["continuity_checkpoint"]["checkpoint_updated_at"] == "2026-05-04T01:00:13.645664+00:00"
    assert boundary["async_consolidation_state"] == "settled_on_canonical_boundary"
    assert boundary["canonical_identity_post_consolidation"]["canonical_subject"] == "did:web:id.dualsubstrate.com:ledgers:chat-demo"
    assert boundary["canonical_identity_post_consolidation"]["continuity_survived"] is True
    assert boundary["latency_boundary"]["settlement_boundary_ns"] == "bounded_async_only"


def test_create_ledger_bootstraps_in_registry_mode_when_unknown_policy_denies(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
    client = _make_client()

    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    payload = {"namespace": "gate-alpha", "name": "Gate Alpha"}

    resp = client.post("/admin/ledgers", json=payload, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ledger"] == "gate-alpha"
    assert body["created"] is True


def test_list_ledgers_excludes_tenant_registry_key(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_client()
    client.app.state.db[TENANT_REGISTRY_V1_KEY] = b'{"version":1,"tenants":{"tenant:acme":{"tenant_id":"tenant:acme"}}}'

    resp = client.get("/admin/ledgers", headers={"x-admin-token": "test-admin-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert "__tenants_v1__" not in body.get("ledgers", [])


def test_list_ledgers_excludes_reserved_keys_from_legacy_registry(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_client()
    client.app.state.db[LEDGER_REGISTRY_KEY] = json.dumps(
        ["default", "__tenants_v1__", "__ledgers_v1__", "chat-acme"]
    ).encode()

    resp = client.get("/admin/ledgers", headers={"x-admin-token": "test-admin-token"})
    assert resp.status_code == 200
    body = resp.json()
    ledgers = body.get("ledgers", [])
    assert "chat-acme" in ledgers
    assert "__tenants_v1__" not in ledgers
    assert "__ledgers_v1__" not in ledgers


def test_get_ledger_founding_purpose_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = _make_client()
    client.app.state.db[LEDGER_REGISTRY_V1_KEY] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                "chat-loam": {
                    "ledger_id": "chat-loam",
                    "display_name": "LOAM Runtime",
                    "metadata": {
                        "founding_constitution": {
                            "name": "LOAM",
                            "purpose": "Hold governed memory.",
                            "source": "control_plane_operator",
                        }
                    },
                }
            },
        }
    ).encode()

    resp = client.get("/admin/ledgers/chat-loam/purpose", headers={"x-admin-token": "test-admin-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ledger_id"] == "chat-loam"
    assert body["purpose"] == "Hold governed memory."
    assert body["name"] == "LOAM"

    resp2 = client.get("/admin/ledgers/unknown/purpose", headers={"x-admin-token": "test-admin-token"})
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["ledger_id"] == "unknown"
    assert body2["purpose"] is None
