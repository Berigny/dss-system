#!/usr/bin/env python3
"""JSON-lines bridge from dssc to dss-benchmark-standalone RetrievalAdapter implementations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.base import RetrievalAdapter, RetrievalResult  # noqa: E402

ADAPTER_MAP: dict[str, str] = {
    "faiss": "adapters.faiss_adapter.FaissAdapter",
    "chroma": "adapters.chroma_adapter.ChromaAdapter",
    "qdrant": "adapters.qdrant_adapter.QdrantAdapter",
    "sentence_transformers": "adapters.sentence_transformers_adapter.SentenceTransformersAdapter",
    "langchain": "adapters.langchain_adapter.LangChainAdapter",
    "llama_index": "adapters.llama_index_adapter.LlamaIndexAdapter",
    "milvus": "adapters.milvus_adapter.MilvusAdapter",
}


def _import_adapter_class(name: str) -> type:
    if name not in ADAPTER_MAP:
        raise ValueError(f"unknown adapter {name}")
    module_path, class_name = ADAPTER_MAP[name].rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


class LexicalBridgeAdapter:
    """Token-overlap adapter with no extra dependencies (smoke / CI)."""

    def __init__(self, min_score: float = 0.35) -> None:
        self.min_score = min_score
        self._documents: List[dict] = []

    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        if not corpus or not corpus.get("documents"):
            return []
        documents = corpus["documents"]
        q_tokens = set(_tokenize(query_text))
        if not q_tokens:
            return []
        scored: List[tuple[float, RetrievalResult]] = []
        for doc in documents:
            d_tokens = set(_tokenize(str(doc.get("text", ""))))
            if not d_tokens:
                continue
            inter = len(q_tokens & d_tokens)
            union = len(q_tokens | d_tokens) or 1
            score = inter / union
            if score >= self.min_score:
                scored.append(
                    (
                        score,
                        RetrievalResult(
                            text=str(doc.get("text", "")),
                            score=score,
                            identifier=doc.get("id"),
                            metadata=doc.get("metadata") or {},
                        ),
                    )
                )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:10]]


def _tokenize(text: str) -> List[str]:
    import re

    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def _field(doc: dict, *keys: str) -> Any:
    for key in keys:
        if key in doc:
            return doc[key]
    return None


def _normalize_doc(raw: dict) -> dict:
    return {
        "id": _field(raw, "ID", "id"),
        "text": _field(raw, "Text", "text") or "",
        "metadata": _field(raw, "Metadata", "metadata") or {},
    }


def _make_adapter(name: str, mock_embeddings: bool) -> RetrievalAdapter:
    if name == "lexical":
        return LexicalBridgeAdapter()
    cls = _import_adapter_class(name)
    if name == "faiss":
        return cls(mock_embeddings=mock_embeddings)
    return cls()


def _corpus(documents: List[dict]) -> dict:
    return {"documents": documents}


def _respond(rid: str, ok: bool, **payload: Any) -> None:
    body = {"id": rid, "ok": ok, **payload}
    print(json.dumps(body), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="dssc subprocess adapter bridge")
    parser.add_argument("--adapter", default="lexical", help="adapter name")
    parser.add_argument(
        "--mock-embeddings",
        action="store_true",
        help="use mocked embeddings for faiss",
    )
    args, _ = parser.parse_known_args()

    documents: List[dict] = []
    adapter: Optional[RetrievalAdapter] = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        op = req.get("op")
        rid = str(req.get("id", ""))

        try:
            if op == "reset":
                documents = []
                adapter = _make_adapter(args.adapter, args.mock_embeddings)
                _respond(rid, True)
            elif op == "ingest":
                if adapter is None:
                    adapter = _make_adapter(args.adapter, args.mock_embeddings)
                raw_docs = req.get("docs") or []
                documents = [_normalize_doc(d) for d in raw_docs]
                _respond(rid, True)
            elif op == "query":
                if adapter is None:
                    adapter = _make_adapter(args.adapter, args.mock_embeddings)
                query = str(req.get("query") or "")
                results = adapter.query(_corpus(documents), query)
                if not results:
                    _respond(rid, True, outcome="completed_empty", results=[])
                else:
                    hits = [
                        {
                            "ID": r.identifier or "",
                            "Text": r.text,
                            "Score": float(r.score),
                            "Metadata": r.metadata or {},
                        }
                        for r in results
                    ]
                    _respond(rid, True, outcome="hits", results=hits)
            elif op == "close":
                _respond(rid, True)
                return 0
            else:
                _respond(rid, False, error=f"unknown op {op}")
        except Exception as exc:  # noqa: BLE001
            _respond(rid, False, error=str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
