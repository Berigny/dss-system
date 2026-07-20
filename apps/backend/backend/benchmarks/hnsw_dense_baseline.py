"""HNSW dense-retrieval baseline using hnswlib + pinned MiniLM embeddings.

This baseline satisfies the same matched-information requirement as
:mod:`backend.benchmarks.real_embedding_baseline`, but it indexes the embeddings
with an HNSW graph so that query latency at scale reflects a production dense
retriever rather than a brute-force scan.
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from backend.benchmarks.comparison_baselines import Baseline, BaselineResult
from backend.benchmarks.real_embedding_baseline import PINNED_MODEL_NAME, load_embedder


PINNED_EF_CONSTRUCTION = 200
PINNED_M = 16
PINNED_EF_SEARCH = 64


@dataclass(frozen=True)
class HnswBuildConfig:
    """Tunable HNSW index parameters."""

    space: str = "cosine"
    ef_construction: int = PINNED_EF_CONSTRUCTION
    m: int = PINNED_M
    ef_search: int = PINNED_EF_SEARCH


class HnswDenseBaseline(Baseline):
    """Dense embedding baseline backed by an hnswlib approximate-nearest-neighbour index.

    The baseline uses the same pinned ``sentence-transformers/all-MiniLM-L6-v2``
    model as :class:`backend.benchmarks.real_embedding_baseline.RealEmbeddingBaseline`,
    but queries are served from an HNSW graph rather than a full linear scan.
    """

    name = "hnsw_dense"

    def __init__(
        self,
        model_name: str = PINNED_MODEL_NAME,
        build_config: HnswBuildConfig | None = None,
        embedder: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.build_config = build_config or HnswBuildConfig()
        self._embedder = embedder
        self._model_info: dict[str, Any] | None = None

    def _ensure_embedder(self) -> Any:
        if self._embedder is None:
            self._embedder = load_embedder(self.model_name)
            self._model_info = {
                "model_name": self.model_name,
                "index_type": "hnswlib",
                "space": self.build_config.space,
                "ef_construction": self.build_config.ef_construction,
                "m": self.build_config.m,
                "ef_search": self.build_config.ef_search,
            }
        return self._embedder

    @property
    def model_info(self) -> dict[str, Any] | None:
        return self._model_info

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        embedder = self._ensure_embedder()
        return embedder.encode(list(texts), convert_to_numpy=True)  # type: ignore[attr-defined]

    def _build_index(self, memory_embeddings: np.ndarray) -> Any:
        try:
            import hnswlib
        except ImportError as exc:
            raise RuntimeError(
                "hnswlib is required for the HNSW dense baseline; "
                "install with: pip install hnswlib"
            ) from exc

        dim = memory_embeddings.shape[1]
        index = hnswlib.Index(space=self.build_config.space, dim=dim)
        index.init_index(
            max_elements=len(memory_embeddings),
            ef_construction=self.build_config.ef_construction,
            M=self.build_config.m,
        )
        index.add_items(memory_embeddings.astype(np.float32))
        index.set_ef(self.build_config.ef_search)
        return index

    def run(
        self,
        memories: Sequence[Mapping[str, Any]],
        queries: Sequence[Mapping[str, Any]],
        *,
        top_k: int = 10,
    ) -> BaselineResult:
        start = time.perf_counter()

        memory_texts = [str(m.get("text", "")) for m in memories]
        memory_ids = [str(m.get("id", i)) for i, m in enumerate(memories)]
        memory_embeddings = self._encode(memory_texts)

        index = self._build_index(memory_embeddings)

        hits = 0
        hits_at_1 = 0
        rr_total = 0.0
        prompt_tokens = 0.0
        precision_at_k: dict[int, float] = {k: 0.0 for k in range(1, top_k + 1)}
        ndcg_at_k: dict[int, float] = {k: 0.0 for k in range(1, top_k + 1)}

        for query in queries:
            query_text = str(query.get("text", ""))
            relevant_ids = set(query.get("relevant_ids", []))
            query_embedding = self._encode([query_text]).astype(np.float32)
            labels, _ = index.knn_query(query_embedding, k=top_k)
            ranked_ids = [memory_ids[i] for i in labels[0]]
            relevance = [1.0 if mid in relevant_ids else 0.0 for mid in ranked_ids]

            for k in range(1, top_k + 1):
                precision_at_k[k] += self._precision_at_k(relevance, k)
                ndcg_at_k[k] += self._ndcg_at_k(relevance, k)

            prompt_tokens += len(query_text.split()) + sum(
                len(memory_texts[memory_ids.index(mid)].split()) for mid in ranked_ids
            )

            rank_hit = None
            for idx, mid in enumerate(ranked_ids):
                if mid in relevant_ids:
                    rank_hit = idx
                    break

            if rank_hit is not None:
                hits += 1
                if rank_hit < 1:
                    hits_at_1 += 1
                rr_total += 1.0 / float(rank_hit + 1)

        query_count = len(queries)
        for k in precision_at_k:
            precision_at_k[k] /= query_count if query_count else 1.0
            ndcg_at_k[k] /= query_count if query_count else 1.0

        latency_ms = (time.perf_counter() - start) * 1000.0

        return BaselineResult(
            baseline_name=self.name,
            recall_at_1=hits_at_1 / query_count if query_count else 0.0,
            recall_at_k=hits / query_count if query_count else 0.0,
            mrr=rr_total / query_count if query_count else 0.0,
            avg_latency_ms=latency_ms,
            token_cost=prompt_tokens + query_count * 64.0,
            prompt_tokens=prompt_tokens,
            completion_tokens=query_count * 64.0,
            precision_at_k=precision_at_k,
            ndcg_at_k=ndcg_at_k,
        )

    def estimate_storage_bytes(self, memories: Sequence[Mapping[str, Any]]) -> int:
        """Return the on-disk byte size of the HNSW index for ``memories``.

        This builds a temporary index and saves it to a scratch file so the
        measurement includes both graph overhead and stored vectors.
        """
        memory_texts = [str(m.get("text", "")) for m in memories]
        memory_embeddings = self._encode(memory_texts)
        index = self._build_index(memory_embeddings)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            index.save_index(str(tmp_path))
            return tmp_path.stat().st_size
        finally:
            if tmp_path.exists():
                os.unlink(tmp_path)

    @staticmethod
    def _precision_at_k(relevance: Sequence[float], k: int) -> float:
        if not relevance or k <= 0:
            return 0.0
        top = relevance[:k]
        return sum(top) / len(top)

    @staticmethod
    def _ndcg_at_k(relevance: Sequence[float], k: int) -> float:
        if not relevance or k <= 0:
            return 0.0
        import math

        top = relevance[:k]
        dcg = sum((2**rel - 1) / math.log2(i + 2) for i, rel in enumerate(top))
        ideal = sorted(relevance, reverse=True)[:k]
        idcg = sum((2**rel - 1) / math.log2(i + 2) for i, rel in enumerate(ideal))
        return dcg / idcg if idcg > 0 else 0.0


__all__ = (
    "HnswBuildConfig",
    "HnswDenseBaseline",
    "PINNED_EF_CONSTRUCTION",
    "PINNED_M",
    "PINNED_EF_SEARCH",
)
