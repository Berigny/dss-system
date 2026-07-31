"""Tests for the Epic 65 adversarial anti-gaming suite."""

from __future__ import annotations

from suites.adversarial_peo import AdversarialSuite


def test_adversarial_suite_zero_adversarial_accrual() -> None:
    report = AdversarialSuite.run()
    results = report.to_results()
    assert results["self_deal_accrual"] == 0.0
    assert results["sybil_accrual"] == 0.0
    assert results["rapid_fire_accrual"] == 0.0
    assert results["total_adversarial_accrual"] == 0.0


def test_adversarial_suite_claim_ids() -> None:
    report = AdversarialSuite.run()
    results = report.to_results()
    assert results["claim_ids"]["ATK-01"] is True
    assert results["claim_ids"]["ATK-02"] is True
    assert results["claim_ids"]["ATK-03"] is True


def test_adversarial_suite_produces_alerts_on_failure() -> None:
    report = AdversarialSuite.run()
    # In the current implementation all three attacks are mitigated, so no alerts.
    assert len(report.alerts) == 0
