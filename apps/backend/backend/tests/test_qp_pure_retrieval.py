"""Tests for pure Qp retrieval ranking (DS-REVIEW-193 P2-04)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest
from fastapi import FastAPI

from backend.config import settings as _settings
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.qp_coordinate import QpCoordinate, derive_p_adic_coordinate
import backend.fieldx_kernel.substrate.ledger_store_v2 as ledger_module
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.retrieval.fuzzy_retrieve import fuzzy_retrieve
from backend.search.service import search
from backend.search.token_index import TokenPrimeIndex


def _make_coord(kernel_exponents: Mapping[int, int]) -> QpCoordinate:
    """Build a deterministic coordinate from kernel prime exponents."""
    coord = derive_p_adic_coordinate({"kernel_prime_exponents": dict(kernel_exponents)})
    assert coord is not None
    return coord


class _FakeMemoryService:
    def __init__(
        self,
        memories: Sequence[Mapping[str, Any]],
        anchor_payload: Mapping[str, Any] | None = None,
    ) -> None:
        self._memories = memories
        self._anchor_payload = anchor_payload or {}

    def get_all_memories(self, entity: str | None = None) -> Sequence[Mapping[str, Any]]:
        return list(self._memories)

    def anchor(self, text: str, entity: str | None = None) -> Mapping[str, Any]:
        return dict(self._anchor_payload)


def test_fuzzy_retrieve_qp_pure_ranks_by_ultrametric_distance(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", True)

    # Query anchored at 5^1 (Eq2, metric prime 5).
    anchor = {"factors": [{"prime": 5, "delta": 1}]}

    exact_coord = _make_coord({5: 1}).as_dict()
    close_coord = _make_coord({5: 2, 2: 1}).as_dict()
    far_coord = _make_coord({3: 1}).as_dict()  # metric prime 3 -> filtered

    memories = [
        {"text": "close", "p_adic_coordinate": close_coord},
        {"text": "exact", "p_adic_coordinate": exact_coord},
        {"text": "far", "p_adic_coordinate": far_coord},
    ]
    service = _FakeMemoryService(memories, anchor_payload=anchor)

    results = fuzzy_retrieve("query", memory_service=service, top_k=5)

    assert len(results) == 2
    assert results[0]["text"] == "exact"
    assert results[1]["text"] == "close"
    assert results[0]["qp_distance"] == pytest.approx(0.0)
    assert results[0]["qp_score"] == pytest.approx(1.0)
    assert results[1]["qp_distance"] > results[0]["qp_distance"]
    assert all(r.get("qp_pure") for r in results)


def test_fuzzy_retrieve_qp_pure_applies_circulation_depth_filter(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", True)

    anchor = {"factors": [{"prime": 5, "delta": 1}]}
    query_coord = _make_coord({5: 1})
    candidate_coord = _make_coord({5: 1})

    # Recreate the candidate with a far circulation pass while keeping the same
    # rational representative so the distance would otherwise be zero.
    filtered = QpCoordinate(
        coordinate_id=candidate_coord.coordinate_id,
        kernel_node=candidate_coord.kernel_node,
        metric_prime=candidate_coord.metric_prime,
        tetrahedron=candidate_coord.tetrahedron,
        dual_complement=candidate_coord.dual_complement,
        unit_digits=candidate_coord.unit_digits,
        valuation_offset=candidate_coord.valuation_offset,
        working_precision=candidate_coord.working_precision,
        rational_representative=candidate_coord.rational_representative,
        circulation_pass=10,
        hysteresis_depth=10.0,
        pass_entry_node=candidate_coord.pass_entry_node,
        dual_state=candidate_coord.dual_state,
        mediator_state=candidate_coord.mediator_state,
    )

    memories = [
        {"text": "filtered", "p_adic_coordinate": filtered.as_dict()},
        {"text": "kept", "p_adic_coordinate": candidate_coord.as_dict()},
    ]
    service = _FakeMemoryService(memories, anchor_payload=anchor)

    results = fuzzy_retrieve("query", memory_service=service, top_k=5)

    assert len(results) == 1
    assert results[0]["text"] == "kept"


def test_fuzzy_retrieve_qp_pure_falls_back_when_no_query_coordinate(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", True)

    # Anchor provides no kernel primes, so no query coordinate can be derived.
    anchor = {"factors": [{"prime": 97, "delta": 1}]}
    memories = [{"text": "fallback memory"}]
    service = _FakeMemoryService(memories, anchor_payload=anchor)

    results = fuzzy_retrieve("query", memory_service=service, top_k=5)

    assert len(results) == 1
    assert results[0]["text"] == "fallback memory"
    assert results[0].get("qp_pure_fallback") is True
    assert "qp_pure" not in results[0]


def test_fuzzy_retrieve_qp_pure_falls_back_when_no_compatible_candidates(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", True)

    # Query anchored at kernel prime 5, but every candidate lives at kernel prime 3.
    anchor = {"factors": [{"prime": 5, "delta": 1}]}
    memories = [
        {"text": "mismatch a", "p_adic_coordinate": _make_coord({3: 1}).as_dict()},
        {"text": "mismatch b", "p_adic_coordinate": _make_coord({3: 2}).as_dict()},
    ]
    service = _FakeMemoryService(memories, anchor_payload=anchor)

    results = fuzzy_retrieve("query", memory_service=service, top_k=5)

    assert len(results) == 2
    assert all(r.get("qp_pure_fallback") is True for r in results)
    assert all("qp_pure" not in r for r in results)


def test_search_qp_pure_ranks_by_ultrametric_distance(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", True)
    monkeypatch.setattr(ledger_module, "MIN_BODY_PRIME", 2)
    monkeypatch.setattr(ledger_module, "FLOW_PRIMES", frozenset())

    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)

    # Map each entry's text to a deterministic kernel-prime set.  The query
    # token "anything" maps to kernel prime 5 so the query coordinate shares
    # the metric prime with the first two candidates.
    def _primes_for_tokens(tokens: list[str]) -> list[int]:
        if "exact" in tokens:
            return [5]
        if "close" in tokens:
            return [5, 11]
        if "other" in tokens or "metric" in tokens:
            return [3]
        if "anything" in tokens:
            # Match the default kernel activation of a written turn so that the
            # query coordinate shares the same rational representative.
            return [5, 13, 17]
        return [5]

    monkeypatch.setattr(token_index, "primes_for_tokens", _primes_for_tokens)

    store = LedgerStoreV2(app.state.db, token_index=token_index)

    def _seed(ident: str) -> None:
        entry = LedgerEntry(
            LedgerKey("search-demo", ident),
            ContinuousState(metadata={"content": ident}),
        )
        store.write(entry)

    _seed("exact")
    _seed("close")
    _seed("other-metric")

    results = search("anything", store=store, token_index=token_index, limit=10)

    assert len(results) == 2
    assert results[0]["entry_id"].endswith("exact")
    assert results[1]["entry_id"].endswith("close")
    assert results[0]["qp_distance"] == pytest.approx(0.0)
    assert all(r.get("qp_pure") for r in results)


def test_search_falls_back_to_legacy_when_qp_pure_disabled(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", False)
    monkeypatch.setattr(ledger_module, "MIN_BODY_PRIME", 2)
    monkeypatch.setattr(ledger_module, "FLOW_PRIMES", frozenset())

    app = FastAPI()
    app.state.db = {}
    token_index = TokenPrimeIndex(app)
    monkeypatch.setattr(
        token_index, "primes_for_tokens", lambda tokens: [5] if "legacy" in tokens or "anything" in tokens else [5]
    )

    store = LedgerStoreV2(app.state.db, token_index=token_index)
    entry = LedgerEntry(
        LedgerKey("search-demo", "legacy"),
        ContinuousState(metadata={"content": "legacy result"}),
    )
    store.write(entry)

    results = search("anything", store=store, token_index=token_index, limit=10)

    assert len(results) == 1
    assert "qp_pure" not in results[0]


def test_fuzzy_retrieve_legacy_still_functions_when_qp_pure_disabled(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", False)

    service = _FakeMemoryService([{"text": "legacy memory"}])
    results = fuzzy_retrieve("query", memory_service=service, top_k=5)
    assert len(results) == 1
    assert results[0]["text"] == "legacy memory"
