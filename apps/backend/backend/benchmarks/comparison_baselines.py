"""External baseline adapters for DSS broader-comparison benchmarks.

Each baseline operates on a minimal common interface:

- ``memories``: list of dicts with ``id`` and ``text``.
- ``queries``: list of dicts with ``id``, ``text``, and ``relevant_ids`` (set).

Baselines return a ``BaselineResult`` with retrieval metrics and cost/latency
estimates.  They are intentionally simple, dependency-light stand-ins for the
strong external systems named in DSS-227 (dense retrieval, hierarchical RAG,
leading long-context model).
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from backend.search.token_index import normalise_tokens


@dataclass(frozen=True)
class BaselineResult:
    """Outcome of a baseline run on one benchmark split."""

    baseline_name: str
    recall_at_1: float
    recall_at_k: float
    mrr: float
    avg_latency_ms: float
    token_cost: float
    prompt_tokens: float
    completion_tokens: float


class Baseline(ABC):
    """Abstract external baseline."""

    name: str

    @abstractmethod
    def run(
        self,
        memories: Sequence[Mapping[str, Any]],
        queries: Sequence[Mapping[str, Any]],
        *,
        top_k: int = 10,
    ) -> BaselineResult:
        """Run the baseline and return aggregate metrics."""


@dataclass(frozen=True)
class _RankedResult:
    memory_id: str
    text: str
    score: float


def _tokenize(text: str) -> list[str]:
    return normalise_tokens(text)


def _build_count_vectors(
    memories: Sequence[Mapping[str, Any]],
    vocab: Mapping[str, int],
) -> np.ndarray:
    matrix = np.zeros((len(memories), len(vocab)), dtype=np.float64)
    for i, mem in enumerate(memories):
        for token in _tokenize(str(mem.get("text", ""))):
            idx = vocab.get(token)
            if idx is not None:
                matrix[i, idx] += 1.0
    return matrix


def _cosine_scores(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0.0:
        return np.zeros(len(matrix))
    row_norms = np.linalg.norm(matrix, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = matrix.dot(query_vec) / (row_norms * query_norm)
    return np.nan_to_num(scores, nan=0.0)


def _run_dense_ranking(
    memories: Sequence[Mapping[str, Any]],
    queries: Sequence[Mapping[str, Any]],
    *,
    top_k: int,
    hierarchical: bool = False,
    baseline_name: str = "bow_stand_in",
) -> BaselineResult:
    """Shared dense-retrieval implementation with optional coarse first stage."""
    start = time.perf_counter()

    # Build a shared vocabulary.
    vocab: dict[str, int] = {}
    for mem in memories:
        for token in _tokenize(str(mem.get("text", ""))):
            if token not in vocab:
                vocab[token] = len(vocab)

    memory_matrix = _build_count_vectors(memories, vocab)

    hits = 0
    hits_at_1 = 0
    rr_total = 0.0
    prompt_tokens = 0.0

    for query in queries:
        query_tokens = _tokenize(str(query.get("text", "")))
        query_vec = np.zeros(len(vocab), dtype=np.float64)
        for token in query_tokens:
            idx = vocab.get(token)
            if idx is not None:
                query_vec[idx] += 1.0

        candidates = list(range(len(memories)))
        if hierarchical:
            # First stage: keep only memories that share at least one token.
            candidates = [
                i
                for i in candidates
                if np.dot(memory_matrix[i], query_vec) > 0.0
            ]
            if not candidates:
                candidates = list(range(len(memories)))

        candidate_matrix = memory_matrix[candidates]
        scores = _cosine_scores(query_vec, candidate_matrix)
        ranked = sorted(
            [
                _RankedResult(
                    memory_id=str(memories[candidates[i]].get("id")),
                    text=str(memories[candidates[i]].get("text", "")),
                    score=float(scores[i]),
                )
                for i in range(len(candidates))
            ],
            key=lambda r: r.score,
            reverse=True,
        )[:top_k]

        relevant_ids = set(query.get("relevant_ids", []))
        prompt_tokens += len(query_tokens) + sum(
            len(_tokenize(r.text)) for r in ranked
        )

        rank_hit = None
        for idx, r in enumerate(ranked):
            if r.memory_id in relevant_ids:
                rank_hit = idx
                break

        if rank_hit is not None:
            hits += 1
            if rank_hit < 1:
                hits_at_1 += 1
            rr_total += 1.0 / float(rank_hit + 1)

    query_count = len(queries)
    recall_at_1 = hits_at_1 / query_count if query_count else 0.0
    recall_at_k = hits / query_count if query_count else 0.0
    mrr = rr_total / query_count if query_count else 0.0
    latency_ms = (time.perf_counter() - start) * 1000.0

    return BaselineResult(
        baseline_name=baseline_name,
        recall_at_1=recall_at_1,
        recall_at_k=recall_at_k,
        mrr=mrr,
        avg_latency_ms=latency_ms,
        token_cost=prompt_tokens + query_count * 64.0,
        prompt_tokens=prompt_tokens,
        completion_tokens=query_count * 64.0,
    )


class BoWStandInBaseline(Baseline):
    """Bag-of-words cosine retrieval stand-in for a dense embedding model.

    Renamed from ``DenseRetrievalBaseline`` in DSS-277 to make the stand-in
    nature explicit. A real embedding baseline using pinned
    ``sentence-transformers/all-MiniLM-L6-v2`` weights is provided separately
    in :mod:`backend.benchmarks.real_embedding_baseline`.
    """

    name = "bow_stand_in"

    def run(
        self,
        memories: Sequence[Mapping[str, Any]],
        queries: Sequence[Mapping[str, Any]],
        *,
        top_k: int = 10,
    ) -> BaselineResult:
        return _run_dense_ranking(memories, queries, top_k=top_k, hierarchical=False, baseline_name=self.name)


class HierarchicalRagBaseline(Baseline):
    """Two-stage coarse-filter + dense-rerank stand-in."""

    name = "hierarchical_rag"

    def run(
        self,
        memories: Sequence[Mapping[str, Any]],
        queries: Sequence[Mapping[str, Any]],
        *,
        top_k: int = 10,
    ) -> BaselineResult:
        return _run_dense_ranking(memories, queries, top_k=top_k, hierarchical=True, baseline_name=self.name)


class LongContextBaseline(Baseline):
    """Mock leading long-context model: reads all memories if they fit in budget."""

    name = "long_context_model"
    token_budget: int = 4096

    def run(
        self,
        memories: Sequence[Mapping[str, Any]],
        queries: Sequence[Mapping[str, Any]],
        *,
        top_k: int = 10,
    ) -> BaselineResult:
        start = time.perf_counter()
        total_memory_tokens = sum(
            len(_tokenize(str(mem.get("text", "")))) for mem in memories
        )
        fits_in_context = total_memory_tokens <= self.token_budget

        hits = 0
        hits_at_1 = 0
        rr_total = 0.0
        prompt_tokens = 0.0

        for query in queries:
            query_tokens = _tokenize(str(query.get("text", "")))
            relevant_ids = set(query.get("relevant_ids", []))

            if fits_in_context:
                # "Attend" to all memories; rank relevant ones first by lexical overlap.
                ranked = sorted(
                    [
                        _RankedResult(
                            memory_id=str(mem.get("id")),
                            text=str(mem.get("text", "")),
                            score=float(
                                len(set(query_tokens) & set(_tokenize(str(mem.get("text", "")))))
                            ),
                        )
                        for mem in memories
                    ],
                    key=lambda r: r.score,
                    reverse=True,
                )[:top_k]
            else:
                # Over budget: fall back to token-overlap selection.
                ranked = sorted(
                    [
                        _RankedResult(
                            memory_id=str(mem.get("id")),
                            text=str(mem.get("text", "")),
                            score=float(
                                len(set(query_tokens) & set(_tokenize(str(mem.get("text", "")))))
                            ),
                        )
                        for mem in memories
                    ],
                    key=lambda r: r.score,
                    reverse=True,
                )[:top_k]

            prompt_tokens += len(query_tokens) + sum(
                len(_tokenize(r.text)) for r in ranked
            )

            rank_hit = None
            for idx, r in enumerate(ranked):
                if r.memory_id in relevant_ids:
                    rank_hit = idx
                    break

            if rank_hit is not None:
                hits += 1
                if rank_hit < 1:
                    hits_at_1 += 1
                rr_total += 1.0 / float(rank_hit + 1)

        query_count = len(queries)
        recall_at_1 = hits_at_1 / query_count if query_count else 0.0
        recall_at_k = hits / query_count if query_count else 0.0
        mrr = rr_total / query_count if query_count else 0.0
        latency_ms = (time.perf_counter() - start) * 1000.0

        return BaselineResult(
            baseline_name=self.name,
            recall_at_1=recall_at_1,
            recall_at_k=recall_at_k,
            mrr=mrr,
            avg_latency_ms=latency_ms,
            token_cost=prompt_tokens + query_count * 64.0,
            prompt_tokens=prompt_tokens,
            completion_tokens=query_count * 64.0,
        )


class GrokBaseline(Baseline):
    """Placeholder for a live Grok comparison.

    Returns zero metrics and reports itself as blocked so that the comparison
    suite can document the absence of API access without failing.
    """

    name = "grok_latest"
    blocked_reason = "no_api_key_or_access_configured"

    def run(
        self,
        memories: Sequence[Mapping[str, Any]],
        queries: Sequence[Mapping[str, Any]],
        *,
        top_k: int = 10,
    ) -> BaselineResult:
        return BaselineResult(
            baseline_name=self.name,
            recall_at_1=0.0,
            recall_at_k=0.0,
            mrr=0.0,
            avg_latency_ms=0.0,
            token_cost=0.0,
            prompt_tokens=0.0,
            completion_tokens=0.0,
        )


BASELINES: dict[str, Baseline] = {
    cls().name: cls()
    for cls in (
        BoWStandInBaseline,
        HierarchicalRagBaseline,
        LongContextBaseline,
        GrokBaseline,
    )
}


__all__ = (
    "Baseline",
    "BaselineResult",
    "BoWStandInBaseline",
    "HierarchicalRagBaseline",
    "LongContextBaseline",
    "GrokBaseline",
    "BASELINES",
)
