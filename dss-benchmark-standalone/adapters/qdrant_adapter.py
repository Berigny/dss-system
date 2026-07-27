"""Qdrant-backed local vector adapter with optional dependency handling."""
from __future__ import annotations

from typing import Any, List

from adapters.base import RetrievalAdapter, RetrievalResult
from config.loader import get_adapter_config

try:
    from qdrant_client import QdrantClient  # type: ignore
    from qdrant_client.models import Distance, PointStruct, VectorParams  # type: ignore

    QDRANT_AVAILABLE = True
except Exception:
    QDRANT_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer  # type: ignore

    ST_AVAILABLE = True
except Exception:
    ST_AVAILABLE = False


class QdrantAdapter(RetrievalAdapter):
    """Self-indexing Qdrant adapter using an in-memory (or local) client.

    Requires ``qdrant-client`` and ``sentence-transformers``.  The adapter
    embeds corpus and query text with the configured sentence-transformer
    model and stores vectors in Qdrant with cosine distance.
    """

    def __init__(
        self,
        location: str | None = None,
        collection_name: str | None = None,
        vector_size: int | None = None,
        threshold: float | None = None,
        embedding_model: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        cfg = config or get_adapter_config("qdrant")
        self.location = location or cfg.get("location", ":memory:")
        self.collection_name = collection_name or cfg.get("collection_name", "dss_eval")
        self.vector_size = vector_size if vector_size is not None else cfg.get("vector_size", 384)
        self.threshold = threshold if threshold is not None else cfg.get("threshold", 0.25)
        self.embedding_model = embedding_model or cfg.get(
            "embedding_model", "all-MiniLM-L6-v2"
        )
        self._client: Any = None
        self._docs: List[dict] = []

        if not QDRANT_AVAILABLE:
            raise RuntimeError(
                "QdrantAdapter requires 'qdrant-client'. "
                "Install with: pip install qdrant-client sentence-transformers"
            )
        if not ST_AVAILABLE:
            raise RuntimeError(
                "QdrantAdapter requires 'sentence-transformers'. "
                "Install with: pip install sentence-transformers"
            )

    def _encoder(self) -> Any:
        return SentenceTransformer(self.embedding_model)

    def _ensure_indexed(self, corpus: Any) -> None:
        if self._client is not None:
            return

        self._client = QdrantClient(self.location)
        self._client.recreate_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
        )

        documents = corpus.get("documents", []) if isinstance(corpus, dict) else []
        if not documents:
            return

        model = self._encoder()
        embeddings = model.encode([d["text"] for d in documents], show_progress_bar=False)
        points = [
            PointStruct(
                id=i,
                vector=embeddings[i].tolist(),
                payload={"text": doc["text"], **doc.get("metadata", {})},
            )
            for i, doc in enumerate(documents)
        ]
        self._client.upsert(collection_name=self.collection_name, points=points)
        self._docs = documents

    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        self._ensure_indexed(corpus)
        if self._client is None:
            return []

        model = self._encoder()
        vector = model.encode([query_text], show_progress_bar=False)[0].tolist()
        result = self._client.search(
            collection_name=self.collection_name,
            query_vector=vector,
            limit=1,
        )
        if not result:
            return []

        match = result[0]
        if match.score < self.threshold:
            return []

        payload = dict(match.payload or {})
        text = payload.pop("text", "")
        return [
            RetrievalResult(
                text=text,
                score=float(match.score),
                identifier=str(match.id),
                metadata=payload,
            )
        ]
