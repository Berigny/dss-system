"""LangChain-backed adapter with graceful fallback when unavailable."""
from typing import Any, List

from adapters.base import RetrievalAdapter, RetrievalResult


try:
    from langchain.schema import Document  # type: ignore
    LANGCHAIN_AVAILABLE = True
except Exception:
    LANGCHAIN_AVAILABLE = False


class LangChainAdapter(RetrievalAdapter):
    """Adapter for LangChain retriever objects.

    If LangChain is not installed the adapter degrades to a stub that returns
    an empty result set, keeping the package installable without proprietary
    dependencies.
    """

    def __init__(self, retriever: Any = None):
        self.retriever = retriever

    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        if not LANGCHAIN_AVAILABLE:
            return []

        retriever = self.retriever
        if retriever is None and isinstance(corpus, dict) and "retriever" in corpus:
            retriever = corpus["retriever"]

        if retriever is None:
            return []

        docs = retriever.get_relevant_documents(query_text)
        results: List[RetrievalResult] = []
        for doc in docs:
            if isinstance(doc, Document):
                results.append(
                    RetrievalResult(
                        text=doc.page_content,
                        score=float(doc.metadata.get("score", 0.0)),
                        identifier=doc.metadata.get("id"),
                        metadata=dict(doc.metadata),
                    )
                )
            else:
                results.append(
                    RetrievalResult(
                        text=str(doc),
                        score=0.0,
                        metadata={},
                    )
                )
        return results
