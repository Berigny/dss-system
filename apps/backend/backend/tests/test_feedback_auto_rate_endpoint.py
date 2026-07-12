from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from backend.api.chat import assess_router as chat_assess_router, router as chat_router
from backend.api.enrich import router as enrich_router
from backend.api.http import router as ledger_router, web4_router
from backend.api.ingest import router as ingest_router
from backend.api.resolver import router as resolver_router
from backend.api.stats import router as stats_router
from backend.services.evidence_manifests import upsert_evidence_manifest
from backend.services.live_verifier_signatures import canonical_attestation_payload
from backend.services.verifier_public_keys import upsert_verifier_public_key
from backend.services.verifier_portals import upsert_verifier_portal
from backend.services.verifier_proof_checks import upsert_verifier_proof_check
from backend.services.verifier_signature_checks import upsert_verifier_signature_check


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(chat_router)
    app.include_router(chat_assess_router)
    app.include_router(ingest_router)
    app.include_router(enrich_router)
    app.include_router(resolver_router)
    app.include_router(stats_router)
    app.include_router(ledger_router)
    app.include_router(web4_router)
    return TestClient(app)


def _register_verifier_portal(client: TestClient, *, portal_id: str, verification_mode: str = "registry_backed") -> None:
    upsert_verifier_portal(
        client.app.state.db,
        portal_id=portal_id,
        portal_type="decoder_app",
        trust_basis="local_registry",
        verification_mode=verification_mode,
        trusted_identities=["human:decoder"],
        allowed_sources=["decoder_app"],
        public_key_ref="pub:web4-decoder" if verification_mode == "signature_required" else None,
        resolver_ref="resolver:web4-decoder" if verification_mode == "resolver_backed" else None,
        status="active",
    )


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def test_auto_feedback_rejects_path_header_ledger_mismatch() -> None:
    client = _make_client()
    resp = client.post(
        "/ledger/feedback/auto/chat-team-a:WX-1",
        json={"rating": 3, "reason": "model approves", "model": "gpt-4.1-mini"},
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:demo"},
    )
    assert resp.status_code == 400
    payload = resp.json()
    detail = payload.get("detail") if isinstance(payload, dict) else None
    assert isinstance(detail, dict)
    assert detail.get("error") == "ledger_scope_mismatch"


def test_auto_feedback_persists_rollup_with_default_model_actor() -> None:
    client = _make_client()
    entry_id = "chat-team-a:WX-1"

    write_resp = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-1"},
            "state": {"coordinates": {}, "phase": "chat", "metadata": {"content": "hello"}},
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert write_resp.status_code == 200

    auto_resp = client.post(
        f"/ledger/feedback/auto/{entry_id}",
        json={
            "rating": 3,
            "reason": "high coherence and grounded answer",
            "model": "openai/gpt-4.1-mini",
            "confidence": 0.91,
        },
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert auto_resp.status_code == 200
    payload = auto_resp.json()
    assert payload.get("status") == "ok"
    assert payload.get("entry_id") == entry_id

    applied = payload.get("applied") or {}
    assert applied.get("actor_id") == "model:openai/gpt-4.1-mini"
    assert applied.get("actor_type") == "model"
    assert applied.get("source") == "mcp_auto_rate"
    assert float(applied.get("confidence")) == 0.91

    rollup = payload.get("rollup") or {}
    assert float(rollup.get("score")) == 3.0
    assert int(rollup.get("actors")) == 1
    assert int(rollup.get("samples")) == 1


def test_feedback_portal_attestation_is_persisted_and_returned() -> None:
    client = _make_client()
    _register_verifier_portal(client, portal_id="web4_decoder_app")
    entry_id = "chat-team-a:WX-portal"

    write_resp = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-portal"},
            "state": {"coordinates": {}, "phase": "chat", "metadata": {"content": "portal evidence"}},
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert write_resp.status_code == 200

    feedback_resp = client.post(
        f"/ledger/feedback/{entry_id}",
        json={
            "actor_id": "human:decoder",
            "actor_type": "human",
            "rating": 3,
            "reason": "externally verified via decoder",
            "source": "decoder_app",
            "verifier_portal": "web4_decoder_app",
            "verifier_identity": "human:decoder",
            "verification_signature_ref": "sig:decoder:1",
            "verification_proof_ref": "proof:decoder:1",
            "context_id": "ctx:demo",
        },
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert feedback_resp.status_code == 200
    payload = feedback_resp.json()
    external_verification = payload.get("external_verification") or {}
    latest = external_verification.get("latest_attestation") or {}
    assert external_verification.get("verified") is True
    assert latest.get("verifier_portal") == "web4_decoder_app"
    assert latest.get("verification_signature_ref") == "sig:decoder:1"

    readback = client.get(
        f"/ledger/feedback/{entry_id}",
        params={"context_id": "ctx:demo"},
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert readback.status_code == 200
    readback_payload = readback.json()
    assert (readback_payload.get("external_verification") or {}).get("verified") is True


def test_evidence_manifest_derives_verified_status_from_portal_attestation() -> None:
    client = _make_client()
    _register_verifier_portal(client, portal_id="web4_decoder_app")
    entry_id = "chat-team-a:WX-derived"

    write_resp = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-derived"},
            "state": {"coordinates": {}, "phase": "chat", "metadata": {"content": "derived evidence"}},
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert write_resp.status_code == 200

    feedback_resp = client.post(
        f"/ledger/feedback/{entry_id}",
        json={
            "actor_id": "human:decoder",
            "actor_type": "human",
            "rating": 3,
            "reason": "verified for evidence manifest",
            "source": "decoder_app",
            "verifier_portal": "web4_decoder_app",
            "verifier_identity": "human:decoder",
            "verification_proof_ref": "proof:decoder:derived",
            "context_id": "ctx:demo",
        },
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert feedback_resp.status_code == 200

    manifest = upsert_evidence_manifest(
        client.app.state.db,
        issuer="operator:portal",
        authority_subject_id="subject:did:key:z6MkPortal",
        evidence_refs=[entry_id],
        package_type="signed_manifest",
        signature_status="verified",
    )
    assert manifest["verification_method"] == "external_resolver"
    assert manifest["verification_status"] == "verified"
    assert manifest["verification_proof_ref"] == "proof:decoder:derived"


def test_evidence_manifest_fails_closed_for_unregistered_portal_attestation() -> None:
    client = _make_client()
    entry_id = "chat-team-a:WX-untrusted"

    write_resp = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-untrusted"},
            "state": {"coordinates": {}, "phase": "chat", "metadata": {"content": "untrusted evidence"}},
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert write_resp.status_code == 200

    feedback_resp = client.post(
        f"/ledger/feedback/{entry_id}",
        json={
            "actor_id": "human:decoder",
            "actor_type": "human",
            "rating": 3,
            "reason": "asserted but untrusted",
            "source": "decoder_app",
            "verifier_portal": "unknown_portal",
            "verifier_identity": "human:decoder",
            "verification_proof_ref": "proof:unknown:1",
            "context_id": "ctx:demo",
        },
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert feedback_resp.status_code == 200
    external_verification = feedback_resp.json().get("external_verification") or {}
    assert external_verification.get("trusted") is False
    assert "portal_unregistered" in (external_verification.get("trust_reasons") or [])

    manifest = upsert_evidence_manifest(
        client.app.state.db,
        issuer="operator:portal",
        authority_subject_id="subject:did:key:z6MkPortalUntrusted",
        evidence_refs=[entry_id],
        package_type="signed_manifest",
        signature_status="verified",
    )
    assert manifest["verification_method"] == "external_resolver"
    assert manifest["verification_status"] == "failed"
    assert "chat-team-a:WX-untrusted" in (manifest.get("external_verifier_summary") or {}).get("untrusted_refs", [])


def test_resolver_backed_portal_requires_matching_proof_check() -> None:
    client = _make_client()
    _register_verifier_portal(client, portal_id="web4_decoder_app", verification_mode="resolver_backed")
    entry_id = "chat-team-a:WX-resolver"

    write_resp = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-resolver"},
            "state": {"coordinates": {}, "phase": "chat", "metadata": {"content": "resolver backed evidence"}},
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert write_resp.status_code == 200

    without_check = client.post(
        f"/ledger/feedback/{entry_id}",
        json={
            "actor_id": "human:decoder",
            "actor_type": "human",
            "rating": 3,
            "reason": "resolver proof asserted only",
            "source": "decoder_app",
            "verifier_portal": "web4_decoder_app",
            "verifier_identity": "human:decoder",
            "verification_proof_ref": "proof:decoder:resolver",
            "context_id": "ctx:demo",
        },
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert without_check.status_code == 200
    external_verification = without_check.json().get("external_verification") or {}
    assert external_verification.get("trusted") is False
    assert "resolver_proof_unverified" in (external_verification.get("trust_reasons") or [])

    upsert_verifier_proof_check(
        client.app.state.db,
        proof_ref="proof:decoder:resolver",
        resolver_ref="resolver:web4-decoder",
        portal_id="web4_decoder_app",
        verifier_identity="human:decoder",
        verification_status="verified",
        trust_root_ref="trust-root:web4",
    )

    with_check = client.get(
        f"/ledger/feedback/{entry_id}",
        params={"context_id": "ctx:demo"},
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert with_check.status_code == 200
    readback = with_check.json().get("external_verification") or {}
    assert readback.get("trusted") is True
    latest_proof_check = readback.get("latest_proof_check") or {}
    assert latest_proof_check.get("resolver_ref") == "resolver:web4-decoder"


def test_signature_required_portal_requires_matching_signature_check() -> None:
    client = _make_client()
    _register_verifier_portal(client, portal_id="web4_decoder_app", verification_mode="signature_required")
    entry_id = "chat-team-a:WX-signature"

    write_resp = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-signature"},
            "state": {"coordinates": {}, "phase": "chat", "metadata": {"content": "signature backed evidence"}},
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert write_resp.status_code == 200

    without_check = client.post(
        f"/ledger/feedback/{entry_id}",
        json={
            "actor_id": "human:decoder",
            "actor_type": "human",
            "rating": 3,
            "reason": "signature asserted only",
            "source": "decoder_app",
            "verifier_portal": "web4_decoder_app",
            "verifier_identity": "human:decoder",
            "verification_signature_ref": "sig:decoder:signed",
            "context_id": "ctx:demo",
        },
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert without_check.status_code == 200
    external_verification = without_check.json().get("external_verification") or {}
    assert external_verification.get("trusted") is False
    assert "signature_unverified" in (external_verification.get("trust_reasons") or [])

    upsert_verifier_signature_check(
        client.app.state.db,
        signature_ref="sig:decoder:signed",
        public_key_ref="pub:web4-decoder",
        portal_id="web4_decoder_app",
        verifier_identity="human:decoder",
        verification_status="verified",
        trust_root_ref="trust-root:web4",
    )

    with_check = client.get(
        f"/ledger/feedback/{entry_id}",
        params={"context_id": "ctx:demo"},
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert with_check.status_code == 200
    readback = with_check.json().get("external_verification") or {}
    assert readback.get("trusted") is True
    latest_signature_check = readback.get("latest_signature_check") or {}
    assert latest_signature_check.get("public_key_ref") == "pub:web4-decoder"


def test_signature_required_portal_can_verify_live_signature_submission() -> None:
    client = _make_client()
    _register_verifier_portal(client, portal_id="web4_decoder_app", verification_mode="signature_required")
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    upsert_verifier_public_key(
        client.app.state.db,
        public_key_ref="pub:web4-decoder",
        algorithm="ecdsa-p256",
        public_key_pem=public_key_pem,
        trust_root_ref="trust-root:web4",
    )
    entry_id = "chat-team-a:WX-live-signature"

    write_resp = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-live-signature"},
            "state": {"coordinates": {}, "phase": "chat", "metadata": {"content": "live signature evidence"}},
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert write_resp.status_code == 200

    payload = canonical_attestation_payload(
        evidence_ref=entry_id,
        actor_id="human:decoder",
        actor_type="human",
        rating=3,
        reason="live signature verified",
        source="decoder_app",
        verifier_portal="web4_decoder_app",
        verifier_identity="human:decoder",
        verification_signature_ref="sig:decoder:live",
        verification_proof_ref=None,
    )
    signature = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))

    feedback_resp = client.post(
        f"/ledger/feedback/{entry_id}",
        json={
            "actor_id": "human:decoder",
            "actor_type": "human",
            "rating": 3,
            "reason": "live signature verified",
            "source": "decoder_app",
            "verifier_portal": "web4_decoder_app",
            "verifier_identity": "human:decoder",
            "verification_signature_ref": "sig:decoder:live",
            "verification_signature_b64u": _b64u_encode(signature),
            "context_id": "ctx:demo",
        },
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:demo"},
    )
    assert feedback_resp.status_code == 200
    external_verification = feedback_resp.json().get("external_verification") or {}
    assert external_verification.get("trusted") is True
    latest_signature_check = external_verification.get("latest_signature_check") or {}
    assert latest_signature_check.get("verification_status") == "verified"
    assert latest_signature_check.get("trust_root_ref") == "trust-root:web4"
