from __future__ import annotations

import math
import time

import pytest

from backend.kernel import constants
from backend.kernel.base_foundation import BaseFoundationService
from backend.kernel.rocksdb_layer_store import RocksDBLayerStore
from backend.retrieval.coord_retriever import CoordRetriever


@pytest.fixture
def retriever():
    db: dict[bytes, bytes] = {}
    BaseFoundationService(db).write_foundation("default")
    return CoordRetriever(RocksDBLayerStore(db, provision_id="default"))


def _insert(store: RocksDBLayerStore, coord: str, block_height: int, *, layer: str | None = None, **kwargs) -> None:
    """Insert a layer-store entry directly to avoid foundation overhead in bulk tests."""
    v_awareness = int(kwargs.get("v_awareness", 0))
    v_unity = int(kwargs.get("v_unity", 0))
    v_ethics = int(kwargs.get("v_ethics", 0))
    if layer is None:
        from backend.kernel.quaternary_gates import QuaternaryGate
        from backend.kernel.layer_router import LayerRouter

        eval_result = QuaternaryGate.evaluate(v_awareness, v_unity, v_ethics)
        layer = LayerRouter.route_from_levels(eval_result["levels"])
    key = store._make_key(
        layer,
        coord,
        block_height,
        store._hash_prefix(coord),
    )
    value = store._make_value(
        v_awareness,
        v_unity,
        v_ethics,
        kwargs.get("merkle_path", ""),
        kwargs.get("zk_proof_stub", ""),
        kwargs.get("timestamp", 1.0),
        kwargs.get("elevation_bundle"),
    )
    store._db[key] = value


def test_exact_coord_retrieval(retriever: CoordRetriever) -> None:
    target_coord = "ethics/lawfulness/refusal/clean_refusal/v3"
    store = retriever._store
    # Seed 10k entries across layers directly.
    for i in range(10_000):
        _insert(
            store,
            f"awareness/attention/focus/item{i}",
            block_height=i + 1,
            layer=constants.LAYER_LOAM,
            v_awareness=3,
        )
    _insert(
        store,
        target_coord,
        block_height=10_001,
        layer=constants.LAYER_CLAY,
        v_awareness=6,
        v_unity=6,
        v_ethics=6,
    )

    start = time.perf_counter()
    results = retriever.retrieve_exact(target_coord)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(results) == 1
    assert results[0]["coord"] == target_coord
    assert results[0]["entry"]["v_awareness"] == 6
    assert elapsed_ms < 10.0, f"exact retrieval took {elapsed_ms:.2f}ms"


def test_branch_expansion_depth_one(retriever: CoordRetriever) -> None:
    store = retriever._store
    _insert(
        store,
        "ethics/lawfulness/refusal/v3",
        block_height=1,
        layer=constants.LAYER_LOAM,
        v_ethics=3,
    )
    _insert(
        store,
        "ethics/lawfulness/acceptance/v3",
        block_height=2,
        layer=constants.LAYER_LOAM,
        v_ethics=3,
    )
    _insert(
        store,
        "awareness/attention/focus/v3",
        block_height=3,
        layer=constants.LAYER_LOAM,
        v_awareness=3,
    )

    results = retriever.expand_branch("ethics/lawfulness/refusal/v3", max_depth=2)
    coords = {row["coord"] for row in results}
    assert "ethics/lawfulness/refusal/v3" in coords
    assert "ethics/lawfulness/acceptance/v3" in coords
    assert "awareness/attention/focus/v3" not in coords


def test_hysteresis_returns_loam_ancestors_ordered(retriever: CoordRetriever) -> None:
    store = retriever._store
    clay_coord = "ethics/lawfulness/refusal/clean_refusal/v6"
    _insert(
        store,
        "ethics/lawfulness/refusal",
        block_height=10,
        layer=constants.LAYER_LOAM,
        v_awareness=3,
        v_unity=3,
        v_ethics=4,
    )
    _insert(
        store,
        "ethics/lawfulness/refusal/clean_refusal",
        block_height=20,
        layer=constants.LAYER_LOAM,
        v_awareness=5,
        v_unity=5,
        v_ethics=5,
    )
    _insert(
        store,
        "awareness/attention/focus",
        block_height=15,
        layer=constants.LAYER_LOAM,
        v_awareness=4,
    )
    _insert(
        store,
        clay_coord,
        block_height=30,
        layer=constants.LAYER_CLAY,
        v_awareness=6,
        v_unity=6,
        v_ethics=6,
    )

    ancestors = retriever.hysteresis_ancestors(clay_coord, clay_block_height=30)
    assert len(ancestors) == 2
    assert ancestors[0]["block_height"] == 10
    assert ancestors[1]["block_height"] == 20

    # Decay: 30 - 10 = 20 blocks -> log2(20) ≈ 4.32; 4 - 4.32 = 0.
    assert ancestors[0]["decayed_values"]["v_ethics"] == pytest.approx(0.0, abs=1e-9)
    # Decay: 30 - 20 = 10 blocks -> log2(10) ≈ 3.32; 5 - 3.32 ≈ 1.68.
    assert ancestors[1]["decayed_values"]["v_awareness"] == pytest.approx(
        5.0 - math.log2(10), abs=1e-9
    )


def test_coord_retrieval_uses_no_full_text_index(retriever: CoordRetriever) -> None:
    store = retriever._store
    coord = "ethics/lawfulness/refusal"
    _insert(
        store,
        coord,
        block_height=1,
        layer=constants.LAYER_CLAY,
        v_awareness=6,
        v_unity=6,
        v_ethics=6,
        merkle_path="this is raw text that should never be indexed",
        zk_proof_stub="another raw text blob",
    )
    results = retriever.retrieve_exact(coord)
    assert len(results) == 1
    # The retriever never inspects value text to find the match.
    assert results[0]["entry"]["merkle_path"] == "this is raw text that should never be indexed"
