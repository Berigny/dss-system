"""Tests for the Epic 69 SlateDB storage conformance harness."""

from __future__ import annotations

import pytest

from harness.claims_registry import bind_claims, load_registry
from suites.epic69_storage_conformance import (
    Epic69StorageConformanceHarness,
    run_epic69_storage_conformance,
)


EXPECTED_CLAIMS = {
    "STO-01",
    "STO-02",
    "STO-03",
    "STO-04",
    "STO-05",
    "STO-06",
    "STO-09",
    "KRN-03",
    "KRN-09",
}


def test_harness_all_pass() -> None:
    report = Epic69StorageConformanceHarness.run()
    assert report["all_passed"] is True


def test_all_expected_claims_present() -> None:
    report = run_epic69_storage_conformance()
    missing = EXPECTED_CLAIMS - set(report["claim_ids"].keys())
    assert not missing, f"Missing Epic 69 claims: {missing}"
    for cid in EXPECTED_CLAIMS:
        assert report["claim_ids"][cid] is True, f"Claim {cid} did not pass"


def test_registry_binding_no_unknown_claims() -> None:
    registry = load_registry()
    report = run_epic69_storage_conformance()
    harness_payload = {"suite": report["suite"], "claim_ids": report["claim_ids"]}
    binding = bind_claims(registry, [harness_payload])
    assert binding["unknown_claims"] == []
