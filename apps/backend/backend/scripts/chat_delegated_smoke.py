"""Manual smoke test for the chat surface using the delegated Codex principal.

Run from repo root with:
    PYTHONPATH=. python -m backend.scripts.chat_delegated_smoke

This script is intentionally standalone and self-mocking so it does not require
an OpenAI API key or external services.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("LEDGER_AUTHZ_MODE", "registry")
os.environ.setdefault("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "deny")
os.environ.setdefault("LEDGER_AUTHZ_ADMIN_PRINCIPAL_TYPES", "admin,service")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.admin import control_plane_router
from backend.api.auth import router as auth_router
from backend.api import chat as chat_module
from backend.api.chat import router as chat_router
from backend.services.ledger_service import LedgerService
from backend.search.token_index import TokenPrimeIndex


OPERATOR_DID = os.getenv("OPERATOR_DID", "")
TENANT_ID = "tenant:smoke"
LEDGER_ID = "chat-smoke-delegated"
SURFACE_ID = "surface:codex-cli"


def _seed_ledger(db: dict) -> None:
    db[b"__ledgers_v1__"] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                LEDGER_ID: {
                    "ledger_id": LEDGER_ID,
                    "display_name": "Delegated Smoke Ledger",
                    "namespace": LEDGER_ID,
                    "tenant_id": TENANT_ID,
                    "canonical_subject": f"did:web:{os.getenv('DEFAULT_DID_HOST', '')}:ledgers:{LEDGER_ID}",
                    "status": "active",
                    "metadata": {
                        "founding_constitution": {
                            "name": "Smoke",
                            "purpose": "Delegated principal chat smoke tests.",
                        }
                    },
                }
            }
        }
    ).encode()


def _admin_headers() -> dict[str, str]:
    return {
        "x-admin-token": os.environ["ADMIN_TOKEN"],
        "x-principal-type": "admin",
        "x-principal-id": "smoke-operator",
        "x-principal-did": OPERATOR_DID,
    }


def _patch_chat_for_smoke() -> None:
    async def _fake_assemble_context(**_kwargs: Any) -> dict[str, Any]:
        return {"recent": [], "claims": [], "retrieved": [], "assessments": {}}

    async def _fake_stream(**_kwargs: Any) -> tuple:
        async def _tokens():
            for token in ("Delegated", " response"):
                yield token

        fut: asyncio.Future[str] = asyncio.Future()
        fut.set_result("stop")
        return _tokens(), fut

    chat_module.assemble_context = _fake_assemble_context
    chat_module.yield_chat_stream = _fake_stream


def _events_from_ndjson(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def main() -> int:
    app = FastAPI()
    app.state.db = {}
    app.include_router(control_plane_router)
    app.include_router(auth_router)
    app.include_router(chat_router)

    _seed_ledger(app.state.db)
    client = TestClient(app)
    headers = _admin_headers()

    print("[1] Provisioning Codex delegated principal...")
    provision = client.post(
        "/api/control-plane/principals/codex/provision",
        json={
            "tenant_id": TENANT_ID,
            "ledger_id": LEDGER_ID,
            "surface_ids": [SURFACE_ID],
            "delegated_by_principal_did": OPERATOR_DID,
            "display_name": "Codex Delegated Smoke",
        },
        headers=headers,
    )
    if provision.status_code != 200:
        print("FAILED provision:", provision.status_code, provision.text)
        return 1
    principal = provision.json()["principal"]
    codex_did = principal["principal_did"]
    print("   -> codex DID:", codex_did)
    print("   -> delegated_by:", principal["metadata"]["delegated_authority"]["delegated_by_principal_did"])

    print("[2] Issuing session token for Codex principal...")
    token_resp = client.post(
        "/auth/token",
        json={"principal_did": codex_did, "ttl_seconds": 600},
        headers=headers,
    )
    if token_resp.status_code != 200:
        print("FAILED token:", token_resp.status_code, token_resp.text)
        return 1
    token = token_resp.json()["session"]["token"]
    print("   -> token issued")

    print("[3] Calling /chat/stream with delegated principal headers...")
    _patch_chat_for_smoke()
    stream = client.post(
        "/chat/stream",
        json={
            "session_id": "smoke-session-1",
            "entity": LEDGER_ID,
            "ledger_id": LEDGER_ID,
            "message": "hello via delegated principal",
            "provider": "openai",
            "history": [],
        },
        headers={
            "authorization": f"Bearer {token}",
            "x-principal-type": "agent",
            "x-principal-id": "codex",
            "x-principal-did": codex_did,
            "x-ledger-id": LEDGER_ID,
            "x-tenant-id": TENANT_ID,
            "x-delegated-cli-request": "true",
            "x-delegated-by-principal-did": OPERATOR_DID,
            "x-delegated-ledger-scope": LEDGER_ID,
            "x-surface-id": SURFACE_ID,
        },
    )
    if stream.status_code != 200:
        print("FAILED chat stream:", stream.status_code, stream.text)
        return 1

    events = _events_from_ndjson(stream.text)
    token_parts = [str(evt.get("content") or "") for evt in events if evt.get("type") == "token"]
    context_meta_event = next((evt for evt in events if evt.get("type") == "context_meta"), {})
    meta_event = next((evt for evt in events if evt.get("type") == "meta"), {})

    print("   -> stream status:", stream.status_code)
    print("   -> tokens:", "".join(token_parts))

    streamed_delegated = context_meta_event.get("delegated_prompt_path") if isinstance(context_meta_event, dict) else None
    print("   -> context_meta.delegated_prompt_path active:", bool(streamed_delegated))
    if streamed_delegated:
        print("   -> streamed requested_by_principal_did:", streamed_delegated.get("requested_by_principal_did"))

    authz = meta_event.get("authz") if isinstance(meta_event, dict) else None
    print("   -> authz.delegated_prompt_path_active:", authz.get("delegated_prompt_path_active") if isinstance(authz, dict) else None)

    coordinate = meta_event.get("coordinate") if isinstance(meta_event, dict) else None
    persisted_delegated = None
    if coordinate:
        token_index = TokenPrimeIndex(app)
        store = LedgerService(app.state.db, token_index=token_index).store
        entry = store.read(coordinate)
        if entry is not None:
            persisted_delegated = (entry.state.metadata or {}).get("delegated_prompt_path")

    print("   -> persisted delegated_prompt_path:", bool(persisted_delegated))
    if persisted_delegated:
        print("   -> persisted requested_by_principal_did:", persisted_delegated.get("requested_by_principal_did"))
        print("   -> persisted target_ledger_id:", persisted_delegated.get("target_ledger_id"))
        print("   -> persisted target_surface_id:", persisted_delegated.get("target_surface_id"))
        print("   -> persisted ledger_scope:", persisted_delegated.get("ledger_scope"))

    if not streamed_delegated or not streamed_delegated.get("active"):
        print("FAILED: context_meta did not surface delegated_prompt_path")
        return 1
    if streamed_delegated.get("requested_by_principal_did") != OPERATOR_DID:
        print("FAILED: streamed requested_by_principal_did mismatch")
        return 1

    if not authz or not authz.get("delegated_prompt_path_active"):
        print("FAILED: authz did not activate delegated prompt path")
        return 1
    if not persisted_delegated:
        print("FAILED: delegated_prompt_path was not persisted to ledger metadata")
        return 1
    if persisted_delegated.get("requested_by_principal_did") != OPERATOR_DID:
        print("FAILED: persisted requested_by_principal_did mismatch")
        return 1
    if not persisted_delegated.get("target_ledger_id"):
        print("FAILED: persisted target_ledger_id missing")
        return 1

    print("\n✅ Delegated chat surface smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
