"""Neural dense-retrieval adapter using sentence-transformers embeddings.

Uses FAISS when available for fast search; otherwise falls back to a brute-force
cosine scan in NumPy.  This establishes a strong neural baseline that other
engines can be compared against.
"""
from __future__ import annotations

from typing import Any, List

from adapters.base import RetrievalAdapter, RetrievalResult
from config.loader import get_adapter_config

try:
    from sentence_transformers import SentenceTransformer  # type: ignore

    ST_AVAILABLE = True
except Exception:
    ST_AVAILABLE = False

try:
    import faiss  # type: ignore

    FAISS_AVAILABLE = True
except Exception:
    FAISS_AVAILABLE = False

try:
    import numpy as np  # type: ignore

    NUMPY_AVAILABLE = True
except Exception:
    NUMPY_AVAILABLE = False


class SentenceTransformersAdapter(RetrievalAdapter):
    """Self-indexing adapter powered by sentence-transformers embeddings."""

    def __init__(
        self,
        model_name: str | None = None,
        threshold: float | None = None,
        config: dict[str, Any] | None = None,
    ):
        cfg = config or get_adapter_config("sentence_transformers")
        self.model_name = model_name or cfg.get("model_name", "all-MiniLM-L6-v2")
        self.threshold = threshold if threshold is not None else cfg.get("threshold", 0.25)
        self._model: Any = None
        self._faiss_index: Any = None
        self._doc_embeddings: Any = None
        self._docs: List[dict] = []

        if not ST_AVAILABLE:
            raise RuntimeError(
                "SentenceTransformersAdapter requires 'sentence-transformers'. "
                "Install with: pip install sentence-transformers"
            )
        if not NUMPY_AVAILABLE:
            raise RuntimeError(
                "SentenceTransformersAdapter requires 'numpy'. "
                "Install with: pip install numpy"
            )

    def _encoder(self) -> Any:
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _ensure_indexed(self, corpus: Any) -> None:
        if self._faiss_index is not None or self._doc_embeddings is not None:
            return

        documents = corpus.get("documents", []) if isinstance(corpus, dict) else []
        if not documents:
            return

        model = self._encoder()
        embeddings = model.encode(
            [d["text"] for d in documents], show_progress_bar=False
        )
        self._docs = documents

        if FAISS_AVAILABLE:
            dim = int(embeddings.shape[1])
            index = faiss.IndexFlatIP(dim)
            index.add(embeddings.astype("float32"))
            self._faiss_index = index
        else:
            self._doc_embeddings = embeddings

    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        self._ensure_indexed(corpus)
        if self._faiss_index is None and self._doc_embeddings is None:
            return []

        model = self._encoder()
        query_vec = model.encode([query_text], show_progress_bar=False)

        if self._faiss_index is not None:
            scores, indices = self._faiss_index.search(
                query_vec.astype("float32"), min(10, len(self._docs) or 1)
            )
            score = float(scores[0][0])
            idx = int(indices[0][0])
        else:
            query_vec = query_vec[0]
            norms = np.linalg.norm(self._doc_embeddings, axis=1) * np.linalg.norm(
                query_vec
            )
            scores = np.dot(self._doc_embeddings, query_vec) / np.where(
                norms == 0, 1.0, norms
            )
            idx = int(np.argmax(scores))
            score = float(scores[idx])

        if score < self.threshold:
            return []

        doc = self._docs[idx]
        return [
            RetrievalResult(
                text=doc["text"],
                score=score,
                identifier=doc.get("id"),
                metadata=doc.get("metadata", {}),
            )
        ]
