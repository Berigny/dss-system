"""End-to-end pipeline tests."""

from __future__ import annotations

import pytest

from dss_ledger.service import ProcessService


@pytest.mark.parametrize(
    "text,valid",
    [
        ("autonomy action mastery", True),
        ("mastery action autonomy", False),
        ("autonomy action potential context", True),
        ("autonomy", False),
    ],
)
def test_query_valid_and_invalid(service: ProcessService, text: str, valid: bool):
    if valid:
        service.append_text(text)
    result = service.query(text)
    assert result["valid"] is valid


def test_append_then_query(service: ProcessService):
    append_result = service.append_text("autonomy action mastery")
    assert append_result["append"]["status"] == "APPENDED"

    query_result = service.query("autonomy action mastery")
    assert query_result["valid"] is True
    assert query_result["pipeline"] == ["parse", "encode", "validate"]


def test_query_with_expected_result(service: ProcessService):
    service.append_text("autonomy action mastery potential")
    result = service.query("autonomy action mastery potential", expected_result="potential")
    assert result["valid"] is True


def test_query_expected_result_mismatch(service: ProcessService):
    service.append_text("autonomy action mastery potential")
    result = service.query("autonomy action mastery potential", expected_result="mastery")
    assert result["valid"] is False
    assert result["validate"]["error"] == "RESULT_MISMATCH"


def test_factor_after_append(service: ProcessService):
    append_result = service.append_text("autonomy action mastery")
    pid = append_result["encoded"]["pid"]
    factored = service.factor(pid)
    assert factored["canonical"] == "autonomy.action.mastery"
