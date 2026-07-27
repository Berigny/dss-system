"""Retrieval integrity suite (DSS-295 equivalent)."""
from typing import Any, Dict, List

from adapters.base import RetrievalAdapter, RetrievalResult
from corpora.synthetic import make_adversarial_distractors, make_needle_corpus
from corpora.real import load_hotpot_qa, load_narrative_qa


class IntegritySuite:
    """Evaluate structural vs. semantic coherence of retrieval."""

    def __init__(self, adapter: RetrievalAdapter, seeds: List[int] = None):
        self.adapter = adapter
        self.seeds = seeds or [42, 43, 44]

    def _is_structurally_valid(self, doc: Dict, result: RetrievalResult) -> bool:
        """A retrieval is structurally valid if provenance metadata is present."""
        if result.identifier is None:
            return False
        meta = result.metadata or {}
        return bool(meta.get("id") or meta.get("label") or meta.get("source"))

    def _is_transparent(self, result: RetrievalResult) -> bool:
        """A result is transparent if it carries a source/provenance marker."""
        meta = result.metadata or {}
        return bool(meta.get("source") or meta.get("id") or meta.get("label"))

    def _evaluate_corpus(self, corpus: Dict, queries: List[str], expect_valid: bool) -> Dict:
        total = 0
        incoherent = 0
        transparent = 0
        for query_text in queries:
            results = self.adapter.query(corpus, query_text)
            total += 1
            if not results:
                continue
            top = results[0]
            if expect_valid and not self._is_structurally_valid(corpus["documents"][0], top):
                incoherent += 1
            if self._is_transparent(top):
                transparent += 1
        return {
            "queries": total,
            "incoherent": incoherent,
            "transparent": transparent,
        }

    def run(self) -> Dict[str, Any]:
        total_queries = 0
        total_incoherent = 0
        total_transparent = 0
        per_seed: List[Dict] = []

        for seed in self.seeds:
            needle = make_needle_corpus(seed, num_documents=50, needle_every=10)
            distractor = make_adversarial_distractors(seed, num_distractors=10)
            real_hotpot = load_hotpot_qa("validation")
            real_narrative = load_narrative_qa("valid")

            # Query every needle fact and every distractor.
            needle_queries = [
                d["text"] for d in needle["documents"] if d["metadata"].get("label") == "needle"
            ]
            distractor_queries = [d["text"] for d in distractor["documents"]]

            needle_eval = self._evaluate_corpus(needle, needle_queries, expect_valid=True)
            distractor_eval = self._evaluate_corpus(distractor, distractor_queries, expect_valid=False)

            # Real corpora: if stubs, queries are empty so they contribute nothing.
            real_queries = []
            if not real_hotpot.get("stub"):
                real_queries.extend([d["metadata"]["question"] for d in real_hotpot["documents"][:5]])
            if not real_narrative.get("stub"):
                real_queries.extend([d["metadata"]["question"] for d in real_narrative["documents"][:5]])
            real_eval = self._evaluate_corpus(
                {"documents": real_hotpot["documents"] + real_narrative["documents"]},
                real_queries,
                expect_valid=True,
            )

            seed_total = (
                needle_eval["queries"] + distractor_eval["queries"] + real_eval["queries"]
            )
            seed_incoherent = (
                needle_eval["incoherent"] + distractor_eval["incoherent"] + real_eval["incoherent"]
            )
            seed_transparent = (
                needle_eval["transparent"] + distractor_eval["transparent"] + real_eval["transparent"]
            )

            total_queries += seed_total
            total_incoherent += seed_incoherent
            total_transparent += seed_transparent

            per_seed.append({
                "seed": seed,
                "queries": seed_total,
                "incoherent_retrievals": seed_incoherent,
                "transparent_retrievals": seed_transparent,
            })

        incoherent_rate = total_incoherent / total_queries if total_queries else 0.0
        transparency_rate = total_transparent / total_queries if total_queries else 0.0

        return {
            "suite": "integrity",
            "seeds": self.seeds,
            "total_queries": total_queries,
            "incoherent_retrieval_rate": round(incoherent_rate, 6),
            "transparency_rate": round(transparency_rate, 6),
            "per_seed": per_seed,
        }
