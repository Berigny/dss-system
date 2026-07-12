"""Routers exposing coherence and ethics evaluation services."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.http import get_ledger_store
from backend.api.schemas import (
    ActionRequestSchema,
    CoherenceResponseSchema,
    PolicyDecisionSchema,
)
from backend.api.logging_utils import log_operation
from backend.coherence_layer import PolicyEngine
from backend.ethics_layer import Constraint, RelaxationModel
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.fieldx_kernel.geometry import Lattice

router = APIRouter(tags=["governance"])
LOGGER = logging.getLogger(__name__)


class CoherenceAnalyzer:
    """Lightweight analyzer leveraging lattice geometry."""

    def __init__(self, dimensions: int = 3) -> None:
        self.dimensions = max(1, dimensions)

    def evaluate(self, request: ActionRequestSchema) -> CoherenceResponseSchema:
        params = request.parameters or {}
        steps_raw = [params[key] for key in sorted(params.keys())]
        dims = max(len(steps_raw), self.dimensions)
        lattice = Lattice(dims)
        origin = lattice.origin()

        padded = steps_raw + [0.0] * (dims - len(steps_raw))
        steps = [int(round(value)) for value in padded[:dims]]
        point = lattice.trace_path(origin, steps)
        magnitude = sum(abs(step) for step in steps)
        coherence = 1.0 / (1.0 + magnitude)

        return CoherenceResponseSchema(
            action=request.action,
            coherence_score=coherence,
            lattice_point=list(point.coordinates),
            steps=steps,
        )


coherence_analyzer = CoherenceAnalyzer()


def _map_legacy_action(entity: str, deltas: dict[str, float]) -> ActionRequestSchema:
    """Internal helper to convert legacy ``(entity, deltas)`` payloads.

    The legacy shape provided an ``entity`` string and a mapping of ``deltas``
    representing parameter steps. This helper produces a canonical
    ``ActionRequestSchema`` with the entity used as the ``action`` field,
    a placeholder ``actor`` value, and the deltas forwarded as ``parameters``.
    It is intentionally not exported or wired to the API yet but allows
    controlled migration to the canonical contract.
    """

    return ActionRequestSchema(
        actor="legacy-client",
        action=entity,
        parameters=deltas,
    )


def get_policy_engine(store: LedgerStoreV2 = Depends(get_ledger_store)) -> PolicyEngine:
    return PolicyEngine(
        ledger_store=store, laws=[Constraint(name="alignment", weight=0.25)], relaxation=RelaxationModel()
    )


@router.post("/coherence/evaluate", response_model=CoherenceResponseSchema)
def evaluate_coherence(
    request: ActionRequestSchema, http_request: Request
) -> CoherenceResponseSchema:
    """Return a coherence score derived from lattice traversal.

    The request must include an ``actor`` and ``action`` identifier. The
    optional ``parameters`` mapping supplies numeric steps for the lattice
    traversal, and ``key`` may be provided for downstream ledger correlation.
    """

    with log_operation(
        LOGGER,
        "coherence_evaluate",
        request=http_request,
        actor=request.actor,
        action=request.action,
    ) as ctx:
        response = coherence_analyzer.evaluate(request)
        ctx.update(
            {
                "coherence_score": response.coherence_score,
                "parameter_count": len(request.parameters or {}),
            }
        )
        return response


@router.post("/ethics/evaluate", response_model=PolicyDecisionSchema)
def evaluate_ethics(
    request: ActionRequestSchema,
    http_request: Request,
    store: LedgerStoreV2 = Depends(get_ledger_store),
    engine: PolicyEngine = Depends(get_policy_engine),
) -> PolicyDecisionSchema:
    """Evaluate an action against registered policies and relaxation model.

    The request must include ``actor`` and ``action`` identifiers. Provide the
    optional ``key`` field to point to a ledger entry used during evaluation;
    ``parameters`` may also be supplied for richer policy contexts.
    """

    with log_operation(
        LOGGER,
        "ethics_evaluate",
        request=http_request,
        actor=request.actor,
        action=request.action,
    ) as ctx:
        if request.key is None:
            raise HTTPException(status_code=400, detail="Ledger key is required")

        ledger_id = request.key.to_model().as_path()
        entry = store.read(ledger_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")

        scores = engine.evaluate(request.key.to_model())
        permitted = scores.get("grace", 0.0) >= 0
        response = PolicyDecisionSchema(
            action=request.action,
            key=request.key,
            lawfulness=scores.get("lawfulness", 0.0),
            grace=scores.get("grace", 0.0),
            permitted=permitted,
        )

        ctx.update(
            {
                "ledger_id": ledger_id,
                "permitted": response.permitted,
                "law_count": len(engine.laws),
            }
        )

        return response


__all__ = ["coherence_analyzer", "get_policy_engine", "router"]
