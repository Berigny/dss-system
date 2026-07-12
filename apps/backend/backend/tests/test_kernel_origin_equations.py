import math

import pytest

from backend.fieldx_kernel.kernel_origin_equations import (
    calculate_alpha_from_primes,
    equation_1_substrate_kernel_origin,
    equation_2_temporalization,
    equation_6_operational,
)
from backend.fieldx_kernel.p_adic import PAdicInteger
from backend.fieldx_kernel.state import GRACE_PRIME, LAW_PRIME


def test_alpha_paper_default() -> None:
    assert calculate_alpha_from_primes() == pytest.approx(0.007297397451611984, rel=1e-10)


def test_alpha_custom_sector() -> None:
    assert calculate_alpha_from_primes(
        n_qubits=9,
        distance=3,
        use_paper_defaults=False,
    ) == pytest.approx(1 / 137.017578125, rel=1e-10)


def test_alpha_paper_overrides_validation() -> None:
    calculate_alpha_from_primes(n_qubits=2, distance=0, use_paper_defaults=True)


def test_alpha_invalid_when_not_default() -> None:
    with pytest.raises(ValueError, match="must be integers"):
        calculate_alpha_from_primes(n_qubits=3.5)


def test_equation_1_substrate_kernel_origin() -> None:
    assert equation_1_substrate_kernel_origin() == "R_0 = R x Prod(Q_p)"


# --- EQ 2 p-adic shift map --------------------------------------------------


def test_equation_2_zero_is_fixed_point() -> None:
    assert equation_2_temporalization(0, p=5, N=4) == 0


def test_equation_2_int_state_returns_int() -> None:
    # 7 mod 5^4 has valuation 0, so shift by 5^0 = 1.
    result = equation_2_temporalization(7, p=5, N=4, hysteresis=0.0)
    assert isinstance(result, int)
    assert result == 8


def test_equation_2_padic_state_returns_padic() -> None:
    state = PAdicInteger.from_int(p=5, n=7, N=4)
    result = equation_2_temporalization(state, hysteresis=0.0)
    assert isinstance(result, PAdicInteger)
    assert result._value() == 8


def test_equation_2_respects_valuation() -> None:
    # 25 under p=5 has valuation 2, so shift by 5^2 = 25.
    result = equation_2_temporalization(25, p=5, N=4, hysteresis=0.0)
    assert result == 50


def test_equation_2_hysteresis_nudges_shift_depth() -> None:
    # 7 (v=0) with hysteresis large enough to nudge shift_exponent to 1.
    result = equation_2_temporalization(7, p=5, N=4, hysteresis=0.5)
    # nudge = round(0.5 * 4) = 2, capped at N-1=3, shift_exponent = 2.
    assert result == 7 + 5**2


def test_equation_2_preserves_modular_ring() -> None:
    # A state near the top of the ring should wrap around correctly.
    state = 5**4 - 5  # 620 under p=5, N=4; v_p(620)=1 (divisible by 5).
    result = equation_2_temporalization(state, p=5, N=4, hysteresis=0.0)
    expected = (state + 5) % (5**4)
    assert result == expected


# --- EQ 6 operational -------------------------------------------------------


def test_equation_6_operational_keys_and_determinism() -> None:
    payload = {"summary": "alpha beta", "skim": {"one_line": "alpha"}}

    first = equation_6_operational(
        query_text="alpha beta",
        retrieval_payload=payload,
        closure_threshold=0.2,
    )
    second = equation_6_operational(
        query_text="alpha beta",
        retrieval_payload=payload,
        closure_threshold=0.2,
    )

    assert set(first.keys()) == {"lawfulness_level", "mediator_prime", "commit_allowed"}
    assert first == second


def test_equation_6_operational_mediator_selection() -> None:
    blocked = equation_6_operational(
        query_text="alpha",
        retrieval_payload=None,
    )
    allowed = equation_6_operational(
        query_text="alpha beta",
        retrieval_payload={"summary": "alpha beta"},
        closure_threshold=0.1,
    )

    assert blocked["mediator_prime"] == LAW_PRIME
    assert allowed["mediator_prime"] == GRACE_PRIME


def test_equation_6_operational_commit_gating_rules() -> None:
    missing_retrieval = equation_6_operational(
        query_text="alpha",
        retrieval_payload=None,
        lawfulness_level=3,
        hysteresis_coherence=0.9,
        closure_threshold=0.1,
    )
    low_lawfulness = equation_6_operational(
        query_text="alpha",
        retrieval_payload={"summary": "alpha"},
        lawfulness_level=1,
        hysteresis_coherence=0.9,
        closure_threshold=0.1,
    )
    low_closure_score = equation_6_operational(
        query_text="alpha",
        retrieval_payload={"summary": "beta"},
        lawfulness_level=2,
        hysteresis_coherence=0.9,
        closure_threshold=0.9,
    )
    low_hysteresis = equation_6_operational(
        query_text="alpha",
        retrieval_payload={"summary": "alpha"},
        lawfulness_level=2,
        hysteresis_coherence=0.5,
        closure_threshold=0.1,
    )
    allowed = equation_6_operational(
        query_text="alpha",
        retrieval_payload={"summary": "alpha"},
        lawfulness_level=2,
        hysteresis_coherence=0.9,
        closure_threshold=0.1,
    )

    assert missing_retrieval["commit_allowed"] is False
    assert low_lawfulness["commit_allowed"] is False
    assert low_closure_score["commit_allowed"] is False
    assert low_hysteresis["commit_allowed"] is False
    assert allowed["commit_allowed"] is True
