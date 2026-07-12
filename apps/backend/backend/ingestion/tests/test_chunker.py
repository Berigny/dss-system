from __future__ import annotations

from backend.ingestion.chunker import chunk_document


def test_three_clause_document_yields_three_chunks() -> None:
    text = "First clause is here. Second clause follows. Third clause ends."
    chunks = chunk_document(text, chunk_max_tokens=512)
    assert len(chunks) == 3
    assert chunks[0].text == "First clause is here."
    assert chunks[1].text == "Second clause follows."
    assert chunks[2].text == "Third clause ends."


def test_chunk_size_limit_enforced() -> None:
    text = "word " * 100
    chunks = chunk_document(text, chunk_max_tokens=20)
    assert chunks
    assert all(chunk.token_count <= 20 for chunk in chunks)


def test_empty_document_returns_no_chunks() -> None:
    assert chunk_document("", chunk_max_tokens=512) == []
    assert chunk_document("   ", chunk_max_tokens=512) == []
