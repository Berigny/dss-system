"""Placeholder policy engine coordinating ledger and ethics layers."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from backend.ethics_layer.constraint import Constraint
from backend.ethics_layer.relaxation import RelaxationModel
from backend.fieldx_kernel import LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.fieldx_kernel.schema import (
    FLOW_PRIMES,
    MIN_BODY_PRIME,
)



class PolicyEngine:
    """Coordinates policy checks before storing or acting on entries."""

    def enforce_prime_policy(self, entry: LedgerEntry) -> None:
        """
        Enforce FIELD-X prime policy:

        - Flow primes (S/A/C) are protected and MAY NOT be used
          for token primes or body primes.
        - All token/body primes must be >= MIN_BODY_PRIME (23).
        
        This prevents conflicts between:
        - flow semantics,
        - memory primes,
        - token primes generated for search/indexing.
        """

        metadata = entry.state.metadata or {}
        token_primes = metadata.get("token_primes", [])

        for prime in token_primes:
            if prime in FLOW_PRIMES:
                raise ValueError(
                    f"Prime {prime} is a protected flow prime and cannot be used "
                    f"as a token prime. Reserved primes: {FLOW_PRIMES}"
                )

            if prime < MIN_BODY_PRIME:
                raise ValueError(
                    f"Prime {prime} is below allowed token/body threshold "
                    f"(minimum is {MIN_BODY_PRIME})."
                )
  
    def __init__(
        self,
        ledger_store: LedgerStoreV2,
        laws: Optional[List[Constraint]] = None,
        relaxation: Optional[RelaxationModel] = None,
    ) -> None:
        self.ledger_store = ledger_store
        self.laws = laws or []
        self.relaxation = relaxation or RelaxationModel()
        self._callbacks: List[Callable[[LedgerEntry], None]] = []

    def register_callback(self, callback: Callable[[LedgerEntry], None]) -> None:
        """Register a callback to be invoked after successful evaluation."""

        self._callbacks.append(callback)

    def evaluate(self, key: LedgerKey) -> Dict[str, float]:
        """Score the provided key against known constraints with relaxation applied."""

        entry = self.ledger_store.read(key.as_path())
        if entry is None:
            return {"lawfulness": 0.0, "grace": 0.0}

        lawfulness = sum(constraint.evaluate(entry.state) for constraint in self.laws)
        relaxation_score = self.relaxation.mediate(lawfulness)
        return {"lawfulness": lawfulness, "grace": relaxation_score}

    def record(self, entry: LedgerEntry) -> None:
        """Store the entry after running policy callbacks."""
        
        # 1. Enforce prime policy before writing
        self.enforce_prime_policy(entry)

        # 2. Write entry
        self.ledger_store.write(entry)

        # 3. Trigger callbacks
        for callback in self._callbacks:
            callback(entry)
