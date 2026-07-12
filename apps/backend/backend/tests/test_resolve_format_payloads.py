"""Tests for resolve_format blob/projection payload helpers."""

from __future__ import annotations

from backend.utils.resolve_format import build_payload_for_blob, build_payload_for_projections


def test_build_payload_for_blob() -> None:
    payload = build_payload_for_blob("hello world", coordinate="ns:blob-123")
    assert payload["type"] == "blob_full"
    assert payload["text"] == "hello world"
    assert payload["coordinate"] == "ns:blob-123"
    assert payload["tokens_est"] == 2  # len 11 // 4, at least 1


def test_build_payload_for_projections() -> None:
    projections = [
        {"coord": "ns/proj-000", "layer": "LOAM"},
        {"coord": "ns/proj-composite", "layer": "CLAY"},
    ]
    payload = build_payload_for_projections(projections)
    assert payload["type"] == "kernel_projections"
    assert payload["count"] == 2
    assert payload["projections"] == projections
