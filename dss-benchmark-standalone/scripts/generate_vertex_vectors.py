#!/usr/bin/env python3
"""Generate Vertex AI Vector Search JSONL embeddings from a corpus.

Reads a corpus JSON with a "documents" array, embeds each document's "text"
using a sentence-transformers model, validates the embedding dimension, and
writes a JSONL file ready for Vertex AI Vector Search.

Example:
    python scripts/generate_vertex_vectors.py \
        --corpus corpora/sample_corpus.json \
        --output vectors.jsonl \
        --gcs-uri gs://dss-evidence-prod/vector-init/ \
        --expected-dimensions 384
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load_corpus(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    documents = data.get("documents", [])
    if not documents:
        raise SystemExit(f"No documents found in {path}")
    return documents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Embed a corpus and produce Vertex AI Vector Search JSONL"
    )
    parser.add_argument("--corpus", required=True, help="Path to corpus JSON file")
    parser.add_argument("--output", required=True, help="Local JSONL output path")
    parser.add_argument(
        "--gcs-uri",
        help="Optional GCS prefix to upload the JSONL, e.g. gs://bucket/vector-init/",
    )
    parser.add_argument(
        "--model", default="all-MiniLM-L6-v2", help="sentence-transformers model name"
    )
    parser.add_argument(
        "--batch-size", type=int, default=32, help="Embedding batch size"
    )
    parser.add_argument(
        "--expected-dimensions",
        type=int,
        default=384,
        help="Expected embedding dimension; aborts if any vector differs",
    )
    args = parser.parse_args(argv)

    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        print(
            "error: sentence-transformers is not installed. "
            "Install it with: pip install sentence-transformers",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    corpus_path = Path(args.corpus)
    output_path = Path(args.output)
    documents = _load_corpus(corpus_path)

    ids = [doc.get("id", f"doc-{i:04d}") for i, doc in enumerate(documents)]
    texts = [doc["text"] for doc in documents]

    print(f"Encoding {len(texts)} documents with '{args.model}'...")
    model = SentenceTransformer(args.model)
    embeddings = model.encode(texts, batch_size=args.batch_size, show_progress_bar=True)

    actual_dim = int(embeddings.shape[1])
    print(f"Model produced dimension: {actual_dim}")
    if actual_dim != args.expected_dimensions:
        print(
            f"error: expected {args.expected_dimensions} dimensions, got {actual_dim}",
            file=sys.stderr,
        )
        return 1

    failures = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        for doc_id, vec in zip(ids, embeddings):
            vec_list = [float(v) for v in vec]
            if len(vec_list) != args.expected_dimensions:
                print(
                    f"error: vector for {doc_id} has {len(vec_list)} dimensions",
                    file=sys.stderr,
                )
                failures += 1
                continue
            fh.write(json.dumps({"id": doc_id, "embedding": vec_list}) + "\n")

    if failures:
        print(f"error: {failures} vector(s) failed validation", file=sys.stderr)
        return 1

    print(f"Wrote {len(texts)} validated vectors to {output_path}")

    if args.gcs_uri:
        dest = args.gcs_uri.rstrip("/") + "/" + output_path.name
        cmd = ["gcloud", "storage", "cp", str(output_path), dest]
        print("Uploading:", " ".join(cmd))
        subprocess.run(cmd, check=True)
        print(f"Uploaded to {dest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
