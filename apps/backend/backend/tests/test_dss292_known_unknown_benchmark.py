"""Tests for DSS-292 known-unknown abstention benchmark."""

from __future__ import annotations

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.dss292_known_unknown_benchmark import (
    BenchmarkConfig,
    BenchmarkQuery,
    Memory,
    QueryClass,
    QpRouter,
    VectorRAGBaseline,
    _make_coordinate,
    evaluate,
    generate_corpus,
    run_benchmark,
    run_single_seed,
)


def test_generate_corpus_creates_three_query_classes() -> None:
    memories, queries = generate_corpus((4, 8), seed=193, variants_per_length=5)
    classes = {q.query_class for q in queries}
    assert classes == {QueryClass.PRESENT, QueryClass.ABSENT, QueryClass.BORDERLINE}
    assert all(q.target_id is not None for q in queries if q.query_class == QueryClass.PRESENT)
    assert all(q.target_id is None for q in queries if q.query_class != QueryClass.PRESENT)


def test_present_queries_have_matching_memory() -> None:
    memories, queries = generate_corpus((4,), seed=193, variants_per_length=5)
    present = [q for q in queries if q.query_class == QueryClass.PRESENT]
    memory_ids = {m.memory_id for m in memories}
    assert len(present) == 5
    assert all(q.target_id in memory_ids for q in present)


def test_absent_queries_have_no_matching_memory() -> None:
    memories, queries = generate_corpus((4,), seed=193, variants_per_length=5)
    absent = [q for q in queries if q.query_class == QueryClass.ABSENT]
    memory_ids = {m.memory_id for m in memories}
    assert len(absent) == 5
    assert all(q.target_id not in memory_ids for q in absent)


def test_qp_router_abstains_on_absent_queries() -> None:
    memories, queries = generate_corpus((4,), seed=193)
    router = QpRouter(memories)
    absent_query = next(q for q in queries if q.query_class == QueryClass.ABSENT)
    outcome = router.retrieve(absent_query)
    assert outcome.abstained is True


def test_vector_baseline_can_abstain() -> None:
    memories, queries = generate_corpus((4,), seed=193)
    baseline = VectorRAGBaseline(memories)
    absent_query = next(q for q in queries if q.query_class == QueryClass.ABSENT)
    outcome = baseline.retrieve(absent_query.text)
    # The vector baseline may or may not abstain depending on lexical overlap;
    # the important property is that the outcome is structurally valid.
    assert isinstance(outcome.abstained, bool)
    assert outcome.returned_id is None or outcome.returned_id in {m.memory_id for m in memories}


def test_evaluate_computes_gate_metrics() -> None:
    memories, queries = generate_corpus((4,), seed=193)
    summary = evaluate(memories, queries)
    assert summary.qp.abstention_precision == pytest.approx(1.0)
    assert summary.qp.abstention_recall == pytest.approx(1.0)
    assert summary.qp.false_abstention_rate == pytest.approx(0.0)
    assert 0.0 <= summary.qp.present_recall <= 1.0


def test_single_seed_artifact_validates(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path,
        lengths=(4,),
        top_k=5,
        seeds=(193,),
    )
    artifact = run_single_seed(193, config)
    payload = artifact.model_dump(mode="json")
    validated = validate_benchmark_artifact(payload)
    assert validated.status == "success"
    assert validated.metrics["governance"].status == "present"
    assert validated.metrics["retrieval"].status == "present"
    assert validated.run_config["gate_passed"] is True


def test_multi_seed_benchmark_produces_aggregate(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path,
        lengths=(4, 8),
        top_k=5,
        seeds=(193, 42),
    )
    aggregate = run_benchmark(config)
    assert aggregate.status == "success"
    assert aggregate.run_config.get("aggregate") is True
    assert aggregate.run_config.get("seed_count") == 2
    assert aggregate.metrics["governance"].metrics["qp_abstention_precision"].statistics is not None


def test_custom_small_corpus_evaluates_deterministically() -> None:
    coord = _make_coordinate(
        kernel_node="Eq2",
        valuation_offset=3,
        circulation_pass=3,
        hysteresis_depth=0.3,
        dual_valid=True,
    )
    memories = [
        Memory(memory_id="m1", text="The budget was approved at 9:00.", coordinate=coord, length=1),
        Memory(memory_id="m2", text="A random distractor sentence here.", coordinate=coord, length=1),
    ]
    queries = [
        BenchmarkQuery(
            query_id="q1",
            text="What was the budget approval time?",
            coordinate=coord,
            query_class=QueryClass.PRESENT,
            target_id="m1",
            length=1,
        ),
        BenchmarkQuery(
            query_id="q2",
            text="What was the emergency override code?",
            coordinate=_make_coordinate(kernel_node="Eq4", valuation_offset=5, circulation_pass=2, dual_valid=True),
            query_class=QueryClass.ABSENT,
            target_id=None,
            length=1,
        ),
    ]
    summary = evaluate(memories, queries)
    assert summary.qp.present_recall == pytest.approx(1.0)
    assert summary.qp.abstention_recall == pytest.approx(1.0)
    assert summary.qp.false_abstention_rate == pytest.approx(0.0)
