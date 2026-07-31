#!/usr/bin/env python3
"""Generate a cross-adapter leaderboard from per-cell benchmark reports.

Reads reports written by ``scripts/run_matrix.py`` under
``eval/reports/<adapter>_<suite>_s<seed>/benchmark_report.json`` and
compares each adapter's average metric value against the registered
claims in ``eval/claims_registry.yaml``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.claims_registry import load_registry

REPORT_DIR = ROOT / "eval" / "reports"

DEFAULT_ADAPTERS = [
    "faiss",
    "chroma",
    "qdrant",
    "sentence_transformers",
]


def _check(value: float, operator: str, threshold: float) -> bool:
    if operator == ">=":
        return value >= threshold
    if operator == "<=":
        return value <= threshold
    if operator == "==":
        return value == threshold
    if operator == "!=":
        return value != threshold
    if operator == ">":
        return value > threshold
    if operator == "<":
        return value < threshold
    return False


def _load_cell(adapter: str, seed: int) -> dict[str, Any] | None:
    path = REPORT_DIR / f"{adapter}_all_suites_s{seed}" / "benchmark_report.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_metric(report: dict[str, Any] | None, suite: str, metric: str) -> float | None:
    if report is None:
        return None
    for suite_result in report.get("suites", []):
        if suite_result.get("suite") == suite and metric in suite_result:
            value = suite_result[metric]
            if isinstance(value, (int, float)):
                return float(value)
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate benchmark leaderboard")
    parser.add_argument(
        "--adapters",
        nargs="+",
        default=DEFAULT_ADAPTERS,
        help="Adapter names to include",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=[42, 43, 44],
        help="Seeds to average over",
    )
    parser.add_argument(
        "--suites",
        nargs="+",
        default=None,
        help="Only evaluate claims from these suites (default: all gated claims)",
    )
    args = parser.parse_args(argv)

    registry = load_registry()
    gated_claims = [
        c
        for c in registry.get("claims", [])
        if c.get("threshold") is not None
    ]
    if args.suites:
        gated_claims = [c for c in gated_claims if c["suite"] in set(args.suites)]

    headers = ["Adapter"] + [f"{c['suite']}.{c['metric']}" for c in gated_claims] + ["Overall"]
    rows: list[list[str]] = []

    for adapter in args.adapters:
        row = [adapter]
        all_pass = True
        for claim in gated_claims:
            suite = claim["suite"]
            metric = claim["metric"]
            operator = claim.get("operator", ">=")
            threshold = float(claim["threshold"])

            values: list[float] = []
            for seed in args.seeds:
                report = _load_cell(adapter, seed)
                value = _collect_metric(report, suite, metric)
                if value is not None:
                    values.append(value)

            if values:
                avg = sum(values) / len(values)
                passed = _check(avg, operator, threshold)
                icon = "✅" if passed else "❌"
                row.append(f"{avg:.4f} {icon}")
                if not passed:
                    all_pass = False
            else:
                row.append("N/A")
                all_pass = False

        row.append("✅ PASS" if all_pass else "❌ FAIL")
        rows.append(row)

    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        print("| " + " | ".join(row) + " |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
