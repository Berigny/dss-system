"""Transparency and limitations report generator for DSS-228.

Produces quantified failure analysis and screened sample traces for the
architecture-aligned Qp retrieval benchmark.  Traces highlight coordinate paths
(kernel node, valuation offset, circulation pass, hysteresis depth, dual state)
for both the DSS (Qp) routing and the vector-RAG baseline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backend.benchmarks.retrieval_architecture_benchmark import (
    Memory,
    QpRouter,
    Query,
    VectorRAGBaseline,
    load_corpus,
)

DEFAULT_OUTPUT_ROOT: Path = Path(__file__).parent / "output" / "transparency"
DEFAULT_CORPUS_PATH: Path = (
    Path(__file__).parent
    / "corpus"
    / "qp_retrieval"
    / "transparency_corpus_v1.jsonl"
)
TAXONOMY: dict[str, str] = {
    "success": "Top retrieved memory is architecture-valid.",
    "failure_incoherent_top1": "Top retrieved memory violates an architecture invariant (e.g., broken dual state).",
    "failure_valid_missed": "At least one valid memory exists but is not ranked first.",
    "failure_empty": "No candidates were returned (e.g., filtered out by compatibility gates).",
}


@dataclass(frozen=True)
class TraceRecord:
    query_id: str
    task_type: str
    query_text: str
    query_coordinate: dict[str, Any]
    qp_outcome: str
    vector_outcome: str
    qp_ranked: list[dict[str, Any]]
    vector_ranked: list[dict[str, Any]]


def _coordinate_dict(coord: Any) -> dict[str, Any]:
    """Serialize the visible coordinate path of a QpCoordinate."""
    dual_node = None
    if getattr(coord, "dual_state", None) is not None:
        dual_node = coord.dual_state.kernel_node
    return {
        "kernel_node": coord.kernel_node,
        "metric_prime": coord.metric_prime,
        "tetrahedron": coord.tetrahedron,
        "valuation_offset": coord.valuation_offset,
        "circulation_pass": coord.circulation_pass,
        "hysteresis_depth": coord.hysteresis_depth,
        "dual_kernel_node": dual_node,
    }


def _memory_trace(memory: Memory, rank: int, score: float) -> dict[str, Any]:
    return {
        "rank": rank,
        "memory_id": memory.memory_id,
        "text": memory.text,
        "valid": memory.valid,
        "score": score,
        "coordinate": _coordinate_dict(memory.coordinate),
    }


def _top_trace(
    ranked: Sequence[tuple[str, float]],
    memories_by_id: dict[str, Memory],
    top_k: int,
) -> list[dict[str, Any]]:
    return [
        _memory_trace(memories_by_id[mid], rank=idx + 1, score=score)
        for idx, (mid, score) in enumerate(ranked[:top_k])
        if mid in memories_by_id
    ]


def _classify_outcome(ranked: Sequence[tuple[str, float]], memories_by_id: dict[str, Memory]) -> str:
    if not ranked:
        return "failure_empty"
    top_id = ranked[0][0]
    top_memory = memories_by_id.get(top_id)
    if top_memory is None:
        return "failure_empty"
    if top_memory.valid:
        return "success"
    return "failure_incoherent_top1"


def generate_traces(
    *,
    corpus_path: Path = DEFAULT_CORPUS_PATH,
    top_k: int = 5,
) -> list[TraceRecord]:
    memories, queries = load_corpus(corpus_path)
    qp_router = QpRouter(memories)
    vector_baseline = VectorRAGBaseline(memories)
    memories_by_id = {m.memory_id: m for m in memories}

    traces: list[TraceRecord] = []
    for query in queries:
        qp_ranked = qp_router.rank(query, top_k)
        vector_ranked = vector_baseline.rank(query.text, top_k)
        traces.append(
            TraceRecord(
                query_id=query.query_id,
                task_type=query.task_type,
                query_text=query.text,
                query_coordinate=_coordinate_dict(query.coordinate),
                qp_outcome=_classify_outcome(qp_ranked, memories_by_id),
                vector_outcome=_classify_outcome(vector_ranked, memories_by_id),
                qp_ranked=_top_trace(qp_ranked, memories_by_id, top_k),
                vector_ranked=_top_trace(vector_ranked, memories_by_id, top_k),
            )
        )
    return traces


def _summarize(traces: Sequence[TraceRecord]) -> dict[str, Any]:
    total = len(traces)
    qp_success = sum(1 for t in traces if t.qp_outcome == "success")
    vector_success = sum(1 for t in traces if t.vector_outcome == "success")

    by_task: dict[str, dict[str, Any]] = {}
    for t in traces:
        by_task.setdefault(t.task_type, {"count": 0, "qp_success": 0, "vector_success": 0})
        by_task[t.task_type]["count"] += 1
        if t.qp_outcome == "success":
            by_task[t.task_type]["qp_success"] += 1
        if t.vector_outcome == "success":
            by_task[t.task_type]["vector_success"] += 1

    return {
        "total_queries": total,
        "qp_success_count": qp_success,
        "qp_failure_count": total - qp_success,
        "qp_success_rate": qp_success / total if total else 0.0,
        "vector_success_count": vector_success,
        "vector_failure_count": total - vector_success,
        "vector_success_rate": vector_success / total if total else 0.0,
        "by_task_type": by_task,
    }


def _failure_mode_counts(traces: Sequence[TraceRecord]) -> dict[str, Any]:
    qp_counts: dict[str, int] = {}
    vector_counts: dict[str, int] = {}
    for t in traces:
        qp_counts[t.qp_outcome] = qp_counts.get(t.qp_outcome, 0) + 1
        vector_counts[t.vector_outcome] = vector_counts.get(t.vector_outcome, 0) + 1
    return {"qp": qp_counts, "vector": vector_counts}


def _pick_sample_traces(
    traces: Sequence[TraceRecord],
    *,
    success_count: int = 3,
    failure_count: int = 3,
) -> list[dict[str, Any]]:
    successes = [t for t in traces if t.qp_outcome == "success"]
    failures = [t for t in traces if t.qp_outcome != "success" or t.vector_outcome != "success"]
    selected = successes[:success_count] + failures[:failure_count]
    return [_trace_to_dict(t) for t in selected]


def _trace_to_dict(trace: TraceRecord) -> dict[str, Any]:
    return {
        "query_id": trace.query_id,
        "task_type": trace.task_type,
        "query_text": trace.query_text,
        "query_coordinate": trace.query_coordinate,
        "qp_outcome": trace.qp_outcome,
        "vector_outcome": trace.vector_outcome,
        "qp_ranked": trace.qp_ranked,
        "vector_ranked": trace.vector_ranked,
    }


def build_report(
    *,
    corpus_path: Path = DEFAULT_CORPUS_PATH,
    top_k: int = 5,
    sample_success: int = 3,
    sample_failure: int = 3,
) -> dict[str, Any]:
    traces = generate_traces(corpus_path=corpus_path, top_k=top_k)
    return {
        "report_schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "source_corpus": str(corpus_path),
        "screening_note": "All memories and queries are synthetic; no PII or proprietary data is present.",
        "summary": _summarize(traces),
        "failure_mode_taxonomy": TAXONOMY,
        "failure_mode_counts": _failure_mode_counts(traces),
        "sample_traces": _pick_sample_traces(
            traces, success_count=sample_success, failure_count=sample_failure
        ),
        "out_of_scope": [
            "Live LLM generation and answer-level hallucination are not measured.",
            "Human-evaluation preference scores are collected separately (see human_evaluation_protocol.md).",
            "Traceability and governance metric groups are not populated by this retrieval benchmark.",
            "External API baselines (OpenAI embeddings, Grok) are represented by deterministic stand-ins.",
        ],
    }


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Transparency & Limitations Report",
        "",
        f"**Generated:** {report['generated_at']}  ",
        f"**Source corpus:** `{report['source_corpus']}`",
        "",
        "## Summary",
        "",
        f"- **Total queries:** {report['summary']['total_queries']}",
        f"- **Qp (DSS) success:** {report['summary']['qp_success_count']} / {report['summary']['total_queries']} "
        f"({report['summary']['qp_success_rate']:.2%})",
        f"- **Vector-RAG success:** {report['summary']['vector_success_count']} / {report['summary']['total_queries']} "
        f"({report['summary']['vector_success_rate']:.2%})",
        "",
        "## Failure-mode taxonomy",
        "",
        "| Mode | Description |",
        "|------|-------------|",
    ]
    for mode, description in report["failure_mode_taxonomy"].items():
        lines.append(f"| `{mode}` | {description} |")

    lines.extend(["", "## Failure-mode counts", ""])
    for system, counts in report["failure_mode_counts"].items():
        lines.append(f"### {system}")
        for mode, count in counts.items():
            lines.append(f"- `{mode}`: {count}")
        lines.append("")

    lines.extend(["## Sample traces", ""])
    for idx, trace in enumerate(report["sample_traces"], 1):
        lines.append(f"### {idx}. `{trace['query_id']}` ({trace['task_type']})")
        lines.append(f"**Query:** {trace['query_text']}")
        lines.append(
            f"**Qp outcome:** `{trace['qp_outcome']}` | "
            f"**Vector outcome:** `{trace['vector_outcome']}`"
        )
        coord = trace["query_coordinate"]
        lines.append(
            f"**Query coordinate:** node={coord['kernel_node']}, "
            f"valuation={coord['valuation_offset']}, pass={coord['circulation_pass']}, "
            f"depth={coord['hysteresis_depth']}, dual={coord['dual_kernel_node']}"
        )
        lines.append("")
        lines.append("| Rank | System | Memory | Valid | Score | Coordinate |")
        lines.append("|------|--------|--------|-------|-------|------------|")
        for system, key in [("Qp", "qp_ranked"), ("Vector", "vector_ranked")]:
            for row in trace[key]:
                c = row["coordinate"]
                coord_str = (
                    f"{c['kernel_node']}/v{c['valuation_offset']}/"
                    f"p{c['circulation_pass']}/d{c['hysteresis_depth']}"
                )
                lines.append(
                    f"| {row['rank']} | {system} | {row['memory_id']} | "
                    f"{row['valid']} | {row['score']:.4f} | {coord_str} |"
                )
        lines.append("")

    lines.extend(["## Out of scope", ""])
    for item in report["out_of_scope"]:
        lines.append(f"- {item}")
    lines.append("")

    return "\n".join(lines)


def run_transparency_report(
    *,
    corpus_path: Path = DEFAULT_CORPUS_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    top_k: int = 5,
) -> dict[str, Any]:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    report = build_report(corpus_path=corpus_path, top_k=top_k)

    json_path = output_root / f"transparency_report_{timestamp}.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    md_path = output_root / f"transparency_report_{timestamp}.md"
    md_path.write_text(_to_markdown(report), encoding="utf-8")

    traces_path = output_root / f"sample_traces_{timestamp}.jsonl"
    traces_path.write_text(
        "".join(json.dumps(t) + "\n" for t in report["sample_traces"]),
        encoding="utf-8",
    )

    return report


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    report = run_transparency_report(
        corpus_path=args.corpus,
        output_root=args.output_root,
        top_k=args.top_k,
    )
    print(_to_markdown(report))


__all__ = (
    "TAXONOMY",
    "TraceRecord",
    "generate_traces",
    "build_report",
    "run_transparency_report",
)

if __name__ == "__main__":
    main()
