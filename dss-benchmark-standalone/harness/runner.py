"""Multi-seed deterministic benchmark runner."""
import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Type

# Allow running without package installation.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.base import RetrievalAdapter
from adapters.chroma_adapter import ChromaAdapter
from adapters.faiss_adapter import FaissAdapter
from adapters.langchain_adapter import LangChainAdapter
from adapters.llama_index_adapter import LlamaIndexAdapter
from adapters.milvus_adapter import MilvusAdapter
from adapters.qdrant_adapter import QdrantAdapter
from adapters.sentence_transformers_adapter import SentenceTransformersAdapter
from harness.claims_registry import check_registry, load_registry
from harness.reporter import generate_report, write_json_report, write_markdown_summary
from suites.abstention import AbstentionSuite
from suites.base import BaseSuite
from suites.integrity import IntegritySuite
from suites.poisoning import PoisoningSuite


SUITE_MAP = {
    "poisoning": PoisoningSuite,
    "integrity": IntegritySuite,
    "abstention": AbstentionSuite,
}

ADAPTER_MAP = {
    "faiss": FaissAdapter,
    "chroma": ChromaAdapter,
    "qdrant": QdrantAdapter,
    "sentence_transformers": SentenceTransformersAdapter,
    "langchain": LangChainAdapter,
    "llama_index": LlamaIndexAdapter,
    "milvus": MilvusAdapter,
}


def _apply_thresholds(suites: List[Dict[str, Any]], registry: Dict[str, Any]) -> None:
    """Mark each suite pass/fail using registered thresholds where available."""
    claims = {c["id"]: c for c in registry.get("claims", [])}
    for suite in suites:
        suite_name = suite.get("suite")
        suite_fail = False
        for metric, value in suite.items():
            if metric in ("suite", "seeds", "per_seed") or not isinstance(value, (int, float)):
                continue
            cid = f"{suite_name}.{metric}"
            claim = claims.get(cid)
            if not claim:
                continue
            threshold = claim.get("threshold")
            operator = claim.get("operator", ">=")
            if threshold is None:
                continue
            if operator == "<=":
                suite_fail = suite_fail or (value > threshold)
            elif operator == "==":
                suite_fail = suite_fail or (value != threshold)
            elif operator == ">=":
                suite_fail = suite_fail or (value < threshold)
            else:
                suite_fail = True
        suite["pass"] = not suite_fail


def run_benchmarks(
    adapter_name: str = "faiss",
    seeds: List[int] = None,
    suites: List[str] = None,
    mock_embeddings: bool = False,
) -> Dict[str, Any]:
    """Run selected suites and return the assembled report."""
    adapter_cls = ADAPTER_MAP.get(adapter_name)
    if adapter_cls is None:
        raise ValueError(f"Unknown adapter: {adapter_name}")

    if adapter_name == "faiss":
        adapter: RetrievalAdapter = adapter_cls(mock_embeddings=mock_embeddings)
    else:
        adapter: RetrievalAdapter = adapter_cls()

    seeds = seeds or [42, 43, 44]
    suites = suites or list(SUITE_MAP.keys())

    suite_results: List[Dict[str, Any]] = []
    for suite_name in suites:
        suite_cls = SUITE_MAP[suite_name]
        suite: BaseSuite = suite_cls(adapter, seeds=seeds)
        result = suite.run()
        suite_results.append(result)

    registry = load_registry()
    _apply_thresholds(suite_results, registry)
    overall_pass, binding = check_registry(registry, suite_results)

    report = generate_report(suite_results, binding)
    return report


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="DSS Benchmark Standalone Runner")
    parser.add_argument("--adapter", default="faiss", choices=list(ADAPTER_MAP.keys()))
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    parser.add_argument("--suites", nargs="+", choices=list(SUITE_MAP.keys()),
                        default=list(SUITE_MAP.keys()))
    parser.add_argument("--mock-embeddings", action="store_true")
    parser.add_argument("--report-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    report = run_benchmarks(
        adapter_name=args.adapter,
        seeds=args.seeds,
        suites=args.suites,
        mock_embeddings=args.mock_embeddings,
    )

    report_dir = args.report_dir or (ROOT / "eval" / "reports")
    json_path = write_json_report(report, report_dir / "benchmark_report.json")
    md_path = write_markdown_summary(report, report_dir / "benchmark_summary.md")

    print(f"JSON report: {json_path}")
    print(f"Markdown summary: {md_path}")
    print(f"Overall pass: {report['overall_pass']}")

    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
