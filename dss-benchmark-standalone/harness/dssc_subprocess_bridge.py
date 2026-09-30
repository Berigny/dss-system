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
from harness.runner import ADAPTER_MAP  # noqa: E402

V1_BRIDGE_ADAPTERS = frozenset({"faiss", "chroma", "qdrant"})

UNSUPPORTED_ADAPTERS: dict[str, str] = {
    "langchain": "requires a retriever object in the corpus, not a documents list",
    "llama_index": "requires a retriever object in the corpus, not a documents list",
    "milvus": "adapter is an unimplemented stub in dss-benchmark-standalone",
    "sentence_transformers": "not verified for plain documents-list corpus behind the bridge in v1",
}

PROVENANCE_KEYS = ("id", "source", "label", "flagged", "conflict")


def _import_adapter_class(name: str) -> type:
    cls = ADAPTER_MAP.get(name)
    if cls is None:
        raise ValueError(f"unknown adapter {name}")
    return cls


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
                            metadata=dict(doc.get("metadata") or {}),
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
    doc_id = _field(raw, "ID", "id")
    metadata = dict(_field(raw, "Metadata", "metadata") or {})
    if doc_id is not None and "id" not in metadata:
        metadata["id"] = doc_id
    return {
        "id": doc_id,
        "text": _field(raw, "Text", "text") or "",
        "metadata": metadata,
    }


def _make_adapter(name: str, mock_embeddings: bool) -> RetrievalAdapter:
    if name == "lexical":
        return LexicalBridgeAdapter()
    cls = _import_adapter_class(name)
    if name == "faiss":
        return cls(mock_embeddings=mock_embeddings)
    return cls()


def _validate_adapter_choice(name: str) -> None:
    if name == "lexical":
        return
    reason = UNSUPPORTED_ADAPTERS.get(name)
    if reason:
        print(
            f"dssc_subprocess_bridge: adapter {name!r} is not supported in v1: {reason}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if name not in V1_BRIDGE_ADAPTERS:
        print(
            f"dssc_subprocess_bridge: adapter {name!r} is not in the v1 bridge subset "
            f"({', '.join(sorted(V1_BRIDGE_ADAPTERS))})",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _probe_adapter(name: str, mock_embeddings: bool) -> None:
    try:
        _make_adapter(name, mock_embeddings)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(
            f"dssc_subprocess_bridge: failed to construct adapter {name!r}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


def _corpus(documents: List[dict]) -> dict:
    return {"documents": documents}


def _metadata_for_result(documents: List[dict], result: RetrievalResult) -> Dict[str, Any]:
    by_id = {str(d.get("id")): d.get("metadata") or {} for d in documents if d.get("id") is not None}
    merged: Dict[str, Any] = dict(by_id.get(str(result.identifier), {}))
    merged.update(result.metadata or {})
    if result.identifier is not None:
        merged["id"] = result.identifier
    return merged


def _respond(rid: str, ok: bool, **payload: Any) -> None:
    body = {"id": rid, "ok": ok, **payload}
    print(json.dumps(body), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="dssc subprocess adapter bridge")
    parser.add_argument("--adapter", default="faiss", help="adapter name (v1: faiss, chroma, qdrant, or lexical)")
    parser.add_argument(
        "--mock-embeddings",
        action="store_true",
        help="use mocked embeddings for faiss",
    )
    args, _ = parser.parse_known_args()

    _validate_adapter_choice(args.adapter)
    _probe_adapter(args.adapter, args.mock_embeddings)

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
                try:
                    results = adapter.query(_corpus(documents), query)
                except Exception as exc:  # noqa: BLE001
                    _respond(rid, True, outcome="error", error=str(exc), results=[])
                    continue
                if not results:
                    _respond(rid, True, outcome="completed_empty", results=[])
                else:
                    hits = [
                        {
                            "ID": r.identifier or "",
                            "Text": r.text,
                            "Score": float(r.score),
                            "Metadata": _metadata_for_result(documents, r),
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
