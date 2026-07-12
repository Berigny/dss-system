"""PID encoder tests."""

from __future__ import annotations

import pytest

from dss_ledger.schema import LedgerSchema
from dss_ledger.src.encoder import LedgerEncoder


def test_encode_basic_process(schema: LedgerSchema):
    encoder = LedgerEncoder(schema)
    result = encoder.encode_process("autonomy", "action", "mastery")
    assert result["pid"] > 0
    assert result["canonical"] == "autonomy.action.mastery"
    assert set(result["slots"]) == {"agent", "verb", "patient"}


def test_swapped_agent_patient_differs(schema: LedgerSchema):
    encoder = LedgerEncoder(schema)
    a = encoder.encode_process("autonomy", "action", "mastery")["pid"]
    b = encoder.encode_process("mastery", "action", "autonomy")["pid"]
    assert a != b


def test_unknown_concept_raises(schema: LedgerSchema):
    encoder = LedgerEncoder(schema)
    with pytest.raises(ValueError, match="Unknown concept"):
        encoder.encode_process("autonomy", "action", "unknown")


def test_encode_with_result_and_context(schema: LedgerSchema):
    encoder = LedgerEncoder(schema)
    result = encoder.encode_process(
        "autonomy", "action", "mastery", result="potential", context="context"
    )
    assert "result" in result["slots"]
    assert "context" in result["slots"]
    assert result["canonical"] == "autonomy.action.mastery→potential@context"


def test_encode_compound(schema: LedgerSchema):
    encoder = LedgerEncoder(schema)
    compound = encoder.encode_compound("patch_001", "autonomy", "mastery")
    assert compound["compound_id"] > 0
    assert compound["relation"] == "patch_001"
    assert compound["components"] == ["autonomy", "mastery"]
