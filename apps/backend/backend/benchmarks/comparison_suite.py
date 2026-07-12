"""Multi-seed broader-comparison suite for DSS-227.

Runs every registered external baseline against the LongBench needle and
multi-hop benchmarks through the multi-seed harness, archives aggregate
artefacts, and emits a comparison report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.benchmarks.comparison_baselines import BASELINES, Baseline
from backend.benchmarks.comparison_benchmark import (
    _mode_for_baseline,
    run_multihop_baseline,
    run_needle_baseline,
    run_ruler_baseline,
)
from backend.benchmarks.harness import BenchmarkHarness


DEFAULT_OUTPUT_ROOT: Path = Path(__file__).parent / "output" / "comparisons"
DEFAULT_SEEDS: Sequence[int] = (193, 42, 7)


@dataclass(frozen=True)
class ComparisonSuiteReport:
    """Summary of all baseline-benchmark comparisons."""

    suite_id: str
    suite_version: str
    executed_at: str
    comparisons: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "executed_at": self.executed_at,
            "comparisons": self.comparisons,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Broader Comparisons Report",
            "",
            f"**Suite:** {self.suite_id} `{self.suite_version}`  ",
            f"**Executed:** {self.executed_at}",
            "",
            "| Baseline | Benchmark | Recall@1 | Recall@K | MRR | Latency (ms) | Token Cost | Status |",
            "|----------|-----------|----------|----------|-----|--------------|------------|--------|",
        ]
        for row in self.comparisons:
            status = "blocked" if row.get("blocked") else "ok"
            lines.append(
                f"| {row.get('baseline', '-')} | {row.get('benchmark', '-')} | "
                f"{row.get('recall_at_1', 0):.3f} | "
                f"{row.get('recall_at_k', 0):.3f} | "
                f"{row.get('mrr', 0):.3f} | "
                f"{row.get('avg_latency_ms', 0):.3f} | "
                f"{row.get('token_cost', 0):.1f} | {status} |"
            )
        lines.append("")
        lines.append("## Notes")
        lines.append(
            "- `dense_retrieval`, `hierarchical_rag`, and `long_context_model` are "
            "deterministic stand-ins for the external systems named in DSS-227."
        )
        lines.append(
            "- `grok_latest` is documented as blocked because no API key or access "
            "is configured in this environment."
        )
        lines.append(
            "- Human-evaluation protocol and annotated examples are published separately."
        )
        lines.append("")
        return "\n".join(lines)


def _extract_comparison_row(artifact: BenchmarkArtifact) -> dict[str, Any]:
    retrieval = artifact.metrics["retrieval"].metrics
    latency = artifact.metrics["latency"].metrics
    cost = artifact.metrics["cost"].metrics
    run_config = artifact.run_config
    return {
        "baseline": run_config.get("baseline"),
        "benchmark": run_config.get("benchmark"),
        "recall_at_1": retrieval["recall_at_1"].value,
        "recall_at_k": retrieval["recall_at_k"].value,
        "mrr": retrieval["mrr"].value,
        "avg_latency_ms": latency["avg_latency_ms"].value,
        "token_cost": cost["token_cost"].value,
        "blocked": bool(run_config.get("blocked")),
        "blocked_reason": run_config.get("blocked_reason"),
    }


def run_comparison_suite(
    *,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> ComparisonSuiteReport:
    """Run the full comparison matrix and write artefacts and reports."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    executed_at = datetime.now(timezone.utc)
    report_timestamp = executed_at.strftime("%Y%m%dT%H%M%SZ")

    comparisons: list[dict[str, Any]] = []

    benchmark_jobs: list[tuple[str, Callable[[Baseline, int], BenchmarkArtifact]]] = [
        ("needle", lambda b, s: run_needle_baseline(b, seed=s)),
        ("multihop", lambda b, s: run_multihop_baseline(b, seed=s)),
        ("ruler-256k", lambda b, s: run_ruler_baseline(b, seed=s, haystack_length=1000)),
    ]

    for baseline_name, baseline in BASELINES.items():
        for benchmark_label, job in benchmark_jobs:
            job_root = output_root / f"{baseline_name}-{benchmark_label}"
            harness = BenchmarkHarness(
                suite_id=f"comparison-{benchmark_label}",
                suite_version="v1",
                mode=_mode_for_baseline(baseline),
                seeds=seeds,
                output_root=job_root,
                run_label=f"comparison-{baseline_name}-{benchmark_label}",
            )

            def _runner(
                seed: int,
                baseline: Baseline = baseline,
                job: Callable[[Baseline, int], BenchmarkArtifact] = job,
            ) -> BenchmarkArtifact:
                return job(baseline, seed)

            aggregate = harness.run(_runner)
            comparisons.append(_extract_comparison_row(aggregate))

    report = ComparisonSuiteReport(
        suite_id="broader_comparisons",
        suite_version="v1",
        executed_at=report_timestamp,
        comparisons=comparisons,
    )

    report_path = output_root / f"comparison_report_{report_timestamp}.json"
    report_path.write_text(
        json.dumps(report.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path = output_root / f"comparison_report_{report_timestamp}.md"
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")

    return report


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seeds",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_SEEDS,
        help="Comma-separated random seeds.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for artefacts and reports.",
    )
    args = parser.parse_args(argv)

    report = run_comparison_suite(seeds=args.seeds, output_root=args.output_root)
    print(report.to_markdown())


__all__ = (
    "ComparisonSuiteReport",
    "run_comparison_suite",
)

if __name__ == "__main__":
    main()
