"""Smoke tests for the leaderboard generator."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_report(adapter: str, seed: int, suite_values: dict) -> None:
    report_dir = REPO_ROOT / "eval" / "reports" / f"{adapter}_all_suites_s{seed}"
    report_dir.mkdir(parents=True, exist_ok=True)
    suites = []
    for suite, values in suite_values.items():
        entry = {"suite": suite, **values}
        suites.append(entry)
    report = {
        "timestamp": "2026-07-27T00:00:00",
        "overall_pass": True,
        "suites": suites,
        "claims_binding": {},
    }
    (report_dir / "benchmark_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )


def test_leaderboard_pass(capsys) -> None:
    _write_report(
        "faiss",
        42,
        {
            "poisoning": {
                "silent_displacement_rate": 0.0,
                "flagged_or_preserved_rate": 1.0,
            },
            "integrity": {
                "incoherent_retrieval_rate": 0.0,
                "transparency_rate": 1.0,
            },
            "abstention": {
                "precision": 1.0,
                "recall": 1.0,
                "false_abstention_rate": 0.0,
            },
        },
    )

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_leaderboard.py"),
         "--adapters", "faiss", "--seeds", "42",
         "--suites", "poisoning", "integrity", "abstention"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "faiss" in result.stdout
    assert "✅ PASS" in result.stdout
    assert "❌ FAIL" not in result.stdout
