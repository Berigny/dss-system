"""Multi-seed benchmark harness with statistical aggregation.

The harness runs a benchmark callable over a list of random seeds, persists one
artefact per seed, and emits a single aggregate artefact containing mean,
standard deviation, min/max, and approximate 95% confidence intervals for every
numeric metric.  A hardware profile is attached to every artefact.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from backend.benchmarks.artifact_schema import (
    BenchmarkArtifact,
    BenchmarkMode,
    BenchmarkStatus,
    DatasetRef,
    FreshnessInfo,
    HardwareProfile as SchemaHardwareProfile,
    MetricEntry,
    MetricGroup,
    MetricStatistics,
    RepoRef,
    validate_benchmark_artifact,
)
from backend.benchmarks.determinism import (
    is_deterministic_mode,
    set_global_seed,
)
from backend.benchmarks.hardware import detect_hardware_profile


RunFn = Callable[[int], BenchmarkArtifact]


@dataclass(frozen=True)
class SeedRun:
    """A single seeded benchmark invocation and its persisted artefact."""

    seed: int
    artifact: BenchmarkArtifact
    artifact_path: Path | None


class BenchmarkHarness:
    """Run a benchmark across multiple seeds and produce an aggregate artefact."""

    def __init__(
        self,
        *,
        suite_id: str,
        suite_version: str,
        mode: BenchmarkMode,
        seeds: Sequence[int],
        output_root: Path,
        run_label: str | None = None,
    ) -> None:
        if not seeds:
            raise ValueError("at least one seed is required")
        self.suite_id = suite_id
        self.suite_version = suite_version
        self.mode = mode
        self.seeds = list(seeds)
        self.output_root = Path(output_root)
        self.run_label = run_label or f"{suite_id}-{mode}"
        self.hardware = self._schema_hardware_profile()

    @staticmethod
    def _schema_hardware_profile() -> SchemaHardwareProfile:
        profile = detect_hardware_profile()
        return SchemaHardwareProfile(**profile.to_dict())

    def _seed_output_path(self, seed: int, executed_at: datetime) -> Path:
        stamp = executed_at.strftime("%Y%m%dT%H%M%SZ")
        return self.output_root / "seeds" / str(seed) / f"{stamp}.json"

    def _aggregate_output_path(self, executed_at: datetime) -> Path:
        stamp = executed_at.strftime("%Y%m%dT%H%M%SZ")
        return self.output_root / "aggregate" / f"{stamp}.json"

    @staticmethod
    def _attach_hardware_and_seed(
        artifact: BenchmarkArtifact,
        seed: int,
        hardware: SchemaHardwareProfile,
    ) -> BenchmarkArtifact:
        """Return a copy of ``artifact`` with seed and hardware metadata."""
        payload = artifact.model_dump(mode="json")
        payload["hardware"] = hardware.model_dump(mode="json")
        payload["run_config"] = {
            **payload.get("run_config", {}),
            "seed": seed,
            "deterministic_mode": is_deterministic_mode(),
        }
        return validate_benchmark_artifact(payload)

    def _write_artifact(self, artifact: BenchmarkArtifact, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )

    def run(self, runner: RunFn) -> BenchmarkArtifact:
        """Run ``runner`` once per seed and return the aggregate artefact."""
        seed_runs: list[SeedRun] = []
        for seed in self.seeds:
            set_global_seed(seed)
            artifact = runner(seed)
            artifact = self._attach_hardware_and_seed(artifact, seed, self.hardware)
            executed_at = artifact.executed_at
            path = self._seed_output_path(seed, executed_at)
            self._write_artifact(artifact, path)
            seed_runs.append(SeedRun(seed=seed, artifact=artifact, artifact_path=path))

        aggregate = self._aggregate(seed_runs)
        aggregate_path = self._aggregate_output_path(aggregate.executed_at)
        self._write_artifact(aggregate, aggregate_path)
        return aggregate

    def _aggregate(self, seed_runs: Sequence[SeedRun]) -> BenchmarkArtifact:
        executed_at = datetime.now(timezone.utc)
        run_suffix = executed_at.strftime("%Y%m%dT%H%M%SZ")

        reference = seed_runs[0].artifact
        repos = list(reference.repos)
        datasets = list(reference.datasets)

        all_metric_groups = set(reference.metrics.keys())
        for run in seed_runs:
            all_metric_groups |= set(run.artifact.metrics.keys())

        aggregate_metrics: dict[str, MetricGroup] = {}
        for group_name in sorted(all_metric_groups):
            group = self._aggregate_group(group_name, seed_runs)
            aggregate_metrics[group_name] = group

        status = self._aggregate_status(seed_runs)
        failure_reason = (
            "one or more seed runs failed" if status == "failed" else None
        )
        seed_values = [run.seed for run in seed_runs]
        seed_run_ids = [run.artifact.run_id for run in seed_runs]

        return BenchmarkArtifact(
            failure_reason=failure_reason,
            artefact_schema_version=reference.artefact_schema_version,
            run_id=f"{self.run_label}-aggregate-{run_suffix}",
            suite_id=self.suite_id,
            suite_version=self.suite_version,
            executed_at=executed_at,
            mode=self.mode,
            status=status,
            repos=repos,
            datasets=datasets,
            metrics=aggregate_metrics,
            freshness=FreshnessInfo(
                status="fresh",
                checked_at=executed_at,
                max_age_hours=24,
                age_hours=0.0,
            ),
            hardware=self.hardware,
            run_config={
                **reference.run_config,
                "aggregate": True,
                "seeds": ",".join(str(s) for s in seed_values),
                "seed_count": len(seed_values),
                "seed_run_ids": ",".join(seed_run_ids),
                "deterministic_mode": is_deterministic_mode(),
            },
        )

    @staticmethod
    def _aggregate_status(seed_runs: Sequence[SeedRun]) -> BenchmarkStatus:
        statuses = {run.artifact.status for run in seed_runs}
        if "failed" in statuses:
            return "failed"
        if "partial" in statuses:
            return "partial"
        return "success"

    def _aggregate_group(
        self, group_name: str, seed_runs: Sequence[SeedRun]
    ) -> MetricGroup:
        groups = [
            run.artifact.metrics.get(group_name) for run in seed_runs
        ]
        present_groups = [g for g in groups if g is not None and g.status == "present"]

        if not present_groups:
            # All seeds lack this group; carry forward the first absence reason.
            first = next((g for g in groups if g is not None), None)
            return MetricGroup(
                status="absent",
                absence_reason=first.absence_reason if first else "not_measured_in_any_seed",
            )

        metric_names: set[str] = set()
        for g in present_groups:
            metric_names |= set(g.metrics.keys())

        aggregated_metrics: dict[str, MetricEntry] = {}
        for name in sorted(metric_names):
            values: list[float] = []
            first_entry: MetricEntry | None = None
            for g in present_groups:
                entry = g.metrics.get(name)
                if entry is None:
                    continue
                if first_entry is None:
                    first_entry = entry
                if isinstance(entry.value, (int, float)):
                    values.append(float(entry.value))

            if first_entry is None:
                continue

            if len(values) >= 1:
                mean, std, min_v, max_v, ci_low, ci_high = self._compute_statistics(values)
                aggregated_metrics[name] = MetricEntry(
                    value=mean,
                    unit=first_entry.unit,
                    description=first_entry.description,
                    statistics=MetricStatistics(
                        mean=mean,
                        standard_deviation=std,
                        min=min_v,
                        max=max_v,
                        ci_95_low=ci_low,
                        ci_95_high=ci_high,
                        sample_count=len(values),
                    ),
                )
            else:
                # Non-numeric metric: preserve the first observed value.
                aggregated_metrics[name] = MetricEntry(
                    value=first_entry.value,
                    unit=first_entry.unit,
                    description=first_entry.description,
                )

        return MetricGroup(status="present", metrics=aggregated_metrics)

    @staticmethod
    def _compute_statistics(values: Sequence[float]) -> tuple[float, float, float, float, float, float]:
        n = len(values)
        mean = float(statistics.mean(values))
        if n > 1:
            std = float(statistics.stdev(values))
        else:
            std = 0.0
        min_v = float(min(values))
        max_v = float(max(values))
        if n > 1:
            se = std / math.sqrt(n)
            # Approximate 95% CI using the normal critical value.
            z = 1.959963984540054
            ci_low = mean - z * se
            ci_high = mean + z * se
        else:
            ci_low = ci_high = mean
        return mean, std, min_v, max_v, ci_low, ci_high


def run_benchmark_suite(
    runner: RunFn,
    *,
    suite_id: str,
    suite_version: str,
    mode: BenchmarkMode,
    seeds: Sequence[int] = (193, 42, 7),
    output_root: Path | str = "./benchmark_outputs",
) -> BenchmarkArtifact:
    """Convenience helper for the common multi-seed benchmark flow."""
    harness = BenchmarkHarness(
        suite_id=suite_id,
        suite_version=suite_version,
        mode=mode,
        seeds=seeds,
        output_root=Path(output_root),
    )
    return harness.run(runner)


__all__ = (
    "BenchmarkHarness",
    "SeedRun",
    "run_benchmark_suite",
)
