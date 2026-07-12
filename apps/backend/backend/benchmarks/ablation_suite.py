"""Multi-seed ablation suite harness.

Runs every condition in `ablation_runner.ABLATION_CONDITIONS` across a seed
list, persists per-seed artefacts, writes aggregated artefacts, and emits a
human-readable findings report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backend.benchmarks.ablation_runner import (
    ABLATION_CONDITIONS,
    AblationCondition,
    run_ablation_condition,
)
from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.benchmarks.harness import BenchmarkHarness


DEFAULT_OUTPUT_ROOT: Path = Path(__file__).parent / "output" / "ablations"
DEFAULT_SEEDS: Sequence[int] = (193, 42, 7)


@dataclass(frozen=True)
class AblationSuiteReport:
    """Summary of all ablation conditions."""

    suite_id: str
    suite_version: str
    executed_at: str
    seeds: Sequence[int]
    conditions: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "executed_at": self.executed_at,
            "seeds": list(self.seeds),
            "conditions": self.conditions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Ablation Suite Report",
            "",
            f"**Suite:** {self.suite_id} `{self.suite_version}`  ",
            f"**Executed:** {self.executed_at}  ",
            f"**Seeds:** {', '.join(str(s) for s in self.seeds)}",
            "",
            "## Retrieval results",
            "",
            "| Condition | Mode | Recall@10 | MRR | Latency (ms) |",
            "|-----------|------|-----------|-----|--------------|",
        ]
        for cond in self.conditions:
            retrieval = cond.get("retrieval", {})
            latency = cond.get("latency", {})
            lines.append(
                f"| {cond.get('condition', '-')} | {cond.get('mode', '-')} | "
                f"{retrieval.get('recall_at_10', 0):.3f} | "
                f"{retrieval.get('mrr', 0):.3f} | "
                f"{latency.get('avg_latency_ms', 0):.3f} |"
            )
        lines.append("")
        lines.append("## Per-component latency (ms/query)")
        lines.append("")
        lines.append(
            "| Condition | Retrieval | Coord Resolution | Post-Processing | LLM Gen |"
        )
        lines.append("|-----------|-----------|------------------|-----------------|---------|")
        for cond in self.conditions:
            latency = cond.get("latency", {})
            lines.append(
                f"| {cond.get('condition', '-')} | "
                f"{latency.get('retrieval_ms', 0):.3f} | "
                f"{latency.get('coordinate_resolution_ms', 0):.3f} | "
                f"{latency.get('post_processing_ms', 0):.3f} | "
                f"{latency.get('llm_generation_ms', 0):.3f} |"
            )
        lines.append("")
        lines.append("## Per-component token cost (tokens/query)")
        lines.append("")
        lines.append(
            "| Condition | Retrieval | Coord Resolution | LLM Gen | Post-Processing | USD |"
        )
        lines.append("|-----------|-----------|------------------|---------|-----------------|-----|")
        for cond in self.conditions:
            cost = cond.get("cost", {})
            lines.append(
                f"| {cond.get('condition', '-')} | "
                f"{cost.get('retrieval_tokens', 0):.1f} | "
                f"{cost.get('coordinate_resolution_tokens', 0):.1f} | "
                f"{cost.get('llm_generation_tokens', 0):.1f} | "
                f"{cost.get('post_processing_tokens', 0):.1f} | "
                f"{cost.get('total_cost_usd', 0):.2e} |"
            )
        lines.append("")
        lines.append("## Notes")
        lines.append(
            "- All conditions share the same synthetic retrieval corpus and query set."
        )
        lines.append(
            "- Latency is broken down into retrieval, coordinate resolution, "
            "post-processing, and LLM generation in each artefact."
        )
        lines.append(
            "- LLM generation latency/token counts are nominal because this benchmark "
            "measures retrieval only; wire in an LLM harness to populate real values."
        )
        lines.append("")
        return "\n".join(lines)


def _extract_mean_metrics(artifact: BenchmarkArtifact) -> dict[str, Any]:
    """Flatten an aggregate artefact into a simple metrics table row."""
    out: dict[str, Any] = {
        "condition": str(artifact.run_config.get("condition") or artifact.mode),
        "mode": artifact.mode,
    }
    for group_name, group in artifact.metrics.items():
        if group.status != "present":
            continue
        out[group_name] = {
            metric_name: entry.value for metric_name, entry in group.metrics.items()
        }
    return out


def run_ablation_suite(
    *,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    artefact_schema_version: str = "1.0.0",
) -> AblationSuiteReport:
    """Run the full ablation matrix and write artefacts and a report."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    executed_at = datetime.now(timezone.utc)
    report_timestamp = executed_at.strftime("%Y%m%dT%H%M%SZ")

    condition_rows: list[dict[str, Any]] = []
    for condition in ABLATION_CONDITIONS:
        condition_root = output_root / condition.name
        harness = BenchmarkHarness(
            suite_id="ablation_retrieval",
            suite_version="v1",
            mode=condition.mode,
            seeds=seeds,
            output_root=condition_root,
            run_label=f"ablation-{condition.name}",
        )

        def _runner(seed: int, condition: AblationCondition = condition) -> BenchmarkArtifact:
            return run_ablation_condition(
                condition,
                seed=seed,
                artefact_schema_version=artefact_schema_version,
            )

        aggregate = harness.run(_runner)
        condition_rows.append(_extract_mean_metrics(aggregate))

    report = AblationSuiteReport(
        suite_id="ablation_retrieval",
        suite_version="v1",
        executed_at=report_timestamp,
        seeds=seeds,
        conditions=condition_rows,
    )

    report_path = output_root / f"ablation_report_{report_timestamp}.json"
    report_path.write_text(
        json.dumps(report.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path = output_root / f"ablation_report_{report_timestamp}.md"
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")

    manifest = {
        "suite_id": report.suite_id,
        "suite_version": report.suite_version,
        "executed_at": report_timestamp,
        "seeds": list(seeds),
        "conditions": [cond.get("condition") for cond in condition_rows],
        "artefact_schema_version": artefact_schema_version,
        "report_json": str(report_path.relative_to(output_root)),
        "report_markdown": str(markdown_path.relative_to(output_root)),
        "condition_output_roots": [cond.get("condition") for cond in condition_rows],
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

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
    parser.add_argument(
        "--artefact-schema-version",
        default="1.0.0",
        help="Schema version for emitted artefacts.",
    )
    args = parser.parse_args(argv)

    report = run_ablation_suite(
        seeds=args.seeds,
        output_root=args.output_root,
        artefact_schema_version=args.artefact_schema_version,
    )
    print(report.to_markdown())


__all__ = (
    "AblationSuiteReport",
    "run_ablation_suite",
)

if __name__ == "__main__":
    main()
