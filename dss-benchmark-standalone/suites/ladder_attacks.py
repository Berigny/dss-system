"""Ladder attack-resistance replay suite for DSS-6518.

Imports the deterministic attack simulations from dss-codebase and reports
claim IDs ATK-01..ATK-10 to the claims-registry harness.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

# Mirror the dependency path used by suites/adversarial_peo.py.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[4] / "dss-codebase" / "packages" / "evidence")
)

from dss_evidence.ladder_attacks import AttackSuite, CIReporter


def run_ladder_attacks() -> Dict[str, Any]:
    """Run the ten ladder attack simulations and publish a CI report."""
    results = AttackSuite.run()
    report = AttackSuite.to_results(results)
    return CIReporter.publish(report)


if __name__ == "__main__":
    import json

    print(json.dumps(run_ladder_attacks(), indent=2, sort_keys=True))
