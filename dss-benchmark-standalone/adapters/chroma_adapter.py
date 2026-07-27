"""Chroma-backed local vector adapter with optional dependency handling."""
from __future__ import annotations

from typing import Any, List

from adapters.base import RetrievalAdapter, RetrievalResult
from config.loader import get_adapter_config

try:
    import chromadb  # type: ignore
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction  # type: ignore

    CHROMA_AVAILABLE = True
except Exception:
    CHROMA_AVAILABLE = False


class ChromaAdapter(RetrievalAdapter):
    """Self-indexing Chroma adapter using a local persistent client.

    Requires ``chromadb`` and ``sentence-transformers``.  If the backend is
    unavailable, construction raises a clear error rather than silently
    returning empty results.
    """

    def __init__(
        self,
        persist_dir: str | None = None,
        threshold: float | None = None,
        embedding_model: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        cfg = config or get_adapter_config("chroma")
        self.persist_dir = persist_dir or cfg.get("persist_dir", "./.chroma_cache")
        self.threshold = threshold if threshold is not None else cfg.get("threshold", 0.25)
        self.embedding_model = embedding_model or cfg.get(
            "embedding_model", "all-MiniLM-L6-v2"
        )
        self._client: Any = None
        self._collection: Any = None

        if not CHROMA_AVAILABLE:
            raise RuntimeError(
                "ChromaAdapter requires 'chromadb' and sentence-transformers. "
                "Install with: pip install chromadb sentence-transformers"
            )

    def _ensure_indexed(self, corpus: Any) -> None:
        if self._collection is not None:
            return

        self._client = chromadb.PersistentClient(path=self.persist_dir)
        ef = SentenceTransformerEmbeddingFunction(model_name=self.embedding_model)
        self._collection = self._client.get_or_create_collection(
            name="dss_eval", embedding_function=ef
        )

        documents = corpus.get("documents", []) if isinstance(corpus, dict) else []
        if not documents:
            return

        self._collection.add(
            ids=[doc.get("id", str(i)) for i, doc in enumerate(documents)],
            documents=[doc["text"] for doc in documents],
            metadatas=[doc.get("metadata", {}) for doc in documents],
        )

    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        self._ensure_indexed(corpus)
        if self._collection is None:
            return []

        documents = corpus.get("documents", []) if isinstance(corpus, dict) else []
        n_results = min(10, max(1, len(documents)))
        raw = self._collection.query(query_texts=[query_text], n_results=n_results)

        if not raw or not raw.get("ids") or not raw["ids"][0]:
            return []

        results: List[RetrievalResult] = []
        for i, doc_id in enumerate(raw["ids"][0]):
            distance = raw.get("distances", [[0.0]])[0][i] if raw.get("distances") else 0.0
            sim = 1.0 / (1.0 + float(distance))
            if sim < self.threshold:
                continue
            results.append(
                RetrievalResult(
                    text=raw["documents"][0][i],
                    score=sim,
                    identifier=doc_id,
                    metadata=raw.get("metadatas", [[{}]])[0][i],
                )
            )

        return results[:1] if results else []
