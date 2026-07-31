"""Tests for the Epic 65 ingestion + physical-twin conformance harness."""

from __future__ import annotations

import pytest

from harness.claims_registry import bind_claims, load_registry
from suites.epic65_conformance import (
    IngestionConformanceHarness,
    PhysicalTwinConformanceHarness,
    run_epic65_conformance,
)


def test_ingestion_harness_all_pass() -> None:
    report = IngestionConformanceHarness.run()
    assert report["all_passed"] is True


def test_physical_twin_harness_all_pass() -> None:
    report = PhysicalTwinConformanceHarness.run()
    assert report["all_passed"] is True


def test_all_ing_claims_present() -> None:
    report = run_epic65_conformance()
    expected = {
        "ING-REPLAY-01",
        "ING-REPLAY-02",
        "ING-ATM-01",
        "ING-MNT-01",
        "ING-MNT-02",
        "ING-EMT-01",
        "ING-EPOCH-01",
        "ING-REL-01",
        "ING-VEL-01",
        "ING-STC-01",
    }
    assert expected.issubset(set(report["claim_ids"].keys()))
    assert all(report["claim_ids"][cid] for cid in expected)


def test_all_phy_claims_present() -> None:
    report = run_epic65_conformance()
    expected = {
        "PHY-SCHEMA-01",
        "PHY-GEO-01",
        "PHY-EPOCH-01",
        "PHY-LOG-01",
        "PHY-NODELETE-01",
        "PHY-XFORM-01",
    }
    assert expected.issubset(set(report["claim_ids"].keys()))
    assert all(report["claim_ids"][cid] for cid in expected)


def test_registry_binding_no_unknown_claims() -> None:
    registry = load_registry()
    report = run_epic65_conformance()
    harness_payload = {"suite": report["suite"], "claim_ids": report["claim_ids"]}
    binding = bind_claims(registry, [harness_payload])
    assert binding["unknown_claims"] == []
