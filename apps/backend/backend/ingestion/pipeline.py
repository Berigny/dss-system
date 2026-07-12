"""End-to-end document ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.kernel.layer_router import LayerRouter
from backend.kernel.quaternary_gates import QuaternaryGate

from backend.ingestion.atom_extractor import SemanticAtom, extract_atoms
from backend.ingestion.chunker import Chunk, chunk_document
from backend.ingestion.index_builder import build_index_entries


@dataclass
class ChunkResult:
    """Result of ingesting a single chunk."""

    chunk: Chunk
    atoms: list[SemanticAtom]
    exponents: dict[int, int]
    layer: str
    coord: str | None = None


@dataclass
class IngestionResult:
    """Result of ingesting a whole document."""

    chunks: list[ChunkResult]
    composite_exponents: dict[int, int]
    composite_layer: str
    index_entries: list[tuple[str, str]]
    projection_coords: list[str] = field(default_factory=list)
    composite_coord: str | None = None
    checksum_336_satisfied: bool = False


def _empty_exponents() -> dict[int, int]:
    return {2: 0, 5: 0, 7: 0}


def _exponents_from_atoms(atoms: list[SemanticAtom]) -> dict[int, int]:
    exponents = _empty_exponents()
    for atom in atoms:
        exponents[atom.prime] += atom.v
    return exponents


def _route_layer(exponents: dict[int, int]) -> str:
    result = QuaternaryGate.evaluate(
        exponents.get(5, 0),
        exponents.get(7, 0),
        exponents.get(2, 0),
    )
    return LayerRouter.route_from_levels(result["levels"])


def _safe_projection_coord(coord: str) -> str:
    """Return a layer-store-safe projection coordinate.

    The RocksDB layer store uses ``:`` as its key delimiter, so blob-style
    coordinates that contain ``:`` are normalised to a slash path before a
    projection suffix is appended.
    """
    return coord.replace(":", "/").strip("/")


def ingest_document(text: str, *, chunk_max_tokens: int | None = None) -> IngestionResult:
    """Ingest ``text`` into chunks, atoms, layers, and index entries."""
    chunks = chunk_document(text, chunk_max_tokens=chunk_max_tokens)
    chunk_results: list[ChunkResult] = []
    composite_exponents = _empty_exponents()
    index_entries: list[tuple[str, str]] = []

    for chunk in chunks:
        atoms = extract_atoms(chunk.text)
        exponents = _exponents_from_atoms(atoms)
        layer = _route_layer(exponents)

        chunk_results.append(
            ChunkResult(
                chunk=chunk,
                atoms=atoms,
                exponents=exponents,
                layer=layer,
            )
        )

        for prime in (2, 5, 7):
            composite_exponents[prime] += exponents[prime]

        # Index each atom under its COORD and the chunk's layer.
        for atom in atoms:
            index_entries.extend(
                build_index_entries(
                    coord=atom.coord,
                    exponents={atom.prime: atom.v},
                    layer=layer,
                    raw_text=chunk.text,
                )
            )

    # Document-level composite state: product of all chunk exponents.
    composite_layer = _route_layer(composite_exponents)
    composite_coord = f"telos/purpose/composite/{composite_layer.lower()}"
    index_entries.extend(
        build_index_entries(
            coord=composite_coord,
            exponents=composite_exponents,
            layer=composite_layer,
            raw_text=text,
        )
    )

    return IngestionResult(
        chunks=chunk_results,
        composite_exponents=composite_exponents,
        composite_layer=composite_layer,
        index_entries=index_entries,
    )


def project_blob(
    blob_text: str,
    blob_coord: str,
    *,
    chunk_max_tokens: int | None = None,
) -> IngestionResult:
    """Run the HENGE-008 semantic projection pipeline over a single blob.

    The returned result contains chunk-level and blob-level composite
    projections whose coordinates are deterministic children of
    ``blob_coord``.  Projection coordinates use slash separators so they can be
    stored directly in the RocksDB layer store without colliding with its
    colon-delimited key schema.
    """
    chunks = chunk_document(blob_text, chunk_max_tokens=chunk_max_tokens)
    if not chunks:
        chunks = [Chunk(text="", index=0, token_count=0)]

    chunk_results: list[ChunkResult] = []
    composite_exponents = _empty_exponents()
    index_entries: list[tuple[str, str]] = []
    projection_coords: list[str] = []

    base_coord = _safe_projection_coord(blob_coord)

    for idx, chunk in enumerate(chunks):
        atoms = extract_atoms(chunk.text)
        exponents = _exponents_from_atoms(atoms)
        layer = _route_layer(exponents)
        child_coord = f"{base_coord}-proj-{idx:03d}"
        projection_coords.append(child_coord)

        chunk_results.append(
            ChunkResult(
                chunk=chunk,
                atoms=atoms,
                exponents=exponents,
                layer=layer,
                coord=child_coord,
            )
        )

        for prime in (2, 5, 7):
            composite_exponents[prime] += exponents[prime]

        # Atom COORD branch markers are preserved in the index_entries list for
        # callers that want a raw key/value projection index.
        for atom in atoms:
            index_entries.extend(
                build_index_entries(
                    coord=atom.coord,
                    exponents={atom.prime: atom.v},
                    layer=layer,
                    raw_text=chunk.text,
                )
            )

    composite_layer = _route_layer(composite_exponents)
    composite_coord = f"{base_coord}-proj-composite"
    projection_coords.append(composite_coord)
    index_entries.extend(
        build_index_entries(
            coord=composite_coord,
            exponents=composite_exponents,
            layer=composite_layer,
            raw_text=blob_text,
        )
    )

    evaluation = QuaternaryGate.evaluate(
        composite_exponents.get(5, 0),
        composite_exponents.get(7, 0),
        composite_exponents.get(2, 0),
    )

    return IngestionResult(
        chunks=chunk_results,
        composite_exponents=composite_exponents,
        composite_layer=composite_layer,
        index_entries=index_entries,
        projection_coords=projection_coords,
        composite_coord=composite_coord,
        checksum_336_satisfied=bool(evaluation["checksum_336_satisfied"]),
    )
