"""Tests for the DSS-6518 ladder attack-resistance suite."""

from __future__ import annotations

import pytest

from harness.claims_registry import bind_claims, load_registry
from suites.ladder_attacks import run_ladder_attacks


def test_ladder_attacks_all_pass() -> None:
    report = run_ladder_attacks()
    assert report["all_passed"] is True
    assert report["published"] is True


def test_ladder_attacks_claim_ids() -> None:
    report = run_ladder_attacks()
    assert set(report["claim_ids"].keys()) == {f"ATK-{i:02d}" for i in range(1, 11)}
    assert all(report["claim_ids"].values())


def test_ladder_attacks_registry_binding() -> None:
    registry = load_registry()
    report = run_ladder_attacks()
    # Pass only the claim binding surface to the registry harness.
    harness_payload = {
        "suite": report["suite"],
        "claim_ids": report["claim_ids"],
    }
    binding = bind_claims(registry, [harness_payload])
    assert "ATK-01" in binding["bound"]
    assert "ATK-10" in binding["bound"]
    # The full registry has many other claims; this single suite only binds ATK.
    assert binding["unknown_claims"] == []
