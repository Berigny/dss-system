"""Semantic clause chunker for document ingestion."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

DEFAULT_CHUNK_MAX_TOKENS = 512

# Sentence boundary: terminator followed by whitespace and an uppercase letter or digit.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
# Clause boundary inside an oversized sentence.
_CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[;:,])\s+")


@dataclass
class Chunk:
    """A semantic chunk of an ingested document."""

    text: str
    index: int
    token_count: int


def _token_count(text: str) -> int:
    """Approximate semantic-token count by whitespace splitting."""
    return len(text.split())


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences at semantic clause boundaries."""
    parts = _SENTENCE_BOUNDARY_RE.split(text)
    return [part.strip() for part in parts if part.strip()]


def _split_clauses(text: str) -> list[str]:
    """Split an oversized sentence on clause punctuation."""
    parts = _CLAUSE_BOUNDARY_RE.split(text)
    return [part.strip() for part in parts if part.strip()]


def chunk_document(text: str, *, chunk_max_tokens: int | None = None) -> list[Chunk]:
    """Split ``text`` into semantic chunks bounded by ``chunk_max_tokens``.

    Each sentence becomes its own chunk so that chunk boundaries align with
    semantic clause boundaries. Sentences that exceed the limit are split on
    clause punctuation, and only as a last resort on token count.
    """
    if chunk_max_tokens is None:
        chunk_max_tokens = int(
            os.getenv("KERNEL_CHUNK_MAX_TOKENS", str(DEFAULT_CHUNK_MAX_TOKENS))
        )
    chunk_max_tokens = max(1, int(chunk_max_tokens))

    text = " ".join((text or "").split())
    if not text:
        return []

    sentences = _split_sentences(text)
    chunks: list[Chunk] = []

    for sentence in sentences:
        sentence_tokens = _token_count(sentence)

        if sentence_tokens <= chunk_max_tokens:
            chunks.append(
                Chunk(
                    text=sentence,
                    index=len(chunks),
                    token_count=sentence_tokens,
                )
            )
            continue

        # Try to break the oversized sentence on clause boundaries.
        clauses = _split_clauses(sentence)
        for clause in clauses:
            clause_tokens = _token_count(clause)
            if clause_tokens <= chunk_max_tokens:
                chunks.append(
                    Chunk(
                        text=clause,
                        index=len(chunks),
                        token_count=clause_tokens,
                    )
                )
                continue

            # Last resort: split by token count.
            words = clause.split()
            for start in range(0, len(words), chunk_max_tokens):
                sub = " ".join(words[start : start + chunk_max_tokens])
                chunks.append(
                    Chunk(
                        text=sub,
                        index=len(chunks),
                        token_count=_token_count(sub),
                    )
                )

    # Normalize indices after any splitting.
    for i, chunk in enumerate(chunks):
        chunk.index = i
    return chunks
