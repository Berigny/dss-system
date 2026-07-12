"""Tests for the orchestrator's pure-Qp ranking fallback."""

from __future__ import annotations

import pytest

from backend.fieldx_kernel.orchestrator import _apply_qp_pure_ranking
from backend.fieldx_kernel.qp_coordinate import derive_p_adic_coordinate


def _coord_dict(kernel_exponents: dict[int, int]) -> dict[str, object]:
    coord = derive_p_adic_coordinate({"kernel_prime_exponents": kernel_exponents})
    assert coord is not None
    return coord.as_dict()


def test_apply_qp_pure_ranking_keeps_pure_candidates(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", True)

    query_factors = [{"prime": 5, "delta": 1}]
    candidates = [
        {"text": "exact", "p_adic_coordinate": _coord_dict({5: 1}), "relevance_score": 0.5},
        {"text": "other", "p_adic_coordinate": _coord_dict({3: 1}), "relevance_score": 0.4},
    ]

    _apply_qp_pure_ranking(candidates, query_factors)

    assert len(candidates) == 1
    assert candidates[0]["text"] == "exact"
    assert candidates[0].get("qp_pure") is True
    assert candidates[0].get("p_adic_score") == pytest.approx(1.0)


def test_apply_qp_pure_ranking_falls_back_when_no_query_coordinate(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", True)

    # 97 is not a kernel prime, so no query coordinate can be derived.
    query_factors = [{"prime": 97, "delta": 1}]
    candidates = [
        {"text": "keep me", "relevance_score": 0.5},
    ]

    _apply_qp_pure_ranking(candidates, query_factors)

    assert len(candidates) == 1
    assert candidates[0]["text"] == "keep me"
    assert candidates[0].get("qp_pure_fallback") is True


def test_apply_qp_pure_ranking_falls_back_when_no_compatible_candidates(monkeypatch) -> None:
    monkeypatch.setattr("backend.config.settings.QP_PURE_ENABLED", True)

    query_factors = [{"prime": 5, "delta": 1}]
    candidates = [
        {"text": "mismatch", "p_adic_coordinate": _coord_dict({3: 1}), "relevance_score": 0.5},
    ]

    _apply_qp_pure_ranking(candidates, query_factors)

    assert len(candidates) == 1
    assert candidates[0]["text"] == "mismatch"
    assert candidates[0].get("qp_pure_fallback") is True
