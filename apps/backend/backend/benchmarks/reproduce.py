#!/usr/bin/env python3
"""One-command reproduction entry point for the DSS v0.3 benchmark suite.

Usage:
    python backend/benchmarks/reproduce.py [--seeds SEED [SEED ...]]
                                           [--output-dir DIR]
                                           [--suite {ablation}]

The script:
1. Generates a unique run ID (`ds-benchmark-{YYYYMMDD}-{HHMMSS}-{seed}`).
2. Logs the run ID and every seed to stdout and to `run.log`.
3. Runs the requested benchmark suite across all seeds with deterministic mode on.
4. Persists per-seed artefacts plus an aggregate artefact under `runs/<run_id>/`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.benchmarks.ablation_runner import (
    ABLATION_CONDITIONS,
    run_ablation_condition,
)
from backend.benchmarks.determinism import set_global_seed
from backend.benchmarks.hardware import detect_hardware_profile
from backend.benchmarks.harness import BenchmarkHarness


DEFAULT_SEEDS = [193, 194, 195, 196, 197]
DEFAULT_SUITE = "ablation"
RUN_ID_FORMAT = "ds-benchmark-{date}-{time}-{primary_seed}"


def _make_run_id(seeds: list[int]) -> str:
    now = datetime.now(timezone.utc)
    return RUN_ID_FORMAT.format(
        date=now.strftime("%Y%m%d"),
        time=now.strftime("%H%M%S"),
        primary_seed=seeds[0],
    )


def _setup_logging(output_dir: Path, run_id: str, seeds: list[int]) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_path)],
    )
    logger = logging.getLogger("dss-reproduce")
    logger.info("DSS v0.3 benchmark reproduction started")
    logger.info("run_id=%s", run_id)
    logger.info("output_dir=%s", output_dir)
    logger.info("seeds=%s", seeds)
    return logger


def _write_manifest(output_dir: Path, run_id: str, seeds: list[int]) -> None:
    manifest = {
        "run_id": run_id,
        "seeds": seeds,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "deterministic_mode": os.getenv("DSS_DETERMINISTIC", "").lower()
        in {"1", "true", "yes", "on"},
        "hardware": detect_hardware_profile().to_dict(),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def _run_ablation_suite(seeds: list[int], output_root: Path, logger: logging.Logger) -> None:
    logger.info("Running ablation suite across %d condition(s) and %d seed(s)", len(ABLATION_CONDITIONS), len(seeds))

    for condition in ABLATION_CONDITIONS:
        logger.info("Ablation condition: %s", condition.name)

        def runner(seed: int) -> dict:
            return run_ablation_condition(condition, seed=seed)

        harness = BenchmarkHarness(
            suite_id="dss-v0.3-ablation",
            suite_version="0.3.0",
            mode=condition.mode,
            seeds=seeds,
            output_root=output_root / condition.name,
            run_label=f"ablation-{condition.name}",
        )
        start = time.perf_counter()
        aggregate = harness.run(runner)
        elapsed = time.perf_counter() - start
        logger.info(
            "Ablation condition '%s' complete in %.2fs; aggregate status=%s",
            condition.name,
            elapsed,
            aggregate.status,
        )


def _print_summary(output_dir: Path, logger: logging.Logger) -> None:
    """Print a Markdown summary of aggregate artefacts to stdout and summary.md."""
    lines: list[str] = ["# DSS v0.3 Benchmark Summary\n"]
    aggregate_paths = sorted(output_dir.glob("*/aggregate/*.json"))
    if not aggregate_paths:
        logger.info("No aggregate artefacts found for summary.")
        return

    for path in aggregate_paths:
        data = json.loads(path.read_text())
        condition = path.parent.parent.name
        lines.append(f"\n## {condition}\n")
        lines.append(f"- run_id: `{data.get('run_id')}`")
        lines.append(f"- status: {data.get('status')}")
        lines.append(f"- seeds: {data.get('run_config', {}).get('seeds', 'unknown')}\n")

        metrics = data.get("metrics", {})
        if not metrics:
            lines.append("_No metric groups present._\n")
            continue

        for group_name, group in metrics.items():
            lines.append(f"### {group_name}\n")
            if group.get("status") != "present":
                lines.append(f"_Status: {group.get('status')}_\n")
                continue

            lines.append("| metric | mean | std | min | max | 95% CI low | 95% CI high |")
            lines.append("|--------|------|-----|-----|-----|------------|-------------|")
            for metric_name, entry in group.get("metrics", {}).items():
                stats = entry.get("statistics", {})
                lines.append(
                    f"| {metric_name} | "
                    f"{stats.get('mean', 'n/a')} | "
                    f"{stats.get('standard_deviation', 'n/a')} | "
                    f"{stats.get('min', 'n/a')} | "
                    f"{stats.get('max', 'n/a')} | "
                    f"{stats.get('ci_95_low', 'n/a')} | "
                    f"{stats.get('ci_95_high', 'n/a')} |"
                )
            lines.append("")

    summary_text = "\n".join(lines)
    (output_dir / "summary.md").write_text(summary_text)
    logger.info("Summary:\n%s", summary_text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reproduce DSS v0.3 benchmarks")
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=DEFAULT_SEEDS,
        help="Random seeds to use (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent.parent.parent / "runs",
        help="Root directory for run artefacts (default: repo/runs)",
    )
    parser.add_argument(
        "--suite",
        choices=["ablation"],
        default=DEFAULT_SUITE,
        help="Benchmark suite to run (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    seeds = list(args.seeds)
    if len(seeds) < 3:
        parser.error("at least 3 seeds are required for statistical reporting")

    run_id = _make_run_id(seeds)
    output_dir = args.output_dir / run_id

    logger = _setup_logging(output_dir, run_id, seeds)
    _write_manifest(output_dir, run_id, seeds)

    # Seed-controlled execution for the whole process.
    set_global_seed(seeds[0])

    started = time.perf_counter()
    if args.suite == "ablation":
        _run_ablation_suite(seeds, output_dir, logger)
    else:
        parser.error(f"unknown suite: {args.suite}")

    total = time.perf_counter() - started
    logger.info("Reproduction complete in %.2fs", total)
    logger.info("Artifacts stored under: %s", output_dir)
    _print_summary(output_dir, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
