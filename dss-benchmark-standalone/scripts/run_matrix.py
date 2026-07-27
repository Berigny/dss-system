#!/usr/bin/env python3
"""Run all registered adapters across all suites and seeds.

Runs one subprocess per ``(adapter, seed)`` with all requested suites, then
emits ``eval/reports/matrix.json`` containing one cell per
adapter/suite/seed combination.  Individual subprocess failures are recorded,
not raised, so the matrix keeps running when an adapter is unavailable.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ADAPTERS = ["faiss", "chroma", "qdrant", "sentence_transformers"]
DEFAULT_SUITES = ["poisoning", "integrity", "abstention"]
DEFAULT_SEEDS = [42, 43, 44]


def _run_adapter_seed(
    adapter: str, suites: list[str], seed: int
) -> tuple[int, dict[str, Any] | None, str]:
    report_dir = ROOT / "eval" / "reports" / f"{adapter}_all_suites_s{seed}"
    cmd = [
        sys.executable,
        "harness/runner.py",
        "--adapter",
        adapter,
        "--suites",
        *suites,
        "--seeds",
        str(seed),
        "--report-dir",
        str(report_dir),
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    report_path = report_dir / "benchmark_report.json"
    report: dict[str, Any] | None = None
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return result.returncode, report, result.stderr.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark execution matrix runner")
    parser.add_argument(
        "--adapters",
        nargs="+",
        default=DEFAULT_ADAPTERS,
        help="Adapter names to run",
    )
    parser.add_argument(
        "--suites",
        nargs="+",
        default=DEFAULT_SUITES,
        choices=DEFAULT_SUITES,
        help="Suite names to run",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=DEFAULT_SEEDS,
        help="Random seeds to use",
    )
    args = parser.parse_args(argv)

    matrix: list[dict[str, Any]] = []
    failures = 0
    for adapter in args.adapters:
        for seed in args.seeds:
            print(f">>> {adapter} / all suites / seed {seed}", flush=True)
            returncode, report, stderr = _run_adapter_seed(adapter, args.suites, seed)
            for suite in args.suites:
                cell: dict[str, Any] = {
                    "adapter": adapter,
                    "suite": suite,
                    "seed": seed,
                    "returncode": returncode,
                }
                if report is not None:
                    cell["report"] = report
                    cell["report_dir"] = str(
                        ROOT / "eval" / "reports" / f"{adapter}_all_suites_s{seed}"
                    )
                else:
                    cell["error"] = stderr or "missing_benchmark_report"
                matrix.append(cell)
                if cell.get("error") or cell.get("returncode", 0) != 0:
                    failures += 1
            if returncode != 0 or report is None:
                print(
                    f"    WARNING: {adapter}/seed{seed} failed "
                    f"({stderr or 'non-zero exit'})",
                    flush=True,
                )

    summary = {
        "adapters": args.adapters,
        "suites": args.suites,
        "seeds": args.seeds,
        "cells": matrix,
        "total_cells": len(matrix),
        "failed_cells": failures,
    }
    out = ROOT / "eval" / "reports" / "matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nMatrix complete: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
