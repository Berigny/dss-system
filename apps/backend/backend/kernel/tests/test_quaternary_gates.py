"""Tests for backend/kernel/quaternary_gates.py."""

from __future__ import annotations

import pytest

from backend.kernel import constants
from backend.kernel.quaternary_gates import QuaternaryGate


@pytest.mark.parametrize(
    ("gate_key", "v", "expected_level", "expected_layer"),
    [
        ("awareness", 0, "level_0", "GATE_COLLAPSE"),
        ("awareness", 1, "level_1", "SAND"),
        ("awareness", 2, "level_1", "SAND"),
        ("awareness", 3, "level_2", "LOAM"),
        ("awareness", 5, "level_2", "LOAM"),
        ("awareness", 6, "level_3", "CLAY"),
        ("awareness", 10, "level_3", "CLAY"),
        ("unity", 6, "level_3", "CLAY"),
        ("ethics", 6, "level_3", "CLAY"),
    ],
)
def test_quaternary_levels(
    gate_key: str, v: int, expected_level: str, expected_layer: str
) -> None:
    level_key, meta = QuaternaryGate.level_for(gate_key, v)
    assert level_key == expected_level
    assert meta["layer"] == expected_layer


def test_level_for_none_maps_to_level0() -> None:
    level_key, meta = QuaternaryGate.level_for("ethics", None)
    assert level_key == "level_0"
    assert meta["value"] == 0.0


def test_evaluate_returns_all_gates() -> None:
    result = QuaternaryGate.evaluate(6, 6, 6)
    assert set(result["levels"].keys()) == {"awareness", "unity", "ethics"}
    assert result["levels"] == {
        "awareness": "level_3",
        "unity": "level_3",
        "ethics": "level_3",
    }
    assert result["checksum_factor_product"] == pytest.approx(1.0)
    assert result["clay_admissible"] is True
    assert result["checksum_336_satisfied"] is True


@pytest.mark.parametrize(
    ("v_awareness", "v_unity", "v_ethics"),
    [
        (0, 6, 6),
        (6, 0, 6),
        (6, 6, 0),
        (None, 6, 6),
    ],
)
def test_level0_collapses_checksum(
    v_awareness: int | None, v_unity: int | None, v_ethics: int | None
) -> None:
    result = QuaternaryGate.evaluate(v_awareness, v_unity, v_ethics)
    assert result["checksum_factor_product"] == pytest.approx(0.0)
    assert result["clay_admissible"] is False
    assert result["checksum_336_satisfied"] is False


@pytest.mark.parametrize(
    ("v_awareness", "v_unity", "v_ethics", "expected_clay"),
    [
        (6, 6, 6, True),
        (10, 10, 10, True),
        (5, 6, 6, False),
        (6, 5, 6, False),
        (6, 6, 5, False),
        (3, 3, 3, False),
    ],
)
def test_clay_admission(
    v_awareness: int, v_unity: int, v_ethics: int, expected_clay: bool
) -> None:
    result = QuaternaryGate.evaluate(v_awareness, v_unity, v_ethics)
    assert result["clay_admissible"] is expected_clay
    assert QuaternaryGate.elevation_allowed(v_awareness, v_unity, v_ethics) is expected_clay


def test_constants_expose_quaternary_registry() -> None:
    assert constants.QUATERNARY_GATE_REGISTRY_VERSION == "1.3-alpha"
    assert constants.QUATERNARY_SEMANTIC_CHECKSUM_BASE == 336
    assert constants.QUATERNARY_SEMANTIC_CHECKSUM_NON_COMPENSATORY is True
    assert constants.QUATERNARY_GATE_KEYS == ("awareness", "unity", "ethics")
    assert constants.QUATERNARY_GATE_TO_PRIME == {
        "awareness": constants.QUATERNARY_GATE_TO_PRIME["awareness"],
        "unity": constants.QUATERNARY_GATE_TO_PRIME["unity"],
        "ethics": constants.QUATERNARY_GATE_TO_PRIME["ethics"],
    }
    assert constants.QUATERNARY_LAYER_ORDER == ("SAND", "SILT", "LOAM", "CLAY")
    assert constants.LAYER_CLAY == "CLAY"
    assert "consistency_zk" in constants.ELEVATION_PROOF_REQUIRED_PROOFS
