"""Tests for shared_types.coord_schema."""

from __future__ import annotations

import pytest

from shared_types.coord_schema import (
    Coordinate,
    LedgerEntrySchema,
    format_coordinate,
    normalize_coordinate_payload,
)


def test_coordinate_as_path() -> None:
    coord = Coordinate(namespace="chat", identifier="turn-123")
    assert coord.as_path() == "chat:turn-123"


def test_ledger_entry_schema() -> None:
    entry = LedgerEntrySchema(
        coord=Coordinate(namespace="chat", identifier="turn-123"),
        metadata={"foo": "bar"},
    )
    assert entry.coord.as_path() == "chat:turn-123"
    assert entry.metadata == {"foo": "bar"}


def test_format_coordinate_provided() -> None:
    display, value = format_coordinate(
        timestamp="2024-01-01T00:00:00Z",
        coordinate="chat:turn-123",
        message_id="msg-1",
        content="hello",
    )
    assert display == "01/01/2024 00:00"
    assert value == "chat:turn-123"


def test_format_coordinate_fallback() -> None:
    display, value = format_coordinate(
        timestamp=None,
        coordinate=None,
        message_id="msg-1",
        content="hello",
    )
    assert display == "unknown"
    assert value.startswith("msg-1:unknown:")


def test_normalize_coordinate_payload_v2() -> None:
    decoded = {
        "data": {
            "type": "web4",
            "skim": {"one_line": "summary text"},
            "interpretation": {"claims": [{"name": "claim-1"}]},
            "governance": {
                "appraisal": {"coherence": 0.9},
                "policy_version": "v1",
                "risk_class": "low",
                "claim_source": "inferred",
                "policy_decision": "allow",
            },
        }
    }
    normalized = normalize_coordinate_payload(decoded)
    assert normalized["type"] == "web4"
    assert normalized["summary"] == "summary text"
    assert normalized["coherence"] == 0.9
    assert normalized["claims"] == [{"name": "claim-1"}]
    assert normalized["governance_contract"]["policy_version"] == "v1"


def test_normalize_coordinate_payload_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        normalize_coordinate_payload("not-a-dict")
