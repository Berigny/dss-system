"""Kernel constant immutability tests (HENGE-011)."""

from __future__ import annotations

import pytest

from backend.kernel import constants


def test_modify_prime_map_raises_immutable_kernel():
    with pytest.raises(constants.ImmutableKernelError, match="IMMUTABLE_KERNEL"):
        constants.QUATERNARY_GATE_TO_PRIME["awareness"] = 11


def test_modify_quaternary_thresholds_raises_immutable_kernel():
    with pytest.raises(constants.ImmutableKernelError, match="IMMUTABLE_KERNEL"):
        constants.QUATERNARY_GATES["awareness"]["levels"]["level_1"]["v_max"] = 999


def test_quaternary_threshold_update_is_blocked():
    with pytest.raises(constants.ImmutableKernelError, match="IMMUTABLE_KERNEL"):
        constants.QUATERNARY_GATES["unity"]["levels"].update({"level_0": {}})


def test_checksum_336_constant_unchanged():
    assert constants.CHECKSUM_336 == 336


def test_frozen_mapping_still_supports_read_access():
    assert constants.QUATERNARY_GATE_TO_PRIME["awareness"] == 5
    assert "ethics" in constants.QUATERNARY_GATE_TO_PRIME
    assert dict(constants.QUATERNARY_GATES["awareness"]["levels"])["level_3"]["layer"] == "CLAY"
