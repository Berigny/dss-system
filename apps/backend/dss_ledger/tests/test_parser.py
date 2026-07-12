"""Constrained parser tests."""

from __future__ import annotations

from dss_ledger.schema import LedgerSchema
from dss_ledger.src.parser import ConstrainedParser


def test_parse_valid_three_concepts(schema: LedgerSchema):
    parser = ConstrainedParser(schema)
    result = parser.parse("autonomy action mastery")
    assert result["status"] == "PARSED"
    assert result["slots"]["agent"] == "autonomy"
    assert result["slots"]["verb"] == "action"
    assert result["slots"]["patient"] == "mastery"


def test_parse_five_concepts(schema: LedgerSchema):
    parser = ConstrainedParser(schema)
    result = parser.parse("autonomy action mastery potential context")
    assert result["status"] == "PARSED"
    assert result["slots"]["result"] == "potential"
    assert result["slots"]["context"] == "context"


def test_parse_rejects_insufficient_concepts(schema: LedgerSchema):
    parser = ConstrainedParser(schema)
    result = parser.parse("autonomy")
    assert result["status"] == "REJECT"
    assert result["reason"] == "INSUFFICIENT_CONCEPTS"


def test_parse_is_case_insensitive(schema: LedgerSchema):
    parser = ConstrainedParser(schema)
    result = parser.parse("AUTONOMY action MASTERY")
    assert result["status"] == "PARSED"
    assert result["slots"]["agent"] == "autonomy"
