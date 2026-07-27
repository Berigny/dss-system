"""Minimal dense-retrieval adapter backed by FAISS or a CPU fallback."""
import math
import re
from typing import Any, Dict, List, Tuple

from adapters.base import RetrievalAdapter, RetrievalResult


def _has_faiss() -> bool:
    try:
        import faiss  # type: ignore
        return faiss is not None
    except Exception:
        return False


_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "under", "again", "further", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "just", "and", "but", "if", "or",
    "because", "until", "while", "what", "which", "who", "whom", "this",
    "that", "these", "those", "am", "it", "its", "their", "them", "they",
    "we", "you", "he", "she", "his", "her", "our", "us", "me", "my",
})


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _build_vocab(texts: List[str]) -> Dict[str, int]:
    vocab: Dict[str, int] = {}
    for text in texts:
        for token in _tokenize(text):
            if token not in vocab:
                vocab[token] = len(vocab)
    return vocab


def _vectorize(text: str, vocab: Dict[str, int], dim: int) -> List[float]:
    vec = [0.0] * dim
    for token in _tokenize(text):
        idx = vocab.get(token)
        if idx is not None:
            vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class FaissAdapter(RetrievalAdapter):
    """Dense-retrieval adapter using FAISS when available, else brute force.

    This adapter builds a tiny bag-of-words embedding from the corpus vocabulary
    so that retrieval quality is meaningful for small synthetic corpora without
    pulling in large neural models. If the highest similarity is below
    ``threshold`` the adapter abstains by returning an empty list.
    """

    def __init__(self, dim: int = 512, mock_embeddings: bool = False, threshold: float = 0.25):
        self.dim = dim
        self.mock_embeddings = mock_embeddings
        self.threshold = threshold
        self._faiss = None
        if _has_faiss():
            import faiss  # type: ignore
            self._faiss = faiss
        self._cache: Dict[int, Tuple[List[dict], Dict[str, int], List[List[float]]]] = {}

    def _prepare(self, documents: List[dict]) -> Tuple[Dict[str, int], List[List[float]]]:
        corpus_id = id(documents)
        if corpus_id in self._cache:
            _, vocab, vectors = self._cache[corpus_id]
            return vocab, vectors

        vocab = _build_vocab([d["text"] for d in documents])
        dim = max(len(vocab), 1)
        vectors = [_vectorize(d["text"], vocab, dim) for d in documents]
        self._cache[corpus_id] = (documents, vocab, vectors)
        return vocab, vectors

    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        if not corpus or not corpus.get("documents"):
            return []

        documents = corpus["documents"]
        vocab, doc_vectors = self._prepare(documents)
        dim = max(len(vocab), 1)
        query_vec = _vectorize(query_text, vocab, dim)

        if self._faiss is not None and len(documents) >= 5:
            results = self._faiss_search(documents, doc_vectors, query_vec)
        else:
            results = self._brute_force_search(documents, doc_vectors, query_vec)

        if not results or results[0].score < self.threshold:
            return []
        return results

    def _faiss_search(
        self,
        documents: List[dict],
        doc_vectors: List[List[float]],
        query_vec: List[float],
    ) -> List[RetrievalResult]:
        import numpy as np  # numpy is required by faiss

        dim = len(query_vec)
        vectors = np.array([_normalize(v) for v in doc_vectors], dtype="float32")
        index = self._faiss.IndexFlatIP(dim)
        index.add(vectors)
        scores, indices = index.search(
            np.array([_normalize(query_vec)], dtype="float32"),
            min(10, len(documents)),
        )
        results: List[RetrievalResult] = []
        for score, idx in zip(scores[0], indices[0]):
            doc = documents[idx]
            results.append(
                RetrievalResult(
                    text=doc["text"],
                    score=float(score),
                    identifier=doc.get("id"),
                    metadata=doc.get("metadata", {}),
                )
            )
        return results

    def _brute_force_search(
        self,
        documents: List[dict],
        doc_vectors: List[List[float]],
        query_vec: List[float],
    ) -> List[RetrievalResult]:
        query_vec = _normalize(query_vec)
        scored = []
        for doc, vec in zip(documents, doc_vectors):
            score = _cosine(query_vec, _normalize(vec))
            scored.append(
                (
                    score,
                    RetrievalResult(
                        text=doc["text"],
                        score=score,
                        identifier=doc.get("id"),
                        metadata=doc.get("metadata", {}),
                    ),
                )
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:10]]
