from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.chat as chat_api
from backend.api.admin import public_router
from backend.api.chat import assess_router as chat_assess_router, router as chat_router
from backend.api.enrich import router as enrich_router
from backend.api.http import router as ledger_router, web4_router
from backend.api.ingest import router as ingest_router
from backend.api.resolver import RESOLVER_AUDIT_V1_KEY, router as resolver_router
from backend.api.stats import router as stats_router
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.services.authority_events import append_authority_event
from backend.services.issuer_authorities import upsert_issuer_authority
from backend.services.public_objects import upsert_public_object
from backend.services.subject_events import append_subject_event


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
    app.include_router(public_router)
    return TestClient(app)


def _upsert_issuer(
    client: TestClient,
    *,
    issuer: str,
    issuer_class: str,
    allowed_event_types: list[str],
    credential_ref: str | None = None,
    issuer_did: str | None = None,
    identity_anchor_ref: str | None = None,
    trust_basis: str | None = None,
    verification_state: str = "registry_only",
) -> None:
    upsert_issuer_authority(
        client.app.state.db,
        issuer=issuer,
        issuer_class=issuer_class,
        allowed_event_types=allowed_event_types,
        credential_ref=credential_ref,
        issuer_did=issuer_did,
        identity_anchor_ref=identity_anchor_ref,
        trust_basis=trust_basis,
        verification_state=verification_state,
    )


def _is_ledger_context_error(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    detail = payload.get("detail")
    if not isinstance(detail, dict):
        return False
    return detail.get("error") == "ledger_context_required"


def test_chat_requires_explicit_ledger_context() -> None:
    client = _make_client()
    resp = client.post(
        "/chat",
        json={
            "session_id": "s1",
            "entity": "chat-s1",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
    )
    assert resp.status_code == 422
    assert _is_ledger_context_error(resp.json()) is True


def test_chat_stream_emits_error_event_when_ledger_context_missing() -> None:
    client = _make_client()
    resp = client.post(
        "/chat/stream",
        json={
            "session_id": "s1",
            "entity": "chat-s1",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
    )
    assert resp.status_code == 200
    body = resp.text
    assert "ledger_context_required" in body


def test_commit_answer_requires_explicit_ledger_context() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-s1",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {},
        },
    )
    assert resp.status_code == 422
    assert _is_ledger_context_error(resp.json()) is True


def test_ingest_and_enrich_accept_x_ledger_id_header_fallback() -> None:
    client = _make_client()
    headers = {"x-ledger-id": "chat-team-a"}

    ingest_resp = client.post(
        "/ingest",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": "hello",
            "kind": "text",
            "metadata": {},
        },
        headers=headers,
    )
    assert not (ingest_resp.status_code == 422 and _is_ledger_context_error(ingest_resp.json()))

    enrich_resp = client.post(
        "/enrich",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t2",
            "role": "user",
            "content": "hello",
            "kind": "text",
            "metadata": {},
        },
        headers=headers,
    )
    assert not (enrich_resp.status_code == 422 and _is_ledger_context_error(enrich_resp.json()))


def test_ingest_file_requires_explicit_ledger_context() -> None:
    client = _make_client()
    resp = client.post(
        "/ingest/file",
        data={
            "entity": "chat-team-a",
            "kind": "attachment",
            "session_id": "s1",
            "turn_id": "t1",
        },
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422
    assert _is_ledger_context_error(resp.json()) is True


def test_resolver_namespace_gate_compat_vs_strict(monkeypatch) -> None:
    client = _make_client()
    payload = {"namespace": "chat-team-a", "identifier": "missing"}
    headers = {"x-ledger-id": "chat-team-a"}

    monkeypatch.setenv("RESOLVER_NAMESPACE_GATE_MODE", "compat")
    compat = client.post("/resolve", json=payload, headers=headers)
    assert compat.status_code in {404, 422}

    monkeypatch.setenv("RESOLVER_NAMESPACE_GATE_MODE", "strict")
    strict = client.post("/resolve", json=payload, headers=headers)
    assert strict.status_code in {404, 422}


def test_resolver_rejects_coordinate_namespace_ledger_mismatch() -> None:
    client = _make_client()
    resp = client.post(
        "/resolve",
        json={"namespace": "chat-team-a", "identifier": "missing"},
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert isinstance(payload, dict)
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "ledger_scope_mismatch"


def test_tiered_resolver_public_skim_withholds_native_coords() -> None:
    client = _make_client()
    write = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-tiered"},
            "state": {
                "coordinates": {"coord:WX-secret": 1.0},
                "phase": "chat",
                "metadata": {
                    "content": "resolver tier contract content",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-team-a",
                    "resolved_coords": ["chat-team-a:WX-secret"],
                },
            },
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert write.status_code == 200

    resp = client.post(
        "/resolve/tiered",
        json={"namespace": "chat-team-a", "identifier": "WX-tiered", "read_tier": "public_skim"},
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["read_tier"] == "public_skim"
    assert payload["cache"]["visibility"] == "public"
    assert payload["entry"]["summary"] == "resolver tier contract content"
    assert payload["entry"]["metadata"]["content"]["state"] == "withheld"
    assert payload["entry"]["coordinates"]["reason"] == "native_coord_hidden"
    assert "state.coordinates" in (payload["redaction"]["withheld_fields"] or [])
    assert payload["audit"]["recorded"] is False


def test_tiered_resolver_operator_and_internal_diagnostic_split() -> None:
    client = _make_client()
    write = client.post(
        "/ledger/write",
        json={
            "key": {"namespace": "chat-team-a", "identifier": "WX-tiered-ops"},
            "state": {
                "coordinates": {"coord:WX-secret": 2.0},
                "phase": "chat",
                "metadata": {
                    "content": "operator tier content",
                    "canonical_subject": "did:web:id.dualsubstrate.com:ledgers:chat-team-a",
                    "resolved_coords": ["chat-team-a:WX-secret"],
                },
            },
            "notes": "seed",
            "pinned": False,
        },
        headers={"x-ledger-id": "chat-team-a", "x-principal-id": "ops-admin", "x-principal-type": "admin"},
    )
    assert write.status_code == 200

    operator = client.post(
        "/resolve/tiered",
        json={"namespace": "chat-team-a", "identifier": "WX-tiered-ops", "read_tier": "operator_full"},
        headers={"x-ledger-id": "chat-team-a", "x-principal-id": "ops-admin", "x-principal-type": "admin"},
    )
    assert operator.status_code == 200
    operator_body = operator.json()
    assert operator_body["entry"]["metadata"]["content"] == "operator tier content"
    assert operator_body["entry"]["metadata"]["resolved_coords"]["state"] == "withheld"
    assert operator_body["entry"]["coordinates"]["state"] == "withheld"
    assert operator_body["audit"]["recorded"] is True

    internal = client.post(
        "/resolve/tiered",
        json={"namespace": "chat-team-a", "identifier": "WX-tiered-ops", "read_tier": "internal_diagnostic"},
        headers={"x-ledger-id": "chat-team-a", "x-principal-id": "ops-admin", "x-principal-type": "admin"},
    )
    assert internal.status_code == 200
    internal_body = internal.json()
    assert internal_body["entry"]["metadata"]["resolved_coords"] == ["chat-team-a:WX-secret"]
    assert internal_body["entry"]["coordinates"]["coord:WX-secret"] == 2.0
    records = json.loads(client.app.state.db[RESOLVER_AUDIT_V1_KEY].decode())["records"]
    assert len(records) >= 2
    assert any(row.get("read_tier") == "internal_diagnostic" for row in records if isinstance(row, dict))


def test_tiered_resolver_not_found_returns_normalized_detail() -> None:
    client = _make_client()
    resp = client.post(
        "/resolve/tiered",
        json={"namespace": "chat-team-a", "identifier": "missing", "read_tier": "verifier_full"},
        headers={"x-ledger-id": "chat-team-a", "x-principal-id": "verifier", "x-principal-type": "service"},
    )
    assert resp.status_code == 404
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail["outcome"] == "not_found"
    assert detail["read_tier"] == "verifier_full"


def test_stats_telemetry_requires_explicit_ledger_context_in_enforce_mode(monkeypatch) -> None:
    client = _make_client()
    monkeypatch.setenv("LEDGER_CONTEXT_MODE", "enforce")
    resp = client.post(
        "/stats/telemetry",
        json={
            "session_id": "s1",
        },
    )
    assert resp.status_code == 422
    assert _is_ledger_context_error(resp.json()) is True


def test_stats_telemetry_accepts_x_ledger_id_header_fallback(monkeypatch) -> None:
    client = _make_client()
    monkeypatch.setenv("LEDGER_CONTEXT_MODE", "enforce")
    resp = client.post(
        "/stats/telemetry",
        json={
            "session_id": "s1",
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"


def test_stats_telemetry_rejects_payload_header_ledger_mismatch() -> None:
    client = _make_client()
    resp = client.post(
        "/stats/telemetry",
        json={
            "session_id": "s1",
            "namespace": "chat-team-a",
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert isinstance(payload, dict)
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "ledger_scope_mismatch"


def test_stats_auth_observability_dashboard_includes_runbooks_and_alerts() -> None:
    client = _make_client()
    resp = client.post(
        "/stats/telemetry",
        json={
            "session_id": "s1",
            "namespace": "chat-team-a",
            "authz_denied": True,
            "authz_reason": "did_principal_required",
            "authz_principal_source": "legacy_header",
            "authz_principal_mode": "compat",
            "auth_error_class": "token_validation_failed",
            "auth_token_validation_failed": True,
            "provider": "openai",
            "model": "mock",
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert resp.status_code == 200

    dashboard = client.get("/stats/observability/auth")
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload.get("scope") == "global_auth_observability"
    assert isinstance(payload.get("runbook_links"), dict)
    alerts = payload.get("alerts") if isinstance(payload.get("alerts"), dict) else {}
    assert alerts.get("auth_token_validation_failure_active") is True
    deny_reasons = (
        payload.get("dashboards", {}).get("deny_reasons")
        if isinstance(payload.get("dashboards"), dict)
        else {}
    )
    if isinstance(deny_reasons, dict):
        assert deny_reasons.get("did_principal_required", 0) >= 1


def test_chat_rejects_payload_header_ledger_mismatch() -> None:
    client = _make_client()
    resp = client.post(
        "/chat",
        json={
            "session_id": "s1",
            "entity": "chat-team-a",
            "ledger_id": "chat-team-a",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert isinstance(payload, dict)
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "ledger_scope_mismatch"
    assert detail.get("payload_ledger_id") == "chat-team-a"
    assert detail.get("header_ledger_id") == "chat-team-b"


def test_chat_rejects_payload_header_context_mismatch() -> None:
    client = _make_client()
    resp = client.post(
        "/chat",
        json={
            "session_id": "s1",
            "entity": "chat-team-a",
            "ledger_id": "chat-team-a",
            "context_id": "ctx:a",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
        headers={"x-ledger-id": "chat-team-a", "x-context-id": "ctx:b"},
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert isinstance(payload, dict)
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "context_scope_mismatch"
    assert detail.get("payload_context_id") == "ctx:a"
    assert detail.get("header_context_id") == "ctx:b"


def test_chat_requires_context_id_in_enforce_mode(monkeypatch) -> None:
    client = _make_client()
    monkeypatch.setenv("LEDGER_CONTEXT_ID_MODE", "enforce")
    resp = client.post(
        "/chat",
        json={
            "session_id": "s1",
            "entity": "chat-team-a",
            "ledger_id": "chat-team-a",
            "message": "hello",
            "provider": "openai",
            "history": [],
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert isinstance(payload, dict)
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "context_id_required"


def test_feedback_rejects_path_header_ledger_mismatch() -> None:
    client = _make_client()
    resp = client.post(
        "/ledger/feedback/chat-team-a:WX-1",
        json={
            "actor_id": "human:demo",
            "actor_type": "human",
            "rating": 3,
            "reason": "approve",
            "source": "test",
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert isinstance(payload, dict)
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "ledger_scope_mismatch"
    assert detail.get("header_ledger_id") == "chat-team-b"
    assert detail.get("path_ledger_id") == "chat-team-a"


def test_feedback_requires_context_id_in_enforce_mode(monkeypatch) -> None:
    client = _make_client()
    monkeypatch.setenv("LEDGER_CONTEXT_ID_MODE", "enforce")
    resp = client.post(
        "/ledger/feedback/chat-team-a:WX-1",
        json={
            "actor_id": "human:demo",
            "actor_type": "human",
            "rating": 3,
            "reason": "approve",
            "source": "test",
        },
        headers={"x-ledger-id": "chat-team-a"},
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert isinstance(payload, dict)
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "context_id_required"


def test_feedback_respects_ledger_context_binding(monkeypatch) -> None:
    client = _make_client()
    monkeypatch.setenv("LEDGER_AUTHZ_MODE", "registry")
    registry = {
        "version": 1,
        "ledgers": {
            "chat-team-a": {
                "ledger_id": "chat-team-a",
                "tenant_id": "tenant:alice",
                "owner_principal_id": "alice",
                "owner_principal_type": "user",
                "policy_profile": "standard",
                "status": "active",
                "metadata": {"allowed_context_ids": ["ctx:frontend"]},
            }
        },
    }
    client.app.state.db[b"__ledgers_v1__"] = json.dumps(registry).encode("utf-8")

    denied = client.post(
        "/ledger/feedback/chat-team-a:WX-1",
        json={
            "actor_id": "human:demo",
            "actor_type": "human",
            "rating": 3,
            "reason": "approve",
            "source": "test",
        },
        headers={
            "x-ledger-id": "chat-team-a",
            "x-principal-id": "alice",
            "x-principal-type": "user",
            "x-context-id": "ctx:decoder",
        },
    )
    assert denied.status_code == 403
    detail_denied = denied.json().get("detail")
    assert isinstance(detail_denied, dict)
    assert detail_denied.get("reason") == "context_not_allowed"

    allowed = client.post(
        "/ledger/feedback/chat-team-a:WX-1",
        json={
            "actor_id": "human:demo",
            "actor_type": "human",
            "rating": 3,
            "reason": "approve",
            "source": "test",
        },
        headers={
            "x-ledger-id": "chat-team-a",
            "x-principal-id": "alice",
            "x-principal-type": "user",
            "x-context-id": "ctx:frontend",
        },
    )
    # Entry is missing in this synthetic test app; authz passed if we reached 404.
    assert allowed.status_code == 404


def test_web4_decode_rejects_coordinate_namespace_ledger_mismatch() -> None:
    client = _make_client()
    resp = client.post(
        "/web4/decode",
        json={"coordinate": "chat-team-a:WX-1", "ledger_id": "chat-team-a"},
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert isinstance(payload, dict)
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "ledger_scope_mismatch"


def test_chat_web4_decode_rejects_coordinate_namespace_ledger_mismatch() -> None:
    client = _make_client()
    resp = client.post(
        "/chat/web4/decode",
        json={"coordinate": "chat-team-a:WX-1", "ledger_id": "chat-team-a"},
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, dict)
    assert payload.get("status") == "error"
    detail = payload.get("detail")
    assert isinstance(detail, dict)
    assert detail.get("error") == "ledger_scope_mismatch"


def test_ledger_scope_strict_off_allows_mismatch_with_payload_precedence(monkeypatch) -> None:
    client = _make_client()
    monkeypatch.setenv("LEDGER_SCOPE_STRICT", "false")
    resp = client.post(
        "/ingest",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": "hello",
            "kind": "text",
            "metadata": {},
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    coordinate = payload.get("coordinate")
    assert isinstance(coordinate, str)
    assert coordinate.startswith("chat-team-a:")


def test_namespace_source_entity_compat_uses_entity_namespace(monkeypatch) -> None:
    client = _make_client()
    monkeypatch.setenv("LEDGER_NAMESPACE_SOURCE", "entity_compat")
    resp = client.post(
        "/ingest",
        json={
            "entity": "chat-team-entity",
            "ledger_id": "chat-team-ledger",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": "hello",
            "kind": "text",
            "metadata": {},
        },
        headers={"x-ledger-id": "chat-team-ledger"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    coordinate = payload.get("coordinate")
    assert isinstance(coordinate, str)
    assert coordinate.startswith("chat-team-entity:")


def test_enrich_persists_namespace_from_ledger_scope() -> None:
    client = _make_client()
    resp = client.post(
        "/enrich",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "role": "user",
            "content": "hello",
            "kind": "text",
            "metadata": {},
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    coordinate = payload.get("coordinate")
    assert isinstance(coordinate, str)
    assert coordinate.startswith("chat-team-b:")


def test_ingest_persists_namespace_from_ledger_scope() -> None:
    client = _make_client()
    resp = client.post(
        "/ingest",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": "hello",
            "kind": "text",
            "metadata": {},
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    coordinate = payload.get("coordinate")
    assert isinstance(coordinate, str)
    assert coordinate.startswith("chat-team-b:")


def test_ingest_returns_parent_and_part_coordinates_for_chunked_attachment() -> None:
    client = _make_client()
    resp = client.post(
        "/ingest",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": "A" * 9000,
            "kind": "attachment",
            "metadata": {},
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    coordinate = payload.get("coordinate")
    parent_coordinate = payload.get("parent_coordinate")
    part_coordinates = payload.get("part_coordinates")
    assert isinstance(coordinate, str) and coordinate.startswith("chat-team-b:")
    assert parent_coordinate == coordinate
    assert isinstance(part_coordinates, list)
    assert len(part_coordinates) >= 2
    assert all(isinstance(item, str) and item.startswith("chat-team-b:") for item in part_coordinates)


def test_commit_answer_persists_namespace_from_ledger_scope() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {},
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    coordinate = payload.get("coordinate")
    assert isinstance(coordinate, str)
    assert coordinate.startswith("chat-team-b:")
    assert "eval_contract" in payload


def test_commit_answer_persists_transcript_by_default() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "normalized_input": {"user_message": "u"},
                "reply_contract": {"reply_text": "a"},
                "content": "a",
            },
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    assert metadata.get("transcript_persisted") is True
    assert metadata.get("content") == "a"
    assert metadata.get("user_message") == "u"
    assert metadata.get("assistant_reply") == "a"
    normalized_input = metadata.get("normalized_input")
    if isinstance(normalized_input, dict):
        assert normalized_input.get("user_message") == "u"
    reply_contract = metadata.get("reply_contract")
    if isinstance(reply_contract, dict):
        assert reply_contract.get("reply_text") == "a"


def test_commit_answer_can_redact_transcript_when_requested() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "user_message": "u",
            "assistant_reply": "a",
            "persist_conversation": False,
            "metadata": {
                "normalized_input": {"user_message": "u"},
                "reply_contract": {"reply_text": "a"},
                "content": "a",
            },
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    assert metadata.get("transcript_persisted") is False
    assert metadata.get("content") == ""
    assert "user_message" not in metadata
    assert "assistant_reply" not in metadata
    normalized_input = metadata.get("normalized_input")
    if isinstance(normalized_input, dict):
        assert normalized_input.get("user_message", "") == ""
    reply_contract = metadata.get("reply_contract")
    if isinstance(reply_contract, dict):
        assert reply_contract.get("reply_text", "") == ""


def test_enrich_persists_canonical_provenance_fields() -> None:
    client = _make_client()
    headers = {
        "x-ledger-id": "chat-team-b",
        "x-principal-id": "alice",
        "x-principal-type": "user",
        "x-context-id": "ctx:test",
    }
    resp = client.post(
        "/enrich",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "role": "user",
            "content": "hello",
            "kind": "text",
            "metadata": {"provider": "openrouter", "model": "gpt-4o-mini"},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    assert metadata.get("ledger_id") == "chat-team-b"
    assert metadata.get("contributor_id") == "user:alice"
    assert metadata.get("context_id") == "ctx:test"
    assert metadata.get("provider_id") == "openrouter"
    assert metadata.get("model_id") == "gpt-4o-mini"
    assert metadata.get("session_id") == "s1"
    assert metadata.get("turn_id") == "t1"
    dual = metadata.get("provenance_dual_write") if isinstance(metadata.get("provenance_dual_write"), dict) else {}
    assert dual.get("status") == "legacy_only"


def test_enrich_persists_indefeasible_taxonomy_provenance() -> None:
    client = _make_client()
    resp = client.post(
        "/enrich",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "role": "assistant",
            "content": "visual topology reply",
            "kind": "text",
            "metadata": {"domain": "visual"},
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    taxonomy = metadata.get("taxonomy_provenance") if isinstance(metadata.get("taxonomy_provenance"), dict) else {}
    assert metadata.get("taxonomy_mode") == "indefeasible"
    assert metadata.get("taxonomy_version") == "mmf-projection-v1"
    assert metadata.get("taxonomy_topology_ref") == "visual"
    assert taxonomy.get("domain") == "visual"
    assert taxonomy.get("topology_ref") == "visual"
    assert taxonomy.get("taxonomy_mode") == "indefeasible"
    assert len(taxonomy.get("cube_primes") or []) == 8
    assert len(taxonomy.get("anchor_primes") or []) == 4
    assert len(taxonomy.get("extension_primes") or []) == 4
    response_metadata = resp.json().get("metadata") or {}
    assert response_metadata.get("taxonomy_mode") == "indefeasible"
    assert response_metadata.get("taxonomy_topology_ref") == "visual"
    assert isinstance(response_metadata.get("body_prime"), int)
    assert isinstance(response_metadata.get("token_primes"), list)
    assert response_metadata.get("prime_multiplicative_value") == response_metadata.get("token_prime_product")


def test_chat_enforces_standing_retrieval_write_and_token_caps(monkeypatch) -> None:
    client = _make_client()
    captured: dict[str, object] = {"assemble_called": False}

    async def fake_assemble_context(**_kwargs):
        captured["assemble_called"] = True
        return {"retrieved": [{"coord": "chat-team-b:WX-1"}], "assessments": {}}

    class _Usage:
        prompt_tokens = 11
        completion_tokens = 7

    async def fake_complete_chat(*, provider, messages, max_tokens, log_label=None):
        captured["provider"] = provider
        captured["message_count"] = len(messages)
        captured["max_tokens"] = max_tokens
        return "assistant reply", 0.01, 12.0, _Usage(), "stop"

    monkeypatch.setattr(chat_api, "assemble_context", fake_assemble_context)
    monkeypatch.setattr(chat_api, "complete_chat", fake_complete_chat)

    resp = client.post(
        "/chat",
        json={
            "session_id": "s-standing",
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "message": "hello",
            "provider": "openai",
            "history": [],
            "enable_ledger": True,
            "standing_envelope": {
                "retrieval_scope": "none",
                "write_commit_allowed": False,
                "max_output_tokens": 128,
                "tool_scope": "none",
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert captured.get("assemble_called") is False
    assert captured.get("max_tokens") == 128
    coordinate = payload.get("coordinate")
    assert isinstance(coordinate, str)
    assert payload.get("memories_used") == 0
    assert payload.get("unverified") is True
    store = LedgerStoreV2(client.app.state.db)
    assert store.read(coordinate) is None


def test_enrich_rejects_writes_when_standing_denies_commit() -> None:
    client = _make_client()
    resp = client.post(
        "/enrich",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "role": "assistant",
            "content": "blocked write",
            "kind": "text",
            "metadata": {
                "standing_envelope": {
                    "write_commit_allowed": False,
                    "retrieval_scope": "tenant",
                    "tool_scope": "restricted",
                }
            },
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail") if isinstance(resp.json(), dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("error") == "standing_write_commit_denied"


def test_commit_answer_rejects_writes_when_standing_denies_commit() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "standing_envelope": {
                    "write_commit_allowed": False,
                    "retrieval_scope": "tenant",
                    "tool_scope": "restricted",
                }
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail") if isinstance(resp.json(), dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("error") == "standing_write_commit_denied"


def test_chat_authority_state_overrides_permissive_standing_envelope(monkeypatch) -> None:
    client = _make_client()
    captured: dict[str, object] = {"assemble_called": False}
    _upsert_issuer(
        client,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction", "decay"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
        identity_anchor_ref="anchor:eq9",
        trust_basis="local_registry",
        verification_state="anchored",
    )
    append_authority_event(
        client.app.state.db,
        authority_subject_id="subject:openrouter:model:anthropic/claude-3.7-sonnet",
        event_type="sanction",
        issuer="deterministic:eq9",
        reason_code="eq_blocked:eq9_telos",
        delta={"trust_class": "T0", "posture_class": "P0"},
        evidence_refs=["coord:WX-auth-1"],
        idempotency_key="evt-auth-1",
        canonical_subject="openrouter:model:anthropic/claude-3.7-sonnet",
    )

    async def fake_assemble_context(**_kwargs):
        captured["assemble_called"] = True
        return {"retrieved": [{"coord": "chat-team-b:WX-1"}], "assessments": {}}

    class _Usage:
        prompt_tokens = 5
        completion_tokens = 3

    async def fake_complete_chat(*, provider, messages, max_tokens, log_label=None):
        captured["max_tokens"] = max_tokens
        return "assistant reply", 0.01, 10.0, _Usage(), "stop"

    monkeypatch.setattr(chat_api, "assemble_context", fake_assemble_context)
    monkeypatch.setattr(chat_api, "complete_chat", fake_complete_chat)

    resp = client.post(
        "/chat",
        json={
            "session_id": "s-authority-state",
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "message": "hello",
            "provider": "openai",
            "history": [],
            "enable_ledger": True,
            "standing_envelope": {
                "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
                "canonical_subject_source": "binding:openrouter:model",
                "retrieval_scope": "tenant",
                "write_commit_allowed": True,
                "max_output_tokens": 1024,
                "tool_scope": "standard",
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert captured.get("assemble_called") is False
    assert captured.get("max_tokens") == 256
    assert payload.get("memories_used") == 0
    coordinate = payload.get("coordinate")
    assert isinstance(coordinate, str)
    store = LedgerStoreV2(client.app.state.db)
    assert store.read(coordinate) is None


def test_enrich_authority_state_rejects_write_despite_permissive_envelope() -> None:
    client = _make_client()
    _upsert_issuer(
        client,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction", "decay"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
        identity_anchor_ref="anchor:eq9",
        trust_basis="local_registry",
        verification_state="anchored",
    )
    append_authority_event(
        client.app.state.db,
        authority_subject_id="subject:ollama:model:llama3.2:latest",
        event_type="sanction",
        issuer="deterministic:eq9",
        reason_code="eq_blocked:eq9_telos",
        delta={"trust_class": "T0", "posture_class": "P0"},
        evidence_refs=["coord:WX-auth-2"],
        idempotency_key="evt-auth-2",
        canonical_subject="ollama:model:llama3.2:latest",
    )
    resp = client.post(
        "/enrich",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "role": "assistant",
            "content": "blocked write",
            "kind": "text",
            "metadata": {
                "standing_envelope": {
                    "canonical_subject": "ollama:model:llama3.2:latest",
                    "canonical_subject_source": "binding:ollama:model",
                    "write_commit_allowed": True,
                    "retrieval_scope": "tenant",
                    "tool_scope": "standard",
                }
            },
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail") if isinstance(resp.json(), dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("error") == "standing_write_commit_denied"
    policy = detail.get("standing_policy") if isinstance(detail.get("standing_policy"), dict) else {}
    assert policy.get("source") == "metadata+authority_state"


def test_commit_answer_authority_state_rejects_write_despite_permissive_envelope() -> None:
    client = _make_client()
    _upsert_issuer(
        client,
        issuer="deterministic:eq9",
        issuer_class="deterministic_system",
        allowed_event_types=["sanction", "decay"],
        credential_ref="cred:issuer:eq9",
        issuer_did="did:web:eq9.example",
        identity_anchor_ref="anchor:eq9",
        trust_basis="local_registry",
        verification_state="anchored",
    )
    append_authority_event(
        client.app.state.db,
        authority_subject_id="subject:openrouter:model:anthropic/claude-3.7-sonnet",
        event_type="sanction",
        issuer="deterministic:eq9",
        reason_code="eq_blocked:eq9_telos",
        delta={"trust_class": "T0", "posture_class": "P0"},
        evidence_refs=["coord:WX-auth-3"],
        idempotency_key="evt-auth-3",
        canonical_subject="openrouter:model:anthropic/claude-3.7-sonnet",
    )
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "standing_envelope": {
                    "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
                    "canonical_subject_source": "binding:openrouter:model",
                    "write_commit_allowed": True,
                    "retrieval_scope": "tenant",
                    "tool_scope": "standard",
                }
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail") if isinstance(resp.json(), dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("error") == "standing_write_commit_denied"


def test_ingest_persists_canonical_provenance_fields() -> None:
    client = _make_client()
    headers = {
        "x-ledger-id": "chat-team-b",
        "x-principal-id": "alice",
        "x-principal-type": "user",
        "x-context-id": "ctx:test",
    }
    resp = client.post(
        "/ingest",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": "hello",
            "kind": "text",
            "metadata": {"provider": "openrouter", "model": "gpt-4o-mini"},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    assert metadata.get("ledger_id") == "chat-team-b"
    assert metadata.get("contributor_id") == "user:alice"
    assert metadata.get("context_id") == "ctx:test"
    assert metadata.get("provider_id") == "openrouter"
    assert metadata.get("model_id") == "gpt-4o-mini"
    assert metadata.get("session_id") == "s1"
    assert metadata.get("turn_id") == "t1"
    dual = metadata.get("provenance_dual_write") if isinstance(metadata.get("provenance_dual_write"), dict) else {}
    assert dual.get("status") == "legacy_only"


def test_commit_answer_persists_canonical_provenance_fields() -> None:
    client = _make_client()
    headers = {
        "x-ledger-id": "chat-team-b",
        "x-principal-id": "alice",
        "x-principal-type": "user",
        "x-context-id": "ctx:test",
    }
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "session_id": "s1",
                "turn_id": "t1",
                "provider": "openrouter",
                "model": "gpt-4o-mini",
            },
        },
        headers=headers,
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    assert metadata.get("ledger_id") == "chat-team-b"
    assert metadata.get("contributor_id") == "user:alice"
    assert metadata.get("context_id") == "ctx:test"
    assert metadata.get("provider_id") == "openrouter"
    assert metadata.get("model_id") == "gpt-4o-mini"
    assert metadata.get("session_id") == "s1"
    assert metadata.get("turn_id") == "t1"
    dual = metadata.get("provenance_dual_write") if isinstance(metadata.get("provenance_dual_write"), dict) else {}
    assert dual.get("status") == "legacy_only"


def test_commit_answer_payload_claims_persist_provenance_fields() -> None:
    client = _make_client()
    headers = {
        "x-ledger-id": "chat-team-b",
        "x-principal-id": "alice",
        "x-principal-type": "user",
    }
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "principal_did": "did:key:z6MkPayload",
            "principal_key_id": "did:key:z6MkPayload#k1",
            "session_jti": "jti-payload",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "session_id": "s1",
                "turn_id": "t1",
                "provider": "openrouter",
                "model": "gpt-4o-mini",
            },
        },
        headers=headers,
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    contributor = metadata.get("contributor") if isinstance(metadata.get("contributor"), dict) else {}
    assert metadata.get("ledger_id") == "chat-team-b"
    assert metadata.get("contributor_id") == "user:alice"
    assert metadata.get("context_id") == "ctx:test"
    assert contributor.get("principal_did") == "did:key:z6MkPayload"
    assert contributor.get("principal_key_id") == "did:key:z6MkPayload#k1"
    assert contributor.get("session_jti") == "jti-payload"
    dual = metadata.get("provenance_dual_write") if isinstance(metadata.get("provenance_dual_write"), dict) else {}
    assert dual.get("status") == "dual_write_ok"


def test_commit_answer_persists_canonical_subject_authority_fields() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "principal_did": "did:key:z6MkCanonicalSubject",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "session_id": "s1",
                "turn_id": "t1",
                "model_auth_context": {
                    "identity_vc": {
                        "principal_did": "did:key:z6MkCanonicalSubject",
                        "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
                        "canonical_subject_source": "binding:openrouter:model",
                    },
                    "standing_envelope": {
                        "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
                        "canonical_subject_source": "binding:openrouter:model",
                    },
                },
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    contributor = metadata.get("contributor") if isinstance(metadata.get("contributor"), dict) else {}
    assert metadata.get("canonical_subject") == "openrouter:model:anthropic/claude-3.7-sonnet"
    assert metadata.get("canonical_subject_source") == "binding:openrouter:model"
    assert metadata.get("authority_subject_id") == "subject:openrouter:model:anthropic/claude-3.7-sonnet"
    assert contributor.get("canonical_subject") == "openrouter:model:anthropic/claude-3.7-sonnet"
    assert contributor.get("canonical_subject_source") == "binding:openrouter:model"
    assert contributor.get("authority_subject_id") == "subject:openrouter:model:anthropic/claude-3.7-sonnet"


def test_enrich_persists_canonical_subject_authority_fields() -> None:
    client = _make_client()
    resp = client.post(
        "/enrich",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "role": "assistant",
            "content": "authority subject write",
            "kind": "text",
            "metadata": {
                "standing_envelope": {
                    "canonical_subject": "ollama:model:llama3.2:latest",
                    "canonical_subject_source": "binding:ollama:model",
                }
            },
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    contributor = metadata.get("contributor") if isinstance(metadata.get("contributor"), dict) else {}
    assert metadata.get("canonical_subject") == "ollama:model:llama3.2:latest"
    assert metadata.get("canonical_subject_source") == "binding:ollama:model"
    assert metadata.get("authority_subject_id") == "subject:ollama:model:llama3.2:latest"
    assert contributor.get("canonical_subject") == "ollama:model:llama3.2:latest"
    assert contributor.get("authority_subject_id") == "subject:ollama:model:llama3.2:latest"


def test_commit_answer_rejects_subject_authority_change_without_transition_event() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "principal_did": "did:key:z6MkTransitionA",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "prior_authority_subject_id": "subject:openrouter:model:anthropic/claude-3.5-sonnet",
                "standing_envelope": {
                    "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
                    "canonical_subject_source": "binding:openrouter:model",
                },
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 409
    detail = resp.json().get("detail") if isinstance(resp.json(), dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("error") == "subject_authority_transition_unverified"


def test_commit_answer_persists_subject_transition_review_metadata_when_event_ref_present() -> None:
    client = _make_client()
    event = append_subject_event(
        client.app.state.db,
        event_type="subject_reset_requested",
        issuer="operator:test",
        prior_authority_subject_id="subject:openrouter:model:anthropic/claude-3.5-sonnet",
        resulting_authority_subject_id="subject:openrouter:model:anthropic/claude-3.7-sonnet",
        canonical_subject="openrouter:model:anthropic/claude-3.7-sonnet",
        standing_carryover="probation",
        credential_carryover="review_required",
        event_id="subevt:123",
    )
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "principal_did": "did:key:z6MkTransitionB",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "prior_authority_subject_id": "subject:openrouter:model:anthropic/claude-3.5-sonnet",
                "subject_transition_type": "subject_reset_requested",
                "subject_transition_event_ref": "subevt:123",
                "standing_envelope": {
                    "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
                    "canonical_subject_source": "binding:openrouter:model",
                },
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 200
    coordinate = resp.json().get("coordinate")
    assert isinstance(coordinate, str)
    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    metadata = entry.state.metadata or {}
    assert event.get("event_id") == "subevt:123"
    assert metadata.get("authority_subject_id") == "subject:openrouter:model:anthropic/claude-3.7-sonnet"
    assert metadata.get("prior_authority_subject_id") == "subject:openrouter:model:anthropic/claude-3.5-sonnet"
    assert metadata.get("subject_transition_type") == "subject_reset_requested"
    assert metadata.get("subject_transition_event_ref") == "subevt:123"
    assert metadata.get("subject_transition_event_validated") is True
    assert metadata.get("subject_transition_review_required") is True
    assert metadata.get("standing_carryover") == "probation"
    assert metadata.get("credential_carryover") == "review_required"


def test_commit_answer_rejects_unresolved_subject_transition_event_ref() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "principal_did": "did:key:z6MkTransitionC",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "prior_authority_subject_id": "subject:openrouter:model:anthropic/claude-3.5-sonnet",
                "subject_transition_type": "subject_reset_requested",
                "subject_transition_event_ref": "subevt:missing",
                "standing_envelope": {
                    "canonical_subject": "openrouter:model:anthropic/claude-3.7-sonnet",
                    "canonical_subject_source": "binding:openrouter:model",
                },
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 409
    detail = resp.json().get("detail") if isinstance(resp.json(), dict) else {}
    assert isinstance(detail, dict)
    assert detail.get("error") == "subject_authority_transition_unverified"




def test_commit_answer_publishes_decision_artifact_identity_as_public_object() -> None:
    client = _make_client()
    untp_hash = "sha256:test-decision-artifact"
    public_object_id = f"https://id.example/o/decision-artifact/{untp_hash}"
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "resolved_coords": ["chat-demo:WX-1", "chat-demo:ATT-2"],
                "decision_artifact_identity": {
                    "schema": "dss-decision-artifact-identity-v1",
                    "public_object_kind": "decision-artifact",
                    "public_object_id": public_object_id,
                    "untp_hash": untp_hash,
                    "object_id": untp_hash,
                    "publication_state": "identity_defined_not_published",
                    "coord_bridge": {
                        "coord_ref": None,
                        "coord_exposed_as_primary": False,
                        "bridge_state": "coord_assigned_post_commit",
                    },
                    "canonical_envelope": {
                        "schema": "dss-decision-artifact-envelope-v1",
                    },
                },
            },
        },
        headers={"x-ledger-id": "chat-team-b", "x-context-id": "ctx:test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    coordinate = body.get("coordinate")
    assert isinstance(coordinate, str)
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    identity = metadata.get("decision_artifact_identity") if isinstance(metadata.get("decision_artifact_identity"), dict) else {}
    assert identity.get("publication_state") == "published"
    assert identity.get("status_ref") == f"{public_object_id}/status"
    coord_bridge = identity.get("coord_bridge") if isinstance(identity.get("coord_bridge"), dict) else {}
    assert coord_bridge.get("coord_ref") == coordinate
    assert coord_bridge.get("bridge_state") == "coord_assigned"

    store = LedgerStoreV2(client.app.state.db)
    entry = store.read(coordinate)
    assert entry is not None
    stored_metadata = entry.state.metadata or {}
    stored_identity = stored_metadata.get("decision_artifact_identity") if isinstance(stored_metadata.get("decision_artifact_identity"), dict) else {}
    assert stored_identity.get("publication_state") == "published"
    assert stored_identity.get("status_ref") == f"{public_object_id}/status"
    assert stored_identity.get("coord_bridge", {}).get("coord_ref") == coordinate

    public_doc = client.get(f"/public/objects/decision-artifact/{untp_hash}")
    assert public_doc.status_code == 200
    public_body = public_doc.json()
    assert public_body.get("object_kind") == "decision-artifact"
    assert public_body.get("object_id") == untp_hash
    assert public_body.get("content_digest") == untp_hash
    assert public_body.get("coord_ref_withheld") is True
    public_identity = public_body.get("decision_artifact_identity") if isinstance(public_body.get("decision_artifact_identity"), dict) else {}
    assert public_identity.get("publication_state") == "published"
    assert public_identity.get("coord_bridge", {}).get("coord_ref") == coordinate

    status_doc = client.get(f"/public/objects/decision-artifact/{untp_hash}/status")
    assert status_doc.status_code == 200
    assert status_doc.json().get("subject", {}).get("object_id") == untp_hash


def test_decision_artifact_replay_export_preserves_base_record_and_overlays() -> None:
    client = _make_client()
    untp_hash = "sha256:test-decision-replay"
    public_object_id = f"https://id.example/o/decision-artifact/{untp_hash}"
    base_identity = {
        "schema": "dss-decision-artifact-identity-v1",
        "public_object_kind": "decision-artifact",
        "public_object_id": public_object_id,
        "untp_hash": untp_hash,
        "object_id": untp_hash,
        "publication_state": "published",
        "canonical_envelope": {
            "schema": "dss-decision-artifact-envelope-v1",
            "reply_contract": {"reply_text": "base answer"},
        },
    }
    first = upsert_public_object(
        client.app.state.db,
        public_object_id=public_object_id,
        object_kind="decision-artifact",
        object_id=untp_hash,
        subject_id="subject:test",
        issuer_id="issuer:test",
        content_digest=untp_hash,
        coord_ref="chat-demo:WX-base",
        evidence_refs=["chat-demo:WX-evidence"],
        status_ref=f"{public_object_id}/status",
        lifecycle_state="current",
        shareability="share-ready",
        artifact_identity=base_identity,
    )
    immutable_base = first.get("immutable_base_record")
    assert isinstance(immutable_base, dict)
    assert immutable_base.get("coord_ref") == "chat-demo:WX-base"
    assert first.get("overlay_events") == []

    updated_identity = {
        **base_identity,
        "artifact_overlay": {
            "kind": "governance_enrich",
            "reason": "late verifier annotation",
        },
    }
    updated = upsert_public_object(
        client.app.state.db,
        public_object_id=public_object_id,
        object_kind="decision-artifact",
        object_id=untp_hash,
        subject_id="subject:test",
        issuer_id="issuer:test",
        content_digest=untp_hash,
        coord_ref="chat-demo:WX-base",
        evidence_refs=["chat-demo:WX-evidence"],
        status_ref=f"{public_object_id}/status",
        lifecycle_state="superseded",
        superseded_by="https://id.example/o/decision-artifact/sha256:test-decision-replay-v2",
        shareability="share-ready",
        artifact_identity=updated_identity,
    )

    assert updated.get("immutable_base_record") == immutable_base
    events = updated.get("overlay_events")
    assert isinstance(events, list) and len(events) == 1
    event = events[0]
    assert event.get("schema") == "dss-decision-record-overlay-event-v1"
    assert event.get("event_type") == "supersession_v1"
    assert event.get("seq") == 1
    assert "lifecycle_state" in event.get("changed_fields", [])
    assert "superseded_by" in event.get("changed_fields", [])
    assert event.get("patch", {}).get("lifecycle_state") == "superseded"

    doc = client.get(f"/public/objects/decision-artifact/{untp_hash}")
    assert doc.status_code == 200
    replay_summary = doc.json().get("decision_record_replay")
    assert isinstance(replay_summary, dict)
    assert replay_summary.get("overlay_event_count") == 1
    assert replay_summary.get("append_only_scope") == "overlay_events_only"

    replay = client.get(f"/public/objects/decision-artifact/{untp_hash}/replay")
    assert replay.status_code == 200
    replay_body = replay.json()
    assert replay_body.get("schema") == "dss-decision-record-replay-v1"
    assert replay_body.get("base_record") == immutable_base
    assert replay_body.get("overlay_events", [])[0].get("event_type") == "supersession_v1"
    assert replay_body.get("current_effective_state", {}).get("lifecycle_state") == "superseded"
    contract = replay_body.get("replay_contract")
    assert isinstance(contract, dict)
    assert contract.get("immutable_base_record") is True
    assert contract.get("current_materialized_view_mutable") is True


def test_commit_answer_returns_eval_contract_when_e6_fields_present() -> None:
    client = _make_client()
    resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {
                "e6_header_v0_fields": {
                    "mode": 2,
                    "route": 2,
                    "K": 1,
                    "P": 1,
                    "E": 1,
                    "V_q": 65535,
                    "dW": 0,
                },
                "eq9_target": {"score_min": 0.95},
                "gen_output_tokens": 128,
                "appraisal": {"law_score": 1.0, "grace_score": 1.0},
            },
        },
        headers={"x-ledger-id": "chat-team-b"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    contract = payload.get("eval_contract")
    assert isinstance(contract, dict)
    assert contract.get("commit_allowed") is True
    assert contract.get("failed_eq") is None
    posture = payload.get("posture_policy")
    assert isinstance(posture, dict)
    assert posture.get("policy_decision") in {"allow", "degrade"}


def test_stats_provenance_observability_reports_dual_write_parity() -> None:
    client = _make_client()
    legacy_headers = {
        "x-ledger-id": "chat-team-b",
        "x-principal-id": "alice",
        "x-principal-type": "user",
        "x-context-id": "ctx:test",
    }
    legacy_resp = client.post(
        "/ingest",
        json={
            "entity": "chat-team-a",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": "hello",
            "kind": "text",
            "metadata": {},
        },
        headers=legacy_headers,
    )
    assert legacy_resp.status_code == 200

    did_resp = client.post(
        "/api/chat/commit-answer",
        json={
            "entity": "chat-team-a",
            "ledger_id": "chat-team-b",
            "context_id": "ctx:test",
            "principal_did": "did:key:z6MkPayloadObs",
            "principal_key_id": "did:key:z6MkPayloadObs#k1",
            "session_jti": "jti-payload-obs",
            "user_message": "u",
            "assistant_reply": "a",
            "metadata": {"session_id": "s1", "turn_id": "t2"},
        },
        headers={
            "x-ledger-id": "chat-team-b",
            "x-principal-id": "alice",
            "x-principal-type": "user",
        },
    )
    assert did_resp.status_code == 200

    obs = client.get("/stats/observability/provenance")
    assert obs.status_code == 200
    payload = obs.json()
    status_counts = payload.get("status_counts") if isinstance(payload.get("status_counts"), dict) else {}
    assert int(status_counts.get("legacy_only") or 0) >= 1
    assert int(status_counts.get("dual_write_ok") or 0) >= 1
