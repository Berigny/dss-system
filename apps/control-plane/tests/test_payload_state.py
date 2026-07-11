"""Tests for payload-state exposure on dashboard source records."""

from __future__ import annotations

from app import _normalized_source_record, _payload_state_for_meta


def test_payload_state_for_full_blob() -> None:
    assert _payload_state_for_meta({"full_payload_coord": "ns:blob-123"}) == "full"
    assert _payload_state_for_meta({"full_payload": True}) == "full"


def test_payload_state_for_projection() -> None:
    assert _payload_state_for_meta({"kernel_projections": ["ns/proj-000"]}) == "projection"


def test_payload_state_for_skim() -> None:
    assert _payload_state_for_meta({"summary": "a summary"}) == "skim"


def test_payload_state_for_part_walk() -> None:
    assert _payload_state_for_meta({"part_coordinates": ["ns:part-001"]}) == "part_walk_required"


def test_payload_state_for_gated() -> None:
    assert _payload_state_for_meta({"governance_error": {"reason": "block"}}) == "gated"
    assert _payload_state_for_meta({"blocked": True}) == "gated"


def test_payload_state_default_is_skim() -> None:
    assert _payload_state_for_meta({}) == "skim"


def test_normalized_source_record_exposes_payload_fields() -> None:
    record = _normalized_source_record(
        {
            "source_id": "src-1",
            "payload_state": "full",
            "full_payload_coord": "ns:blob-123",
            "projection_coordinates": ["ns/proj-000"],
        }
    )
    assert record["source_id"] == "src-1"
    assert record["payload_state"] == "full"
    assert record["full_payload_coord"] == "ns:blob-123"
    assert record["projection_coordinates"] == ["ns/proj-000"]
