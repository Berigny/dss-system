"""Benchmark manifest emission per KSR-EVAL v0.4.

Every benchmark run must emit a flat, human-reviewable manifest alongside its
BenchmarkArtifact. The manifest schema is defined in
``eval/reports/benchmarks/benchmark_manifest_schema.yaml``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.benchmarks.hardware import detect_hardware_profile


MANIFEST_SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT_ROOT = Path("eval/reports/benchmarks")


def _first_dataset(artifact: BenchmarkArtifact) -> dict[str, Any]:
    if artifact.datasets:
        ds = artifact.datasets[0]
        return {
            "name": ds.name,
            "version": ds.version,
            "split": ds.split,
            "record_count": ds.record_count,
            "path": "",
            "sha256": "",
        }
    return {"name": "unknown", "version": "unknown", "split": "unknown", "path": "", "sha256": ""}


def _extract_numeric_metrics(artifact: BenchmarkArtifact) -> dict[str, Any]:
    """Return the first numeric metric value from each present metric group."""
    metrics: dict[str, Any] = {}
    for group_name, group in artifact.metrics.items():
        if group.status != "present":
            continue
        for name, entry in group.metrics.items():
            if isinstance(entry.value, (int, float)) and name not in metrics:
                metrics[name] = {
                    "value": float(entry.value),
                    "unit": entry.unit or "ratio",
                    "description": entry.description or name,
                }
                break
    if not metrics:
        metrics["placeholder"] = {
            "value": 0.0,
            "unit": "count",
            "description": "No numeric metrics available",
        }
    return metrics


def build_manifest(
    artifact: BenchmarkArtifact,
    *,
    eval_script_version: str,
    seeds: Sequence[int],
    conditions: dict[str, Any] | None = None,
    dataset_sha256: str = "",
    dataset_path: str = "",
) -> dict[str, Any]:
    """Build a manifest dict from a completed BenchmarkArtifact."""
    hardware = detect_hardware_profile()
    ds = _first_dataset(artifact)
    if dataset_path:
        ds["path"] = dataset_path
    if dataset_sha256:
        ds["sha256"] = dataset_sha256

    repo_sha = ""
    for repo in artifact.repos:
        if repo.name == "ds-backend-local":
            repo_sha = repo.commit_sha
            break

    # Try to extract statistics from a metric that has them.
    stats: dict[str, Any] | None = None
    for group in artifact.metrics.values():
        if group.status != "present":
            continue
        for entry in group.metrics.values():
            if entry.statistics is not None:
                s = entry.statistics
                stats = {
                    "mean": s.mean,
                    "standard_deviation": s.standard_deviation,
                    "min": s.min,
                    "max": s.max,
                    "ci_95_low": s.ci_95_low,
                    "ci_95_high": s.ci_95_high,
                    "sample_count": s.sample_count,
                    "unit": entry.unit or "ratio",
                    "description": entry.description or "aggregated metric",
                }
                break
        if stats is not None:
            break

    if stats is None:
        # Fall back to a single-sample statistic derived from present metrics.
        numeric = _extract_numeric_metrics(artifact)
        first = next(iter(numeric.values()))
        stats = {
            "mean": first["value"],
            "standard_deviation": 0.0,
            "min": first["value"],
            "max": first["value"],
            "ci_95_low": first["value"],
            "ci_95_high": first["value"],
            "sample_count": len(seeds),
            "unit": first["unit"],
            "description": first["description"],
        }

    notes = {}
    if artifact.run_config:
        for key in ("credit", "partial_status_note"):
            if key in artifact.run_config:
                notes[key] = artifact.run_config[key]

    manifest = {
        "artifact_version": "ksr-eval-v0.4",
        "git_commit_sha": repo_sha,
        "eval_script_version": eval_script_version,
        "run_date": artifact.executed_at.isoformat(),
        "seeds": list(seeds),
        "hardware_profile": hardware.to_dict(),
        "dataset": ds,
        "conditions": conditions or {},
        "metrics": stats,
    }
    if notes:
        manifest["notes"] = notes
    return manifest


def write_manifest(
    manifest: dict[str, Any],
    output_path: Path,
) -> Path:
    """Write a manifest to disk and return the path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output_path


__all__ = (
    "MANIFEST_SCHEMA_VERSION",
    "DEFAULT_OUTPUT_ROOT",
    "build_manifest",
    "write_manifest",
)
