"""LlamaIndex-backed adapter with graceful fallback when unavailable."""
from typing import Any, List

from adapters.base import RetrievalAdapter, RetrievalResult


try:
    from llama_index.core.schema import NodeWithScore, TextNode  # type: ignore
    LLAMA_INDEX_AVAILABLE = True
except Exception:
    LLAMA_INDEX_AVAILABLE = False


class LlamaIndexAdapter(RetrievalAdapter):
    """Adapter for LlamaIndex retriever objects.

    If LlamaIndex is not installed the adapter degrades to a stub that returns
    an empty result set, keeping the package installable without proprietary
    dependencies.
    """

    def __init__(self, retriever: Any = None):
        self.retriever = retriever

    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        if not LLAMA_INDEX_AVAILABLE:
            return []

        retriever = self.retriever
        if retriever is None and isinstance(corpus, dict) and "retriever" in corpus:
            retriever = corpus["retriever"]

        if retriever is None:
            return []

        nodes = retriever.retrieve(query_text)
        results: List[RetrievalResult] = []
        for node in nodes:
            if isinstance(node, NodeWithScore):
                text = node.node.text if isinstance(node.node, TextNode) else str(node.node)
                results.append(
                    RetrievalResult(
                        text=text,
                        score=float(node.score or 0.0),
                        identifier=node.node.metadata.get("id"),
                        metadata=dict(node.node.metadata),
                    )
                )
            else:
                results.append(
                    RetrievalResult(
                        text=str(node),
                        score=0.0,
                        metadata={},
                    )
                )
        return results
