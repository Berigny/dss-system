"""Tests for the PEO Schema v0.3 claims-gate adapter."""

from __future__ import annotations

import sys
from pathlib import Path

from suites.peo_v03_conformance import CLAIM_IDS, PEOV03ConformanceHarness


def _fixture_context():
    evidence = Path(__file__).resolve().parents[3] / "dss-codebase" / "packages" / "evidence"
    if str(evidence) not in sys.path:
        sys.path.insert(0, str(evidence))
    from dss_evidence.v03.testing import context, valid_peo

    return valid_peo(), context()


def test_conformance_harness_maps_every_lint_to_a_claim() -> None:
    peo, context = _fixture_context()
    report = PEOV03ConformanceHarness.run([peo], context)
    assert report["suite"] == "peo_v03"
    assert set(report["claim_ids"]) == set(CLAIM_IDS)
    assert report["all_passed"] is True


def test_conformance_harness_fails_a_broken_fixture() -> None:
    peo, context = _fixture_context()
    peo["diagnostics"]["traversal"]["status"] = "unknown"
    report = PEOV03ConformanceHarness.run([peo], context)
    assert report["claim_ids"]["V-10"] is False
    assert report["all_passed"] is False
