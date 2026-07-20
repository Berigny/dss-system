"""DSS-296 — One-click reproduction entrypoint for the v0.5 benchmark suite.

Runs DSS-292 through DSS-298, copies the resulting artifacts and manifests to
``eval/reports/benchmarks/``, and emits a top-level summary manifest.

Environment variables
---------------------
DRY_RUN                Set to ``1`` for a fast deterministic smoke run (default: 0).
DSS_REFRESH_TOKEN      Optional delegated Kimi mode token.  Not required for v0.5.
OPENROUTER_API_KEY     Optional fallback LLM key.  Not required for v0.5.
SKIP_REAL_EMBEDDING    Set to ``1`` to skip the sentence-transformers download.

The v0.5 suite is deterministic and local; no API keys are required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np

from backend.benchmarks.llm_surface_policy import log_surface_and_budget
from backend.benchmarks.pinned_queries import QUERIES_ROOT, verify_query_manifest

# Eval entrypoint is executed from /app/apps/backend with PYTHONPATH set so that
# `backend.*` imports resolve.
REPO_ROOT = Path(__file__).parent.parent
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
BENCHMARK_OUTPUT_ROOT = BACKEND_ROOT / "backend" / "benchmarks" / "output"
REPORTS_ROOT = REPO_ROOT / "eval" / "reports" / "benchmarks"
CORPUS_ROOT = REPO_ROOT / "eval" / "corpus"
QUERIES_ROOT = REPO_ROOT / "eval" / "queries"


@dataclass(frozen=True)
class EvalConfig:
    output_root: Path
    dry_run: bool
    skip_real_embedding: bool
    max_events: int
    force_generate_queries: bool = False
    pinned_query_path: Path | None = None


def _mock_real_embedding_baseline() -> type:
    """Return a mock RealEmbeddingBaseline class that avoids model downloads."""

    class MockRealEmbeddingBaseline:
        name = "real_embedding"

        def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
            self.model_name = model_name
            self._model_info = None

        def _ensure_embedder(self):
            if self._model_info is None:
                self._model_info = MagicMock()
            mock_embedder = MagicMock()

            def _encode(texts, *, convert_to_numpy=True):
                rng = np.random.RandomState(42)
                return rng.randn(len(texts), 8).astype(np.float32)

            mock_embedder.encode.side_effect = _encode
            return mock_embedder

        def run(self, memories, queries, *, top_k: int = 10):
            from backend.benchmarks.comparison_baselines import BaselineResult

            return BaselineResult(
                baseline_name=self.name,
                recall_at_1=1.0,
                recall_at_k=1.0,
                mrr=1.0,
                avg_latency_ms=1.0,
                token_cost=10.0,
                prompt_tokens=5.0,
                completion_tokens=5.0,
                precision_at_k={k: 1.0 for k in range(1, top_k + 1)},
                ndcg_at_k={k: 1.0 for k in range(1, top_k + 1)},
            )

    return MockRealEmbeddingBaseline


def _patch_real_embedding_baseline() -> None:
    """Patch the real embedding baseline so CI/smoke tests avoid downloads.

    The mock replaces the class in the source module and in any downstream
    benchmark modules that have already been imported (they cache the class
    via ``from ... import RealEmbeddingBaseline``).
    """
    import sys

    import backend.benchmarks.real_embedding_baseline as reb

    mock_class = _mock_real_embedding_baseline()
    reb.RealEmbeddingBaseline = mock_class  # type: ignore[misc]

    for downstream in (
        "backend.benchmarks.dss294_bm25_baseline",
        "backend.benchmarks.dss295_latency_storage_benchmark",
    ):
        module = sys.modules.get(downstream)
        if module is not None:
            module.RealEmbeddingBaseline = mock_class


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_corpus_manifest() -> dict[str, Any]:
    """Return the pinned corpus manifest, verifying SHA256 sums."""
    manifest_path = CORPUS_ROOT / "manifest.json"
    if not manifest_path.exists():
        return {"status": "missing", "manifest_path": str(manifest_path)}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified: dict[str, Any] = {"status": "ok", "files": {}}
    for filename, info in manifest.get("files", {}).items():
        file_path = CORPUS_ROOT / filename
        expected = info.get("sha256", "")
        actual = _sha256_file(file_path) if file_path.exists() else ""
        verified["files"][filename] = {
            "expected": expected,
            "actual": actual,
            "valid": expected and actual == expected,
        }
    return verified


def _run_dss292(config: EvalConfig) -> dict[str, Any]:
    from backend.benchmarks.dss292_known_unknown_benchmark import (
        BenchmarkConfig as Dss292Config,
        run_benchmark as run_dss292,
    )

    bench_config = Dss292Config(
        output_root=BENCHMARK_OUTPUT_ROOT / "dss292_known_unknown",
        lengths=(4,) if config.dry_run else (4, 8, 16, 32),
        top_k=5,
        seeds=(193,) if config.dry_run else (193, 42, 7),
        force_generate_queries=config.force_generate_queries,
        pinned_query_path=config.pinned_query_path or QUERIES_ROOT,
    )
    aggregate = run_dss292(bench_config)
    return {
        "suite": "dss292-known-unknown",
        "status": aggregate.status,
        "artifact_path": str(BENCHMARK_OUTPUT_ROOT / "dss292_known_unknown" / "aggregate"),
    }


def _run_dss293(config: EvalConfig) -> dict[str, Any]:
    from backend.benchmarks.dss293_adversarial_poisoning_benchmark import (
        BenchmarkConfig as Dss293Config,
        run_benchmark as run_dss293,
    )

    bench_config = Dss293Config(
        output_root=BENCHMARK_OUTPUT_ROOT / "dss293_adversarial_poisoning",
        seeds=(193,) if config.dry_run else (193, 42, 7),
        force_generate_queries=config.force_generate_queries,
        pinned_query_path=config.pinned_query_path or QUERIES_ROOT,
    )
    aggregate = run_dss293(bench_config)
    return {
        "suite": "dss293-adversarial-poisoning",
        "status": aggregate.status,
        "artifact_path": str(BENCHMARK_OUTPUT_ROOT / "dss293_adversarial_poisoning" / "aggregate"),
    }


def _run_dss294(config: EvalConfig) -> dict[str, Any]:
    from backend.benchmarks.dss294_bm25_baseline import (
        BenchmarkConfig as Dss294Config,
        run_benchmark as run_dss294,
    )

    bench_config = Dss294Config(
        output_root=BENCHMARK_OUTPUT_ROOT / "dss294_bm25_ranking",
        lengths=(4,) if config.dry_run else (4, 8, 16, 32),
        top_k=5,
        seeds=(193,) if config.dry_run else (193, 42, 7),
        force_generate_queries=config.force_generate_queries,
        pinned_query_path=config.pinned_query_path or QUERIES_ROOT,
    )
    aggregate = run_dss294(bench_config)
    return {
        "suite": "dss294-bm25-ranking",
        "status": aggregate.status,
        "artifact_path": str(BENCHMARK_OUTPUT_ROOT / "dss294_bm25_ranking" / "aggregate"),
    }


def _run_dss295(config: EvalConfig) -> dict[str, Any]:
    from backend.benchmarks.dss295_latency_storage_benchmark import (
        BenchmarkConfig as Dss295Config,
        run_benchmark as run_dss295,
    )

    bench_config = Dss295Config(
        output_root=BENCHMARK_OUTPUT_ROOT / "dss295_latency_storage",
        corpus_sizes=(999,) if config.dry_run else (999, 9999, 99999),
        top_k=5,
        query_iterations=5 if config.dry_run else 50,
        warmup_iterations=1 if config.dry_run else 5,
        seeds=(193,) if config.dry_run else (193, 42, 7),
        measure_100k=True,
        max_measured_events=2000 if config.dry_run else 100000,
        force_generate_queries=config.force_generate_queries,
        pinned_query_path=config.pinned_query_path or QUERIES_ROOT,
    )
    aggregate = run_dss295(bench_config)
    return {
        "suite": "dss295-latency-storage",
        "status": aggregate.status,
        "artifact_path": str(BENCHMARK_OUTPUT_ROOT / "dss295_latency_storage" / "aggregate"),
    }


def _run_dss297(config: EvalConfig) -> dict[str, Any]:
    from backend.benchmarks.dss297_citation_faithfulness_benchmark import (
        BenchmarkConfig as Dss297Config,
        run_benchmark as run_dss297,
    )

    bench_config = Dss297Config(
        output_root=BENCHMARK_OUTPUT_ROOT / "dss297_citation_faithfulness",
        seeds=(193,) if config.dry_run else (193, 42, 7),
    )
    aggregate = run_dss297(bench_config)
    return {
        "suite": "dss297-citation-faithfulness",
        "status": aggregate.status,
        "artifact_path": str(BENCHMARK_OUTPUT_ROOT / "dss297_citation_faithfulness" / "aggregate"),
    }


def _run_dss298(config: EvalConfig) -> dict[str, Any]:
    from backend.benchmarks.dss298_label_blind_ingestion_benchmark import (
        BenchmarkConfig as Dss298Config,
        run_benchmark as run_dss298,
    )

    bench_config = Dss298Config(
        output_root=BENCHMARK_OUTPUT_ROOT / "dss298_label_blind_ingestion",
        coverage_gate=0.8,
        seeds=(193,) if config.dry_run else (193, 42, 7),
        transport="R1",
    )
    aggregate = run_dss298(bench_config)
    return {
        "suite": "dss298-label-blind-ingestion",
        "status": aggregate.status,
        "artifact_path": str(BENCHMARK_OUTPUT_ROOT / "dss298_label_blind_ingestion" / "aggregate"),
    }


def _run_dss299(config: EvalConfig) -> dict[str, Any]:
    from backend.benchmarks.dss299_real_data_track_benchmark import (
        BenchmarkConfig as Dss299Config,
        run_benchmark as run_dss299,
    )

    bench_config = Dss299Config(
        output_root=BENCHMARK_OUTPUT_ROOT / "dss299_real_data_track",
        datasets=("hotpotqa", "narrativeqa"),
        samples_per_dataset=2 if config.dry_run else 50,
        top_k=5,
        seeds=(193,) if config.dry_run else (193, 42, 7, 13, 21),
        coverage_gate=0.8,
        max_total_documents=1000,
        max_queries=100,
        max_embedding_calls=2500,
        budget_tokens=500_000,
        skip_real_embedding=config.skip_real_embedding or config.dry_run,
        dry_run=config.dry_run,
    )
    aggregate = run_dss299(bench_config)
    return {
        "suite": "dss299-real-data-track",
        "status": aggregate.status,
        "artifact_path": str(BENCHMARK_OUTPUT_ROOT / "dss299_real_data_track" / "aggregate"),
    }


def _latest_file(directory: Path, pattern: str = "*.json") -> Path | None:
    candidates = list(directory.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _copy_artifacts(
    output_root: Path,
    run_id: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Copy aggregate artifacts and manifests from benchmark output to reports dir."""
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    for result in results:
        aggregate_dir = Path(result["artifact_path"])
        latest = _latest_file(aggregate_dir, "*.json")
        if latest is None:
            copied.append({"suite": result["suite"], "status": "missing"})
            continue
        dest = run_dir / f"{result['suite']}_aggregate.json"
        shutil.copy2(latest, dest)
        manifest_src = latest.with_suffix(".manifest.json")
        if manifest_src.exists():
            manifest_dest = run_dir / f"{result['suite']}_aggregate.manifest.json"
            shutil.copy2(manifest_src, manifest_dest)
        copied.append(
            {
                "suite": result["suite"],
                "status": "copied",
                "source": str(latest),
                "destination": str(dest),
                "sha256": _sha256_file(dest),
            }
        )
    return {"run_dir": str(run_dir), "artifacts": copied}


def _write_summary_manifest(
    output_root: Path,
    run_id: str,
    results: list[dict[str, Any]],
    copy_info: dict[str, Any],
    corpus_verification: dict[str, Any],
    query_verification: dict[str, Any],
) -> Path:
    run_dir = output_root / run_id
    summary = {
        "artifact_version": "ksr-eval-v0.4",
        "run_id": run_id,
        "run_date": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": _repo_sha(),
        "corpus_verification": corpus_verification,
        "query_verification": query_verification,
        "benchmarks": [
            {
                "suite": r["suite"],
                "status": r["status"],
                "source": r["artifact_path"],
            }
            for r in results
        ],
        "copied_artifacts": copy_info["artifacts"],
        "reports_directory": str(run_dir),
    }
    path = run_dir / "summary_manifest.json"
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return path


def run_eval(config: EvalConfig) -> int:
    """Run the full v0.5 benchmark suite and copy reports."""
    if config.skip_real_embedding or config.dry_run:
        _patch_real_embedding_baseline()

    corpus_verification = _verify_corpus_manifest()
    if corpus_verification.get("status") != "ok":
        print(f"WARNING: corpus manifest issue: {corpus_verification}", file=sys.stderr)

    query_verification = verify_query_manifest(root=config.pinned_query_path or QUERIES_ROOT)
    if query_verification.get("status") != "ok":
        print(f"WARNING: query manifest issue: {query_verification}", file=sys.stderr)

    run_id = f"dss_v0.5_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    print(f"Starting DSS v0.5 evaluation run: {run_id}")
    print(f"Dry run: {config.dry_run}")
    surface_info = log_surface_and_budget()
    print(f"LLM surface: {surface_info['llm_surface']}")
    print(f"LLM budget: {surface_info['llm_budget']}")

    results: list[dict[str, Any]] = []
    try:
        results.append(_run_dss292(config))
        results.append(_run_dss293(config))
        results.append(_run_dss294(config))
        results.append(_run_dss295(config))
        results.append(_run_dss297(config))
        results.append(_run_dss298(config))
        results.append(_run_dss299(config))
    except Exception as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1

    failed = [r for r in results if r["status"] != "success"]
    if failed:
        print(f"Failed benchmarks: {[r['suite'] for r in failed]}", file=sys.stderr)

    copy_info = _copy_artifacts(config.output_root, run_id, results)
    summary_path = _write_summary_manifest(
        config.output_root, run_id, results, copy_info, corpus_verification, query_verification
    )

    print("\nBenchmark results")
    print("-" * 50)
    for r in results:
        print(f"  {r['suite']:<35} {r['status']}")
    print(f"\nSummary manifest: {summary_path}")

    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPORTS_ROOT,
        help="Root directory for copied benchmark reports.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a fast deterministic smoke run for CI/validation.",
    )
    parser.add_argument(
        "--skip-real-embedding",
        action="store_true",
        help="Skip the sentence-transformers download by mocking the real embedding baseline.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=10000,
        help="Largest event count to measure directly in DSS-295.",
    )
    parser.add_argument(
        "--force-generate-queries",
        action="store_true",
        help="Ignore pinned query sets and generate queries at runtime.",
    )
    parser.add_argument(
        "--pinned-query-path",
        type=Path,
        default=None,
        help="Directory containing pinned query sets (default: eval/queries).",
    )
    args = parser.parse_args(argv)

    dry_run = args.dry_run or os.environ.get("DRY_RUN", "0") == "1"
    skip_real = args.skip_real_embedding or os.environ.get("SKIP_REAL_EMBEDDING", "0") == "1"

    config = EvalConfig(
        output_root=args.output_root,
        dry_run=dry_run,
        skip_real_embedding=skip_real,
        max_events=args.max_events,
        force_generate_queries=args.force_generate_queries,
        pinned_query_path=args.pinned_query_path,
    )
    return run_eval(config)


if __name__ == "__main__":
    sys.exit(main())
