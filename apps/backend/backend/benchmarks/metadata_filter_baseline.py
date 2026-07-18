"""Metadata-filter baseline for DSS-277 B3 matched-information comparison.

This baseline receives exactly the same structural metadata that DSS uses
(kernel node, circulation pass, valuation offset, dual validity, tetrahedron)
but applies only deterministic filters — no Qp arithmetic, no embeddings, and no
learned ranking. It measures how much of DSS's advantage comes from simply
holding the structural metadata constant, versus the additional value of Qp
coordinate arithmetic and governance gates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from backend.benchmarks.comparison_baselines import Baseline, BaselineResult


@dataclass(frozen=True)
class CoordinateMetadata:
    """Structural metadata fields attached to a memory or query."""

    kernel_node: str | None = None
    valuation_offset: int | None = None
    circulation_pass: int | None = None
    tetrahedron: str | None = None
    dual_valid: bool | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoordinateMetadata":
        return cls(
            kernel_node=data.get("kernel_node"),
            valuation_offset=data.get("valuation_offset"),
            circulation_pass=data.get("circulation_pass"),
            tetrahedron=data.get("tetrahedron"),
            dual_valid=data.get("dual_valid"),
        )


def _matches(query_meta: CoordinateMetadata, memory_meta: CoordinateMetadata) -> bool:
    """Return True if memory metadata satisfies the query filter."""
    if query_meta.kernel_node is not None and memory_meta.kernel_node != query_meta.kernel_node:
        return False
    if query_meta.tetrahedron is not None and memory_meta.tetrahedron != query_meta.tetrahedron:
        return False
    if query_meta.dual_valid is True and memory_meta.dual_valid is not True:
        return False
    if (
        query_meta.circulation_pass is not None
        and memory_meta.circulation_pass is not None
        and abs(memory_meta.circulation_pass - query_meta.circulation_pass) > 2
    ):
        return False
    if (
        query_meta.valuation_offset is not None
        and memory_meta.valuation_offset is not None
        and abs(memory_meta.valuation_offset - query_meta.valuation_offset) > 2
    ):
        return False
    return True


def _token_count(text: str) -> int:
    return len(text.split())


class MetadataFilterBaseline(Baseline):
    """Deterministic structural-metadata filter baseline.

    Each memory and query must carry a ``coordinate_metadata`` dict with the same
    fields DSS consumes. Memories that fail the filter are dropped; survivors are
    ranked by lexical overlap with the query text.
    """

    name = "metadata_filter"

    def run(
        self,
        memories: Sequence[Mapping[str, Any]],
        queries: Sequence[Mapping[str, Any]],
        *,
        top_k: int = 10,
    ) -> BaselineResult:
        start = time.perf_counter()

        hits = 0
        hits_at_1 = 0
        rr_total = 0.0
        prompt_tokens = 0.0

        for query in queries:
            query_text = str(query.get("text", ""))
            query_tokens = set(query_text.lower().split())
            relevant_ids = set(query.get("relevant_ids", []))
            query_meta = CoordinateMetadata.from_dict(query.get("coordinate_metadata", {}))

            candidates = []
            for memory in memories:
                memory_meta = CoordinateMetadata.from_dict(memory.get("coordinate_metadata", {}))
                if not _matches(query_meta, memory_meta):
                    continue
                memory_text = str(memory.get("text", ""))
                memory_tokens = set(memory_text.lower().split())
                overlap = len(query_tokens & memory_tokens)
                candidates.append((str(memory.get("id", "")), overlap))

            candidates.sort(key=lambda pair: pair[1], reverse=True)
            ranked = candidates[:top_k]

            prompt_tokens += _token_count(query_text) + sum(
                _token_count(str(m.get("text", ""))) for m in memories
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
    "CoordinateMetadata",
    "MetadataFilterBaseline",
)
