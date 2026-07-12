"""Document ingestion pipeline for the v1.3-alpha ledger spec."""

from __future__ import annotations

from backend.ingestion.atom_extractor import SemanticAtom, extract_atoms
from backend.ingestion.chunker import Chunk, chunk_document
from backend.ingestion.index_builder import build_index_entries, index_key_contains_raw_text
from backend.ingestion.pipeline import (
    ChunkResult,
    IngestionResult,
    ingest_document,
    project_blob,
)

__all__ = [
    "Chunk",
    "ChunkResult",
    "IngestionResult",
    "SemanticAtom",
    "build_index_entries",
    "chunk_document",
    "extract_atoms",
    "index_key_contains_raw_text",
    "ingest_document",
    "project_blob",
]
