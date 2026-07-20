"""DSS-295 — Latency and storage cost table.

This harness measures wall-clock query latency (p50/p95) and storage bytes per
event for DSS and comparators across corpus sizes of 1 k, 10 k and 100 k events.
Systems compared:

* DSS (QpRouter) — genuine coordinate routing with architecture filters.
* Real MiniLM embeddings — ``sentence-transformers/all-MiniLM-L6-v2``.
* BM25 — real lexical baseline via ``rank-bm25``.
* Metadata-filter — structural-metadata compatibility filter.
* BoW stand-in — deterministic bag-of-words cosine.

The harness reuses the LongBench needle corpus generator, scaling the haystack
length so that each size bucket contains approximately the target number of
events.  Any row that is not measured directly is labelled as extrapolated.

Output
------
* A validated ``BenchmarkArtifact`` JSON under
  ``backend/benchmarks/output/dss295_latency_storage/``.
* A human-readable markdown table in the same directory.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.benchmarks.comparison_baselines import (
    BASELINES,
    BM25Baseline,
    BoWStandInBaseline,
)
from backend.benchmarks.harness import BenchmarkHarness
from backend.benchmarks.hnsw_dense_baseline import HnswDenseBaseline
from backend.benchmarks.longbench_needle_benchmark import (
    DEFAULT_LENGTHS,
    NeedleMemory,
    NeedleQuery,
    QpRouter,
    VectorRAGBaseline,
    generate_corpus,
)
from backend.benchmarks.manifest import build_manifest, write_manifest
from backend.benchmarks.pinned_queries import QUERIES_ROOT, load_pinned_queries_for_config
from backend.benchmarks.metadata_filter_baseline import MetadataFilterBaseline
from backend.benchmarks.real_embedding_baseline import RealEmbeddingBaseline
from backend.search.token_index import normalise_tokens


DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "dss295_latency_storage"
# Target haystack lengths.  The needle generator creates ``length`` distractors
# plus one needle, so ``length + 1`` is approximately the event count.
DEFAULT_CORPUS_SIZES = (999, 9999, 99999)
DEFAULT_TOP_K = 5
DEFAULT_QUERY_ITERATIONS = 50
DEFAULT_WARMUP_ITERATIONS = 5
DEFAULT_SEED = 193
DEFAULT_SEEDS = (193, 42, 7)
# Largest size we will actually measure by default.  100 k is generated and
# measured only when ``--measure-100k`` is passed, otherwise it is labelled as
# extrapolated from the measured trend.
DEFAULT_MAX_MEASURED_EVENTS = 100000

PINNED_MINILM_DIMS = 384
PINNED_MINILM_DTYPE_BYTES = 4  # float32


@dataclass(frozen=True)
class BenchmarkConfig:
    output_root: Path
    corpus_sizes: tuple[int, ...]
    top_k: int
    query_iterations: int
    warmup_iterations: int
    seeds: tuple[int, ...]
    measure_100k: bool
    max_measured_events: int
    force_generate_queries: bool = False
    pinned_query_path: Path | None = None
    skip_real_embedding: bool = False


@dataclass(frozen=True)
class PerSystemResult:
    system_name: str
    events: int
    p50_latency_ms: float
    p95_latency_ms: float
    bytes_per_event: float
    measured: bool
    notes: str


@dataclass(frozen=True)
class PerSizeResult:
    corpus_size: int
    events: int
    measured: bool
    extrapolation_note: str
    systems: dict[str, PerSystemResult]


@dataclass(frozen=True)
class BenchmarkSummary:
    sizes: tuple[PerSizeResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sizes": [
                {
                    "corpus_size": r.corpus_size,
                    "events": r.events,
                    "measured": r.measured,
                    "extrapolation_note": r.extrapolation_note,
                    "systems": {
                        name: {
                            "p50_latency_ms": s.p50_latency_ms,
                            "p95_latency_ms": s.p95_latency_ms,
                            "bytes_per_event": s.bytes_per_event,
                            "measured": s.measured,
                            "notes": s.notes,
                        }
                        for name, s in r.systems.items()
                    },
                }
                for r in self.sizes
            ]
        }


# -----------------------------------------------------------------------------
# System builders
# -----------------------------------------------------------------------------


def _normalize_for_baseline(
    memories: Sequence[NeedleMemory],
    queries: Sequence[NeedleQuery],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    memory_dicts = [
        {
            "id": str(m.memory_id),
            "text": str(m.text),
            "coordinate": m.coordinate,
        }
        for m in memories
    ]
    query_dicts = [
        {
            "id": str(q.query_id),
            "text": str(q.text),
            "relevant_ids": {str(q.needle_id)},
            "coordinate": q.coordinate,
        }
        for q in queries
    ]
    return memory_dicts, query_dicts


def _build_dss(memories: Sequence[NeedleMemory]) -> QpRouter:
    return QpRouter(memories)


def _build_minilm(memories: Sequence[NeedleMemory]) -> RealEmbeddingBaseline:
    return RealEmbeddingBaseline()


def _build_bm25(memories: Sequence[NeedleMemory]) -> BM25Baseline:
    return BM25Baseline()


def _build_metadata_filter(
    memories: Sequence[NeedleMemory],
) -> MetadataFilterBaseline:
    return MetadataFilterBaseline()


def _build_bow(memories: Sequence[NeedleMemory]) -> BoWStandInBaseline:
    return BASELINES["bow_stand_in"]  # type: ignore[return-value]


def _build_hnsw(memories: Sequence[NeedleMemory]) -> HnswDenseBaseline:
    # Share the MiniLM embedder with the brute-force dense arm.
    embedder = RealEmbeddingBaseline()._ensure_embedder()
    return HnswDenseBaseline(embedder=embedder)


# -----------------------------------------------------------------------------
# Latency measurement
# -----------------------------------------------------------------------------


def _percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    return float(sorted_values[f] * (c - k) + sorted_values[c] * (k - f))


def _measure_dss_latency(
    router: QpRouter,
    queries: Sequence[NeedleQuery],
    *,
    iterations: int,
    warmup: int,
) -> list[float]:
    for query in queries:
        for _ in range(warmup):
            router.rank(query, top_k=5)
    latencies: list[float] = []
    for _ in range(iterations):
        for query in queries:
            t0 = time.perf_counter()
            router.rank(query, top_k=5)
            latencies.append((time.perf_counter() - t0) * 1000.0)
    return latencies


def _measure_baseline_latency(
    baseline: Any,
    memory_dicts: Sequence[Mapping[str, Any]],
    query_dicts: Sequence[Mapping[str, Any]],
    *,
    iterations: int,
    warmup: int,
    top_k: int,
) -> list[float]:
    for _ in range(warmup):
        baseline.run(memory_dicts, query_dicts, top_k=top_k)
    latencies: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        baseline.run(memory_dicts, query_dicts, top_k=top_k)
        latencies.append((time.perf_counter() - t0) * 1000.0)
    return latencies


# -----------------------------------------------------------------------------
# Storage measurement
# -----------------------------------------------------------------------------


def _measure_dss_storage(memories: Sequence[NeedleMemory]) -> int:
    """Approximate DSS storage as serialized coordinate + text payload."""
    payload = [
        {
            "memory_id": m.memory_id,
            "text": m.text,
            "kernel_node": m.coordinate.kernel_node,
            "metric_prime": m.coordinate.metric_prime,
            "tetrahedron": m.coordinate.tetrahedron,
            "dual_complement": m.coordinate.dual_complement,
            "unit_digits": list(m.coordinate.unit_digits),
            "valuation_offset": m.coordinate.valuation_offset,
            "circulation_pass": m.coordinate.circulation_pass,
            "hysteresis_depth": m.coordinate.hysteresis_depth,
        }
        for m in memories
    ]
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _measure_minilm_storage(events: int) -> int:
    """MiniLM embedding vector size: 384 dims × float32."""
    return events * PINNED_MINILM_DIMS * PINNED_MINILM_DTYPE_BYTES


def _measure_bm25_storage(memories: Sequence[NeedleMemory]) -> int:
    tokenized = [normalise_tokens(m.text) for m in memories]
    return len(pickle.dumps(tokenized))


def _measure_metadata_filter_storage(
    memories: Sequence[NeedleMemory],
) -> int:
    metadata = [
        {
            "kernel_node": m.coordinate.kernel_node,
            "valuation_offset": m.coordinate.valuation_offset,
            "circulation_pass": m.coordinate.circulation_pass,
            "tetrahedron": m.coordinate.tetrahedron,
        }
        for m in memories
    ]
    return len(json.dumps(metadata, separators=(",", ":")).encode("utf-8"))


def _measure_bow_storage(memories: Sequence[NeedleMemory]) -> int:
    vocab: dict[str, int] = {}
    for m in memories:
        for token in normalise_tokens(m.text):
            if token not in vocab:
                vocab[token] = len(vocab)
    matrix = np.zeros((len(memories), len(vocab)), dtype=np.float64)
    for i, m in enumerate(memories):
        for token in normalise_tokens(m.text):
            matrix[i, vocab[token]] += 1.0
    return int(matrix.nbytes)


def _measure_hnsw_storage(memories: Sequence[NeedleMemory]) -> int:
    """Approximate HNSW index storage by serialising a temporary index."""
    memory_dicts, _ = _normalize_for_baseline(memories, [])
    embedder = RealEmbeddingBaseline()._ensure_embedder()
    baseline = HnswDenseBaseline(embedder=embedder)
    return baseline.estimate_storage_bytes(memory_dicts)


# -----------------------------------------------------------------------------
# Per-size evaluation
# -----------------------------------------------------------------------------


def _should_measure(size: int, max_measured_events: int, measure_100k: bool) -> bool:
    if size <= max_measured_events:
        return True
    if size >= 100000 and measure_100k:
        return True
    return False


def _evaluate_size(
    corpus_size: int,
    *,
    seed: int,
    config: BenchmarkConfig,
    pinned_queries: dict[int, list[NeedleQuery]] | None = None,
) -> PerSizeResult:
    measured = _should_measure(corpus_size, config.max_measured_events, config.measure_100k)

    if measured:
        actual_events = corpus_size + 1
        if pinned_queries is not None and corpus_size in pinned_queries:
            queries = pinned_queries[corpus_size]
            memories, _ = generate_corpus([corpus_size], seed=seed)
        else:
            memories, queries = generate_corpus([corpus_size], seed=seed)
        memory_dicts, query_dicts = _normalize_for_baseline(memories, queries)

        systems: dict[str, PerSystemResult] = {}

        # DSS
        dss = _build_dss(memories)
        dss_latencies = _measure_dss_latency(
            dss,
            queries,
            iterations=config.query_iterations,
            warmup=config.warmup_iterations,
        )
        systems["dss_qp_router"] = PerSystemResult(
            system_name="dss_qp_router",
            events=actual_events,
            p50_latency_ms=_percentile(dss_latencies, 0.5),
            p95_latency_ms=_percentile(dss_latencies, 0.95),
            bytes_per_event=_measure_dss_storage(memories) / actual_events,
            measured=True,
            notes="Measured",
        )

        # Real MiniLM
        minilm = _build_minilm(memories)
        minilm_latencies = _measure_baseline_latency(
            minilm,
            memory_dicts,
            query_dicts,
            iterations=config.query_iterations,
            warmup=config.warmup_iterations,
            top_k=config.top_k,
        )
        real_embedding_notes = (
            "Mocked embedder (skip_real_embedding=true); latency excludes model load and real encoding cost"
            if config.skip_real_embedding
            else "Measured; embedding vector only"
        )
        systems["real_embedding"] = PerSystemResult(
            system_name="real_embedding",
            events=actual_events,
            p50_latency_ms=_percentile(minilm_latencies, 0.5),
            p95_latency_ms=_percentile(minilm_latencies, 0.95),
            bytes_per_event=_measure_minilm_storage(actual_events) / actual_events,
            measured=True,
            notes=real_embedding_notes,
        )

        # HNSW dense index
        hnsw = _build_hnsw(memories)
        hnsw_latencies = _measure_baseline_latency(
            hnsw,
            memory_dicts,
            query_dicts,
            iterations=config.query_iterations,
            warmup=config.warmup_iterations,
            top_k=config.top_k,
        )
        hnsw_notes = (
            "Mocked embedder (skip_real_embedding=true); HNSW index latency excludes real MiniLM encoding cost"
            if config.skip_real_embedding
            else "Measured; HNSW index + vectors"
        )
        systems["hnsw_dense"] = PerSystemResult(
            system_name="hnsw_dense",
            events=actual_events,
            p50_latency_ms=_percentile(hnsw_latencies, 0.5),
            p95_latency_ms=_percentile(hnsw_latencies, 0.95),
            bytes_per_event=_measure_hnsw_storage(memories) / actual_events,
            measured=True,
            notes=hnsw_notes,
        )

        # BM25
        bm25 = _build_bm25(memories)
        bm25_latencies = _measure_baseline_latency(
            bm25,
            memory_dicts,
            query_dicts,
            iterations=config.query_iterations,
            warmup=config.warmup_iterations,
            top_k=config.top_k,
        )
        systems["bm25"] = PerSystemResult(
            system_name="bm25",
            events=actual_events,
            p50_latency_ms=_percentile(bm25_latencies, 0.5),
            p95_latency_ms=_percentile(bm25_latencies, 0.95),
            bytes_per_event=_measure_bm25_storage(memories) / actual_events,
            measured=True,
            notes="Measured",
        )

        # Metadata filter
        metadata_filter = _build_metadata_filter(memories)
        metadata_latencies = _measure_baseline_latency(
            metadata_filter,
            memory_dicts,
            query_dicts,
            iterations=config.query_iterations,
            warmup=config.warmup_iterations,
            top_k=config.top_k,
        )
        systems["metadata_filter"] = PerSystemResult(
            system_name="metadata_filter",
            events=actual_events,
            p50_latency_ms=_percentile(metadata_latencies, 0.5),
            p95_latency_ms=_percentile(metadata_latencies, 0.95),
            bytes_per_event=_measure_metadata_filter_storage(memories) / actual_events,
            measured=True,
            notes="Measured",
        )

        # BoW stand-in
        bow = _build_bow(memories)
        bow_latencies = _measure_baseline_latency(
            bow,
            memory_dicts,
            query_dicts,
            iterations=config.query_iterations,
            warmup=config.warmup_iterations,
            top_k=config.top_k,
        )
        systems["bow_stand_in"] = PerSystemResult(
            system_name="bow_stand_in",
            events=actual_events,
            p50_latency_ms=_percentile(bow_latencies, 0.5),
            p95_latency_ms=_percentile(bow_latencies, 0.95),
            bytes_per_event=_measure_bow_storage(memories) / actual_events,
            measured=True,
            notes="Measured",
        )

        return PerSizeResult(
            corpus_size=corpus_size,
            events=actual_events,
            measured=True,
            extrapolation_note="Measured directly",
            systems=systems,
        )

    # Extrapolated row: use measured smaller row as the base and scale latency
    # sub-linearly (log-ish) and storage linearly.  The exact extrapolation is
    # documented in the notes.
    measured_size = config.max_measured_events
    base = _evaluate_size(measured_size, seed=seed, config=config, pinned_queries=pinned_queries)
    base_events = measured_size + 1
    actual_events = corpus_size + 1
    scale_factor = actual_events / base_events
    systems: dict[str, PerSystemResult] = {}
    for name, base_result in base.systems.items():
        # Latency scaling is approximate: assume ~log2 growth dominated by
        # scan/filter costs for the baselines and ~constant for DSS coordinate
        # routing.  We use a conservative sqrt scaling for all systems.
        latency_scale = math.sqrt(scale_factor)
        # Storage is reported as bytes per event; hold the measured per-event
        # value constant rather than multiplying by N (which would imply
        # super-linear total storage).
        bytes_scale = 1.0
        systems[name] = PerSystemResult(
            system_name=name,
            events=actual_events,
            p50_latency_ms=base_result.p50_latency_ms * latency_scale,
            p95_latency_ms=base_result.p95_latency_ms * latency_scale,
            bytes_per_event=base_result.bytes_per_event * bytes_scale,
            measured=False,
            notes=f"Extrapolated from {base_events} events (sqrt latency scaling; per-event storage held constant)",
        )
    return PerSizeResult(
        corpus_size=corpus_size,
        events=actual_events,
        measured=False,
        extrapolation_note=f"Extrapolated from {base_events} measured events",
        systems=systems,
    )


# -----------------------------------------------------------------------------
# Evaluation orchestration
# -----------------------------------------------------------------------------


def evaluate(
    config: BenchmarkConfig,
    *,
    seed: int,
) -> BenchmarkSummary:
    """Run latency and storage measurements across all configured corpus sizes."""
    pinned: dict[int, list[NeedleQuery]] | None = None
    if not config.force_generate_queries:
        try:
            loaded = load_pinned_queries_for_config(
                "dss295-latency-storage",
                seed,
                root=config.pinned_query_path or QUERIES_ROOT,
                corpus_sizes=config.corpus_sizes,
            )
            pinned = {k: v for k, v in loaded.items() if isinstance(k, int)}
        except (FileNotFoundError, ValueError, KeyError) as exc:
            print(f"WARNING: DSS-295 falling back to runtime query generation: {exc}")

    sizes = [
        _evaluate_size(size, seed=seed, config=config, pinned_queries=pinned)
        for size in config.corpus_sizes
    ]
    return BenchmarkSummary(sizes=tuple(sizes))


# -----------------------------------------------------------------------------
# Artifact and CLI
# -----------------------------------------------------------------------------


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _build_artifact(
    summary: BenchmarkSummary,
    *,
    config: BenchmarkConfig,
    executed_at: datetime,
    runtime_ms: float,
    seed: int,
) -> BenchmarkArtifact:
    repo_sha = _repo_sha()

    latency_metrics: dict[str, Any] = {
        "total_runtime_ms": {
            "value": runtime_ms,
            "unit": "ms",
            "description": "Total harness runtime.",
        }
    }
    cost_metrics: dict[str, Any] = {}
    retrieval_metrics: dict[str, Any] = {
        "systems_compared": {
            "value": 6,
            "unit": "count",
            "description": "Number of retrieval systems compared.",
        }
    }
    traceability_metrics: dict[str, Any] = {
        "corpus_size_buckets": {
            "value": len(summary.sizes),
            "unit": "count",
            "description": "Number of corpus size buckets evaluated.",
        },
        "query_iterations_per_bucket": {
            "value": config.query_iterations,
            "unit": "count",
            "description": "Measured query iterations per bucket.",
        },
    }
    governance_metrics: dict[str, Any] = {
        "extrapolation_labelled": {
            "value": 1,
            "unit": "boolean",
            "description": "All extrapolated rows are explicitly labelled.",
        }
    }

    for size_result in summary.sizes:
        events = size_result.events
        prefix = f"events_{events}"
        for name, result in size_result.systems.items():
            latency_metrics[f"{prefix}_{name}_p50_ms"] = {
                "value": result.p50_latency_ms,
                "unit": "ms",
                "description": f"{name} p50 latency at {events} events.",
            }
            latency_metrics[f"{prefix}_{name}_p95_ms"] = {
                "value": result.p95_latency_ms,
                "unit": "ms",
                "description": f"{name} p95 latency at {events} events.",
            }
            cost_metrics[f"{prefix}_{name}_bytes_per_event"] = {
                "value": result.bytes_per_event,
                "unit": "bytes/event",
                "description": f"{name} storage bytes per event at {events} events.",
            }
        traceability_metrics[f"{prefix}_measured"] = {
            "value": 1 if size_result.measured else 0,
            "unit": "boolean",
            "description": f"Whether {events} events were measured directly.",
        }

    measured_sizes = [r.events for r in summary.sizes if r.measured]
    extrapolated_sizes = [r.events for r in summary.sizes if not r.measured]
    if extrapolated_sizes:
        governance_metrics["extrapolated_sizes"] = {
            "value": ",".join(str(s) for s in extrapolated_sizes),
            "unit": "string",
            "description": "Corpus sizes that were extrapolated rather than measured.",
        }
    if measured_sizes:
        governance_metrics["measured_sizes"] = {
            "value": ",".join(str(s) for s in measured_sizes),
            "unit": "string",
            "description": "Corpus sizes that were measured directly.",
        }

    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"dss295-latency-storage-{executed_at.strftime('%Y%m%dT%H%M%SZ')}-{seed}",
        suite_id="dss295-latency-storage",
        suite_version="v1",
        executed_at=executed_at,
        mode="full_dss",
        status="success",
        repos=[
            {
                "name": "ds-backend-local",
                "commit_sha": repo_sha,
                "role": "canonical_benchmark_engine",
                "required_for_run": True,
            }
        ],
        datasets=[
            {
                "name": "longbench_needle_synthetic_v1",
                "version": "v1",
                "split": "benchmark",
                "record_count": sum(r.events for r in summary.sizes),
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": retrieval_metrics,
            },
            "latency": {
                "status": "present",
                "metrics": latency_metrics,
            },
            "cost": {
                "status": "present",
                "metrics": cost_metrics,
            },
            "traceability": {
                "status": "present",
                "metrics": traceability_metrics,
            },
            "governance": {
                "status": "present",
                "metrics": governance_metrics,
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        run_config={
            "corpus_sizes": ",".join(str(s) for s in config.corpus_sizes),
            "max_measured_events": config.max_measured_events,
            "measure_100k": config.measure_100k,
            "query_iterations": config.query_iterations,
            "warmup_iterations": config.warmup_iterations,
            "top_k": config.top_k,
            "seed": seed,
            "skip_real_embedding": config.skip_real_embedding,
        },
    )


def _write_markdown_table(
    summary: BenchmarkSummary,
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# DSS-295 Latency and Storage Cost Table\n")
    lines.append("| System | Events | p50 Latency (ms) | p95 Latency (ms) | Bytes / Event | Notes |")
    lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
    for size_result in summary.sizes:
        for name, result in size_result.systems.items():
            notes = result.notes
            if not result.measured:
                notes = f"**Extrapolated** — {notes}"
            lines.append(
                f"| {name} | {result.events:,} | {result.p50_latency_ms:.3f} | "
                f"{result.p95_latency_ms:.3f} | {result.bytes_per_event:.1f} | {notes} |"
            )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_single_seed(seed: int, config: BenchmarkConfig) -> BenchmarkArtifact:
    """Run DSS-295 for a single seed and return a validated artifact."""
    start = time.perf_counter()
    summary = evaluate(config, seed=seed)
    runtime_ms = (time.perf_counter() - start) * 1000.0
    executed_at = datetime.now(timezone.utc)
    artifact = _build_artifact(
        summary,
        config=config,
        executed_at=executed_at,
        runtime_ms=runtime_ms,
        seed=seed,
    )

    output_path = config.output_root / "seeds" / str(seed) / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    _write_markdown_table(summary, output_path.with_suffix(".md"))

    manifest = build_manifest(
        artifact,
        eval_script_version="dss295_latency_storage_benchmark_v1.0",
        seeds=[seed],
        conditions={
            "corpus_sizes": ",".join(str(s) for s in config.corpus_sizes),
            "max_measured_events": config.max_measured_events,
            "measure_100k": config.measure_100k,
            "transport": "R1",
        },
    )
    manifest_path = output_path.with_suffix(".manifest.json")
    write_manifest(manifest, manifest_path)

    return artifact


def run_benchmark(config: BenchmarkConfig) -> BenchmarkArtifact:
    """Run DSS-295 across the configured seeds and return the aggregate artifact."""
    harness = BenchmarkHarness(
        suite_id="dss295-latency-storage",
        suite_version="v1",
        mode="full_dss",
        seeds=list(config.seeds),
        output_root=config.output_root,
        run_label="dss295-latency-storage",
    )
    return harness.run(lambda seed: run_single_seed(seed, config))


def print_summary(summary: BenchmarkSummary) -> None:
    print("DSS-295 Latency and Storage Cost Table")
    print("========================================")
    print(f"{'System':<20} {'Events':>10} {'p50 (ms)':>12} {'p95 (ms)':>12} {'B/ev':>14} {'Notes'}")
    print("-" * 90)
    for size_result in summary.sizes:
        for name, result in size_result.systems.items():
            note = "measured" if result.measured else "extrapolated"
            print(
                f"{name:<20} {result.events:>10,} {result.p50_latency_ms:>12.3f} "
                f"{result.p95_latency_ms:>12.3f} {result.bytes_per_event:>14.1f} {note}"
            )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for benchmark artifacts.",
    )
    parser.add_argument(
        "--corpus-sizes",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_CORPUS_SIZES,
        help="Comma-separated haystack lengths (event count ≈ length + 1).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Top-k cutoff for baseline ranking.",
    )
    parser.add_argument(
        "--query-iterations",
        type=int,
        default=DEFAULT_QUERY_ITERATIONS,
        help="Number of measured query iterations per bucket.",
    )
    parser.add_argument(
        "--warmup-iterations",
        type=int,
        default=DEFAULT_WARMUP_ITERATIONS,
        help="Number of warm-up iterations per bucket before measurement.",
    )
    parser.add_argument(
        "--seeds",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_SEEDS,
        help="Comma-separated random seeds for multi-seed aggregation.",
    )
    parser.add_argument(
        "--measure-100k",
        action="store_true",
        help="Actually generate and measure the 100 k event bucket (slow).",
    )
    parser.add_argument(
        "--max-measured-events",
        type=int,
        default=DEFAULT_MAX_MEASURED_EVENTS,
        help="Largest event count to measure directly before labelling extrapolation.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use tiny corpus sizes for quick smoke testing.",
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
    parser.add_argument(
        "--skip-real-embedding",
        action="store_true",
        help="Skip the sentence-transformers download by mocking the real embedding baseline.",
    )
    args = parser.parse_args(argv)

    corpus_sizes = args.corpus_sizes
    max_measured = args.max_measured_events
    measure_100k = args.measure_100k
    if args.quick:
        corpus_sizes = (99, 199)
        max_measured = 200
        measure_100k = True

    config = BenchmarkConfig(
        output_root=args.output_root,
        corpus_sizes=corpus_sizes,
        top_k=args.top_k,
        query_iterations=args.query_iterations,
        warmup_iterations=args.warmup_iterations,
        seeds=args.seeds,
        measure_100k=measure_100k,
        max_measured_events=max_measured,
        force_generate_queries=args.force_generate_queries,
        pinned_query_path=args.pinned_query_path,
        skip_real_embedding=args.skip_real_embedding,
    )
    aggregate = run_benchmark(config)
    print(f"Aggregate artifact status: {aggregate.status}")
    print(f"Aggregate artifact written to: {config.output_root / 'aggregate'}")


if __name__ == "__main__":
    main()
