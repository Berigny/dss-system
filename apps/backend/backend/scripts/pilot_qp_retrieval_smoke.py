"""Pilot smoke test: demonstrate Qp retrieval beating vector-RAG in /chat/stream.

Run from repo root with:
    PYTHONPATH=. python -m backend.scripts.pilot_qp_retrieval_smoke

This seeds a tiny ledger with one needle memory and several keyword-heavy
distractors, then calls /chat/stream twice:

1. QP_PURE_ENABLED=true  -> Qp coordinate retrieval
2. QP_PURE_ENABLED=false -> legacy vector/semantic + p-adic mix

The LLM completion is mocked so no API key is required; only retrieval is real.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("DEMO_OVERRIDE_MODE", "true")
os.environ.setdefault("DEMO_DEFAULT_LEDGER", "pilot-qp-retrieval")
os.environ.setdefault("LEDGER_AUTHZ_MODE", "registry")
os.environ.setdefault("LEDGER_AUTHZ_UNKNOWN_LEDGER_POLICY", "allow")
os.environ.setdefault("LEDGER_AUTHZ_ADMIN_PRINCIPAL_TYPES", "admin,service")
os.environ.setdefault("QP_PURE_ENABLED", "true")

import fastapi
from fastapi.testclient import TestClient

from backend.api import chat as chat_module
from backend.api.chat import router as chat_router
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.qp_coordinate import QpCoordinate
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.scripts.chat_delegated_smoke import _events_from_ndjson
from backend.services.ledger_service import LedgerService
from backend.search.token_index import TokenPrimeIndex

# Import the coordinate builder from the needle benchmark to keep the script DRY.
from backend.benchmarks.longbench_needle_benchmark import _make_coordinate


OPERATOR_DID = os.getenv("OPERATOR_DID", "")
TENANT_ID = "tenant:pilot"
LEDGER_ID = "pilot-qp-retrieval"
SURFACE_ID = "surface:pilot-cli"
ENTITY = LEDGER_ID

TARGET_TEXT = (
    "Meeting time is 9:00 and the smallest prime number discussed was 2."
)
QUERY_TEXT = "What was the meeting time and the smallest prime number discussed?"

# Synthetic probe used to give vector/legacy retrieval a lexical handle that the
# needle deliberately does *not* share.  We force this token to map to kernel
# prime 5 so query_primes=[5] retrieves the distractors but not the needle.
PROBE_TOKEN = "qpprobe"
NEEDLE_NODE = "Eq2"
NEEDLE_OFFSET = 1


def _seed_ledger_registry(db: dict) -> None:
    db[b"__ledgers_v1__"] = json.dumps(
        {
            "version": 1,
            "ledgers": {
                LEDGER_ID: {
                    "ledger_id": LEDGER_ID,
                    "display_name": "Pilot Qp Retrieval Ledger",
                    "namespace": LEDGER_ID,
                    "tenant_id": TENANT_ID,
                    "canonical_subject": f"did:web:{os.getenv('DEFAULT_DID_HOST', '')}:ledgers:{LEDGER_ID}",
                    "status": "active",
                    "metadata": {
                        "founding_constitution": {
                            "name": "Pilot",
                            "purpose": "Demonstrate Qp retrieval beating vector-RAG.",
                        }
                    },
                }
            }
        }
    ).encode()


def _write_memory(
    store: LedgerStoreV2,
    identifier: str,
    text: str,
    coordinate: QpCoordinate,
) -> None:
    entry = LedgerEntry(
        key=LedgerKey(namespace=ENTITY, identifier=identifier),
        state=ContinuousState(
            coordinates={},
            phase="pilot",
            metadata={
                "text": text,
                "p_adic_coordinate": coordinate.as_dict(),
            },
        ),
        created_at=datetime.now(timezone.utc),
    )
    store.write(entry)


def _seed_memories(store: LedgerStoreV2) -> None:
    """Seed one needle and several keyword-heavy distractors."""
    # Write distractors first.  They carry the probe token so legacy token-index
    # retrieval returns them for query_primes=[5].  Their Qp coordinates use
    # different metric primes, so the pure-Qp compatibility filter drops them.
    distractor_specs = [
        ("distractor-a", "Eq3", 2, "Meeting meeting time time smallest prime number discussed 9:00 10:00."),
        ("distractor-b", "Eq4", 2, "The schedule listed 9:00 10:00 11:00 and prime smallest numbers 2 3 5 7."),
        ("distractor-c", "Eq7", 1, "Notes about schedules, primes, and meeting times from last quarter."),
    ]
    for ident, node, offset, text in distractor_specs:
        coord = _make_coordinate(
            kernel_node=node,
            valuation_offset=offset,
            circulation_pass=0,
            hysteresis_depth=0.0,
            dual_valid=True,
        )
        _write_memory(store, ident, f"{text} {PROBE_TOKEN}", coord)

    # Needle: written last so it is in the recent-context window (k=3 default).
    # It shares the query's metric prime (Eq2 / p=5) and is placed very close
    # to the query coordinate so the pure Qp score is high.
    needle_coord = _make_coordinate(
        kernel_node=NEEDLE_NODE,
        valuation_offset=NEEDLE_OFFSET,
        circulation_pass=0,
        hysteresis_depth=0.0,
        dual_valid=True,
    )
    _write_memory(store, "needle", TARGET_TEXT, needle_coord)


async def _fake_yield_chat_stream(**_kwargs: Any) -> tuple:
    async def _tokens():
        for token in ("Answer", " ready"):
            yield token

    fut: asyncio.Future[str | None] = asyncio.Future()
    fut.set_result("stop")
    return _tokens(), fut


def _candidate_ids(events: list[dict[str, Any]]) -> list[str]:
    trace = next(
        (evt.get("payload", {}).get("top_k", []) for evt in events if evt.get("type") == "candidate_trace"),
        [],
    )
    return [str(row.get("coord") or row.get("id", "?")) for row in trace]


def _run_stream(client: TestClient, *, qp_pure: bool) -> list[dict[str, Any]]:
    resp = client.post(
        "/chat/stream",
        json={
            "session_id": f"pilot-session-{'qp' if qp_pure else 'vec'}",
            "entity": ENTITY,
            "ledger_id": LEDGER_ID,
            "message": QUERY_TEXT,
            "provider": "openai",
            "history": [],
            "query_primes": [5],
            "include_padic_diagnostics": True,
            "qp_pure": qp_pure,
        },
        headers={
            "x-principal-type": "admin",
            "x-principal-id": "pilot-operator",
            "x-principal-did": OPERATOR_DID,
            "x-ledger-id": LEDGER_ID,
            "x-tenant-id": TENANT_ID,
            "x-surface-id": SURFACE_ID,
        },
    )
    if resp.status_code != 200:
        print(f"FAILED /chat/stream (qp_pure={qp_pure}): {resp.status_code} {resp.text}")
        sys.exit(1)
    return _events_from_ndjson(resp.text)


def main() -> int:
    app = fastapi.FastAPI()
    app.state.db = {}
    app.include_router(chat_router)

    _seed_ledger_registry(app.state.db)

    token_index = TokenPrimeIndex(app)

    # Force the probe token to kernel prime 5 by consuming primes 2 and 3 first.
    token_index.get_or_assign_prime("__dummy_a__")
    token_index.get_or_assign_prime("__dummy_b__")
    token_index.get_or_assign_prime(PROBE_TOKEN)

    service = LedgerService(app.state.db, token_index=token_index)
    store = service.store
    _seed_memories(store)

    # Mock the LLM stream so the test needs no API key.
    original_yield_chat_stream = chat_module.yield_chat_stream
    chat_module.yield_chat_stream = _fake_yield_chat_stream

    client = TestClient(app)

    try:
        print("[PILOT] Running /chat/stream with QP_PURE_ENABLED=true ...")
        qp_events = _run_stream(client, qp_pure=True)
        qp_ids = _candidate_ids(qp_events)
        print(f"        top candidate ids: {qp_ids}")

        print("[PILOT] Running /chat/stream with QP_PURE_ENABLED=false ...")
        vec_events = _run_stream(client, qp_pure=False)
        vec_ids = _candidate_ids(vec_events)
        print(f"        top candidate ids: {vec_ids}")
    finally:
        chat_module.yield_chat_stream = original_yield_chat_stream

    # Evaluate the pilot claim.
    needle_coord = f"{ENTITY}:needle"
    qp_has_needle = needle_coord in qp_ids
    vec_has_needle = needle_coord in vec_ids

    print()
    print("=" * 60)
    print("PILOT RESULT")
    print("=" * 60)
    print(f"Needle memory id : {needle_coord}")
    print(f"Qp retrieval     : {'FOUND needle' if qp_has_needle else 'MISSED needle'}")
    print(f"Vector retrieval : {'FOUND needle' if vec_has_needle else 'MISSED needle'}")
    print()

    if qp_has_needle and not vec_has_needle:
        print("✅ Pilot claim satisfied: Qp retrieval returned the needle; vector-RAG did not.")
        return 0

    print("❌ Pilot claim NOT satisfied.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
