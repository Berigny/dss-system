"""PEO Schema v0.3 conformance suite adapter.

The lint engine lives in the sibling ``dss-codebase/packages/evidence``
package.  This adapter exposes it in the DSS-EVAL claims-gate shape:
``{suite, claim_ids, all_passed}``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SUITE = "peo_v03"
CLAIM_IDS = ("V-01", "V-02", "V-03", "V-04", "V-05a", "V-05b", "V-06", "V-07", "V-08", "V-09", "V-10")


def _ensure_evidence_package_on_path() -> None:
    evidence = Path(__file__).resolve().parents[3] / "dss-codebase" / "packages" / "evidence"
    if evidence.exists() and str(evidence) not in sys.path:
        sys.path.insert(0, str(evidence))


def _run(peos: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_evidence_package_on_path()
    try:
        from dss_evidence.v03 import run_suite
    except ImportError:
        return {"suite": SUITE, "claim_ids": {claim_id: False for claim_id in CLAIM_IDS}, "all_passed": False}

    peos = peos or []
    claim_values = {claim_id: True for claim_id in CLAIM_IDS}
    for peo in peos:
        report = run_suite(peo, context or {})
        for claim_id, result in report["per_lint"].items():
            if result.get("verdict") != "pass":
                claim_values[claim_id] = False
    if not peos:
        claim_values = {claim_id: False for claim_id in CLAIM_IDS}
    return {
        "suite": SUITE,
        "claim_ids": claim_values,
        "all_passed": all(claim_values.values()),
    }


class PEOV03ConformanceHarness:
    """Run the v0.3 lints against supplied PEOs and expose claim IDs."""

    @classmethod
    def run(cls, peos: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return _run(peos, context)


def run_peo_v03_conformance(
    peos: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run(peos, context)


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run_peo_v03_conformance(), indent=2, sort_keys=True))
