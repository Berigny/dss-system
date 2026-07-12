"""Tests for the orchestrator's coordinate context-admission helper."""

from __future__ import annotations

import pytest

from routes.orchestrator import _build_context_admission


def _decoded_payload(
    coord_type: str,
    text: str,
    one_line: str = "skim summary",
) -> dict:
    return {
        "coord": f"LOAM:{coord_type}-TEST",
        "type": coord_type,
        "skim": {"one_line": one_line},
        "payload": {
            "segments": [{"id": "ANS-01", "kind": "answer", "blob_ref": "BLOB:WX:ANS-01"}],
            "blobs": {"BLOB:WX:ANS-01": text},
        },
    }


def test_wx_coord_admits_full_payload() -> None:
    decoded = _decoded_payload("WX", "This is the full answer text.")
    admitted, kind = _build_context_admission(decoded, message="")
    assert admitted == "This is the full answer text."
    assert kind == "opened_payload"


def test_non_wx_coord_falls_back_to_skim() -> None:
    decoded = _decoded_payload("EV", "Full evidence text.")
    admitted, kind = _build_context_admission(decoded, message="")
    assert admitted == "Summary: skim summary"
    assert kind == "skim_summary"


def test_opened_non_wx_coord_admits_full_payload() -> None:
    decoded = _decoded_payload("EV", "Full evidence text.")
    admitted, kind = _build_context_admission(decoded, message="", opened=True)
    assert admitted == "Full evidence text."
    assert kind == "opened_payload"


def test_attachment_coord_prefers_full_payload() -> None:
    decoded = _decoded_payload("ATT", "Attachment full text.")
    admitted, kind = _build_context_admission(
        decoded, message="", prefer_payload_text=True
    )
    assert admitted == "Attachment full text."
    assert kind == "attachment_payload"


def test_wx_without_payload_falls_back_to_skim() -> None:
    decoded = {"coord": "LOAM:WX-EMPTY", "type": "WX", "skim": {"one_line": "only skim"}}
    admitted, kind = _build_context_admission(decoded, message="")
    assert admitted == "only skim"
    assert kind == "skim_summary"
