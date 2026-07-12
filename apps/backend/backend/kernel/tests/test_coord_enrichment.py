"""Tests for the coordinate enrichment, output formatter, embedding, and reverse parser patches."""

from __future__ import annotations

import pytest

from backend.kernel import constants
from backend.kernel.coord_enrichment import COORD_REGISTRY, CoordEnrichmentCard
from backend.kernel.embeddings import coord_sequence_embedding, embedding_similarity
from backend.kernel.output_formatter import (
    LatticeReadingOutput,
    UnitReading,
    build_lattice_reading_output,
)
from backend.kernel.reverse_parser import ReverseLatticeParser


# ---------------------------------------------------------------------------
# Gate 1 — Coordinate Enrichment Card (CEC) completeness
# ---------------------------------------------------------------------------


def test_coord_registry_has_28_cards() -> None:
    assert len(COORD_REGISTRY) == 28


def test_all_traversal_coordinates_present() -> None:
    # The traversal sequence contains 27 unique coordinates plus a Day-27 reset.
    unique_coords = set(constants.LATTICE_TRAVERSAL_SEQUENCE)
    for coord in unique_coords:
        assert coord in COORD_REGISTRY, f"missing coordinate {coord}"


def test_centroid_reset_card_present() -> None:
    card = COORD_REGISTRY["000_reset"]
    assert card.day_index == 27
    assert card.coord_id == "000_reset"
    assert card.tetrahedron == "reset"
    assert card.kernel_label == "C"


def test_enrichment_cards_have_required_fields() -> None:
    for coord, card in COORD_REGISTRY.items():
        assert card.coord_id == coord
        assert card.hebrew_letter
        assert card.hebrew_name
        assert card.layer in {0, 1, 2}
        assert card.mode in {0, 1, 2}
        assert card.breath in {0, 1, 2}
        assert len(card.embedding_vector) == 5
        assert len(card.narrative_fragments) >= 2
        assert card.adjacent_coords


def test_corner_iching_mappings() -> None:
    # A spot-check of the corner trigram mappings from the cross-domain spec.
    assert COORD_REGISTRY["000"].iching_name == "Kun"
    assert COORD_REGISTRY["222"].iching_name == "Qian"
    assert COORD_REGISTRY["002"].iching_name == "Zhen"
    assert COORD_REGISTRY["020"].iching_name == "Kan"


def test_adjacent_coords_are_one_step_apart() -> None:
    card = COORD_REGISTRY["000"]
    assert card.adjacent_coords == {"001", "010", "100"}
    centroid = COORD_REGISTRY["111"]
    assert "000" not in centroid.adjacent_coords
    assert "011" in centroid.adjacent_coords


# ---------------------------------------------------------------------------
# Gate 2 — MRKO JSON roundtrip
# ---------------------------------------------------------------------------


def _hebrew_unit(coord_path: tuple[str, ...], label: str) -> UnitReading:
    return UnitReading(
        source_type="hebrew_letter",
        source_label=label,
        coordinate_path=coord_path,
        raw_input=",".join(coord_path),
        semantic_tags=("hebrew",),
        confidence_score=0.95,
        prose="",
    )


def test_unit_reading_json_roundtrip() -> None:
    unit = UnitReading(
        source_type="hebrew_letter",
        source_label="day-0",
        coordinate_path=("000", "001"),
        raw_input="Aleph Bet",
        semantic_tags=("origin",),
        confidence_score=0.95,
        prose="",
    )
    payload = unit.to_json()
    restored = UnitReading.from_json(payload)
    assert restored == unit


def test_lattice_reading_output_json_roundtrip() -> None:
    path = constants.LATTICE_TRAVERSAL_SEQUENCE[:5]
    unit = _hebrew_unit(path, "genesis-opening")
    output = build_lattice_reading_output(
        [unit], source_type="multi_reading", source_label="test"
    )
    payload = output.to_json()
    restored = LatticeReadingOutput.from_json(payload)
    assert restored == output
    assert restored.centroid_present is False


def test_aggregate_output_tracks_distributions() -> None:
    path = constants.LATTICE_TRAVERSAL_SEQUENCE[:10]
    unit = _hebrew_unit(path, "first-ten-days")
    output = build_lattice_reading_output([unit])
    assert sum(output.layer_distribution.values()) == len(path)
    assert sum(output.mode_distribution.values()) == len(path)
    assert sum(output.breath_distribution.values()) == len(path)
    assert output.cross_substrate_transitions >= 0


# ---------------------------------------------------------------------------
# Gate 3 — Embedding discrimination
# ---------------------------------------------------------------------------


def test_embedding_self_similarity_is_perfect() -> None:
    seq = list(constants.LATTICE_TRAVERSAL_SEQUENCE[:14])
    emb = coord_sequence_embedding(seq)
    assert embedding_similarity(emb, emb) == pytest.approx(1.0)


def test_hebrew_readings_closer_than_iching_counterparts() -> None:
    hebrew_full = list(constants.LATTICE_TRAVERSAL_SEQUENCE[:27])
    hebrew_sub = hebrew_full[:14]
    # Bagua corner order (Heaven, Earth, Thunder, Water, Mountain, Fire, Wind, Lake).
    iching_sequence = [
        "222",
        "000",
        "002",
        "020",
        "200",
        "202",
        "220",
        "022",
    ] * 2

    emb_hebrew = coord_sequence_embedding(hebrew_full)
    emb_sub = coord_sequence_embedding(hebrew_sub)
    emb_iching = coord_sequence_embedding(iching_sequence)

    sim_hebrew = embedding_similarity(emb_hebrew, emb_sub)
    sim_cross = embedding_similarity(emb_hebrew, emb_iching)

    assert sim_hebrew > sim_cross
    assert sim_cross > -0.1  # sanity: vectors are not anti-correlated


def test_empty_embedding_is_zero_vector() -> None:
    assert coord_sequence_embedding([]) == tuple([0.0] * 12)
    assert embedding_similarity(coord_sequence_embedding([]), coord_sequence_embedding([])) == 0.0


# ---------------------------------------------------------------------------
# Gate 4 — Reverse parser fidelity
# ---------------------------------------------------------------------------


def test_reverse_parser_recover_aggregate_prose() -> None:
    path = list(constants.LATTICE_TRAVERSAL_SEQUENCE[:27])
    unit = _hebrew_unit(tuple(path), "genesis-full")
    output = build_lattice_reading_output(
        [unit], source_type="hebrew", source_label="genesis"
    )
    parser = ReverseLatticeParser()
    parsed = parser.parse_prose(output.prose)
    fidelity = parser.validate_reconstruction(output, parsed)
    assert fidelity >= 0.70
    # The aggregate prose explicitly lists all coordinates, so recovery is perfect.
    assert fidelity == pytest.approx(1.0)


def test_reverse_parser_recover_unit_prose() -> None:
    path = ("000", "001", "002", "010", "011")
    unit = UnitReading(
        source_type="hebrew_letter",
        source_label="opening",
        coordinate_path=path,
        raw_input="",
        semantic_tags=("hebrew",),
        confidence_score=0.9,
        prose="The reading begins at Aleph (000), moves through Bet (001) and Gimel (002), "
        "crosses Dalet (010), and rests at He (011) on the Fire face.",
    )
    output = build_lattice_reading_output([unit], source_label="unit-test")
    parser = ReverseLatticeParser()
    parsed = parser.parse_prose(unit.prose)
    recovered = set(parsed.coordinate_path)
    assert "000" in recovered
    assert "001" in recovered
    assert "011" in recovered


def test_parser_confidence_reflects_token_coverage() -> None:
    parser = ReverseLatticeParser()
    parsed = parser.parse_prose("A reading at Aleph and Bet moving toward Qian.")
    assert parsed.confidence > 0.0
    assert "Aleph" in parsed.matched_tokens
    assert "Qian" in parsed.matched_tokens
