"""End-to-end integration tests for the Epic 26 blob + projection pipeline."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ingest import router as ingest_router
from backend.api.resolver import router as resolver_router
from backend.fieldx_kernel.orchestrator import assemble_context


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.db = {}
    app.include_router(ingest_router)
    app.include_router(resolver_router)
    return TestClient(app)


def test_full_attachment_ingest_blob_and_projection_resolve() -> None:
    client = _make_client()
    headers = {"x-ledger-id": "epic26-ns"}
    raw_text = (
        "We must remain aware and focused. "
        "We align together in unity and collaboration. "
        "We refuse harm and choose ethical safety."
    )

    ingest_resp = client.post(
        "/ingest",
        json={
            "entity": "epic26-ns",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": raw_text,
            "kind": "text",
            "metadata": {},
        },
        headers=headers,
    )
    assert ingest_resp.status_code == 200, ingest_resp.text
    ingest_body = ingest_resp.json()
    full_payload_coordinate = ingest_body["full_payload_coordinate"]
    assert full_payload_coordinate is not None
    assert ingest_body["ingest_diagnostics"]["kernel_projection_count"] > 0

    identifier = full_payload_coordinate.split(":", 1)[1]

    # 1. Resolve the intact blob.
    blob_resp = client.post(
        "/resolve/tiered",
        json={
            "namespace": "epic26-ns",
            "identifier": identifier,
            "read_tier": "blob_full",
        },
        headers=headers,
    )
    assert blob_resp.status_code == 200, blob_resp.text
    blob_body = blob_resp.json()
    assert blob_body["read_tier"] == "blob_full"
    assert blob_body["payload"]["text"] == raw_text

    # 2. Resolve the kernel projections.
    proj_resp = client.post(
        "/resolve/tiered",
        json={
            "namespace": "epic26-ns",
            "identifier": identifier,
            "read_tier": "kernel_projections",
        },
        headers=headers,
    )
    assert proj_resp.status_code == 200, proj_resp.text
    proj_body = proj_resp.json()
    assert proj_body["read_tier"] == "kernel_projections"
    assert proj_body["payload"]["count"] > 0
    assert proj_body["parent"]["quaternary_layer"] is not None
    assert isinstance(proj_body["parent"]["checksum_336_satisfied"], bool)

    # 3. Chat assembly can expand the blob.
    db = client.app.state.db
    result = asyncio.run(
        assemble_context(
            entity="epic26-ns",
            query=None,
            k=5,
            store=type(
                "Store",
                (),
                {
                    "_db": db,
                    "list_by_namespace": lambda self, ns, limit=None: [],
                    "read_blob_text": lambda self, coord: raw_text if coord == full_payload_coordinate else None,
                },
            )(),
            payload_tier="blob_full",
        )
    )
    assert result["recent"] == []


def test_existing_part_coordinates_remain_resolvable() -> None:
    """Backwards compatibility: parent coord and part coords still resolve."""
    client = _make_client()
    headers = {"x-ledger-id": "compat-ns"}
    raw_text = "First sentence. Second sentence. Third sentence."

    ingest_resp = client.post(
        "/ingest",
        json={
            "entity": "compat-ns",
            "session_id": "s1",
            "turn_id": "t1",
            "raw_text": raw_text,
            "kind": "text",
            "metadata": {},
        },
        headers=headers,
    )
    assert ingest_resp.status_code == 200, ingest_resp.text
    ingest_body = ingest_resp.json()
    assert ingest_body["coordinate"] is not None
    assert ingest_body["parent_coordinate"] == ingest_body["coordinate"]
    assert isinstance(ingest_body["part_coordinates"], list)

    # The parent coordinate still resolves to summary metadata.
    parent = ingest_body["coordinate"]
    parts = parent.rsplit(":", 1)
    resolve_resp = client.post(
        "/resolve/tiered",
        json={
            "namespace": parts[0],
            "identifier": parts[1],
            "read_tier": "operator_full",
        },
        headers=headers,
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    entry = resolve_resp.json()["entry"]
    assert entry["metadata"]["summary"] is not None or entry["metadata"].get("attachment_summary") is not None


def test_336_checksum_invariant_is_non_compensatory() -> None:
    """If any gate is Level 0, the composite checksum must not be satisfied."""
    from backend.kernel.quaternary_gates import QuaternaryGate

    result = QuaternaryGate.evaluate(0, 7, 6)
    assert result["clay_admissible"] is False
    assert result["checksum_336_satisfied"] is False

    result = QuaternaryGate.evaluate(6, 6, 6)
    assert result["clay_admissible"] is True
    assert result["checksum_336_satisfied"] is True
