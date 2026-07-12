from __future__ import annotations

import pytest

from backend.fieldx_kernel.kernel_origin_equations import equation_2_temporalization
from backend.fieldx_kernel.p_adic import PAdicInteger
from backend.fieldx_kernel.temporal.hysteresis_engine import CoherentHysteresisEngine


# --- PAdicInteger cycle helpers ---------------------------------------------


def test_digit_rotation_preserves_ring_parameters() -> None:
    padic = PAdicInteger.from_int(p=5, n=42, N=4)
    rotated = padic.digit_rotation(2)
    assert rotated.p == padic.p
    assert rotated.N == padic.N


def test_digit_rotation_by_full_precision_is_identity() -> None:
    padic = PAdicInteger.from_int(p=5, n=123, N=4)
    assert padic.digit_rotation(4) == padic
    assert padic.digit_rotation(0) == padic


def test_digit_rotation_composes_as_expected() -> None:
    padic = PAdicInteger.from_int(p=5, n=7, N=3)  # digits (2,1,0) LSF
    once = padic.digit_rotation(1)
    twice = padic.digit_rotation(2)
    # Rotating by 1 then 1 should equal rotating by 2.
    assert once.digit_rotation(1) == twice


def test_orientation_reversal_is_additive_inverse() -> None:
    padic = PAdicInteger.from_int(p=5, n=12, N=4)
    rev = padic.orientation_reversal()
    assert (padic + rev)._value() == 0
    assert rev.orientation_reversal() == padic


def test_block_rotation_preserves_precision() -> None:
    padic = PAdicInteger.from_int(p=3, n=100, N=5)
    rotated = padic.block_rotation(block_size=2, steps=1)
    assert rotated.p == padic.p
    assert rotated.N == padic.N


def test_block_rotation_by_number_of_blocks_is_identity() -> None:
    padic = PAdicInteger.from_int(p=3, n=100, N=5)
    # N=5, block_size=2 -> blocks [2, 2, 1] => 3 blocks.
    assert padic.block_rotation(block_size=2, steps=3) == padic


def test_block_rotation_zero_steps_is_identity() -> None:
    padic = PAdicInteger.from_int(p=3, n=100, N=5)
    assert padic.block_rotation(block_size=2, steps=0) == padic


def test_block_rotation_invalid_block_size_raises() -> None:
    padic = PAdicInteger.from_int(p=3, n=1, N=3)
    with pytest.raises(ValueError, match="block_size must be positive"):
        padic.block_rotation(block_size=0, steps=1)


# --- Integration with equation_2_temporalization ------------------------------


def test_equation_2_without_cycle_step_is_default_shift() -> None:
    base = equation_2_temporalization(7, p=5, N=4, hysteresis=0.0)
    with_rotation = equation_2_temporalization(
        7, p=5, N=4, hysteresis=0.0, cycle_step="digit_rotation", cycle_steps=0
    )
    assert base == with_rotation


def test_equation_2_digit_rotation_changes_result() -> None:
    base = equation_2_temporalization(7, p=5, N=4, hysteresis=0.0)
    rotated = equation_2_temporalization(
        7, p=5, N=4, hysteresis=0.0, cycle_step="digit_rotation", cycle_steps=1
    )
    assert rotated != base


def test_equation_2_orientation_reversal_changes_result() -> None:
    base = equation_2_temporalization(7, p=5, N=4, hysteresis=0.0)
    reversed_result = equation_2_temporalization(
        7, p=5, N=4, hysteresis=0.0, cycle_step="orientation_reversal"
    )
    assert reversed_result != base
    assert 0 <= reversed_result < 5**4


def test_equation_2_block_rotation_preserves_ring() -> None:
    result = equation_2_temporalization(
        7, p=5, N=4, hysteresis=0.0, cycle_step="block_rotation", cycle_steps=1, cycle_block_size=2
    )
    assert 0 <= result < 5**4


def test_equation_2_unsupported_cycle_step_raises() -> None:
    with pytest.raises(ValueError, match="unsupported cycle_step"):
        equation_2_temporalization(7, p=5, N=4, cycle_step="invalid_step")


# --- Integration with CoherentHysteresisEngine -------------------------------


def test_hysteresis_engine_cycle_step_is_optional() -> None:
    engine = CoherentHysteresisEngine()
    base = engine.equation_2_temporalization(7, p=5, N=4)
    with_rotation = engine.equation_2_temporalization(
        7, p=5, N=4, cycle_step="digit_rotation", cycle_steps=0
    )
    assert base == with_rotation


def test_hysteresis_engine_digit_rotation_changes_result() -> None:
    engine = CoherentHysteresisEngine()
    base = engine.equation_2_temporalization(7, p=5, N=4)
    rotated = engine.equation_2_temporalization(
        7, p=5, N=4, cycle_step="digit_rotation", cycle_steps=1
    )
    assert rotated != base


def test_hysteresis_engine_orientation_reversal_changes_result() -> None:
    engine = CoherentHysteresisEngine()
    base = engine.equation_2_temporalization(7, p=5, N=4)
    reversed_result = engine.equation_2_temporalization(
        7, p=5, N=4, cycle_step="orientation_reversal"
    )
    assert reversed_result != base
    assert 0 <= reversed_result < 5**4
