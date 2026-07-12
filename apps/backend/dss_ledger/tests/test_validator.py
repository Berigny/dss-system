"""Process ledger validator tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss_ledger.schema import LedgerSchema
from dss_ledger.src.encoder import LedgerEncoder
from dss_ledger.src.validator import ProcessLedger


def test_validate_unknown_pid(tmp_path: Path, schema: LedgerSchema):
    ledger = ProcessLedger(tmp_path)
    result = ledger.validate(12345)
    assert result["valid"] is False
    assert result["error"] == "PROCESS_NOT_FOUND"


def test_append_and_validate(tmp_path: Path, schema: LedgerSchema):
    ledger = ProcessLedger(tmp_path)
    encoder = LedgerEncoder(schema)
    encoded = encoder.encode_process("autonomy", "action", "mastery")

    entry = {
        "canonical": encoded["canonical"],
        "canonical_result": "mastery",
        "domain": "kernel",
        "certainty": 1.0,
        "source": "test",
    }
    append_result = ledger.append(encoded["pid"], entry)
    assert append_result["status"] == "APPENDED"

    validation = ledger.validate(encoded["pid"])
    assert validation["valid"] is True
    assert validation["canonical"] == encoded["canonical"]


def test_append_is_idempotent(tmp_path: Path, schema: LedgerSchema):
    ledger = ProcessLedger(tmp_path)
    encoder = LedgerEncoder(schema)
    encoded = encoder.encode_process("autonomy", "action", "mastery")
    entry = {
        "canonical": encoded["canonical"],
        "canonical_result": "mastery",
        "domain": "kernel",
        "certainty": 1.0,
        "source": "test",
    }
    ledger.append(encoded["pid"], entry)
    second = ledger.append(encoded["pid"], entry)
    assert second["status"] == "EXISTS"


def test_result_mismatch(tmp_path: Path, schema: LedgerSchema):
    ledger = ProcessLedger(tmp_path)
    encoder = LedgerEncoder(schema)
    encoded = encoder.encode_process("autonomy", "action", "mastery")
    ledger.append(
        encoded["pid"],
        {
            "canonical": encoded["canonical"],
            "canonical_result": "mastery",
            "domain": "kernel",
            "certainty": 1.0,
            "source": "test",
        },
    )
    result = ledger.validate(encoded["pid"], expected_result="potential")
    assert result["valid"] is False
    assert result["error"] == "RESULT_MISMATCH"
