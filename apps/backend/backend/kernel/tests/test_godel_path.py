from __future__ import annotations

import math

import pytest

from backend.kernel.godel_path import (
    CENTROID_NODE,
    centroid_visited,
    decode_path,
    encode_path,
    path_contains,
    path_length,
    path_overlap,
)


def test_encode_decode_round_trip() -> None:
    path = [0, 2, 6, CENTROID_NODE, 20, 26]
    state = encode_path(path)
    decoded = decode_path(state)
    # Order is not preserved; multiset is.
    assert sorted(decoded) == sorted(path)


def test_centroid_uses_even_prime() -> None:
    without_centroid = encode_path([0, 1, 2])
    with_centroid = encode_path([0, 1, 2, CENTROID_NODE])
    assert not centroid_visited(without_centroid)
    assert centroid_visited(with_centroid)
    assert with_centroid == without_centroid * 2


def test_path_overlap_via_gcd() -> None:
    state_a = encode_path([0, 1, 2, 3])
    state_b = encode_path([2, 3, 4, 5])
    overlap = path_overlap(state_a, state_b)
    assert sorted(overlap) == [2, 3]


def test_path_length_counts_visits() -> None:
    state = encode_path([0, 0, CENTROID_NODE])
    assert path_length(state) == 3


def test_invalid_node_raises() -> None:
    with pytest.raises(ValueError):
        encode_path([0, 100])


def test_decode_empty_state() -> None:
    assert decode_path(1) == []
    assert path_length(1) == 0


def test_path_contains() -> None:
    state = encode_path([0, CENTROID_NODE])
    assert path_contains(state, 0)
    assert path_contains(state, CENTROID_NODE)
    assert not path_contains(state, 5)


def test_uniqueness_no_collision_for_distinct_paths() -> None:
    state_a = encode_path([0, 1])
    state_b = encode_path([2])
    assert state_a != state_b
    # FTA: distinct multisets of primes produce distinct products.
    assert math.gcd(state_a, state_b) == 1
