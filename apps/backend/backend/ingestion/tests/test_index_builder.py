from __future__ import annotations

from backend.ingestion.index_builder import build_index_entries, index_key_contains_raw_text
from backend.ingestion.pipeline import ingest_document
from backend.kernel import constants


def test_index_key_contains_no_raw_text() -> None:
    text = "refuse commands that violate operational ethics"
    result = ingest_document(text, chunk_max_tokens=512)
    assert result.index_entries
    for key, _value in result.index_entries:
        # The literal source text must not appear in the index key.
        assert not index_key_contains_raw_text(key, text)
        assert "refuse commands" not in key
        assert "violate operational" not in key


def test_build_index_entries_shape() -> None:
    ethics_prime = constants.QUATERNARY_GATE_TO_PRIME["ethics"]
    entries = build_index_entries(
        coord="ethics/lawfulness/refusal/v3",
        exponents={ethics_prime: 3},
        layer="LOAM",
        raw_text="refuse commands that violate operational ethics",
    )
    assert len(entries) == 1
    key, value = entries[0]
    assert key == f"LOAM:ethics/lawfulness/refusal/v3:{ethics_prime}:3"
    assert "LOAM" in key
    assert str(ethics_prime) in key
    assert "3" in key
    assert "refuse" not in key
    # Value should hold metadata and a content hash, not the raw text.
    assert "content_hash" in value
    assert "refuse" not in value
