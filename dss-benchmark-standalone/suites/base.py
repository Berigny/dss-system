"""Base suite interface."""
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseSuite(ABC):
    """Abstract base class for benchmark suites."""

    @abstractmethod
    def run(self) -> Dict[str, Any]:
        """Run the suite and return a metric dictionary."""
        ...
