"""Scaling and stress benchmark suite for DSS-230.

Exercises DSS retrieval at large context sizes, under concurrent load, with
noisy/adversarial queries, and across a simple multi-turn agentic trace.  No
live LLM calls are made; all numbers are deterministic retrieval metrics.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backend.benchmarks.artifact_schema import (
    BenchmarkArtifact,
    HardwareProfile as SchemaHardwareProfile,
)
from backend.benchmarks.determinism import set_global_seed
from backend.benchmarks.hardware import detect_hardware_profile
from backend.benchmarks.longbench_needle_benchmark import (
    DEFAULT_SEED,
    NeedleMemory,
    NeedleQuery,
    QpRouter,
    VectorRAGBaseline,
    _distractor_text,
    _make_coordinate,
    _random_kernel_node,
    generate_corpus,
)
from backend.search.token_index import normalise_tokens


DEFAULT_OUTPUT_ROOT: Path = Path(__file__).parent / "output" / "scaling"
DEFAULT_HAYSTACK_LENGTH: int = 26_000  # yields ~512K tokens
DEFAULT_TOP_K: int = 10
DEFAULT_SEED: int = DEFAULT_SEED
DEFAULT_CONCURRENT_WORKERS: int = 4
DEFAULT_CONCURRENT_REQUESTS: int = 20
FULL_DSS_SEMANTIC_WEIGHT: float = 0.3
FULL_DSS_COORDINATE_WEIGHT: float = 0.7


@dataclass(frozen=True)
class ModeResult:
    mode: str
    recall_at_1: float
    recall_at_k: float
    mrr: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    queries: int
    total_tokens: int


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _total_tokens(memories: Sequence[NeedleMemory]) -> int:
    return sum(len(normalise_tokens(m.text)) for m in memories)


def _rank_for_mode(
    memories: Sequence[NeedleMemory],
    query: NeedleQuery,
    *,
    mode: str,
) -> int | None:
    vector_baseline = VectorRAGBaseline(memories)
    qp_router = QpRouter(memories)

    if mode == "semantic_only":
        ranked = [mid for mid, _ in vector_baseline.rank(query.text, top_k=len(memories))]
    elif mode == "coordinate_guided":
        ranked = [mid for mid, _ in qp_router.rank(query, top_k=len(memories))]
    elif mode == "full_dss":
        vector_scores = {mid: score for mid, score in vector_baseline.rank(query.text, top_k=len(memories))}
        qp_scores = {mid: score for mid, score in qp_router.rank(query, top_k=len(memories))}
        blended = []
        for memory in memories:
            score = (
                FULL_DSS_SEMANTIC_WEIGHT * vector_scores.get(memory.memory_id, 0.0)
                + FULL_DSS_COORDINATE_WEIGHT * qp_scores.get(memory.memory_id, 0.0)
            )
            blended.append((score, memory.memory_id))
        blended.sort(key=lambda pair: pair[0], reverse=True)
        ranked = [mid for _, mid in blended]
    else:
        raise ValueError(f"unknown mode: {mode}")

    return ranked.index(query.needle_id) if query.needle_id in ranked else None


def _evaluate_mode(
    memories: Sequence[NeedleMemory],
    queries: Sequence[NeedleQuery],
    *,
    mode: str,
    top_k: int,
) -> ModeResult:
    ranks: list[int | None] = []
    latencies: list[float] = []
    for query in queries:
        start = time.perf_counter()
        rank = _rank_for_mode(memories, query, mode=mode)
        latencies.append((time.perf_counter() - start) * 1000.0)
        ranks.append(rank)

    recalls = [1 if r is not None and r < top_k else 0 for r in ranks]
    recalls_at_1 = [1 if r == 0 else 0 for r in ranks]
    mrrs = [1.0 / (r + 1) if r is not None else 0.0 for r in ranks]
    sorted_latencies = sorted(latencies)
    n = len(sorted_latencies)

    return ModeResult(
        mode=mode,
        recall_at_1=sum(recalls_at_1) / len(queries) if queries else 0.0,
        recall_at_k=sum(recalls) / len(queries) if queries else 0.0,
        mrr=sum(mrrs) / len(queries) if queries else 0.0,
        avg_latency_ms=statistics.mean(latencies) if latencies else 0.0,
        p50_latency_ms=sorted_latencies[n // 2] if n else 0.0,
        p95_latency_ms=sorted_latencies[int(n * 0.95)] if n else 0.0,
        queries=len(queries),
        total_tokens=_total_tokens(memories),
    )


def _build_artifact(
    result: ModeResult,
    *,
    suite_id: str,
    executed_at: datetime,
    repo_sha: str,
    run_config: dict[str, Any],
) -> BenchmarkArtifact:
    run_suffix = executed_at.strftime("%Y%m%dT%H%M%SZ")
    hardware = SchemaHardwareProfile(**detect_hardware_profile().to_dict())
    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"{suite_id}-{result.mode}-{run_suffix}",
        suite_id=suite_id,
        suite_version="v1",
        executed_at=executed_at,
        mode=result.mode,  # type: ignore[arg-type]
        status="partial",
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
                "name": "longbench_needle_synthetic",
                "version": "v1",
                "split": "benchmark",
                "record_count": result.queries,
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "recall_at_1": {"value": result.recall_at_1, "unit": "ratio"},
                    "recall_at_k": {"value": result.recall_at_k, "unit": "ratio"},
                    "mrr": {"value": result.mrr, "unit": "ratio"},
                    "queries": {"value": result.queries, "unit": "count"},
                },
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "avg_latency_ms": {"value": result.avg_latency_ms, "unit": "ms"},
                    "p50_latency_ms": {"value": result.p50_latency_ms, "unit": "ms"},
                    "p95_latency_ms": {"value": result.p95_latency_ms, "unit": "ms"},
                },
            },
            "cost": {
                "status": "present",
                "metrics": {
                    "total_tokens": {"value": result.total_tokens, "unit": "tokens"},
                },
            },
            "traceability": {
                "status": "absent",
                "absence_reason": "scaling_runner_does_not_measure_traceability_yet",
            },
            "governance": {
                "status": "absent",
                "absence_reason": "scaling_runner_does_not_measure_governance_yet",
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        hardware=hardware,
        run_config=run_config,
    )


def _write_artifact(artifact: BenchmarkArtifact, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")


def run_long_context(
    *,
    haystack_length: int,
    top_k: int,
    seed: int,
    output_root: Path,
    repo_sha: str,
) -> dict[str, Any]:
    """512K-token needle-in-haystack benchmark across three retrieval modes."""
    suite_id = "scaling-long-context"
    set_global_seed(seed)
    memories, queries = generate_corpus([haystack_length], seed=seed)
    total_tokens = _total_tokens(memories)
    print(f"[long-context] Generated {len(memories):,} memories, ~{total_tokens:,} tokens")

    results: dict[str, Any] = {}
    for mode in ("semantic_only", "coordinate_guided", "full_dss"):
        executed_at = datetime.now(timezone.utc)
        result = _evaluate_mode(memories, queries, mode=mode, top_k=top_k)
        artifact = _build_artifact(
            result,
            suite_id=suite_id,
            executed_at=executed_at,
            repo_sha=repo_sha,
            run_config={
                "scenario": "long_context",
                "haystack_length": haystack_length,
                "total_tokens": total_tokens,
                "top_k": top_k,
                "seed": seed,
            },
        )
        path = output_root / suite_id / mode / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        _write_artifact(artifact, path)
        results[mode] = {
            "recall_at_1": result.recall_at_1,
            "recall_at_k": result.recall_at_k,
            "mrr": result.mrr,
            "avg_latency_ms": result.avg_latency_ms,
            "p95_latency_ms": result.p95_latency_ms,
            "total_tokens": result.total_tokens,
        }
    return {"suite_id": suite_id, "haystack_length": haystack_length, "total_tokens": total_tokens, "modes": results}


def run_concurrent_load(
    *,
    haystack_length: int,
    top_k: int,
    seed: int,
    output_root: Path,
    repo_sha: str,
    workers: int,
    requests: int,
) -> dict[str, Any]:
    """Run repeated coordinate-guided retrieval queries across a thread pool."""
    suite_id = "scaling-concurrent-load"
    set_global_seed(seed)
    memories, queries = generate_corpus([haystack_length], seed=seed)
    query = queries[0]
    print(f"[concurrent-load] {workers} workers, {requests} requests, {len(memories):,} memories")

    def _one(_: int) -> tuple[int | None, float]:
        start = time.perf_counter()
        rank = _rank_for_mode(memories, query, mode="coordinate_guided")
        latency = (time.perf_counter() - start) * 1000.0
        return rank, latency

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(_one, range(requests)))
    total_time = time.perf_counter() - start

    ranks = [r for r, _ in outcomes]
    latencies = [lat for _, lat in outcomes]
    sorted_latencies = sorted(latencies)
    n = len(sorted_latencies)

    result = {
        "workers": workers,
        "requests": requests,
        "total_time_ms": total_time * 1000.0,
        "throughput_qps": requests / total_time if total_time else 0.0,
        "recall_at_k": sum(1 for r in ranks if r is not None and r < top_k) / requests,
        "avg_latency_ms": statistics.mean(latencies),
        "p50_latency_ms": sorted_latencies[n // 2] if n else 0.0,
        "p95_latency_ms": sorted_latencies[int(n * 0.95)] if n else 0.0,
        "p99_latency_ms": sorted_latencies[int(n * 0.99)] if n else 0.0,
        "max_latency_ms": max(latencies),
    }

    executed_at = datetime.now(timezone.utc)
    artifact = _build_artifact(
        ModeResult(
            mode="coordinate_guided",
            recall_at_1=0.0,
            recall_at_k=result["recall_at_k"],
            mrr=0.0,
            avg_latency_ms=result["avg_latency_ms"],
            p50_latency_ms=result["p50_latency_ms"],
            p95_latency_ms=result["p95_latency_ms"],
            queries=requests,
            total_tokens=_total_tokens(memories),
        ),
        suite_id=suite_id,
        executed_at=executed_at,
        repo_sha=repo_sha,
        run_config={
            "scenario": "concurrent_load",
            "haystack_length": haystack_length,
            "top_k": top_k,
            "seed": seed,
            "workers": workers,
            "requests": requests,
            "throughput_qps": result["throughput_qps"],
        },
    )
    path = output_root / suite_id / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    _write_artifact(artifact, path)
    return {"suite_id": suite_id, **result}


def run_noisy_inputs(
    *,
    haystack_length: int,
    top_k: int,
    seed: int,
    output_root: Path,
    repo_sha: str,
) -> dict[str, Any]:
    """Inject adversarial noise memories and measure coordinate-guided degradation."""
    suite_id = "scaling-noisy-inputs"
    set_global_seed(seed)
    # Use a smaller base corpus so coordinate-guided recall is perfect at zero noise.
    base_length = max(haystack_length // 10, 500)
    base_memories, queries = generate_corpus([base_length], seed=seed)
    base_query = queries[0]
    rng = random.Random(seed)
    query_tokens = set(normalise_tokens(base_query.text))
    print(f"[noisy-inputs] base corpus {base_length} memories, adding adversarial noise")

    noise_levels = [0.0, 0.25, 0.5, 0.75]
    results: list[dict[str, Any]] = []
    for level in noise_levels:
        noisy_memories = list(base_memories)
        noise_count = int(base_length * level)
        for i in range(noise_count):
            coord = _make_coordinate(
                kernel_node=_random_kernel_node(rng),
                valuation_offset=rng.randint(0, 3),
                circulation_pass=rng.randint(0, 3),
                hysteresis_depth=round(rng.uniform(0.0, 0.3), 2),
                dual_valid=None,
            )
            noisy_memories.append(
                NeedleMemory(
                    memory_id=f"noise:{level}:{i}",
                    text=_distractor_text(rng, query_tokens),
                    coordinate=coord,
                    is_needle=False,
                    length=base_query.length,
                )
            )
        result = _evaluate_mode(noisy_memories, [base_query], mode="coordinate_guided", top_k=top_k)
        results.append({
            "noise_level": level,
            "recall_at_k": result.recall_at_k,
            "recall_at_1": result.recall_at_1,
            "mrr": result.mrr,
            "avg_latency_ms": result.avg_latency_ms,
        })

    executed_at = datetime.now(timezone.utc)
    artifact = _build_artifact(
        ModeResult(
            mode="coordinate_guided",
            recall_at_1=results[0]["recall_at_1"],
            recall_at_k=results[0]["recall_at_k"],
            mrr=results[0]["mrr"],
            avg_latency_ms=results[0]["avg_latency_ms"],
            p50_latency_ms=0.0,
            p95_latency_ms=0.0,
            queries=len(results),
            total_tokens=_total_tokens(base_memories),
        ),
        suite_id=suite_id,
        executed_at=executed_at,
        repo_sha=repo_sha,
        run_config={
            "scenario": "noisy_inputs",
            "base_haystack_length": base_length,
            "top_k": top_k,
            "seed": seed,
            "noise_levels": ",".join(str(x) for x in noise_levels),
        },
    )
    path = output_root / suite_id / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    _write_artifact(artifact, path)
    return {"suite_id": suite_id, "noise_levels": results}


def run_multi_turn(
    *,
    turns: int,
    top_k: int,
    seed: int,
    output_root: Path,
    repo_sha: str,
) -> dict[str, Any]:
    """Synthetic agentic trace: each turn adds a fact and later queries it."""
    suite_id = "scaling-multi-turn"
    set_global_seed(seed)
    rng = random.Random(seed)

    # Build a chain of turn memories.
    memories: list[NeedleMemory] = []
    turn_queries: list[NeedleQuery] = []
    for i in range(turns):
        fact = f"Turn {i} committed value {rng.randint(1000, 9999)}."
        coord = _make_turn_coordinate(i)
        mid = f"turn:{i}"
        memories.append(
            NeedleMemory(
                memory_id=mid,
                text=fact,
                coordinate=coord,
                is_needle=False,
                length=1,
            )
        )
        if i > 0:
            # Query the fact from two turns ago.
            target_turn = i - 2
            target_id = f"turn:{target_turn}"
            query = NeedleQuery(
                query_id=f"turn_query:{i}",
                text=f"What value did turn {target_turn} commit?",
                coordinate=memories[target_turn].coordinate,
                needle_id=target_id,
                length=1,
            )
            turn_queries.append(query)

    result = _evaluate_mode(memories, turn_queries, mode="coordinate_guided", top_k=top_k)
    executed_at = datetime.now(timezone.utc)
    artifact = _build_artifact(
        result,
        suite_id=suite_id,
        executed_at=executed_at,
        repo_sha=repo_sha,
        run_config={
            "scenario": "multi_turn",
            "turns": turns,
            "top_k": top_k,
            "seed": seed,
        },
    )
    path = output_root / suite_id / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    _write_artifact(artifact, path)
    return {
        "suite_id": suite_id,
        "turns": turns,
        "queries": result.queries,
        "recall_at_k": result.recall_at_k,
        "mrr": result.mrr,
        "avg_latency_ms": result.avg_latency_ms,
    }


TURN_NODES = ["Eq0", "Eq1", "Eq2", "Eq3", "Eq4", "Eq5", "Eq6", "Eq7", "Eq8", "Eq9"]


def _make_turn_coordinate(turn: int) -> QpCoordinate:
    """Give each turn a distinct metric prime so Qp filters isolate it."""
    kernel_node = TURN_NODES[turn % len(TURN_NODES)]
    return _make_coordinate(
        kernel_node=kernel_node,
        valuation_offset=1,
        circulation_pass=turn,
        hysteresis_depth=round(turn * 0.1, 2),
        dual_valid=True,
    )


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Scaling & Stress Testing Report",
        "",
        f"**Generated:** {report['generated_at']}  ",
        f"**Commit:** {report['repo_sha']}",
        "",
        "## Long-context (512K-token) retrieval",
        "",
    ]
    lc = report["long_context"]
    lines.append(f"Haystack length: {lc['haystack_length']:,} memories, ~{lc['total_tokens']:,} tokens")
    lines.append("")
    lines.append("| Mode | Recall@1 | Recall@K | MRR | Avg latency (ms) | P95 latency (ms) |")
    lines.append("|------|----------|----------|-----|------------------|------------------|")
    for mode, metrics in lc["modes"].items():
        lines.append(
            f"| {mode} | {metrics['recall_at_1']:.3f} | {metrics['recall_at_k']:.3f} | "
            f"{metrics['mrr']:.3f} | {metrics['avg_latency_ms']:.2f} | {metrics['p95_latency_ms']:.2f} |"
        )

    cl = report["concurrent_load"]
    lines.extend([
        "",
        "## Concurrent load",
        "",
        f"Workers: {cl['workers']}, requests: {cl['requests']}",
        f"Throughput: {cl['throughput_qps']:.2f} queries/s",
        f"Recall@K: {cl['recall_at_k']:.3f}",
        f"Avg latency: {cl['avg_latency_ms']:.2f} ms",
        f"P50: {cl['p50_latency_ms']:.2f} ms, P95: {cl['p95_latency_ms']:.2f} ms, "
        f"P99: {cl['p99_latency_ms']:.2f} ms, Max: {cl['max_latency_ms']:.2f} ms",
    ])

    ni = report["noisy_inputs"]
    lines.extend(["", "## Noisy / adversarial inputs", ""])
    lines.append("| Noise level | Recall@1 | Recall@K | MRR |")
    lines.append("|-------------|----------|----------|-----|")
    for row in ni["noise_levels"]:
        lines.append(
            f"| {row['noise_level']:.2f} | {row['recall_at_1']:.3f} | {row['recall_at_k']:.3f} | {row['mrr']:.3f} |"
        )

    mt = report["multi_turn"]
    lines.extend(["", "## Multi-turn agentic trace", ""])
    lines.append(f"Turns: {mt['turns']}, queries: {mt['queries']}")
    lines.append(f"Recall@K: {mt['recall_at_k']:.3f}, MRR: {mt['mrr']:.3f}, Avg latency: {mt['avg_latency_ms']:.2f} ms")
    lines.append("")
    return "\n".join(lines)


def run_scaling_suite(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    haystack_length: int = DEFAULT_HAYSTACK_LENGTH,
    top_k: int = DEFAULT_TOP_K,
    seed: int = DEFAULT_SEED,
    concurrent_workers: int = DEFAULT_CONCURRENT_WORKERS,
    concurrent_requests: int = DEFAULT_CONCURRENT_REQUESTS,
    multi_turn_turns: int = 20,
) -> dict[str, Any]:
    """Run the full scaling and stress suite and write a JSON/Markdown report."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    repo_sha = _repo_sha()
    executed_at = datetime.now(timezone.utc)

    report = {
        "report_schema_version": "1.0.0",
        "generated_at": executed_at.strftime("%Y%m%dT%H%M%SZ"),
        "repo_sha": repo_sha,
        "long_context": run_long_context(
            haystack_length=haystack_length,
            top_k=top_k,
            seed=seed,
            output_root=output_root,
            repo_sha=repo_sha,
        ),
        "concurrent_load": run_concurrent_load(
            haystack_length=max(haystack_length // 4, 1_000),
            top_k=top_k,
            seed=seed,
            output_root=output_root,
            repo_sha=repo_sha,
            workers=concurrent_workers,
            requests=concurrent_requests,
        ),
        "noisy_inputs": run_noisy_inputs(
            haystack_length=max(haystack_length // 4, 1_000),
            top_k=top_k,
            seed=seed,
            output_root=output_root,
            repo_sha=repo_sha,
        ),
        "multi_turn": run_multi_turn(
            turns=multi_turn_turns,
            top_k=top_k,
            seed=seed,
            output_root=output_root,
            repo_sha=repo_sha,
        ),
    }

    json_path = output_root / f"scaling_report_{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path = output_root / f"scaling_report_{executed_at.strftime('%Y%m%dT%H%M%SZ')}.md"
    md_path.write_text(_to_markdown(report), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--haystack-length", type=int, default=DEFAULT_HAYSTACK_LENGTH)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--concurrent-workers", type=int, default=DEFAULT_CONCURRENT_WORKERS)
    parser.add_argument("--concurrent-requests", type=int, default=DEFAULT_CONCURRENT_REQUESTS)
    parser.add_argument("--multi-turn-turns", type=int, default=20)
    args = parser.parse_args(argv)

    run_scaling_suite(
        output_root=args.output_root,
        haystack_length=args.haystack_length,
        top_k=args.top_k,
        seed=args.seed,
        concurrent_workers=args.concurrent_workers,
        concurrent_requests=args.concurrent_requests,
        multi_turn_turns=args.multi_turn_turns,
    )


__all__ = (
    "run_scaling_suite",
    "run_long_context",
    "run_concurrent_load",
    "run_noisy_inputs",
    "run_multi_turn",
)

if __name__ == "__main__":
    main()
