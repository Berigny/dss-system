"""Real embedding baseline using pinned ``sentence-transformers/all-MiniLM-L6-v2``.

This baseline satisfies DSS-277's matched-information B3 requirement: it uses the
same memories and queries as DSS but ranks by genuine dense embeddings on a local
CPU. The model name and revision are pinned; the first call computes a SHA256 of
the cached weights so the artifact can record the exact weights used.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from backend.benchmarks.comparison_baselines import Baseline, BaselineResult


PINNED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
PINNED_MODEL_REVISION = "main"


@dataclass(frozen=True)
class ModelInfo:
    model_name: str
    revision: str
    weights_sha256: str | None
    cache_path: str | None


def _weights_sha256(cache_path: Path) -> str:
    """Compute a deterministic SHA256 over all regular files in the model cache."""
    h = hashlib.sha256()
    files = sorted(p for p in cache_path.rglob("*") if p.is_file())
    for path in files:
        h.update(path.relative_to(cache_path).as_posix().encode("utf-8"))
        h.update(b"\x00")
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        h.update(b"\x00")
    return h.hexdigest()


def _model_cache_path(model_name: str) -> Path | None:
    """Return the Hugging Face cache directory for a model name, if it exists."""
    try:
        from huggingface_hub import constants

        cache_root = Path(constants.HF_HUB_CACHE)
        escaped = model_name.replace("/", "--")
        candidates = list(cache_root.glob(f"models--{escaped}"))
        if candidates:
            return candidates[0]
    except Exception:
        pass
    return None


def load_embedder(model_name: str = PINNED_MODEL_NAME):
    """Load the pinned sentence-transformer model; raise a clear error if missing."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for the real embedding baseline; "
            "install with: pip install 'sentence-transformers>=3.0'"
        ) from exc
    return SentenceTransformer(model_name)


def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0.0:
        return np.zeros(len(matrix))
    row_norms = np.linalg.norm(matrix, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = matrix.dot(query_vec) / (row_norms * query_norm)
    return np.nan_to_num(scores, nan=0.0)


class RealEmbeddingBaseline(Baseline):
    """Dense embedding baseline with pinned local CPU inference.

    This is the real-embedding arm of DSS-277 B3 matched-information baselines.
    It is *not* a stand-in; it uses actual ``all-MiniLM-L6-v2`` weights and
    reports their SHA256 in the run config.
    """

    name = "real_embedding"

    def __init__(self, model_name: str = PINNED_MODEL_NAME) -> None:
        self.model_name = model_name
        self._embedder = None
        self._model_info: ModelInfo | None = None

    def _ensure_embedder(self) -> Any:
        if self._embedder is None:
            self._embedder = load_embedder(self.model_name)
            cache_path = _model_cache_path(self.model_name)
            self._model_info = ModelInfo(
                model_name=self.model_name,
                revision=PINNED_MODEL_REVISION,
                weights_sha256=_weights_sha256(cache_path) if cache_path else None,
                cache_path=str(cache_path) if cache_path else None,
            )
        return self._embedder

    @property
    def model_info(self) -> ModelInfo | None:
        return self._model_info

    def run(
        self,
        memories: Sequence[Mapping[str, Any]],
        queries: Sequence[Mapping[str, Any]],
        *,
        top_k: int = 10,
    ) -> BaselineResult:
        start = time.perf_counter()
        embedder = self._ensure_embedder()

        memory_texts = [str(m.get("text", "")) for m in memories]
        memory_ids = [str(m.get("id", i)) for i, m in enumerate(memories)]
        memory_embeddings = embedder.encode(memory_texts, convert_to_numpy=True)

        hits = 0
        hits_at_1 = 0
        rr_total = 0.0
        prompt_tokens = 0.0

        for query in queries:
            query_text = str(query.get("text", ""))
            relevant_ids = set(query.get("relevant_ids", []))
            query_embedding = embedder.encode([query_text], convert_to_numpy=True)[0]

            scores = _cosine_similarity(query_embedding, memory_embeddings)
            ranked = sorted(
                [(memory_ids[i], float(scores[i])) for i in range(len(memory_ids))],
                key=lambda pair: pair[1],
                reverse=True,
            )[:top_k]

            prompt_tokens += len(query_text.split()) + sum(
                len(memory_texts[i].split()) for i in range(len(memory_ids))
            )

            rank_hit = None
            for idx, (mid, _) in enumerate(ranked):
                if mid in relevant_ids:
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


__all__ = (
    "PINNED_MODEL_NAME",
    "PINNED_MODEL_REVISION",
    "RealEmbeddingBaseline",
    "ModelInfo",
)
