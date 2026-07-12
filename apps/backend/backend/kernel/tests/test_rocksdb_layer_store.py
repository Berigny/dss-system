"""Tests for backend/kernel/rocksdb_layer_store.py."""

from __future__ import annotations

import math

import pytest

from backend.kernel import constants
from backend.kernel.base_foundation import BaseFoundationService
from backend.kernel.rocksdb_layer_store import RocksDBLayerStore


@pytest.fixture
def store():
    db: dict[bytes, bytes] = {}
    BaseFoundationService(db).write_foundation("default")
    return RocksDBLayerStore(db, provision_id="default")


def test_layer_key_prefixes(store: RocksDBLayerStore) -> None:
    """Each geological layer uses a distinct key prefix."""
    # Create Sand, Loam, and Clay directly via routing.
    direct_layers = [
        (constants.LAYER_SAND, 1, 1, 1),
        (constants.LAYER_LOAM, 3, 3, 3),
        (constants.LAYER_CLAY, 6, 6, 6),
    ]
    seen: set[str] = set()
    for layer, va, vu, ve in direct_layers:
        store.write(
            {
                "coord": f"test/{layer}",
                "block_height": 100,
                "v_awareness": va,
                "v_unity": vu,
                "v_ethics": ve,
                "merkle_path": "mp",
                "zk_proof_stub": "zk",
            }
        )
        entries = store.list_layer(layer)
        assert len(entries) == 1, f"missing entry for {layer}"
        key = entries[0][0]
        prefix = key.decode("utf-8")[0]
        seen.add(prefix)
        assert prefix == RocksDBLayerStore.PREFIX_MAP[layer]

    # Create Silt in a fresh store by decaying a Loam entry to zero.
    silt_db: dict[bytes, bytes] = {}
    BaseFoundationService(silt_db).write_foundation("default")
    silt_store = RocksDBLayerStore(silt_db, provision_id="default")
    silt_store.write(
        {
            "coord": "test/silt",
            "block_height": 100,
            "v_awareness": 3,
            "v_unity": 3,
            "v_ethics": 3,
        }
    )
    silt_store.decay_loam(current_block=108)
    silt_entries = silt_store.list_layer(constants.LAYER_SILT)
    assert len(silt_entries) == 1
    silt_prefix = silt_entries[0][0].decode("utf-8")[0]
    seen.add(silt_prefix)
    assert silt_prefix == RocksDBLayerStore.PREFIX_MAP[constants.LAYER_SILT]
    assert seen == {"S", "I", "L", "C"}


def test_sand_evicted_after_one_block(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "test/sand",
            "block_height": 10,
            "v_awareness": 1,
            "v_unity": 1,
            "v_ethics": 1,
        }
    )
    assert len(store.list_layer(constants.LAYER_SAND)) == 1
    evicted = store.evict_sand(current_block=12)
    assert "test/sand" in evicted
    assert len(store.list_layer(constants.LAYER_SAND)) == 0


def test_loam_logarithmic_decay(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "test/loam",
            "block_height": 100,
            "v_awareness": 4,
            "v_unity": 4,
            "v_ethics": 4,
        }
    )
    migrations = store.decay_loam(current_block=108)
    assert len(migrations) == 0
    entries = store.list_layer(constants.LAYER_LOAM)
    assert len(entries) == 1
    entry = entries[0][1]
    # 8 blocks elapsed -> log2(8) == 3, so 4 - 3 = 1.
    assert entry["v_awareness"] == pytest.approx(1.0, abs=1e-9)
    assert entry["v_unity"] == pytest.approx(1.0, abs=1e-9)
    assert entry["v_ethics"] == pytest.approx(1.0, abs=1e-9)


def test_loam_decay_to_zero_migrates_to_silt(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "test/loam-zero",
            "block_height": 100,
            "v_awareness": 3,
            "v_unity": 3,
            "v_ethics": 3,
        }
    )
    # 8 blocks -> 3 - 3 = 0, migrates to Silt.
    migrations = store.decay_loam(current_block=108)
    assert len(migrations) == 1
    assert migrations[0]["to"] == constants.LAYER_SILT
    assert len(store.list_layer(constants.LAYER_LOAM)) == 0
    silt_entries = store.list_layer(constants.LAYER_SILT)
    assert len(silt_entries) == 1
    assert silt_entries[0][1]["v_awareness"] == 0


def test_clay_write_rejected_without_level3(store: RocksDBLayerStore) -> None:
    # Force Clay layer even though ethics is only Level 2.
    with pytest.raises(ValueError, match="Clay write rejected"):
        store.write(
            {
                "coord": "test/clay-bad",
                "block_height": 100,
                "layer": constants.LAYER_CLAY,
                "v_awareness": 6,
                "v_unity": 6,
                "v_ethics": 5,
            }
        )


def test_clay_write_accepted_at_level3(store: RocksDBLayerStore) -> None:
    layer = store.write(
        {
            "coord": "test/clay-good",
            "block_height": 100,
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
        }
    )
    assert layer == constants.LAYER_CLAY
    entries = store.list_layer(constants.LAYER_CLAY)
    assert len(entries) == 1


def test_layer_migration_updates_checksum(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "test/migrate",
            "block_height": 100,
            "v_awareness": 3,
            "v_unity": 3,
            "v_ethics": 3,
        }
    )
    foundation = BaseFoundationService(store._db).read_foundation("default")
    before = foundation["public"]["checksum_336"]["quaternary_state"]["layer_counts"][
        constants.LAYER_LOAM
    ]
    assert before == 1

    migrations = store.decay_loam(current_block=108)
    assert len(migrations) == 1

    foundation = BaseFoundationService(store._db).read_foundation("default")
    state = foundation["public"]["checksum_336"]["quaternary_state"]
    # The migration updates the foundation record in the same locked block.
    assert state["last_block_height"] == 108
    assert state["layer_counts"][constants.LAYER_LOAM] == 0
    assert state["layer_counts"][constants.LAYER_SILT] == 1


def test_clay_key_prefix_and_format(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "ethics/lawfulness/refusal",
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
            "merkle_path": "mp",
            "zk_proof_stub": "zk",
        }
    )
    entries = store.list_layer(constants.LAYER_CLAY)
    assert len(entries) == 1
    key = entries[0][0].decode("utf-8")
    parts = key.split(":")
    assert len(parts) == 4
    assert parts[0] == "C"
    assert parts[1] == "ethics/lawfulness/refusal"
    assert parts[2].isdigit()
    assert parts[3]


def test_clay_value_contains_v_values_and_merkle_path(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "ethics/lawfulness/refusal",
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
            "merkle_path": "test-merkle-path",
            "zk_proof_stub": "test-zk-stub",
        }
    )
    entries = store.list_layer(constants.LAYER_CLAY)
    assert len(entries) == 1
    entry = entries[0][1]
    assert entry["v_awareness"] == 6
    assert entry["v_unity"] == 6
    assert entry["v_ethics"] == 6
    assert entry["merkle_path"] == "test-merkle-path"
    assert entry["zk_proof_stub"] == "test-zk-stub"


def test_block_height_increases_monotonically(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "test/a",
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
        }
    )
    store.write(
        {
            "coord": "test/b",
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
        }
    )
    clay = store.list_layer(constants.LAYER_CLAY)
    heights = sorted(int(key.decode("utf-8").split(":")[2]) for key, _ in clay)
    assert heights[0] < heights[1]


def test_coord_normalized_in_key(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "Ethics/Lawfulness/Refusal",
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
        }
    )
    entries = store.list_layer(constants.LAYER_CLAY)
    assert len(entries) == 1
    key = entries[0][0].decode("utf-8")
    coord_part = key.split(":")[1]
    assert coord_part == "ethics/lawfulness/refusal"


def test_retrieval_by_coord_no_full_text_scan(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "awareness/attention/focus",
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
        }
    )
    store.write(
        {
            "coord": "ethics/lawfulness/refusal",
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
        }
    )
    results = store.retrieve_by_coord("ethics/lawfulness/refusal")
    assert len(results) == 1
    layer, block_height, entry = results[0]
    assert layer == constants.LAYER_CLAY
    assert block_height > 0
    assert entry["v_ethics"] == 6


def test_explicit_low_block_height_rejected(store: RocksDBLayerStore) -> None:
    store.write(
        {
            "coord": "test/a",
            "block_height": 100,
            "v_awareness": 6,
            "v_unity": 6,
            "v_ethics": 6,
        }
    )
    with pytest.raises(ValueError, match="block height must be monotonic"):
        store.write(
            {
                "coord": "test/b",
                "block_height": 50,
                "v_awareness": 6,
                "v_unity": 6,
                "v_ethics": 6,
            }
        )
