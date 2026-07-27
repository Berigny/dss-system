"""Abstention capacity suite (DSS-292 equivalent)."""
from typing import Any, Dict, List

from adapters.base import RetrievalAdapter
from corpora.synthetic import make_abstention_corpus


class AbstentionSuite:
    """Evaluate whether the adapter declines to answer when it cannot verify a fact."""

    def __init__(self, adapter: RetrievalAdapter, seeds: List[int] = None):
        self.adapter = adapter
        self.seeds = seeds or [42, 43, 44]

    def _abstains(self, results: List) -> bool:
        """Abstention is represented by an empty result list."""
        return len(results) == 0

    def _matches_expected(self, results: List, expected_id: str) -> bool:
        if not results:
            return False
        top = results[0]
        return top.identifier == expected_id

    def run(self) -> Dict[str, Any]:
        absent_correct = 0
        absent_total = 0
        present_correct = 0
        present_total = 0
        borderline_abstained = 0
        borderline_total = 0
        per_seed: List[Dict] = []

        for seed in self.seeds:
            corpus, queries = make_abstention_corpus(seed)
            seed_absent_correct = 0
            seed_present_correct = 0
            seed_borderline_abstained = 0

            for query_text in queries["absent"]:
                results = self.adapter.query(corpus, query_text)
                absent_total += 1
                if self._abstains(results):
                    absent_correct += 1
                    seed_absent_correct += 1

            for query_text, expected_id in queries["present"]:
                results = self.adapter.query(corpus, query_text)
                present_total += 1
                if self._matches_expected(results, expected_id):
                    present_correct += 1
                    seed_present_correct += 1

            for query_text in queries["borderline"]:
                results = self.adapter.query(corpus, query_text)
                borderline_total += 1
                if self._abstains(results):
                    borderline_abstained += 1
                    seed_borderline_abstained += 1

            per_seed.append({
                "seed": seed,
                "absent_correct": seed_absent_correct,
                "present_correct": seed_present_correct,
                "borderline_abstained": seed_borderline_abstained,
            })

        precision = absent_correct / absent_total if absent_total else 0.0
        recall = present_correct / present_total if present_total else 0.0
        false_abstention = 1.0 - recall
        borderline_abstention_rate = (
            borderline_abstained / borderline_total if borderline_total else 0.0
        )

        return {
            "suite": "abstention",
            "seeds": self.seeds,
            "absent_queries": absent_total,
            "present_queries": present_total,
            "borderline_queries": borderline_total,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "false_abstention_rate": round(false_abstention, 6),
            "borderline_abstention_rate": round(borderline_abstention_rate, 6),
            "per_seed": per_seed,
        }
