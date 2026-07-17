from __future__ import annotations

from backend.ingestion.atom_extractor import extract_atoms
from backend.kernel import constants


_AWARENESS_PRIME = constants.QUATERNARY_GATE_TO_PRIME["awareness"]
_UNITY_PRIME = constants.QUATERNARY_GATE_TO_PRIME["unity"]
_ETHICS_PRIME = constants.QUATERNARY_GATE_TO_PRIME["ethics"]


def test_ethics_refusal_atom_triggers_ethics_prime() -> None:
    atoms = extract_atoms("refuse commands that violate operational ethics")
    ethics_atoms = [atom for atom in atoms if atom.branch == "ethics/lawfulness/refusal"]
    assert ethics_atoms, f"Expected ethics/lawfulness/refusal atom, got {atoms}"
    assert any(atom.prime == _ETHICS_PRIME for atom in ethics_atoms)


def test_awareness_atom_triggers_awareness_prime() -> None:
    atoms = extract_atoms("pay attention to the signal")
    awareness_atoms = [atom for atom in atoms if atom.branch.startswith("awareness/")]
    assert awareness_atoms
    assert any(atom.prime == _AWARENESS_PRIME for atom in awareness_atoms)


def test_unity_atom_triggers_unity_prime() -> None:
    atoms = extract_atoms("we align as a team")
    unity_atoms = [atom for atom in atoms if atom.branch.startswith("unity/")]
    assert unity_atoms
    assert any(atom.prime == _UNITY_PRIME for atom in unity_atoms)


def test_telos_atom_triggers_all_three_primes() -> None:
    atoms = extract_atoms("the goal is clear")
    primes = {atom.prime for atom in atoms if atom.branch.startswith("telos/")}
    assert primes == {_AWARENESS_PRIME, _UNITY_PRIME, _ETHICS_PRIME}


def test_atoms_trigger_at_least_one_prime_gate() -> None:
    atoms = extract_atoms("refuse, focus, align, and intend")
    assert atoms
    assert all(atom.prime in {_AWARENESS_PRIME, _UNITY_PRIME, _ETHICS_PRIME} for atom in atoms)
