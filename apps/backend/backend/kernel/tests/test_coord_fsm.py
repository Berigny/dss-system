"""Tests for backend/kernel/coord_fsm.py."""

from __future__ import annotations

import pytest

from backend.kernel.coord_fsm import CoordFSM


@pytest.fixture
def fsm():
    return CoordFSM(
        topology={
            "ethics/lawfulness/refusal/clean_refusal/v6",
            "ethics/lawfulness/refusal/firm_boundary/v6",
            "ethics/lawfulness/acceptance/graceful/v6",
        },
        allowed_dimensions={"ethics", "awareness", "unity", "telos"},
    )


def test_topology_member(fsm: CoordFSM) -> None:
    assert fsm.is_topology_member("ethics/lawfulness/refusal/clean_refusal/v6") is True


def test_outside_topology(fsm: CoordFSM) -> None:
    assert fsm.is_topology_member("unknown/foo/bar/baz/v1") is False


def test_wellformed_not_in_topology_passes(fsm: CoordFSM) -> None:
    # Well-formed COORDs are accepted even if not pre-registered.
    assert fsm.is_topology_member("awareness/perception/signal/sharp/v3") is True


def test_malformed_coord_rejected(fsm: CoordFSM) -> None:
    assert fsm.is_topology_member("ethics/too/short") is False
    assert fsm.is_topology_member("forbidden/foo/bar/baz/v1") is False


def test_parent_child_derivation(fsm: CoordFSM) -> None:
    assert fsm.is_derivation_valid(
        "ethics/lawfulness/refusal",
        "ethics/lawfulness/refusal/clean_refusal",
    ) is True


def test_sibling_v_level_derivation(fsm: CoordFSM) -> None:
    assert fsm.is_derivation_valid(
        "ethics/lawfulness/refusal/clean_refusal/v6",
        "ethics/lawfulness/refusal/clean_refusal/v7",
    ) is True


def test_invalid_derivation_rejected(fsm: CoordFSM) -> None:
    assert fsm.is_derivation_valid(
        "ethics/lawfulness/refusal/clean_refusal/v6",
        "awareness/perception/signal/sharp/v3",
    ) is False


def test_derivation_path(fsm: CoordFSM) -> None:
    path = fsm.derivation_path("ethics/lawfulness/refusal/clean_refusal")
    assert path == [
        "ethics",
        "ethics/lawfulness",
        "ethics/lawfulness/refusal",
        "ethics/lawfulness/refusal/clean_refusal",
    ]


def test_novel_coord_requires_flag_and_proof(fsm: CoordFSM) -> None:
    proof = fsm.novelty_proof("ethics/justice/restitution/voluntary/v1")
    assert proof["novelty_flag"] is True
    assert proof["topology_membership_proof"]
    assert "ethics/justice/restitution" in proof["derivation_path"]


def test_novel_proof_fails_for_existing_coord(fsm: CoordFSM) -> None:
    with pytest.raises(ValueError):
        fsm.novelty_proof("ethics/lawfulness/refusal/clean_refusal/v6")


def test_supercession_requires_valid_derivation(fsm: CoordFSM) -> None:
    assert fsm.supercession_valid(
        "ethics/lawfulness/refusal/clean_refusal/v6",
        "ethics/lawfulness/refusal/firm_boundary/v6",
    ) is True


def test_supercession_rejects_unrelated_coord(fsm: CoordFSM) -> None:
    assert fsm.supercession_valid(
        "ethics/lawfulness/refusal/clean_refusal/v6",
        "awareness/perception/signal/sharp/v3",
    ) is False


def test_register_adds_to_topology(fsm: CoordFSM) -> None:
    new_coord = "ethics/justice/restitution/voluntary/v1"
    assert fsm.is_topology_member(new_coord) is True  # well-formed
    fsm.register(new_coord)
    assert new_coord in fsm.topology
