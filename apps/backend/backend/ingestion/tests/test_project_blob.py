"""Tests for the blob semantic projection pipeline."""

from __future__ import annotations

from backend.ingestion.pipeline import project_blob
from backend.kernel import constants


def test_project_blob_creates_child_projection_coords() -> None:
    text = "We must collaborate with ethical awareness to avoid harm."
    result = project_blob(text, "test-ns/blob-deadbeef")

    assert result.composite_coord is not None
    assert result.composite_coord.startswith("test-ns/blob-deadbeef-proj-composite")
    assert result.projection_coords
    assert all("-proj-" in coord for coord in result.projection_coords)
    assert result.composite_coord in result.projection_coords


def test_project_blob_computes_quaternary_layer() -> None:
    text = "Collaboration requires shared intent and ethical focus."
    result = project_blob(text, "test-ns/blob-123")

    assert result.composite_layer in constants.QUATERNARY_LAYER_ORDER
    assert result.composite_layer != constants.LAYER_CLAY or result.checksum_336_satisfied


def test_project_blob_short_text_still_has_projection() -> None:
    text = "Hello."
    result = project_blob(text, "test-ns/blob-short")

    assert len(result.projection_coords) == 2  # one chunk + composite
    assert result.composite_coord is not None


def test_project_blob_empty_text_has_sand_projection() -> None:
    result = project_blob("", "test-ns/blob-empty")

    assert len(result.projection_coords) == 2
    assert result.composite_layer == constants.LAYER_SAND
    assert result.checksum_336_satisfied is False


def test_project_blob_336_checksum_satisfied_for_strong_text() -> None:
    # Repeat keywords that trigger all three primes enough times to push each
    # exponent to 6 or higher (each atom contributes v=3).
    text = (
        "Attention focus aware. " * 3
        + "Together align unity coherent. " * 3
        + "Refuse unethical harm safety. " * 3
    )
    result = project_blob(text, "test-ns/blob-strong")

    assert result.composite_exponents.get(5, 0) >= 6
    assert result.composite_exponents.get(7, 0) >= 6
    assert result.composite_exponents.get(2, 0) >= 6
    assert result.checksum_336_satisfied is True
    assert result.composite_layer == constants.LAYER_CLAY


def test_project_blob_coords_are_layer_store_safe() -> None:
    result = project_blob("any text", "entity:blob-hash")

    for coord in result.projection_coords:
        assert ":" not in coord
