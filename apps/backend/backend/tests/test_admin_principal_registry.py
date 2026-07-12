from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import PRINCIPAL_REGISTRY_V1_KEY, public_router, router as admin_router
from backend.services.authority_events import AUTHORITY_STATE_V1_KEY


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(admin_router)
    app.include_router(public_router)
    return TestClient(app)


def _recent_iso() -> str:
    """Return a recent UTC ISO timestamp for freshness-dependent checks."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _register_issuer(
    client: TestClient,
    headers: dict[str, str],
    *,
    issuer: str,
    issuer_class: str,
    allowed_event_types: list[str],
    credential_ref: str | None = None,
    issuer_did: str | None = None,
    identity_anchor_ref: str | None = None,
    trust_basis: str | None = None,
    verification_state: str = "registry_only",
    policy_ref: str | None = None,
    policy_verdict: str | None = None,
    policy_scope: list[str] | None = None,
    verifier_policy_ref: str | None = None,
    vc_type: str | None = None,
    vc_id: str | None = None,
    vc_envelope: dict | None = None,
    credential_status_ref: str | None = None,
    credential_status_state: str = "active",
    vc_verification_method: str | None = None,
    vc_verification_status: str | None = None,
    vc_verification_checked_at: str | None = None,
    vc_verification_proof_ref: str | None = None,
) -> None:
    has_vc = bool(vc_type or vc_id or vc_envelope)
    recent = _recent_iso()
    resp = client.post(
        "/admin/issuer-authorities",
        json={
            "issuer": issuer,
            "issuer_class": issuer_class,
            "allowed_event_types": allowed_event_types,
            "evidence_requirement": "required",
            "credential_ref": credential_ref,
            "issuer_did": issuer_did,
            "identity_anchor_ref": identity_anchor_ref,
            "trust_basis": trust_basis,
            "verification_state": verification_state,
            "policy_ref": policy_ref,
            "policy_verdict": policy_verdict,
            "policy_scope": policy_scope or [],
            "verifier_policy_ref": verifier_policy_ref,
            "vc_type": vc_type,
            "vc_id": vc_id,
            "vc_envelope": vc_envelope,
            "credential_status_ref": credential_status_ref,
            "credential_status_state": credential_status_state,
            "vc_verification_method": vc_verification_method or ("manual_attestation" if has_vc else None),
            "vc_verification_status": vc_verification_status or ("verified" if has_vc else "unverified"),
            "vc_verification_checked_at": vc_verification_checked_at or (recent if has_vc else None),
            "vc_verification_proof_ref": vc_verification_proof_ref or (f"proof:{issuer}" if has_vc else None),
        },
        headers=headers,
    )
    assert resp.status_code == 200


def _register_live_issuer_checks(
    client: TestClient,
    headers: dict[str, str],
    *,
    issuer_did: str,
    credential_ref: str,
    identity_anchor_ref: str,
    credential_status_ref: str,
    resolver_ref: str = "resolver:untp-live",
) -> None:
    recent = _recent_iso()
    identity_resp = client.post(
        "/admin/live-identity-checks",
        json={
            "subject_ref": issuer_did,
            "subject_type": "issuer",
            "resolver_ref": resolver_ref,
            "resolution_status": "verified",
            "resolved_identity": issuer_did,
            "authority_binding_ref": credential_ref,
            "identity_anchor_ref": identity_anchor_ref,
            "checked_at": recent,
            "trust_root_ref": "trust-root:untp",
            "evidence_ref": "did-resolution:issuer",
        },
        headers=headers,
    )
    assert identity_resp.status_code == 200

    status_resp = client.post(
        "/admin/credential-status-checks",
        json={
            "credential_status_ref": credential_status_ref,
            "credential_id": credential_ref,
            "resolver_ref": resolver_ref,
            "status_state": "active",
            "checked_at": recent,
            "proof_ref": "status-proof:issuer",
            "trust_root_ref": "trust-root:untp",
        },
        headers=headers,
    )
    assert status_resp.status_code == 200


def test_principal_registry_create_read_disable_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    principal_did = "did:key:z6MkLifecycle"
    payload = {
        "principal_did": principal_did,
        "tenant_id": "tenant:demo",
        "display_name": "Lifecycle User",
        "actor_type": "organization",
        "status": "active",
        "key_references": [f"{principal_did}#k1", f"{principal_did}#k1", f"{principal_did}#k2"],
        "metadata": {"tier": "internal", "actor_type": "organization", "vc_status": "bound", "wallet_capable": 1},
    }

    create_1 = client.post("/admin/principals", json=payload, headers=headers)
    assert create_1.status_code == 200
    body_1 = create_1.json()
    assert body_1["status"] == "ok"
    assert body_1["created"] is True
    principal_1 = body_1["principal"]
    assert principal_1["principal_did"] == principal_did
    assert principal_1["tenant_id"] == "tenant:demo"
    assert principal_1["status"] == "active"
    assert principal_1["principal_key_refs"] == [f"{principal_did}#k1", f"{principal_did}#k2"]
    assert principal_1["key_references"] == [f"{principal_did}#k1", f"{principal_did}#k2"]
    assert principal_1["canonical_subject"] == principal_did
    assert principal_1["canonical_subject_source"] == "principal_did"
    assert principal_1["actor_type"] == "organisation"
    assert principal_1["metadata"]["actor_type"] == "organisation"
    assert principal_1["metadata"]["vc_status"] == "bound"
    assert principal_1["metadata"]["wallet_capable"] is True
    assert principal_1["metadata"]["probation_status"] == "probation"
    standing_view = principal_1.get("standing_view")
    assert isinstance(standing_view, dict)
    assert standing_view["trust_class"] == "T1"
    assert standing_view["posture_class"] == "P1"
    assert standing_view["probation_status"] == "probation"

    create_2 = client.post("/admin/principals", json=payload, headers=headers)
    assert create_2.status_code == 200
    body_2 = create_2.json()
    assert body_2["created"] is False
    assert body_2["updated"] is True

    list_resp = client.get("/admin/principals", headers=headers)
    assert list_resp.status_code == 200
    principals = list_resp.json().get("principals", [])
    by_did = {row.get("principal_did"): row for row in principals if isinstance(row, dict)}
    assert principal_did in by_did
    assert by_did[principal_did]["status"] == "active"
    assert by_did[principal_did]["tenant_id"] == "tenant:demo"

    get_resp = client.get(f"/admin/principals/{principal_did}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["principal"]["principal_did"] == principal_did

    disable_resp = client.post(
        f"/admin/principals/{principal_did}/disable",
        json={"reason": "compromised_key"},
        headers=headers,
    )
    assert disable_resp.status_code == 200
    disabled = disable_resp.json()["principal"]
    assert disabled["status"] == "disabled"
    assert disabled["disable_reason"] == "compromised_key"
    assert isinstance(disabled.get("disabled_at"), str) and disabled["disabled_at"]

    get_after_disable = client.get(f"/admin/principals/{principal_did}", headers=headers)
    assert get_after_disable.status_code == 200
    assert get_after_disable.json()["principal"]["status"] == "disabled"

    reactivate = client.post(
        f"/admin/principals/{principal_did}/status",
        json={"status": "active"},
        headers=headers,
    )
    assert reactivate.status_code == 200
    reactivated = reactivate.json()["principal"]
    assert reactivated["status"] == "active"
    assert reactivated["disabled_at"] is None
    assert reactivated["disable_reason"] is None

    raw_v1 = client.app.state.db.get(PRINCIPAL_REGISTRY_V1_KEY)
    assert raw_v1 is not None
    parsed = json.loads(raw_v1.decode() if isinstance(raw_v1, (bytes, bytearray)) else raw_v1)
    assert parsed.get("version") == 1
    assert principal_did in (parsed.get("principals") or {})
    persisted = parsed.get("principals", {}).get(principal_did)
    assert isinstance(persisted, dict)
    assert persisted.get("canonical_subject") == principal_did
    assert persisted.get("tenant_id") == "tenant:demo"


def test_principal_registry_defaults_unknown_tenant_and_rejects_invalid_actor_type(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkTenantDefault",
            "metadata": {"actor_type": "service"},
        },
        headers=headers,
    )
    assert create.status_code == 200
    principal = create.json()["principal"]
    assert principal["tenant_id"] == "tenant:unknown"
    assert principal["canonical_subject"] == "did:key:z6MkTenantDefault"

    bad = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkBadActor",
            "metadata": {"actor_type": "robot-overlord"},
        },
        headers=headers,
    )
    assert bad.status_code == 422


def test_principal_registry_derives_canonical_subject_from_normalized_model_binding(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkModelDerived",
            "tenant_id": "tenant:demo",
            "key_references": ["OpenRouter:Model:Anthropic/Claude-3.7-Sonnet"],
            "metadata": {"actor_type": "model", "wallet_capable": False},
        },
        headers=headers,
    )
    assert create.status_code == 200
    principal = create.json()["principal"]
    assert principal["principal_key_refs"] == ["openrouter:model:anthropic/claude-3.7-sonnet"]
    assert principal["key_references"] == ["openrouter:model:anthropic/claude-3.7-sonnet"]
    assert principal["canonical_subject"] == "openrouter:model:anthropic/claude-3.7-sonnet"
    assert principal["canonical_subject_source"] == "binding:openrouter:model"


def test_principal_registry_enforces_closed_binding_namespaces(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    accepted = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkClosedNs",
            "tenant_id": "tenant:demo",
            "status": "active",
            "principal_key_refs": [
                "service:url:https://OPS.EXAMPLE/v1/",
                "wallet:Portable:HolderA",
            ],
            "metadata": {"actor_type": "service"},
        },
        headers=headers,
    )
    assert accepted.status_code == 200
    principal = accepted.json()["principal"]
    assert principal["principal_key_refs"] == [
        "service:url:https://ops.example/v1",
        "wallet:portable:holdera",
    ]

    rejected = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkUnknownNs",
            "tenant_id": "tenant:demo",
            "status": "active",
            "principal_key_refs": ["custom:model:alpha"],
            "metadata": {"actor_type": "service"},
        },
        headers=headers,
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "unsupported principal_key_ref namespace"


def test_principal_registry_accepts_principal_key_refs_and_persists_legacy_alias(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkCanonicalRefs",
            "tenant_id": "tenant:demo",
            "principal_key_refs": ["Node:URL:https://Inference.EXAMPLE/v1/", "github:user:ExampleOrg"],
            "metadata": {"actor_type": "service"},
        },
        headers=headers,
    )
    assert create.status_code == 200
    principal = create.json()["principal"]
    assert principal["principal_key_refs"] == [
        "node:url:https://inference.example/v1",
        "github:user:exampleorg",
    ]
    assert principal["key_references"] == principal["principal_key_refs"]

    fetched = client.get("/admin/principals/did:key:z6MkCanonicalRefs", headers=headers)
    assert fetched.status_code == 200
    fetched_principal = fetched.json()["principal"]
    assert fetched_principal["principal_key_refs"] == principal["principal_key_refs"]
    assert fetched_principal["key_references"] == principal["principal_key_refs"]


def test_principal_registry_existing_post_merges_metadata_and_key_refs(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    initial = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkUpsertBackend",
            "tenant_id": "tenant:demo",
            "display_name": "Registry Model",
            "principal_key_refs": ["github:user:ExampleOrg"],
            "metadata": {"actor_type": "service", "email": "Ops@Example.com"},
        },
        headers=headers,
    )
    assert initial.status_code == 200
    first = initial.json()["principal"]
    assert first["canonical_subject"] == "github:user:exampleorg"
    assert first["metadata"]["email_normalized"] == "ops@example.com"

    updated = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkUpsertBackend",
            "principal_key_refs": ["OpenRouter:Model:Anthropic/Claude-3.7-Sonnet"],
            "metadata": {"phone": "+61 400 111 222", "vc_status": "verified"},
        },
        headers=headers,
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["created"] is False
    assert payload["updated"] is True
    principal = payload["principal"]
    assert principal["display_name"] == "Registry Model"
    assert principal["principal_key_refs"] == [
        "github:user:exampleorg",
        "openrouter:model:anthropic/claude-3.7-sonnet",
    ]
    assert principal["key_references"] == principal["principal_key_refs"]
    assert principal["canonical_subject"] == "openrouter:model:anthropic/claude-3.7-sonnet"
    assert principal["canonical_subject_source"] == "binding:openrouter:model"
    assert principal["metadata"]["email_normalized"] == "ops@example.com"
    assert principal["metadata"]["phone_normalized"] == "+61400111222"
    assert principal["metadata"]["vc_status"] == "verified"


def test_principal_registry_lookup_by_key_ref_and_contact(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create_active = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkLookupActive",
            "tenant_id": "tenant:alpha",
            "principal_key_refs": ["Node:URL:https://Inference.EXAMPLE/v1/"],
            "metadata": {"actor_type": "service", "email": "Ops@Example.com", "phone": "+61 400 111 222"},
        },
        headers=headers,
    )
    assert create_active.status_code == 200

    create_disabled = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkLookupDisabled",
            "tenant_id": "tenant:alpha",
            "principal_key_refs": ["github:user:DisabledCase"],
            "metadata": {"actor_type": "service", "email": "ops@example.com"},
        },
        headers=headers,
    )
    assert create_disabled.status_code == 200
    disable = client.post(
        "/admin/principals/did:key:z6MkLookupDisabled/disable",
        json={"reason": "test_disabled"},
        headers=headers,
    )
    assert disable.status_code == 200

    key_ref_lookup = client.get(
        "/admin/principals/lookup/by-key-ref",
        params={"principal_key_ref": "node:url:https://inference.example/v1", "tenant_id": "tenant:alpha"},
        headers=headers,
    )
    assert key_ref_lookup.status_code == 200
    assert key_ref_lookup.json()["principal"]["principal_did"] == "did:key:z6MkLookupActive"

    contact_lookup = client.get(
        "/admin/principals/lookup/by-contact",
        params={"email": "OPS@example.com", "phone": "+61 400 111 222", "tenant_id": "tenant:alpha"},
        headers=headers,
    )
    assert contact_lookup.status_code == 200
    principals = contact_lookup.json()["principals"]
    assert [row["principal_did"] for row in principals] == ["did:key:z6MkLookupActive"]

    contact_lookup_all_status = client.get(
        "/admin/principals/lookup/by-contact",
        params={"email": "ops@example.com", "tenant_id": "tenant:alpha", "status": ""},
        headers=headers,
    )
    assert contact_lookup_all_status.status_code == 200
    principal_dids = [row["principal_did"] for row in contact_lookup_all_status.json()["principals"]]
    assert principal_dids == ["did:key:z6MkLookupActive", "did:key:z6MkLookupDisabled"]

    missing_contact = client.get("/admin/principals/lookup/by-contact", headers=headers)
    assert missing_contact.status_code == 400


def test_principal_lookup_by_key_ref_returns_conflict_shape_for_duplicate_active_bindings(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY] = json.dumps(
        {
            "version": 1,
            "principals": {
                "did:key:z6MkConflictA": {
                    "principal_did": "did:key:z6MkConflictA",
                    "tenant_id": "tenant:alpha",
                    "principal_key_refs": ["github:user:duplicate"],
                    "canonical_subject": "github:user:duplicate:a",
                    "status": "active",
                },
                "did:key:z6MkConflictB": {
                    "principal_did": "did:key:z6MkConflictB",
                    "tenant_id": "tenant:alpha",
                    "principal_key_refs": ["github:user:duplicate"],
                    "canonical_subject": "github:user:duplicate:b",
                    "status": "active",
                },
            },
        }
    ).encode()

    resp = client.get(
        "/admin/principals/lookup/by-key-ref",
        params={"principal_key_ref": "github:user:duplicate", "tenant_id": "tenant:alpha"},
        headers=headers,
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["outcome"] == "conflict"
    assert detail["canonical_principal_key_ref"] == "github:user:duplicate"
    assert [row["principal_did"] for row in detail["conflicting_principals"]] == [
        "did:key:z6MkConflictA",
        "did:key:z6MkConflictB",
    ]


def test_principal_registry_bind_key_ref_endpoint_records_binding_event(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkBindEventA",
            "tenant_id": "tenant:alpha",
            "metadata": {"actor_type": "model"},
        },
        headers=headers,
    )
    assert create.status_code == 200

    bind = client.post(
        "/admin/principals/did:key:z6MkBindEventA/bind-key-ref",
        json={
            "principal_key_ref": "openrouter:model:anthropic/claude-3.7-sonnet",
            "tenant_id": "tenant:alpha",
            "issuer": "operator:review-board",
            "reason": "governed_activation",
            "evidence_refs": ["evidence:ticket:123"],
            "idempotency_key": "admin-bind-1",
        },
        headers=headers,
    )
    assert bind.status_code == 200
    event = bind.json()["binding_event"]
    assert event["issuer"] == "operator:review-board"
    assert event["reason"] == "governed_activation"
    assert event["evidence_refs"] == ["evidence:ticket:123"]
    assert event["idempotency_key"] == "admin-bind-1"

    listing = client.get(
        "/admin/principals/did:key:z6MkBindEventA/binding-events",
        headers=headers,
    )
    assert listing.status_code == 200
    assert [row["event_id"] for row in listing.json()["binding_events"]] == [event["event_id"]]


def test_principal_registry_list_supports_status_tenant_limit_and_offset(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    for principal_did, tenant_id in [
        ("did:key:z6MkListA", "tenant:alpha"),
        ("did:key:z6MkListB", "tenant:alpha"),
        ("did:key:z6MkListC", "tenant:beta"),
    ]:
        create = client.post(
            "/admin/principals",
            json={"principal_did": principal_did, "tenant_id": tenant_id, "metadata": {"actor_type": "service"}},
            headers=headers,
        )
        assert create.status_code == 200

    disable = client.post(
        "/admin/principals/did:key:z6MkListB/disable",
        json={"reason": "test_disabled"},
        headers=headers,
    )
    assert disable.status_code == 200

    active_alpha = client.get(
        "/admin/principals",
        params={"tenant_id": "tenant:alpha", "status": "active"},
        headers=headers,
    )
    assert active_alpha.status_code == 200
    body = active_alpha.json()
    assert body["count"] == 1
    assert body["total_count"] == 1
    assert [row["principal_did"] for row in body["principals"]] == ["did:key:z6MkListA"]

    paged_all = client.get(
        "/admin/principals",
        params={"limit": 1, "offset": 1},
        headers=headers,
    )
    assert paged_all.status_code == 200
    paged_body = paged_all.json()
    assert paged_body["count"] == 1
    assert paged_body["total_count"] == 3
    assert len(paged_body["principals"]) == 1


def test_principal_registry_bind_key_ref_endpoint_updates_canonical_subject(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkBindKeyRef",
            "tenant_id": "tenant:demo",
            "principal_key_refs": ["github:user:exampleorg"],
            "metadata": {"actor_type": "service"},
        },
        headers=headers,
    )
    assert create.status_code == 200
    assert create.json()["principal"]["canonical_subject"] == "github:user:exampleorg"

    bind = client.post(
        "/admin/principals/did:key:z6MkBindKeyRef/bind-key-ref",
        json={
            "principal_key_ref": "openrouter:model:anthropic/claude-3.7-sonnet",
            "tenant_id": "tenant:demo",
            "binding_metadata": {"binding_source": "operator"},
        },
        headers=headers,
    )
    assert bind.status_code == 200
    principal = bind.json()["principal"]
    assert principal["principal_key_refs"] == [
        "github:user:exampleorg",
        "openrouter:model:anthropic/claude-3.7-sonnet",
    ]
    assert principal["canonical_subject"] == "openrouter:model:anthropic/claude-3.7-sonnet"
    assert principal["canonical_subject_source"] == "binding:openrouter:model"
    assert principal["metadata"]["binding_source"] == "operator"


def test_principal_registry_link_github_endpoint_materializes_metadata(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkGithubLink",
            "tenant_id": "tenant:demo",
            "metadata": {"actor_type": "service", "email": "owner@example.com"},
        },
        headers=headers,
    )
    assert create.status_code == 200

    link = client.post(
        "/admin/principals/did:key:z6MkGithubLink/link-github",
        json={
            "github_user_id": "ExampleOrg",
            "github_login": "example-org",
            "github_email": "team@example.com",
        },
        headers=headers,
    )
    assert link.status_code == 200
    principal = link.json()["principal"]
    assert principal["principal_key_refs"] == ["github:user:exampleorg"]
    assert principal["key_references"] == ["github:user:exampleorg"]
    assert principal["canonical_subject"] == "github:user:exampleorg"
    assert principal["metadata"]["auth_provider"] == "github"
    assert principal["metadata"]["github_user_id"] == "ExampleOrg"
    assert principal["metadata"]["github_login"] == "example-org"
    assert principal["metadata"]["github_email"] == "team@example.com"
    assert principal["metadata"]["github_link_status"] == "linked"
    assert principal["metadata"]["email_normalized"] == "owner@example.com"


def test_principal_registry_human_prefers_principal_did_and_rejects_duplicate_binding(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    human = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkHumanCanonical",
            "tenant_id": "tenant:demo",
            "key_references": ["openrouter:model:anthropic/claude-3.7-sonnet"],
            "metadata": {"actor_type": "human", "wallet_capable": True},
        },
        headers=headers,
    )
    assert human.status_code == 200
    principal = human.json()["principal"]
    assert principal["canonical_subject"] == "did:key:z6MkHumanCanonical"
    assert principal["canonical_subject_source"] == "principal_did"

    duplicate = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkModelDuplicate",
            "tenant_id": "tenant:demo",
            "key_references": ["openrouter:model:anthropic/claude-3.7-sonnet"],
            "metadata": {"actor_type": "model", "wallet_capable": False},
        },
        headers=headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "principal_key_ref already bound: openrouter:model:anthropic/claude-3.7-sonnet"


def test_principal_registry_requires_admin_principal_in_registry_mode(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    resp = client.post(
        "/admin/principals",
        json={"principal_did": "did:key:z6MkDenied", "key_references": ["did:key:z6MkDenied#k1"]},
        headers={
            "x-admin-token": "test-admin-token",
            "x-principal-id": "alice",
            "x-principal-type": "user",
        },
    )
    assert resp.status_code == 403
    payload = resp.json()
    detail = payload.get("detail") if isinstance(payload, dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("reason") == "admin_principal_required"


def test_admin_subject_event_store_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/subject-events",
        json={
            "event_id": "subevt:test-1",
            "event_type": "subject_reset_requested",
            "issuer": "operator:test",
            "principal_did": "did:key:z6MkSubjectA",
            "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
            "prior_authority_subject_id": "subject:openrouter:model:anthropic/claude-3.5-sonnet",
            "resulting_authority_subject_id": "subject:openrouter:model:anthropic/claude-3.7-sonnet",
            "evidence_refs": ["ticket:123"],
            "standing_carryover": "probation",
            "credential_carryover": "review_required",
        },
        headers=headers,
    )
    assert create.status_code == 200
    event = create.json().get("event")
    assert isinstance(event, dict)
    assert event.get("event_id") == "subevt:test-1"

    get_resp = client.get("/admin/subject-events/subevt:test-1", headers=headers)
    assert get_resp.status_code == 200
    fetched = get_resp.json().get("event")
    assert isinstance(fetched, dict)
    assert fetched.get("principal_did") == "did:key:z6MkSubjectA"

    list_resp = client.get(
        "/admin/subject-events",
        params={"principal_did": "did:key:z6MkSubjectA"},
        headers=headers,
    )
    assert list_resp.status_code == 200
    rows = list_resp.json().get("events")
    assert isinstance(rows, list)
    assert any(row.get("event_id") == "subevt:test-1" for row in rows if isinstance(row, dict))

    subject_resp = client.get(
        "/admin/authority-subjects/subject:openrouter:model:anthropic/claude-3.7-sonnet",
        headers=headers,
    )
    assert subject_resp.status_code == 200
    subject = subject_resp.json().get("subject")
    assert isinstance(subject, dict)
    assert subject.get("last_event_id") == "subevt:test-1"
    assert subject.get("prior_authority_subject_id") == "subject:openrouter:model:anthropic/claude-3.5-sonnet"


def test_admin_subject_event_store_rejects_duplicate_event_id(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    payload = {
        "event_id": "subevt:test-dup",
        "event_type": "subject_reset_requested",
        "issuer": "operator:test",
        "resulting_authority_subject_id": "subject:did:key:z6MkDup",
    }
    first = client.post("/admin/subject-events", json=payload, headers=headers)
    assert first.status_code == 200
    second = client.post("/admin/subject-events", json=payload, headers=headers)
    assert second.status_code == 409


def test_admin_authority_event_store_materializes_and_replays(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction", "decay"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
        identity_anchor_ref="anchor:eq9",
        trust_basis="local_registry",
        verification_state="anchored",
        vc_type="VerifiableCredential",
        vc_id="vc:eq9",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:eq9",
    )
    principal_create = client.post(
        "/admin/principals",
        json={
            "principal_did": "did:key:z6MkStandingBackend",
            "tenant_id": "tenant:demo",
            "key_references": ["openrouter:model:anthropic/claude-3.7-sonnet"],
            "metadata": {"actor_type": "model", "wallet_capable": False},
        },
        headers=headers,
    )
    assert principal_create.status_code == 200

    subject_event = client.post(
        "/admin/subject-events",
        json={
            "event_id": "subevt:standing-1",
            "event_type": "subject_reset_requested",
            "issuer": "operator:test",
            "principal_did": "did:key:z6MkStandingBackend",
            "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
            "resulting_authority_subject_id": "subject:openrouter:model:anthropic/claude-3.7-sonnet",
        },
        headers=headers,
    )
    assert subject_event.status_code == 200
    principal_after_subject = client.get("/admin/principals/did:key:z6MkStandingBackend", headers=headers)
    assert principal_after_subject.status_code == 200
    subject_view = principal_after_subject.json()["principal"]["standing_view"]
    assert subject_view["authority_subject_id"] == "subject:openrouter:model:anthropic/claude-3.7-sonnet"
    assert subject_view["subject_transition_event_ref"] == "subevt:standing-1"
    assert subject_view["canonical_subject"] == "openrouter:model:anthropic/claude-3.7-sonnet"
    assert subject_view["principal_did"] == "did:key:z6MkStandingBackend"

    sanction = client.post(
        "/admin/authority-events",
        json={
            "event_id": "aevt:test-1",
            "authority_subject_id": "subject:openrouter:model:anthropic/claude-3.7-sonnet",
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-1"],
            "idempotency_key": "evt-001",
            "principal_did": "did:key:z6MkStandingBackend",
            "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
            "credential_ref": "cred:demo-1",
            "standing_envelope_ref": "env:demo-1",
            "subject_transition_event_ref": "subevt:standing-1",
        },
        headers=headers,
    )
    assert sanction.status_code == 200
    sanction_body = sanction.json()
    assert sanction_body["status"] == "ok"
    event = sanction_body["event"]
    standing = sanction_body["standing"]
    assert event["event_id"] == "aevt:test-1"
    assert isinstance(event.get("evidence_manifest_ref"), str) and event["evidence_manifest_ref"]
    assert isinstance(event.get("evidence_manifest_hash"), str) and event["evidence_manifest_hash"]
    assert standing["trust_class"] == "T0"
    assert standing["posture_class"] == "P0"
    assert standing["standing_envelope_ref"] == "env:demo-1"
    assert "eq_blocked:eq9_telos" in standing["active_sanctions"]
    assert standing["current_validation_status"] == "active"
    principal_after_sanction = client.get("/admin/principals/did:key:z6MkStandingBackend", headers=headers)
    assert principal_after_sanction.status_code == 200
    sanction_view = principal_after_sanction.json()["principal"]["standing_view"]
    assert sanction_view["authority_subject_id"] == "subject:openrouter:model:anthropic/claude-3.7-sonnet"
    assert sanction_view["trust_class"] == "T0"
    assert sanction_view["posture_class"] == "P0"
    assert sanction_view["credential_ref"] == "cred:demo-1"
    assert sanction_view["standing_envelope_ref"] == "env:demo-1"
    assert sanction_view["current_validation_status"] == "active"
    assert "eq_blocked:eq9_telos" in sanction_view["active_sanctions"]

    listed = client.get(
        "/admin/authority-events",
        params={"authority_subject_id": "subject:openrouter:model:anthropic/claude-3.7-sonnet"},
        headers=headers,
    )
    assert listed.status_code == 200
    rows = listed.json()["events"]
    assert any(row.get("idempotency_key") == "evt-001" for row in rows if isinstance(row, dict))

    state_resp = client.get(
        "/admin/authority-state/subject:openrouter:model:anthropic/claude-3.7-sonnet",
        headers=headers,
    )
    assert state_resp.status_code == 200
    state = state_resp.json()["subject"]
    assert state["last_event_type"] == "sanction"
    assert state["subject_transition_event_ref"] == "subevt:standing-1"

    principal_registry = json.loads(client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY].decode())
    principal_registry["principals"]["did:key:z6MkStandingBackend"]["standing_view"]["trust_class"] = "BROKEN"
    client.app.state.db[PRINCIPAL_REGISTRY_V1_KEY] = json.dumps(principal_registry).encode()
    client.app.state.db[AUTHORITY_STATE_V1_KEY] = json.dumps(
        {"version": 1, "subjects": {"subject:openrouter:model:anthropic/claude-3.7-sonnet": {"authority_subject_id": "subject:openrouter:model:anthropic/claude-3.7-sonnet", "trust_class": "BROKEN"}}}
    ).encode()
    replay = client.post(
        "/admin/authority-events/replay",
        params={"authority_subject_id": "subject:openrouter:model:anthropic/claude-3.7-sonnet"},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["replayed_subjects"] == ["subject:openrouter:model:anthropic/claude-3.7-sonnet"]

    repaired_state_resp = client.get(
        "/admin/authority-state/subject:openrouter:model:anthropic/claude-3.7-sonnet",
        headers=headers,
    )
    assert repaired_state_resp.status_code == 200
    repaired_state = repaired_state_resp.json()["subject"]
    assert repaired_state["trust_class"] == "T0"
    assert repaired_state["last_event_id"] == "aevt:test-1"
    assert repaired_state["current_validation_status"] == "active"
    repaired_principal_resp = client.get("/admin/principals/did:key:z6MkStandingBackend", headers=headers)
    assert repaired_principal_resp.status_code == 200
    repaired_principal_view = repaired_principal_resp.json()["principal"]["standing_view"]
    assert repaired_principal_view["trust_class"] == "T0"
    assert repaired_principal_view["authority_subject_id"] == "subject:openrouter:model:anthropic/claude-3.7-sonnet"
    assert repaired_principal_view["last_event_id"] == "aevt:test-1"


def test_admin_unified_authority_surface_materializes_subject_and_standing_timeline(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    _register_issuer(
        client,
        headers,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction", "repair", "probation", "decay", "trust_adjustment"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
    )

    subject = client.post(
        "/admin/subject-events",
        json={
            "event_type": "binding_succession",
            "issuer": "operator:test",
            "principal_did": "did:key:z6MkUnifiedAuthority",
            "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
            "prior_authority_subject_id": "subject:openrouter:model:anthropic/claude-3.5-sonnet",
            "resulting_authority_subject_id": "subject:openrouter:model:anthropic/claude-3.7-sonnet",
            "evidence_refs": ["evidence:subject:1"],
        },
        headers=headers,
    )
    assert subject.status_code == 200
    subject_event_id = subject.json()["event"]["event_id"]

    standing = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:openrouter:model:anthropic/claude-3.7-sonnet",
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0", "probation_status": "probation"},
            "evidence_refs": ["evidence:standing:1"],
            "idempotency_key": "unified-authority-1",
            "principal_did": "did:key:z6MkUnifiedAuthority",
            "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
            "subject_transition_event_ref": subject_event_id,
            "credential_ref": "cred:demo-1",
            "standing_envelope_ref": "env:demo-1",
        },
        headers=headers,
    )
    assert standing.status_code == 200

    unified = client.get(
        "/admin/authority-unified/subject:openrouter:model:anthropic/claude-3.7-sonnet",
        headers=headers,
    )
    assert unified.status_code == 200
    body = unified.json()
    diagnostics = body["diagnostics"]
    assert diagnostics["subject_event_count"] == 1
    assert diagnostics["authority_event_count"] == 1
    assert diagnostics["timeline_count"] == 2
    assert diagnostics["current_validation_status"] == "active"
    assert diagnostics["materialized_from_backend_replay"] is True
    current_subject = body["current_subject"]
    current_standing = body["current_standing"]
    assert current_subject["authority_subject_id"] == "subject:openrouter:model:anthropic/claude-3.7-sonnet"
    assert current_standing["authority_subject_id"] == "subject:openrouter:model:anthropic/claude-3.7-sonnet"
    assert current_standing["subject_transition_event_ref"] == subject_event_id
    timeline = body["timeline"]
    assert [row["family"] for row in timeline] == ["subject", "authority"]
    assert timeline[1]["subject_transition_event_ref"] == subject_event_id


def test_admin_authority_event_store_enforces_idempotency_and_issuer_policy(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction", "repair"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
        identity_anchor_ref="anchor:eq9",
        trust_basis="local_registry",
        verification_state="anchored",
        vc_type="VerifiableCredential",
        vc_id="vc:eq9",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:eq9",
    )
    _register_issuer(
        client,
        headers,
        issuer="advisory:model",
        issuer_class="advisory_model_evaluator",
        allowed_event_types=["sanction", "repair"],
        issuer_did="did:key:z6MkAdvisory",
        identity_anchor_ref="anchor:advisory",
        trust_basis="local_registry",
        verification_state="anchored",
        vc_type="VerifiableCredential",
        vc_id="vc:advisory",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:advisory",
    )
    authority_subject_id = "subject:did:key:z6MkStandingPolicy"
    payload = {
        "authority_subject_id": authority_subject_id,
        "event_type": "sanction",
        "issuer": "deterministic:eq9",
        "reason_code": "eq_blocked:eq9_telos",
        "evidence_refs": ["coord:WX-2"],
        "idempotency_key": "evt-001",
    }

    first = client.post("/admin/authority-events", json=payload, headers=headers)
    assert first.status_code == 200

    duplicate = client.post("/admin/authority-events", json=payload, headers=headers)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "authority_event already recorded: deterministic:eq9:evt-001"

    missing_evidence = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "repair",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "idempotency_key": "evt-002",
        },
        headers=headers,
    )
    assert missing_evidence.status_code == 422

    advisory_repair = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "repair",
            "issuer": "advisory:model",
            "reason_code": "eq_blocked:eq9_telos",
            "evidence_refs": ["coord:WX-3"],
            "idempotency_key": "evt-003",
        },
        headers=headers,
    )
    assert advisory_repair.status_code == 422

    self_adjustment = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "trust_adjustment",
            "issuer": "self:model",
            "reason_code": "self_upgrade",
            "evidence_refs": ["coord:WX-4"],
            "idempotency_key": "evt-004",
        },
        headers=headers,
    )
    assert self_adjustment.status_code == 422

    unregistered = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "repair",
            "issuer": "operator:unknown",
            "reason_code": "manual_repair",
            "evidence_refs": ["coord:WX-5"],
            "idempotency_key": "evt-005",
        },
        headers=headers,
    )
    assert unregistered.status_code == 422


def test_admin_live_identity_and_status_checks_gate_high_impact_events(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="review:live",
        issuer_class="human_review_issuer",
        allowed_event_types=["sanction", "repair"],
        credential_ref="cred:review-live",
        issuer_did="did:web:review-live.example",
        identity_anchor_ref="anchor:review-live",
        trust_basis="local_registry",
        verification_state="verified",
        vc_type="VerifiableCredential",
        vc_id="vc:review-live",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:review-live",
    )
    authority_subject_id = "subject:did:key:z6MkLiveChecks"

    sanction = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "sanction",
            "issuer": "review:live",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-9"],
            "idempotency_key": "evt-live-001",
        },
        headers=headers,
    )
    assert sanction.status_code == 200

    missing_live_checks = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "repair",
            "issuer": "review:live",
            "reason_code": "eq_blocked:eq9_telos",
            "evidence_refs": ["coord:WX-10"],
            "idempotency_key": "evt-live-002",
        },
        headers=headers,
    )
    assert missing_live_checks.status_code == 422
    assert "live identity resolution is missing" in str(missing_live_checks.json().get("detail"))

    _register_live_issuer_checks(
        client,
        headers,
        issuer_did="did:web:review-live.example",
        credential_ref="cred:review-live",
        identity_anchor_ref="anchor:review-live",
        credential_status_ref="status:review-live",
    )

    repair = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "repair",
            "issuer": "review:live",
            "reason_code": "eq_blocked:eq9_telos",
            "evidence_refs": ["coord:WX-11"],
            "idempotency_key": "evt-live-003",
        },
        headers=headers,
    )
    assert repair.status_code == 200
    body = repair.json()
    assert body["event"]["event_type"] == "repair"
    assert body["standing"]["current_validation_status"] == "active"


def test_admin_live_identity_and_status_check_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create_identity = client.post(
        "/admin/live-identity-checks",
        json={
            "subject_ref": "did:web:issuer.example",
            "subject_type": "issuer",
            "resolver_ref": "resolver:untp-live",
            "resolution_status": "verified",
            "resolved_identity": "did:web:issuer.example",
            "authority_binding_ref": "cred:issuer-example",
            "identity_anchor_ref": "anchor:issuer-example",
            "checked_at": "2026-03-15T00:00:00Z",
            "trust_root_ref": "trust-root:untp",
            "evidence_ref": "did-resolution:issuer-example",
        },
        headers=headers,
    )
    assert create_identity.status_code == 200

    create_status = client.post(
        "/admin/credential-status-checks",
        json={
            "credential_status_ref": "status:issuer-example",
            "credential_id": "cred:issuer-example",
            "resolver_ref": "resolver:untp-live",
            "status_state": "active",
            "checked_at": "2026-03-15T00:00:00Z",
            "proof_ref": "status-proof:issuer-example",
            "trust_root_ref": "trust-root:untp",
            "issuer": "issuer:example",
        },
        headers=headers,
    )
    assert create_status.status_code == 200

    list_identity = client.get("/admin/live-identity-checks", headers=headers)
    assert list_identity.status_code == 200
    identity_rows = list_identity.json().get("checks", [])
    assert any(row.get("subject_ref") == "did:web:issuer.example" for row in identity_rows if isinstance(row, dict))

    get_identity = client.get("/admin/live-identity-checks/did:web:issuer.example", headers=headers)
    assert get_identity.status_code == 200
    assert get_identity.json()["check"]["identity_anchor_ref"] == "anchor:issuer-example"

    list_status = client.get("/admin/credential-status-checks", headers=headers)
    assert list_status.status_code == 200
    status_rows = list_status.json().get("checks", [])
    assert any(row.get("credential_status_ref") == "status:issuer-example" for row in status_rows if isinstance(row, dict))

    get_status = client.get("/admin/credential-status-checks/status:issuer-example", headers=headers)
    assert get_status.status_code == 200
    assert get_status.json()["check"]["status_state"] == "active"


def test_admin_authority_event_repair_clears_active_sanction(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="deterministic:eq9",
        issuer_class="human_review_issuer",
        allowed_event_types=["sanction", "repair"],
        credential_ref="cred:issuer:eq9-review",
        issuer_did="did:web:review.example",
        identity_anchor_ref="anchor:review-board",
        trust_basis="local_registry",
        verification_state="verified",
        vc_type="VerifiableCredential",
        vc_id="vc:review-board",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:review-board",
    )
    _register_live_issuer_checks(
        client,
        headers,
        issuer_did="did:web:review.example",
        credential_ref="cred:issuer:eq9-review",
        identity_anchor_ref="anchor:review-board",
        credential_status_ref="status:review-board",
    )
    authority_subject_id = "subject:did:key:z6MkStandingRepair"

    sanction = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-5"],
            "idempotency_key": "evt-010",
        },
        headers=headers,
    )
    assert sanction.status_code == 200

    repair = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": authority_subject_id,
            "event_type": "repair",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T1", "posture_class": "P1", "probation_status": "cleared"},
            "evidence_refs": ["coord:WX-6"],
            "idempotency_key": "evt-011",
        },
        headers=headers,
    )
    assert repair.status_code == 200
    repaired = repair.json()["standing"]
    assert repaired["trust_class"] == "T1"
    assert repaired["posture_class"] == "P1"
    assert repaired["probation_status"] == "cleared"
    assert "eq_blocked:eq9_telos" not in repaired["active_sanctions"]


def test_admin_issuer_authority_registry_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    upsert = client.post(
        "/admin/issuer-authorities",
        json={
            "issuer": "operator:review-board",
            "issuer_class": "human_review_issuer",
            "allowed_event_types": ["sanction", "repair", "trust_adjustment"],
            "credential_ref": "cred:issuer:review-board",
            "issuer_did": "did:web:review-board.example",
            "identity_anchor_ref": "anchor:review-board",
            "trust_basis": "untp_dia",
            "verification_state": "verified",
            "policy_ref": "policy:issuer:review-board:v1",
            "policy_verdict": "allow",
            "policy_scope": ["identity.assertion", "trust.anchor.publish"],
            "verifier_policy_ref": "https://id.example/.well-known/trust-anchor.json",
            "vc_type": "VerifiableCredential",
            "vc_id": "vc:review-board",
            "vc_envelope": {"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
            "credential_status_ref": "status:review-board",
            "vc_verification_method": "did_document_check",
            "vc_verification_proof_ref": "https://id.example/.well-known/did.json",
            "notes": "review-board authority",
        },
        headers=headers,
    )
    assert upsert.status_code == 200
    record = upsert.json()["issuer"]
    assert record["issuer"] == "operator:review-board"
    assert record["issuer_class"] == "human_review_issuer"

    listed = client.get("/admin/issuer-authorities", headers=headers)
    assert listed.status_code == 200
    issuers = listed.json()["issuers"]
    assert any(row.get("issuer") == "operator:review-board" for row in issuers if isinstance(row, dict))

    fetched = client.get("/admin/issuer-authorities/operator:review-board", headers=headers)
    assert fetched.status_code == 200
    fetched_record = fetched.json()["issuer"]
    assert fetched_record["credential_ref"] == "cred:issuer:review-board"
    assert fetched_record["identity_anchor_ref"] == "anchor:review-board"
    assert fetched_record["verification_state"] == "verified"
    assert fetched_record["policy_ref"] == "policy:issuer:review-board:v1"
    assert fetched_record["policy_verdict"] == "allow"
    assert fetched_record["policy_scope"] == ["identity.assertion", "trust.anchor.publish"]
    assert fetched_record["verifier_policy_ref"] == "https://id.example/.well-known/trust-anchor.json"
    assert fetched_record["vc_id"] == "vc:review-board"
    assert fetched_record["credential_status_ref"] == "status:review-board"
    assert fetched_record["vc_verification_method"] == "did_document_check"
    assert fetched_record["vc_verification_proof_ref"] == "https://id.example/.well-known/did.json"


def test_public_trust_anchor_documents_publish_fresh_status(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://id.example")
    monkeypatch.setenv("TRUST_ANCHOR_ISSUER_DID", "did:web:id.example")
    monkeypatch.setenv("PUBLIC_STATUS_MAX_AGE_SECONDS", "31536000")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    status_upsert = client.post(
        "/admin/credential-status-checks",
        json={
            "credential_status_ref": "status:review-board",
            "credential_id": "vc:review-board",
            "resolver_ref": "resolver:untp-live",
            "status_state": "active",
            "checked_at": "2026-04-09T00:00:00Z",
            "proof_ref": "status-proof:review-board",
            "trust_root_ref": "trust-root:untp",
            "issuer": "operator:review-board",
        },
        headers=headers,
    )
    assert status_upsert.status_code == 200

    issuer_upsert = client.post(
        "/admin/issuer-authorities",
        json={
            "issuer": "operator:review-board",
            "issuer_class": "human_review_issuer",
            "allowed_event_types": ["sanction", "repair", "trust_anchor"],
            "credential_ref": "cred:issuer:review-board",
            "issuer_did": "did:web:id.example",
            "identity_anchor_ref": "https://id.example/.well-known/did.json",
            "trust_basis": "untp_dia",
            "verification_state": "verified",
            "policy_ref": "https://id.example/.well-known/trust-anchor.json#issuer-policy",
            "policy_verdict": "allow",
            "policy_scope": ["identity.assertion", "trust.anchor.publish"],
            "verifier_policy_ref": "https://id.example/.well-known/trust-anchor.json",
            "vc_type": "VerifiableCredential",
            "vc_id": "vc:review-board",
            "vc_envelope": {"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
            "credential_status_ref": "status:review-board",
            "credential_status_state": "active",
            "credential_status_checked_at": "2026-04-09T00:00:00Z",
            "vc_verification_method": "did_document_check",
            "vc_verification_status": "verified",
            "vc_verification_checked_at": "2026-04-09T00:00:00Z",
            "vc_verification_proof_ref": "https://id.example/.well-known/did.json",
        },
        headers=headers,
    )
    assert issuer_upsert.status_code == 200

    authority_resp = client.get("/public/trust-anchor/issuer-authority")
    assert authority_resp.status_code == 200
    authority = authority_resp.json()
    assert authority["id"] == "https://id.example/.well-known/issuer-authority.json"
    assert authority["credential_family"] == "authority"
    assert authority["status_discovery"]["credential_status_ref"] == "https://id.example/api/trust-anchor/credential-status/status:review-board"

    authority_status_resp = client.get("/public/trust-anchor/issuer-authority-status")
    assert authority_status_resp.status_code == 200
    authority_status = authority_status_resp.json()
    assert authority_status["credential_family"] == "status"
    assert authority_status["freshness"]["checked_at"] == "2026-04-09T00:00:00Z"
    assert authority_status["freshness"]["is_fresh"] is True
    assert authority_status["invalidation"]["is_invalidated"] is False

    bundle_resp = client.get("/public/trust-anchor/bundle")
    assert bundle_resp.status_code == 200
    bundle = bundle_resp.json()
    assert bundle["publication_intent"]["current_publication_state"] == "minimum_live"
    assert bundle["service_endpoints"]["credential_status_object"] == "https://id.example/api/trust-anchor/credential-status/status:review-board"

    status_resp = client.get("/public/status/status:review-board")
    assert status_resp.status_code == 200
    status_doc = status_resp.json()
    assert status_doc["type"] == "DssCredentialStatus"
    assert status_doc["status"]["current"] == "active"
    assert status_doc["freshness"]["checked_at"] == "2026-04-09T00:00:00Z"
    assert status_doc["freshness"]["is_fresh"] is True
    assert status_doc["invalidation"]["reasons"] == []


def test_public_object_lifecycle_routes_distinguish_current_superseded_and_revoked(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    current = client.post(
        "/admin/public-objects",
        json={
            "public_object_id": "https://id.example/o/claim/2026/obj-v2",
            "object_kind": "claim",
            "object_id": "obj-123",
            "subject_id": "did:web:id.example:principals:p_123",
            "issuer_id": "did:web:id.example",
            "content_digest": "sha256:v2",
            "coord_ref": "chat-demo:WX-secret-v2",
            "status_ref": "https://id.example/o/claim/2026/obj-v2/status",
            "previous_version_id": "https://id.example/o/claim/2026/obj-v1",
            "lifecycle_state": "current",
            "shareability": "share-ready",
        },
        headers=headers,
    )
    assert current.status_code == 200

    superseded = client.post(
        "/admin/public-objects",
        json={
            "public_object_id": "https://id.example/o/claim/2026/obj-v1",
            "object_kind": "claim",
            "object_id": "obj-122",
            "subject_id": "did:web:id.example:principals:p_123",
            "issuer_id": "did:web:id.example",
            "content_digest": "sha256:v1",
            "coord_ref": "chat-demo:WX-secret-v1",
            "status_ref": "https://id.example/o/claim/2026/obj-v1/status",
            "superseded_by": "https://id.example/o/claim/2026/obj-v2",
            "lifecycle_state": "superseded",
            "shareability": "share-ready",
        },
        headers=headers,
    )
    assert superseded.status_code == 200

    revoked = client.post(
        "/admin/public-objects",
        json={
            "public_object_id": "https://id.example/o/claim/2026/obj-v0",
            "object_kind": "claim",
            "object_id": "obj-121",
            "subject_id": "did:web:id.example:principals:p_123",
            "issuer_id": "did:web:id.example",
            "content_digest": "sha256:v0",
            "coord_ref": "chat-demo:WX-secret-v0",
            "status_ref": "https://id.example/o/claim/2026/obj-v0/status",
            "lifecycle_state": "revoked",
            "invalidation_reason": "issuer_revoked",
            "revoked_at": "2026-04-09T00:00:00Z",
            "shareability": "share-ready",
        },
        headers=headers,
    )
    assert revoked.status_code == 200

    current_doc = client.get("/public/objects/claim/obj-123")
    assert current_doc.status_code == 200
    current_body = current_doc.json()
    assert current_body["dereference"]["outcome"] == "current"
    assert current_body["preferred_reference"]["value"] == "https://id.example/o/claim/2026/obj-v2"
    assert current_body["coord_ref_withheld"] is True

    superseded_doc = client.get("/public/objects/claim/obj-122")
    assert superseded_doc.status_code == 200
    superseded_body = superseded_doc.json()
    assert superseded_body["dereference"]["outcome"] == "superseded"
    assert superseded_body["lifecycle"]["current_public_object_id"] == "https://id.example/o/claim/2026/obj-v2"
    assert superseded_body["shareability"] == "fallback-only"

    superseded_status = client.get("/public/objects/claim/obj-122/status")
    assert superseded_status.status_code == 200
    assert superseded_status.json()["dereference"]["outcome"] == "superseded"

    revoked_doc = client.get("/public/objects/claim/obj-121")
    assert revoked_doc.status_code == 403
    revoked_detail = revoked_doc.json()["detail"]
    assert revoked_detail["outcome"] == "not_authorized"
    assert revoked_detail["lifecycle_state"] == "revoked"

    revoked_status = client.get("/public/objects/claim/obj-121/status")
    assert revoked_status.status_code == 200
    assert revoked_status.json()["dereference"]["outcome"] == "revoked"


def test_admin_evidence_manifest_lifecycle_and_revocation_replay(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="operator:review-board",
        issuer_class="human_review_issuer",
        allowed_event_types=["repair"],
        credential_ref="cred:issuer:review-board",
        issuer_did="did:web:review-board.example",
        identity_anchor_ref="anchor:review-board",
        trust_basis="untp_dia",
        verification_state="verified",
        vc_type="VerifiableCredential",
        vc_id="vc:review-board",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:review-board",
    )
    _register_live_issuer_checks(
        client,
        headers,
        issuer_did="did:web:review-board.example",
        credential_ref="cred:issuer:review-board",
        identity_anchor_ref="anchor:review-board",
        credential_status_ref="status:review-board",
    )
    create = client.post(
        "/admin/evidence-manifests",
        json={
            "issuer": "operator:review-board",
            "authority_subject_id": "subject:did:key:z6MkReplay",
            "evidence_refs": ["coord:WX-r1", "coord:WX-r2"],
            "package_type": "signed_manifest",
            "signature_ref": "sig:review-board:r1",
            "signature_status": "verified",
        },
        headers=headers,
    )
    assert create.status_code == 200
    manifest = create.json()["manifest"]
    assert manifest["status"] == "active"
    assert isinstance(manifest.get("manifest_hash"), str) and manifest["manifest_hash"]
    assert manifest["signature_status"] == "verified"

    listed = client.get("/admin/evidence-manifests", headers=headers)
    assert listed.status_code == 200
    manifests = listed.json()["manifests"]
    assert any(row.get("manifest_ref") == manifest["manifest_ref"] for row in manifests if isinstance(row, dict))

    sanction = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkReplay",
            "event_type": "repair",
            "issuer": "operator:review-board",
            "reason_code": "manual_repair",
            "delta": {"trust_class": "T1", "posture_class": "P1"},
            "evidence_refs": ["coord:WX-r1", "coord:WX-r2"],
            "idempotency_key": "evt-r1",
        },
        headers=headers,
    )
    assert sanction.status_code == 200
    event = sanction.json()["event"]
    assert event["evidence_manifest_ref"] == manifest["manifest_ref"]

    revoke_manifest = client.post(
        "/admin/evidence-manifests",
        json={
            "issuer": "operator:review-board",
            "authority_subject_id": "subject:did:key:z6MkReplay",
            "manifest_ref": manifest["manifest_ref"],
            "evidence_refs": ["coord:WX-r1", "coord:WX-r2"],
            "status": "revoked",
        },
        headers=headers,
    )
    assert revoke_manifest.status_code == 200

    replay = client.post(
        "/admin/authority-events/replay",
        params={"authority_subject_id": "subject:did:key:z6MkReplay"},
        headers=headers,
    )
    assert replay.status_code == 200

    state_resp = client.get("/admin/authority-state/subject:did:key:z6MkReplay", headers=headers)
    assert state_resp.status_code == 200
    state = state_resp.json()["subject"]
    assert state["current_validation_status"] == "invalidated"
    assert "evidence_manifest_revoked" in (state.get("current_invalidation_reasons") or [])


def test_admin_replay_marks_revoked_issuer_without_erasing_history(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
        identity_anchor_ref="anchor:eq9",
        trust_basis="local_registry",
        verification_state="anchored",
    )
    event_resp = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkIssuerReplay",
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-r3"],
            "idempotency_key": "evt-r2",
        },
        headers=headers,
    )
    assert event_resp.status_code == 200
    event = event_resp.json()["event"]

    revoke_issuer = client.post(
        "/admin/issuer-authorities",
        json={
            "issuer": "deterministic:eq9",
            "issuer_class": "deterministic_system",
            "allowed_event_types": ["sanction"],
            "credential_ref": "cred:issuer:eq9",
            "issuer_did": "did:web:eq9.example",
            "identity_anchor_ref": "anchor:eq9",
            "trust_basis": "local_registry",
            "verification_state": "anchored",
            "status": "revoked",
        },
        headers=headers,
    )
    assert revoke_issuer.status_code == 200

    replay = client.post(
        "/admin/authority-events/replay",
        params={"authority_subject_id": "subject:did:key:z6MkIssuerReplay"},
        headers=headers,
    )
    assert replay.status_code == 200

    fetched_event = client.get(f"/admin/authority-events/{event['event_id']}", headers=headers)
    assert fetched_event.status_code == 200
    assert fetched_event.json()["event"]["event_id"] == event["event_id"]

    state_resp = client.get("/admin/authority-state/subject:did:key:z6MkIssuerReplay", headers=headers)
    assert state_resp.status_code == 200
    state = state_resp.json()["subject"]
    assert state["trust_class"] == "T0"
    assert state["current_validation_status"] == "invalidated"
    assert "issuer_revoked" in (state.get("current_invalidation_reasons") or [])


def test_admin_replay_marks_unsigned_signed_manifest_invalid(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
        identity_anchor_ref="anchor:eq9",
        trust_basis="local_registry",
        verification_state="anchored",
        vc_type="VerifiableCredential",
        vc_id="vc:eq9",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:eq9",
    )
    manifest_create = client.post(
        "/admin/evidence-manifests",
        json={
            "issuer": "deterministic:eq9",
            "authority_subject_id": "subject:did:key:z6MkUnsigned",
            "evidence_refs": ["coord:WX-u1"],
            "package_type": "signed_manifest",
            "signature_status": "unsigned",
        },
        headers=headers,
    )
    assert manifest_create.status_code == 200

    event_resp = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkUnsigned",
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-u1"],
            "idempotency_key": "evt-u1",
        },
        headers=headers,
    )
    assert event_resp.status_code == 200

    replay = client.post(
        "/admin/authority-events/replay",
        params={"authority_subject_id": "subject:did:key:z6MkUnsigned"},
        headers=headers,
    )
    assert replay.status_code == 200
    state_resp = client.get("/admin/authority-state/subject:did:key:z6MkUnsigned", headers=headers)
    assert state_resp.status_code == 200
    state = state_resp.json()["subject"]
    assert state["current_validation_status"] == "invalidated"
    assert "evidence_manifest_unsigned" in (state.get("current_invalidation_reasons") or [])


def test_admin_authority_event_rejects_duplicate_high_impact_evidence_reuse(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
        identity_anchor_ref="anchor:eq9",
        trust_basis="local_registry",
        verification_state="anchored",
        vc_type="VerifiableCredential",
        vc_id="vc:eq9",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:eq9",
    )
    first = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkDupEvidence",
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-dupe-1"],
            "idempotency_key": "evt-dupe-1",
        },
        headers=headers,
    )
    assert first.status_code == 200
    second = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkDupEvidence",
            "event_type": "sanction",
            "issuer": "deterministic:eq9",
            "reason_code": "eq_blocked:eq9_repeat",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-dupe-1"],
            "idempotency_key": "evt-dupe-2",
        },
        headers=headers,
    )
    assert second.status_code == 409


def test_admin_authority_event_rejects_unanchored_high_impact_issuer(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="operator:local-review",
        issuer_class="human_review_issuer",
        allowed_event_types=["repair"],
        credential_ref="cred:issuer:local-review",
        verification_state="registry_only",
    )
    resp = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkUnanchored",
            "event_type": "repair",
            "issuer": "operator:local-review",
            "reason_code": "manual_repair",
            "evidence_refs": ["coord:WX-6"],
            "idempotency_key": "evt-999",
        },
        headers=headers,
    )
    assert resp.status_code == 422


def test_admin_verifier_portal_registry_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/verifier-portals",
        json={
            "portal_id": "web4_decoder_app",
            "portal_type": "decoder_app",
            "trust_basis": "local_registry",
            "verification_mode": "resolver_backed",
            "trusted_identities": ["human:decoder"],
            "allowed_sources": ["decoder_app"],
            "resolver_ref": "resolver:web4-decoder",
            "status": "active",
        },
        headers=headers,
    )
    assert create.status_code == 200
    portal = create.json()["portal"]
    assert portal["portal_id"] == "web4_decoder_app"
    assert portal["verification_mode"] == "resolver_backed"

    listed = client.get("/admin/verifier-portals", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()["portals"]
    assert any(row.get("portal_id") == "web4_decoder_app" for row in rows if isinstance(row, dict))

    fetched = client.get("/admin/verifier-portals/web4_decoder_app", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["portal"]["resolver_ref"] == "resolver:web4-decoder"


def test_admin_verifier_proof_check_registry_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/verifier-proof-checks",
        json={
            "proof_ref": "proof:decoder:resolver",
            "resolver_ref": "resolver:web4-decoder",
            "portal_id": "web4_decoder_app",
            "verifier_identity": "human:decoder",
            "verification_status": "verified",
            "trust_root_ref": "trust-root:web4",
        },
        headers=headers,
    )
    assert create.status_code == 200
    proof = create.json()["proof"]
    assert proof["proof_ref"] == "proof:decoder:resolver"
    assert proof["resolver_ref"] == "resolver:web4-decoder"

    listed = client.get("/admin/verifier-proof-checks", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()["proofs"]
    assert any(row.get("proof_ref") == "proof:decoder:resolver" for row in rows if isinstance(row, dict))

    fetched = client.get("/admin/verifier-proof-checks/proof:decoder:resolver", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["proof"]["trust_root_ref"] == "trust-root:web4"


def test_admin_verifier_signature_check_registry_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/verifier-signature-checks",
        json={
            "signature_ref": "sig:decoder:signed",
            "public_key_ref": "pub:web4-decoder",
            "portal_id": "web4_decoder_app",
            "verifier_identity": "human:decoder",
            "verification_status": "verified",
            "trust_root_ref": "trust-root:web4",
        },
        headers=headers,
    )
    assert create.status_code == 200
    signature = create.json()["signature"]
    assert signature["signature_ref"] == "sig:decoder:signed"
    assert signature["public_key_ref"] == "pub:web4-decoder"

    listed = client.get("/admin/verifier-signature-checks", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()["signatures"]
    assert any(row.get("signature_ref") == "sig:decoder:signed" for row in rows if isinstance(row, dict))

    fetched = client.get("/admin/verifier-signature-checks/sig:decoder:signed", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["signature"]["trust_root_ref"] == "trust-root:web4"


def test_admin_verifier_public_key_registry_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }

    create = client.post(
        "/admin/verifier-public-keys",
        json={
            "public_key_ref": "pub:web4-decoder",
            "algorithm": "ecdsa-p256",
            "public_key_pem": "-----BEGIN PUBLIC KEY-----\\nZmFrZQ==\\n-----END PUBLIC KEY-----",
            "trust_root_ref": "trust-root:web4",
        },
        headers=headers,
    )
    assert create.status_code == 200
    key = create.json()["key"]
    assert key["public_key_ref"] == "pub:web4-decoder"
    assert key["algorithm"] == "ecdsa-p256"

    listed = client.get("/admin/verifier-public-keys", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()["keys"]
    assert any(row.get("public_key_ref") == "pub:web4-decoder" for row in rows if isinstance(row, dict))

    fetched = client.get("/admin/verifier-public-keys/pub:web4-decoder", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["key"]["trust_root_ref"] == "trust-root:web4"


def test_admin_authority_event_rejects_high_impact_issuer_with_non_active_vc_status(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="operator:suspended-review",
        issuer_class="human_review_issuer",
        allowed_event_types=["repair"],
        credential_ref="cred:issuer:suspended-review",
        issuer_did="did:web:suspended.example",
        identity_anchor_ref="anchor:suspended",
        trust_basis="untp_dia",
        verification_state="verified",
        vc_type="VerifiableCredential",
        vc_id="vc:suspended-review",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:suspended-review",
        credential_status_state="revoked",
    )
    resp = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkVcRevoked",
            "event_type": "repair",
            "issuer": "operator:suspended-review",
            "reason_code": "manual_repair",
            "evidence_refs": ["coord:WX-vc-1"],
            "idempotency_key": "evt-vc-1",
        },
        headers=headers,
    )
    assert resp.status_code == 422


def test_admin_authority_event_rejects_unverified_issuer_vc(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="operator:unverified-review",
        issuer_class="human_review_issuer",
        allowed_event_types=["repair"],
        credential_ref="cred:issuer:unverified-review",
        issuer_did="did:web:unverified.example",
        identity_anchor_ref="anchor:unverified",
        trust_basis="untp_dia",
        verification_state="verified",
        vc_type="VerifiableCredential",
        vc_id="vc:unverified-review",
        vc_envelope={
            "type": ["VerifiableCredential", "IssuerAuthorityCredential"],
            "issuer": "did:web:unverified.example",
            "credentialStatus": {"id": "status:unverified-review"},
        },
        credential_status_ref="status:unverified-review",
        credential_status_state="active",
        vc_verification_status="unverified",
        vc_verification_checked_at="2026-03-15T00:00:00Z",
    )
    resp = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkUnverifiedVc",
            "event_type": "repair",
            "issuer": "operator:unverified-review",
            "reason_code": "manual_repair",
            "evidence_refs": ["coord:WX-unverified-1"],
            "idempotency_key": "evt-unverified-1",
        },
        headers=headers,
    )
    assert resp.status_code == 422


def test_admin_rejects_malformed_issuer_vc_envelope(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    resp = client.post(
        "/admin/issuer-authorities",
        json={
            "issuer": "operator:bad-vc",
            "issuer_class": "human_review_issuer",
            "allowed_event_types": ["repair"],
            "credential_ref": "cred:issuer:bad-vc",
            "issuer_did": "did:web:bad.example",
            "identity_anchor_ref": "anchor:bad",
            "trust_basis": "untp_dia",
            "verification_state": "verified",
            "vc_type": "VerifiableCredential",
            "vc_id": "vc:bad-vc",
            "vc_envelope": {"type": ["VerifiableCredential"]},
            "credential_status_ref": "status:bad-vc",
        },
        headers=headers,
    )
    assert resp.status_code == 422


def test_admin_authority_event_rejects_stale_vc_status_check(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="operator:stale-review",
        issuer_class="human_review_issuer",
        allowed_event_types=["repair"],
        credential_ref="cred:issuer:stale-review",
        issuer_did="did:web:stale.example",
        identity_anchor_ref="anchor:stale",
        trust_basis="untp_dia",
        verification_state="verified",
        vc_type="VerifiableCredential",
        vc_id="vc:stale-review",
        vc_envelope={
            "type": ["VerifiableCredential", "IssuerAuthorityCredential"],
            "issuer": "did:web:stale.example",
            "credentialStatus": {"id": "status:stale-review"},
        },
        credential_status_ref="status:stale-review",
        credential_status_state="active",
    )
    stale = client.post(
        "/admin/issuer-authorities",
        json={
            "issuer": "operator:stale-review",
            "issuer_class": "human_review_issuer",
            "allowed_event_types": ["repair"],
            "credential_ref": "cred:issuer:stale-review",
            "issuer_did": "did:web:stale.example",
            "identity_anchor_ref": "anchor:stale",
            "trust_basis": "untp_dia",
            "verification_state": "verified",
            "vc_type": "VerifiableCredential",
            "vc_id": "vc:stale-review",
            "vc_envelope": {
                "type": ["VerifiableCredential", "IssuerAuthorityCredential"],
                "issuer": "did:web:stale.example",
                "credentialStatus": {"id": "status:stale-review"},
            },
            "credential_status_ref": "status:stale-review",
            "credential_status_state": "active",
            "credential_status_checked_at": "2024-01-01T00:00:00Z",
        },
        headers=headers,
    )
    assert stale.status_code == 200
    resp = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkStaleVc",
            "event_type": "repair",
            "issuer": "operator:stale-review",
            "reason_code": "manual_repair",
            "evidence_refs": ["coord:WX-stale-1"],
            "idempotency_key": "evt-stale-1",
        },
        headers=headers,
    )
    assert resp.status_code == 422


def test_admin_replay_marks_stale_signed_manifest_verification_invalid(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    monkeypatch.setenv("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")

    client = _make_client()
    headers = {
        "x-admin-token": "test-admin-token",
        "x-principal-id": "ops-admin",
        "x-principal-type": "admin",
    }
    _register_issuer(
        client,
        headers,
        issuer="deterministic:stale-manifest",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction"],
        credential_ref="cred:issuer:stale-manifest",
        issuer_did="did:web:stale-manifest.example",
        identity_anchor_ref="anchor:stale-manifest",
        trust_basis="local_registry",
        verification_state="anchored",
        vc_type="VerifiableCredential",
        vc_id="vc:stale-manifest",
        vc_envelope={"type": ["VerifiableCredential", "IssuerAuthorityCredential"]},
        credential_status_ref="status:stale-manifest",
    )
    create = client.post(
        "/admin/evidence-manifests",
        json={
            "issuer": "deterministic:stale-manifest",
            "authority_subject_id": "subject:did:key:z6MkStaleManifest",
            "evidence_refs": ["coord:WX-sm-1"],
            "package_type": "signed_manifest",
            "signature_ref": "sig:stale-manifest:1",
            "signature_status": "verified",
            "verification_method": "signature_check",
            "verification_status": "verified",
            "verification_checked_at": "2024-01-01T00:00:00Z",
        },
        headers=headers,
    )
    assert create.status_code == 200

    event_resp = client.post(
        "/admin/authority-events",
        json={
            "authority_subject_id": "subject:did:key:z6MkStaleManifest",
            "event_type": "sanction",
            "issuer": "deterministic:stale-manifest",
            "reason_code": "eq_blocked:eq9_telos",
            "delta": {"trust_class": "T0", "posture_class": "P0"},
            "evidence_refs": ["coord:WX-sm-1"],
            "idempotency_key": "evt-sm-1",
        },
        headers=headers,
    )
    assert event_resp.status_code == 200

    replay = client.post(
        "/admin/authority-events/replay",
        params={"authority_subject_id": "subject:did:key:z6MkStaleManifest"},
        headers=headers,
    )
    assert replay.status_code == 200

    state_resp = client.get("/admin/authority-state/subject:did:key:z6MkStaleManifest", headers=headers)
    assert state_resp.status_code == 200
    state = state_resp.json()["subject"]
    assert state["current_validation_status"] == "invalidated"
    assert "evidence_manifest_verification_stale" in (state.get("current_invalidation_reasons") or [])
