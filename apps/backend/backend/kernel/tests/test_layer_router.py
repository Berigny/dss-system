"""Tests for backend/kernel/layer_router.py."""

from __future__ import annotations

import pytest

from backend.kernel import constants
from backend.kernel.layer_router import LayerRouter


@pytest.mark.parametrize(
    ("v_awareness", "v_unity", "v_ethics", "expected_layer"),
    [
        (0, 6, 6, constants.LAYER_SAND),  # Level 0 collapse -> Sand forensic.
        (1, 6, 6, constants.LAYER_SAND),  # Level 1 -> Sand.
        (2, 6, 6, constants.LAYER_SAND),
        (3, 6, 6, constants.LAYER_LOAM),  # Level 2 -> Loam.
        (5, 6, 6, constants.LAYER_LOAM),
        (6, 6, 6, constants.LAYER_CLAY),  # Level 3 -> Clay.
        (10, 10, 10, constants.LAYER_CLAY),
        (3, 3, 3, constants.LAYER_LOAM),
        (6, 1, 6, constants.LAYER_SAND),  # Most restrictive wins.
    ],
)
def test_route_to_layer(
    v_awareness: int, v_unity: int, v_ethics: int, expected_layer: str
) -> None:
    entry = {
        "v_awareness": v_awareness,
        "v_unity": v_unity,
        "v_ethics": v_ethics,
    }
    assert LayerRouter.route(entry) == expected_layer


def test_route_never_returns_silt() -> None:
    """SILT is reserved for decayed Loam, not initial routing."""
    entry = {"v_awareness": 3, "v_unity": 3, "v_ethics": 3}
    assert LayerRouter.route(entry) != constants.LAYER_SILT


def test_route_from_levels() -> None:
    assert LayerRouter.route_from_levels(
        {"awareness": "level_3", "unity": "level_3", "ethics": "level_3"}
    ) == constants.LAYER_CLAY
    assert LayerRouter.route_from_levels(
        {"awareness": "level_2", "unity": "level_3", "ethics": "level_3"}
    ) == constants.LAYER_LOAM
