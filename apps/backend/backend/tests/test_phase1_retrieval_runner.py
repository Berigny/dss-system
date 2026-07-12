from __future__ import annotations

from pathlib import Path

from backend.benchmarks.run_dual_retrieval_benchmark import (
    BenchmarkMemoryService,
    PHASE1_RETRIEVAL_SUITES,
    evaluate,
    load_dataset,
    ranked_results_for_mode,
    seed_memories,
)


def test_phase1_retrieval_suite_datasets_load_and_evaluate() -> None:
    suite_ids = {"MuSiQue", "HotpotQA", "2WikiMultiHopQA"}
    assert set(PHASE1_RETRIEVAL_SUITES.keys()) == suite_ids

    for config in PHASE1_RETRIEVAL_SUITES.values():
        dataset_path = Path("backend/benchmarks") / config.dataset_filename
        service = BenchmarkMemoryService()
        seed_memories(service, dataset_path)
        _, specs = load_dataset(dataset_path)
        result = evaluate(service, specs, mode="full_dss", top_k=5)
        assert result.queries > 0
        assert result.recall_at_1 >= 0.0
        assert result.recall_at_5 >= result.recall_at_1
        assert result.token_cost > 0.0


def test_phase1_retrieval_suite_modes_remain_comparable() -> None:
    config = PHASE1_RETRIEVAL_SUITES["HotpotQA"]
    dataset_path = Path("backend/benchmarks") / config.dataset_filename
    service = BenchmarkMemoryService()
    seed_memories(service, dataset_path)
    _, specs = load_dataset(dataset_path)
    first_query = specs[0]

    semantic_results = ranked_results_for_mode(service, first_query, mode="semantic_only", top_k=5)
    coordinate_results = ranked_results_for_mode(service, first_query, mode="coordinate_guided", top_k=5)
    full_results = ranked_results_for_mode(service, first_query, mode="full_dss", top_k=5)

    assert semantic_results
    assert coordinate_results
    assert full_results
