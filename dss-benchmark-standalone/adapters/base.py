"""Abstract adapter interface for the DSS benchmark harness.

This module defines the vendor-neutral contract that every retrieval system
must satisfy to be exercised by the benchmark suites.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RetrievalResult:
    """A single retrieval result returned by an adapter.

    Fields are intentionally generic so that adapters for vector databases,
    framework wrappers, or custom retrieval systems can all map into the same
    shape.
    """
    text: str
    score: float = 0.0
    identifier: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class RetrievalAdapter(ABC):
    """Abstract base class for retrieval-system adapters."""

    @abstractmethod
    def query(self, corpus: Any, query_text: str) -> List[RetrievalResult]:
        """Execute a retrieval query against ``corpus``.

        Args:
            corpus: A corpus object. The exact type is adapter-defined; the
                harness builds corpora through the ``corpora`` package and
                passes them unchanged to the active adapter.
            query_text: The natural-language or structured query string.

        Returns:
            A list of :class:`RetrievalResult` objects ordered by relevance
            (best first). An adapter may return an empty list to indicate
            abstention or a retrieval failure.
        """
        ...
