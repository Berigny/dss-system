"""Tests for backend.kernel.coherence_diagnostics."""

from __future__ import annotations

import pytest

from backend.kernel import constants
from backend.kernel.coherence_diagnostics import (
    DreamingCheckResult,
    RainbowSerpentCirculation,
    dreaming_check,
)


def test_dreaming_check_all_synced_and_balanced() -> None:
    # All dual pairs synchronised at or above min_valuation; centroid primes active.
    valuations = {
        "Eq0": 8, "Eq1": 8, "Eq2": 7, "Eq3": 7,
        "Eq4": 8, "Eq5": 8, "Eq6": 7, "Eq7": 7,
        "Eq8": 1, "Eq9": 1,
    }
    result = dreaming_check(valuations)
    assert isinstance(result, DreamingCheckResult)
    assert result.coherent is True
    assert result.dual_pairs_synced is True
    assert result.centroid_balanced is True
    assert result.zero_strain is True


def test_dreaming_check_fails_when_dual_pair_out_of_sync() -> None:
    valuations = {
        "Eq0": 8, "Eq1": 8, "Eq2": 7, "Eq3": 7,
        "Eq4": 2, "Eq5": 8, "Eq6": 7, "Eq7": 7,
        "Eq8": 1, "Eq9": 1,
    }
    result = dreaming_check(valuations)
    assert result.coherent is False
    assert result.dual_pairs_synced is False
    assert result.centroid_balanced is True


def test_dreaming_check_fails_when_centroid_inactive() -> None:
    valuations = {
        "Eq0": 8, "Eq1": 8, "Eq2": 7, "Eq3": 7,
        "Eq4": 8, "Eq5": 8, "Eq6": 7, "Eq7": 7,
        "Eq8": 0, "Eq9": 0,
    }
    result = dreaming_check(valuations)
    assert result.coherent is False
    assert result.dual_pairs_synced is True
    assert result.centroid_balanced is False


def test_dreaming_check_fails_with_strain() -> None:
    valuations = {
        "Eq0": 8, "Eq1": 8, "Eq2": 7, "Eq3": 7,
        "Eq4": 8, "Eq5": 8, "Eq6": 7, "Eq7": 7,
        "Eq8": 1, "Eq9": 1,
    }
    result = dreaming_check(valuations, strain=0.5)
    assert result.coherent is False
    assert result.zero_strain is False


def test_dreaming_check_accepts_kernel_ids() -> None:
    # K0..K7 are mapped to Eq0..Eq7; centroid is Eq8/Eq9.
    valuations = {
        "K0": 8, "K1": 8, "K2": 7, "K3": 7,
        "K4": 8, "K5": 8, "K6": 7, "K7": 7,
        "Eq8": 1, "Eq9": 1,
    }
    result = dreaming_check(valuations)
    assert result.coherent is True


def test_rainbow_serpent_starts_at_origin() -> None:
    serpent = RainbowSerpentCirculation()
    assert serpent.current_position == 0
    assert serpent.current_coordinate == constants.LATTICE_TRAVERSAL_SEQUENCE[0]
    assert serpent.is_at_waterhole() is False


def test_rainbow_serpent_reaches_centroid_waterhole() -> None:
    serpent = RainbowSerpentCirculation()
    for _ in range(13):
        serpent.slither()
    assert serpent.current_position == 13
    assert serpent.current_coordinate == constants.LATTICE_TRAVERSAL_SEQUENCE[13]
    assert serpent.is_at_waterhole() is True


def test_rainbow_serpent_wraps_from_day_26_to_day_0() -> None:
    serpent = RainbowSerpentCirculation(position=26)
    assert serpent.current_position == 26
    serpent.slither()
    assert serpent.current_position == 27
    assert serpent.is_at_waterhole() is True
    serpent.slither()
    assert serpent.current_position == 0
    assert serpent.is_at_waterhole() is False


def test_rainbow_serpent_wraps_backward() -> None:
    serpent = RainbowSerpentCirculation(position=0)
    serpent.slither(-1)
    assert serpent.current_position == 27
    assert serpent.is_at_waterhole() is True
