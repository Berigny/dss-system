"""Shared test fixtures for the dual-layer ledger."""

from __future__ import annotations

import pytest

from dss_ledger.schema import LedgerSchema
from dss_ledger.service import ProcessService


@pytest.fixture
def schema() -> LedgerSchema:
    return LedgerSchema.from_config_dir()


@pytest.fixture
def service(tmp_path) -> ProcessService:
    return ProcessService(ledger_dir=tmp_path / "ledger")
