"""Integration tests for the dual-layer ledger and geological layer store."""

from __future__ import annotations

import pytest

from backend.services.ledger_service import LedgerService
from dss_ledger.service import ProcessService


def test_append_process_writes_loam_entry():
    db: dict[bytes, bytes] = {}
    service = LedgerService(db)
    service.ensure_base_foundation("default")

    result = service.append_process(
        {"agent": "autonomy", "verb": "action", "patient": "mastery"}
    )

    assert result["pid"] > 0
    assert result["canonical"] == "autonomy.action.mastery"
    assert result["layer"] == "LOAM"

    # The layer store should contain the entry under the canonical COORD.
    entries = service.layer_store.retrieve_by_coord("autonomy.action.mastery")
    assert len(entries) == 1


def test_process_service_round_trip(tmp_path):
    ledger_dir = tmp_path / "ledger"
    ps = ProcessService(ledger_dir=ledger_dir)

    append_result = ps.append_text("autonomy action mastery")
    assert append_result["append"]["status"] == "APPENDED"

    query_result = ps.query("autonomy action mastery")
    assert query_result["valid"] is True
