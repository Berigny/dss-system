"""Run a retrieval benchmark against the DualSubstrate memory stack."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from backend.benchmarks.artifact_schema import BenchmarkArtifact, BenchmarkMode
from backend.retrieval import p_adic_distance
from backend.search.token_index import normalise_tokens

try:  # Production prime set used by the ledger and inference lane
    from core.ledger import PRIME_ARRAY as PRIMES
except Exception:  # pragma: no cover - fallback to a small deterministic prime list
    from backend.search.token_index import _ensure_primes

    PRIMES = tuple(_ensure_primes(16))


@dataclass
class QuerySpec:
    entity: str
    query: str
    relevant_texts: set[str]


class BenchmarkMemoryService:
    """Minimal in-memory adapter that mirrors the backend memory surface area."""

    def __init__(self) -> None:
        self._entries: list[dict[str, object]] = []
        self._token_map: MutableMapping[str, int] = {}

    def _token_prime(self, token: str) -> int:
        token = token.strip().lower()
        if token in self._token_map:
            return self._token_map[token]

        index = len(self._token_map)
        prime = PRIMES[index % len(PRIMES)]
        self._token_map[token] = prime
        return prime

    def _factors_for_text(self, text: str) -> list[dict[str, object]]:
        tokens = normalise_tokens(text)
        factors = []
        for token in tokens:
            prime = self._token_prime(token)
            factors.append({"prime": prime, "delta": 1})
        return factors

    # --- production-style memory hooks ---------------------------------
    def anchor_memory(self, *, entity: str, text: str) -> Mapping[str, object]:
        factors = self._factors_for_text(text)
        entry = {"entity": entity, "text": text, "factors": factors}
        self._entries.append(entry)
        return entry

    def clear_entity(self, entity: str) -> None:
        self._entries = [row for row in self._entries if row.get("entity") != entity]

    # --- protocol support for fuzzy_retrieve ----------------------------
    def get_all_memories(self, entity: str | None = None) -> Sequence[Mapping[str, object]]:
        if entity is None:
            return list(self._entries)
        return [row for row in self._entries if row.get("entity") == entity]

    def anchor(self, text: str, entity: str | None = None) -> Mapping[str, object]:
        return self.anchor_memory(entity=entity or "", text=text)


@dataclass
class BenchmarkResult:
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    avg_latency_ms: float
    token_cost: float
    queries: int

    def as_dict(self) -> dict[str, object]:
        return {
            "recall_at_1": self.recall_at_1,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "mrr": self.mrr,
            "avg_latency_ms": self.avg_latency_ms,
            "token_cost": self.token_cost,
            "queries": self.queries,
        }


@dataclass(frozen=True)
class RunnerConfig:
    mode: BenchmarkMode
    suite_id: str
    suite_version: str
    dataset_version: str
    top_k: int


@dataclass(frozen=True)
class Phase1SuiteConfig:
    suite_id: str
    suite_version: str
    dataset_filename: str
    dataset_version: str


PHASE1_RETRIEVAL_SUITES: dict[str, Phase1SuiteConfig] = {
    "MuSiQue": Phase1SuiteConfig(
        suite_id="MuSiQue",
        suite_version="phase1-v1",
        dataset_filename="musique_phase1_dataset.jsonl",
        dataset_version="phase1-v1",
    ),
    "HotpotQA": Phase1SuiteConfig(
        suite_id="HotpotQA",
        suite_version="phase1-v1",
        dataset_filename="hotpotqa_phase1_dataset.jsonl",
        dataset_version="phase1-v1",
    ),
    "2WikiMultiHopQA": Phase1SuiteConfig(
        suite_id="2WikiMultiHopQA",
        suite_version="phase1-v1",
        dataset_filename="2wikimultihopqa_phase1_dataset.jsonl",
        dataset_version="phase1-v1",
    ),
}

PHASE1_MEMORY_SUITES: dict[str, Phase1SuiteConfig] = {
    "LongMemEval": Phase1SuiteConfig(
        suite_id="LongMemEval",
        suite_version="phase1-v1",
        dataset_filename="longmemeval_phase1_dataset.jsonl",
        dataset_version="phase1-v1",
    ),
    "LoCoMo": Phase1SuiteConfig(
        suite_id="LoCoMo",
        suite_version="phase1-v1",
        dataset_filename="locomo_phase1_dataset.jsonl",
        dataset_version="phase1-v1",
    ),
}

PHASE1_ALL_SUITES: dict[str, Phase1SuiteConfig] = {
    **PHASE1_RETRIEVAL_SUITES,
    **PHASE1_MEMORY_SUITES,
}


def phase1_suite_config(suite_name: str) -> Phase1SuiteConfig:
    return PHASE1_ALL_SUITES[str(suite_name).strip()]


MODE_CONFIG: dict[BenchmarkMode, dict[str, Any]] = {
    "semantic_only": {
        "label": "Deterministic lexical retrieval baseline.",
        "semantic_weight": 1.0,
        "p_adic_weight": 0.0,
    },
    "coordinate_guided": {
        "label": "Prime-factor coordinate retrieval baseline.",
        "semantic_weight": 0.0,
        "p_adic_weight": 1.0,
    },
    "full_dss": {
        "label": "Blended lexical plus coordinate-guided retrieval baseline.",
        "semantic_weight": 0.6,
        "p_adic_weight": 0.4,
    },
}


def build_artifact(
    result: BenchmarkResult,
    *,
    config: RunnerConfig,
    dataset_path: Path,
    executed_at: datetime,
    repo_sha: str,
    artefact_schema_version: str,
) -> BenchmarkArtifact:
    run_suffix = executed_at.strftime("%Y%m%dT%H%M%SZ")
    return BenchmarkArtifact(
        artefact_schema_version=artefact_schema_version,
        run_id=f"{config.suite_id}-{config.mode}-{run_suffix}",
        suite_id=config.suite_id,
        suite_version=config.suite_version,
        executed_at=executed_at,
        mode=config.mode,
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
                "name": dataset_path.stem,
                "version": config.dataset_version,
                "split": "benchmark",
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "recall_at_10": {
                        "value": result.recall_at_10,
                        "unit": "ratio",
                        "description": "Fraction of benchmark queries with a relevant memory in the top 10 results.",
                    },
                    "recall_at_1": {
                        "value": result.recall_at_1,
                        "unit": "ratio",
                        "description": "Fraction of benchmark queries with a relevant memory in the top result.",
                    },
                    "recall_at_5": {
                        "value": result.recall_at_5,
                        "unit": "ratio",
                        "description": "Fraction of benchmark queries with a relevant memory in the top 5 results.",
                    },
                    "mrr": {
                        "value": result.mrr,
                        "unit": "ratio",
                        "description": "Mean reciprocal rank across benchmark queries.",
                    },
                    "queries": {
                        "value": result.queries,
                        "unit": "count",
                        "description": "Number of evaluated benchmark queries.",
                    },
                },
            },
            "traceability": {
                "status": "absent",
                "absence_reason": "dual_retrieval_runner_does_not_measure_traceability_yet",
            },
            "governance": {
                "status": "absent",
                "absence_reason": "dual_retrieval_runner_does_not_measure_governance_yet",
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "avg_latency_ms": {
                        "value": result.avg_latency_ms,
                        "unit": "ms",
                        "description": "Average end-to-end benchmark query latency.",
                    }
                },
            },
            "cost": {
                "status": "present",
                "metrics": {
                    "token_cost": {
                        "value": result.token_cost,
                        "unit": "tokens",
                        "description": "Estimated token cost for the retrieval benchmark run.",
                    }
                },
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0,
        },
        run_config={
            "mode_label": str(MODE_CONFIG[config.mode]["label"]),
            "top_k": config.top_k,
            "dataset_name": dataset_path.name,
            "phase_1_activation_family": (
                "retrieval_and_multihop"
                if config.suite_id in PHASE1_RETRIEVAL_SUITES
                else "long_memory"
                if config.suite_id in PHASE1_MEMORY_SUITES
                else "custom"
            ),
        },
    )


def build_failed_artifact(
    *,
    config: RunnerConfig,
    dataset_path: Path,
    executed_at: datetime,
    repo_sha: str,
    artefact_schema_version: str,
    failure_reason: str,
) -> BenchmarkArtifact:
    run_suffix = executed_at.strftime("%Y%m%dT%H%M%SZ")
    absent_groups = {
        "retrieval": {"status": "absent", "absence_reason": "run_failed_before_metrics_emitted"},
        "traceability": {"status": "absent", "absence_reason": "run_failed_before_metrics_emitted"},
        "governance": {"status": "absent", "absence_reason": "run_failed_before_metrics_emitted"},
        "latency": {"status": "absent", "absence_reason": "run_failed_before_metrics_emitted"},
        "cost": {"status": "absent", "absence_reason": "run_failed_before_metrics_emitted"},
    }
    return BenchmarkArtifact(
        artefact_schema_version=artefact_schema_version,
        run_id=f"{config.suite_id}-{config.mode}-{run_suffix}",
        suite_id=config.suite_id,
        suite_version=config.suite_version,
        executed_at=executed_at,
        mode=config.mode,
        status="failed",
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
                "name": dataset_path.stem,
                "version": config.dataset_version,
                "split": "benchmark",
            }
        ],
        metrics=absent_groups,
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0,
        },
        failure_reason=failure_reason,
        run_config={
            "mode_label": str(MODE_CONFIG[config.mode]["label"]),
            "top_k": config.top_k,
            "dataset_name": dataset_path.name,
        },
    )


def lexical_similarity(query: str, candidate_text: str) -> float:
    query_tokens = set(normalise_tokens(query))
    candidate_tokens = set(normalise_tokens(candidate_text))
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens)
    return float(overlap) / math.sqrt(float(len(query_tokens) * len(candidate_tokens)))


def coordinate_similarity(
    service: BenchmarkMemoryService,
    query: str,
    candidate: Mapping[str, object],
    *,
    entity: str,
) -> float:
    query_anchor = service.anchor(query, entity=entity)
    query_factors = query_anchor.get("factors") if isinstance(query_anchor, Mapping) else []
    candidate_factors = candidate.get("factors") if isinstance(candidate, Mapping) else []
    distance, overlap = p_adic_distance(query_factors or [], candidate_factors or [])
    if overlap == 0 or distance == float("inf"):
        return 0.0
    return 1.0 / (1.0 + distance)


def ranked_results_for_mode(
    service: BenchmarkMemoryService,
    spec: QuerySpec,
    *,
    mode: BenchmarkMode,
    top_k: int,
) -> list[Mapping[str, object]]:
    mode_profile = MODE_CONFIG[mode]
    memories = service.get_all_memories(spec.entity)
    scored: list[tuple[float, Mapping[str, object]]] = []
    for row in memories:
        text = str(row.get("text") or "")
        semantic_score = lexical_similarity(spec.query, text)
        p_adic_score = coordinate_similarity(service, spec.query, row, entity=spec.entity)
        score = (mode_profile["semantic_weight"] * semantic_score) + (
            mode_profile["p_adic_weight"] * p_adic_score
        )
        enriched = dict(row)
        enriched["score"] = score
        enriched["semantic_similarity"] = semantic_score
        enriched["p_adic_similarity"] = p_adic_score
        scored.append((float(score), enriched))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [payload for _, payload in scored[:top_k]]


def output_path_for_run(output_root: Path, *, config: RunnerConfig, executed_at: datetime) -> Path:
    timestamp = executed_at.strftime("%Y%m%dT%H%M%SZ")
    return output_root / config.suite_id / config.suite_version / config.mode / f"{timestamp}.json"


def write_benchmark_artifact(artifact: BenchmarkArtifact, target_output: Path) -> None:
    target_output.parent.mkdir(parents=True, exist_ok=True)
    target_output.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )


def run_phase1_suite_benchmark(
    *,
    suite_name: str,
    mode: BenchmarkMode,
    output_root: Path,
    repo_sha: str,
    artefact_schema_version: str = "1.0.0",
    top_k: int = 10,
    executed_at: datetime | None = None,
    write_output: bool = True,
) -> tuple[BenchmarkArtifact, Path]:
    suite = phase1_suite_config(suite_name)
    dataset_path = Path(__file__).with_name(suite.dataset_filename)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    run_time = executed_at or datetime.now(timezone.utc)
    config = RunnerConfig(
        mode=mode,
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        dataset_version=suite.dataset_version,
        top_k=top_k,
    )
    try:
        service = BenchmarkMemoryService()
        seed_memories(service, dataset_path)
        _, specs = load_dataset(dataset_path)
        result = evaluate(service, specs, mode=mode, top_k=top_k)
        artifact = build_artifact(
            result,
            config=config,
            dataset_path=dataset_path,
            executed_at=run_time,
            repo_sha=repo_sha,
            artefact_schema_version=artefact_schema_version,
        )
    except Exception as exc:
        artifact = build_failed_artifact(
            config=config,
            dataset_path=dataset_path,
            executed_at=run_time,
            repo_sha=repo_sha,
            artefact_schema_version=artefact_schema_version,
            failure_reason=str(exc),
        )

    target_output = output_path_for_run(output_root, config=config, executed_at=run_time)
    if write_output:
        write_benchmark_artifact(artifact, target_output)
    return artifact, target_output


def load_dataset(path: Path) -> tuple[list[str], list[QuerySpec]]:
    entities: set[str] = set()
    queries: list[QuerySpec] = []

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            entity = str(payload.get("entity", "")) or "default"
            entities.add(entity)
            for query in payload.get("queries", []):
                text = str(query.get("query", "")).strip()
                if not text:
                    continue
                relevant = query.get("relevant") or query.get("answers") or []
                queries.append(
                    QuerySpec(
                        entity=entity,
                        query=text,
                        relevant_texts={str(item) for item in relevant},
                    )
                )
    return sorted(entities), queries


def seed_memories(service: BenchmarkMemoryService, dataset_path: Path) -> None:
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            entity = str(payload.get("entity", "")) or "default"
            service.clear_entity(entity)
            for memory in payload.get("memories", []):
                if isinstance(memory, str):
                    text = memory
                else:
                    text = str(memory.get("text", ""))
                if text:
                    service.anchor_memory(entity=entity, text=text)


def evaluate(
    service: BenchmarkMemoryService,
    specs: Iterable[QuerySpec],
    *,
    mode: BenchmarkMode,
    top_k: int,
) -> BenchmarkResult:
    hits = 0
    hits_at_1 = 0
    hits_at_5 = 0
    rr_total = 0.0
    latencies: list[float] = []
    token_costs: list[float] = []
    query_count = 0

    for spec in specs:
        start = time.perf_counter()
        results = ranked_results_for_mode(service, spec, mode=mode, top_k=top_k)
        duration_ms = (time.perf_counter() - start) * 1000.0
        latencies.append(duration_ms)
        token_costs.append(float(len(normalise_tokens(spec.query)) * 32 + len(results) * 48))
        query_count += 1

        rank_hit = None
        for idx, row in enumerate(results):
            text = str(row.get("text") or row.get("body") or row.get("value") or "")
            if text in spec.relevant_texts:
                rank_hit = idx
                break

        if rank_hit is not None:
            hits += 1
            if rank_hit < 1:
                hits_at_1 += 1
            if rank_hit < 5:
                hits_at_5 += 1
            rr_total += 1.0 / float(rank_hit + 1)

    recall_at_1 = float(hits_at_1) / query_count if query_count else 0.0
    recall_at_5 = float(hits_at_5) / query_count if query_count else 0.0
    recall = float(hits) / query_count if query_count else 0.0
    mrr = rr_total / query_count if query_count else 0.0
    avg_latency = statistics.mean(latencies) if latencies else 0.0
    token_cost = statistics.mean(token_costs) if token_costs else 0.0
    return BenchmarkResult(
        recall_at_1=recall_at_1,
        recall_at_5=recall_at_5,
        recall_at_10=recall,
        mrr=mrr,
        avg_latency_ms=avg_latency,
        token_cost=token_cost,
        queries=query_count,
    )


def print_summary(result: BenchmarkResult) -> None:
    targets = {"recall_at_10": 0.6, "mrr": 0.5, "avg_latency_ms": 800.0}
    print("Dual Retrieval Benchmark")
    print("========================")
    print(f"Queries        : {result.queries}")
    print(f"Recall@1       : {result.recall_at_1:.3f}")
    print(f"Recall@5       : {result.recall_at_5:.3f}")
    print(f"Recall@10      : {result.recall_at_10:.3f} (target >= {targets['recall_at_10']:.2f})")
    print(f"MRR            : {result.mrr:.3f} (target >= {targets['mrr']:.2f})")
    print(f"Avg latency ms : {result.avg_latency_ms:.2f} (target <= {targets['avg_latency_ms']:.0f})")
    print(f"Token cost     : {result.token_cost:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("benchmark_dataset.jsonl"),
        help="Path to the benchmark dataset JSONL",
    )
    parser.add_argument(
        "--output-artifact",
        type=Path,
        default=None,
        help="Optional JSON path for the benchmark artefact output.",
    )
    parser.add_argument(
        "--print-artifact",
        action="store_true",
        help="Print the benchmark artefact JSON to stdout after the human summary.",
    )
    parser.add_argument(
        "--repo-sha",
        default="local-dev",
        help="Backend repo commit SHA or identifier captured in artefact provenance.",
    )
    parser.add_argument(
        "--mode",
        choices=tuple(MODE_CONFIG.keys()),
        default="semantic_only",
        help="Benchmark runner mode.",
    )
    parser.add_argument(
        "--artefact-schema-version",
        default="1.0.0",
        help="Schema version for the benchmark artefact contract.",
    )
    parser.add_argument(
        "--suite-id",
        default="dual_retrieval_benchmark",
        help="Logical benchmark suite identifier.",
    )
    parser.add_argument(
        "--phase1-suite",
        choices=tuple(PHASE1_ALL_SUITES.keys()),
        default=None,
        help="Optional Phase 1 retrieval suite preset. Overrides suite id, version, dataset path, and dataset version.",
    )
    parser.add_argument(
        "--suite-version",
        default="v1",
        help="Logical benchmark suite version.",
    )
    parser.add_argument(
        "--dataset-version",
        default="local-v1",
        help="Version identifier for the benchmark dataset.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Maximum ranked results considered for recall and MRR.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional deterministic output root; artefact path will be derived from suite/version/mode/timestamp.",
    )
    args = parser.parse_args()

    if args.phase1_suite:
        suite = PHASE1_ALL_SUITES[args.phase1_suite]
        dataset_path = Path(__file__).with_name(suite.dataset_filename)
        suite_id = suite.suite_id
        suite_version = suite.suite_version
        dataset_version = suite.dataset_version
    else:
        dataset_path = args.dataset
        suite_id = args.suite_id
        suite_version = args.suite_version
        dataset_version = args.dataset_version
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    executed_at = datetime.now(timezone.utc)
    config = RunnerConfig(
        mode=args.mode,
        suite_id=suite_id,
        suite_version=suite_version,
        dataset_version=dataset_version,
        top_k=args.top_k,
    )
    try:
        service = BenchmarkMemoryService()
        seed_memories(service, dataset_path)
        _, specs = load_dataset(dataset_path)
        result = evaluate(service, specs, mode=args.mode, top_k=args.top_k)
        artifact = build_artifact(
            result,
            config=config,
            dataset_path=dataset_path,
            executed_at=executed_at,
            repo_sha=args.repo_sha,
            artefact_schema_version=args.artefact_schema_version,
        )
    except Exception as exc:
        result = None
        artifact = build_failed_artifact(
            config=config,
            dataset_path=dataset_path,
            executed_at=executed_at,
            repo_sha=args.repo_sha,
            artefact_schema_version=args.artefact_schema_version,
            failure_reason=str(exc),
        )
    if result is not None:
        print_summary(result)
    else:
        print("Dual Retrieval Benchmark")
        print("========================")
        print(f"Status         : {artifact.status}")
        print(f"Failure reason : {artifact.failure_reason}")
    target_output = args.output_artifact
    if target_output is None and args.output_root is not None:
        target_output = output_path_for_run(args.output_root, config=config, executed_at=executed_at)
    if target_output:
        target_output.parent.mkdir(parents=True, exist_ok=True)
        target_output.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
    if args.print_artifact:
        print()
        print(json.dumps(artifact.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
