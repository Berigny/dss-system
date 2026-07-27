"""Milvus-backed adapter with graceful fallback when unavailable."""
from typing import Any, List

from adapters.base import RetrievalAdapter, RetrievalResult


try:
    from pymilvus import MilvusClient  # type: ignore
    MILVUS_AVAILABLE = True
except Exception:
    MILVUS_AVAILABLE = False


class MilvusAdapter(RetrievalAdapter):
    """Adapter for Milvus vector collections.

    When Milvus (``pymilvus``) is unavailable the adapter returns an empty
    result set rather than failing import, preserving vendor-neutrality and
    optional dependencies.
    """

    def __init__(self, client: Any = None, collection_name: str = "benchmark"):
        self.client = client
        self.collection_name = collection_name

    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        if not MILVUS_AVAILABLE:
            raise NotImplementedError(
                "MilvusAdapter requires pymilvus, which is not installed."
            )

        client = self.client
        if client is None and isinstance(corpus, dict) and "client" in corpus:
            client = corpus["client"]

        if client is None:
            raise NotImplementedError(
                "MilvusAdapter requires a MilvusClient instance or corpus['client']."
            )

        raise NotImplementedError(
            "MilvusAdapter.query is a stub; wire it to your collection schema and embedding function."
        )
