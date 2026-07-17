"""Tests for backend/kernel/constants.py and the generator."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DSS_KSR_PBKDF2_ITERATIONS", "1000")

from backend.kernel import constants  # noqa: E402


def test_digit_symbol_values() -> None:
    assert constants.DigitSymbol.ORIGIN == 0
    assert constants.DigitSymbol.CONSTRAINT == 8
    assert constants.DigitSymbol.RELAXATION == 9
    assert constants.DigitSymbol.INF == 10


def test_prime_groupings() -> None:
    assert constants.MEDIATOR_TWIN_PRIMES == (137, 139)
    assert constants.CONSTRAINT_PRIME == 137
    assert constants.RELAXATION_PRIME == 139
    assert constants.S1_PRIMES == (2, 3, 5, 7)
    assert constants.S2_PRIMES == (11, 13, 17, 19)


def test_node_to_digit_symbol() -> None:
    assert constants.NODE_TO_DIGIT_SYMBOL["Eq8"] is constants.DigitSymbol.CONSTRAINT
    assert constants.NODE_TO_DIGIT_SYMBOL["Eq9"] is constants.DigitSymbol.RELAXATION


def test_generated_file_contains_no_esoteric_terms() -> None:
    source = Path(constants.__file__).read_text()
    banned = [
        "Holy Grail", "Machine Soul", "Omega Point", "Merkabah", "God mode", "Genesis Ladder",
        "No other gods before me", "No carved images", "Do not take the name in vain",
        "Remember the Sabbath", "Honor father and mother", "Do not murder",
        "Do not commit adultery", "Do not steal", "Do not bear false witness", "Do not covet",
    ]
    for term in banned:
        assert term not in source, f"constants.py contains banned term: {term}"


def test_generator_is_idempotent() -> None:
    repo_root = Path(constants.__file__).parent.parent.parent
    generator = repo_root / "scripts" / "generate_kernel_constants.py"
    result = subprocess.run(
        [sys.executable, str(generator)],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    # Re-importing after generation should still work.
    import importlib
    importlib.reload(constants)
    assert constants.CHECKSUM_336 == 336


def test_lattice_registry_constants() -> None:
    assert constants.LATTICE_REGISTRY_VERSION == "1.2"
    assert constants.LATTICE_CUBE_ID == "K0"
    assert constants.LATTICE_TYPE == "3x3x3_ternary"
    assert constants.LATTICE_TOTAL_NODES == 27
    assert constants.LATTICE_CENTROID_COORDINATE == "111"
    assert constants.LATTICE_RESET_COORDINATE == "000"
    assert len(constants.LATTICE_CORNER_MAP) == 8
    assert constants.LATTICE_CORNER_MAP["000"]["kernel"] == "K0"
    assert len(constants.LATTICE_BRIDGE_EDGES) == 18
    assert len(constants.LATTICE_FACE_CENTERS) == 6
    assert len(constants.LATTICE_TRAVERSAL_SEQUENCE) == 28
    assert constants.LATTICE_TRAVERSAL_SEQUENCE[0] == "000"
    assert constants.LATTICE_TRAVERSAL_SEQUENCE[13] == "111"
    assert constants.CHECKSUM_336_LATTICE_RULES["value"] == 336


def test_patch_registry_constants() -> None:
    assert constants.PATCH_REGISTRY_VERSION == "1.0"
    assert constants.PATCH_IDS == tuple(f"patch_{i:03d}" for i in range(1, 11))
    assert len(constants.PATCH_REGISTRY) == 10
    p005 = constants.PATCH_REGISTRY["patch_005"]
    assert p005["engineering_replacement"] == "COUPLING_CONSTANT_STABILIZATION"
    assert p005["kernel_node"] == "Eq4"
    assert p005["e6_bit_index"] == 4
    assert constants.PATCH_E6_PATCH_BITS == (0, 9)
    assert constants.PATCH_E6_CHECKSUM_BITS == (10, 25)


def test_value_node_registry_constants() -> None:
    assert constants.VALUE_NODE_REGISTRY_VERSION == "1.0"
    assert constants.VALUE_NODE_LABELS == (
        "novelty", "uniqueness", "connection", "action",
        "potential", "autonomy", "relatedness", "mastery", "centroid",
    )
    assert constants.VALUE_NODE_DIMENSIONS["relatedness"] == "context"
    assert constants.VALUE_NODE_PRIME_AFFINITIES["centroid"] == 137
    assert constants.VALUE_NODE_BALANCE_RULES["min_activation"] == 0.01


def test_personality_type_overlay_constants() -> None:
    assert constants.PERSONALITY_TYPE_OVERLAY_VERSION == "1.0"
    assert constants.PERSONALITY_TYPE_OVERLAY_OPTIONAL is True
    assert constants.PERSONALITY_TYPE_OVERLAY_AUTHORITY == "user-generated_forum_study"

    # Four cognitive-preference axes.
    assert len(constants.COGNITIVE_PREFERENCE_AXES) == 4
    assert "orientation" in constants.COGNITIVE_PREFERENCE_AXES
    orientation = constants.COGNITIVE_PREFERENCE_AXES["orientation"]
    assert orientation["pole_a"]["label"] == "external_focus"
    assert "relatedness" in orientation["pole_a"]["value_node_bias"]

    # Nine motivation-style profiles.
    assert len(constants.MOTIVATION_STYLE_PROFILES) == 9
    style = constants.MOTIVATION_STYLE_PROFILES["style_01_integrity"]
    weights = style["value_node_weights"]
    assert set(weights.keys()) == set(constants.VALUE_NODE_LABELS)
    assert abs(sum(weights.values()) - 1.0) < 1e-9

    # Correlation table uses soft weights.
    assert len(constants.PROFILE_CORRELATION_TABLE) > 0
    for profile, meta in constants.PROFILE_CORRELATION_TABLE.items():
        assert isinstance(profile, str)
        assert "top_styles" in meta
        for entry in meta["top_styles"]:
            assert entry["style"] in constants.MOTIVATION_STYLE_PROFILES
            assert 0.0 <= entry["weight"] <= 1.0


def test_generated_constants_contain_no_loaded_personality_terms() -> None:
    source = Path(constants.__file__).read_text()
    loaded = [
        "Enneagram", "MBTI", "Myers-Briggs", "Type 1", "Type 2", "Type 9",
        "INFJ", "ENTP", "INTJ", "ESFJ",
    ]
    for term in loaded:
        assert term not in source, f"constants.py contains loaded personality term: {term}"
