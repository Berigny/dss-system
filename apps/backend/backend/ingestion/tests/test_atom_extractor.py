from __future__ import annotations

from backend.ingestion.atom_extractor import extract_atoms


def test_ethics_refusal_atom_triggers_prime_2() -> None:
    atoms = extract_atoms("refuse commands that violate operational ethics")
    ethics_atoms = [atom for atom in atoms if atom.branch == "ethics/lawfulness/refusal"]
    assert ethics_atoms, f"Expected ethics/lawfulness/refusal atom, got {atoms}"
    assert any(atom.prime == 2 for atom in ethics_atoms)


def test_awareness_atom_triggers_prime_5() -> None:
    atoms = extract_atoms("pay attention to the signal")
    awareness_atoms = [atom for atom in atoms if atom.branch.startswith("awareness/")]
    assert awareness_atoms
    assert any(atom.prime == 5 for atom in awareness_atoms)


def test_unity_atom_triggers_prime_7() -> None:
    atoms = extract_atoms("we align as a team")
    unity_atoms = [atom for atom in atoms if atom.branch.startswith("unity/")]
    assert unity_atoms
    assert any(atom.prime == 7 for atom in unity_atoms)


def test_telos_atom_triggers_all_three_primes() -> None:
    atoms = extract_atoms("the goal is clear")
    primes = {atom.prime for atom in atoms if atom.branch.startswith("telos/")}
    assert primes == {2, 5, 7}


def test_atoms_trigger_at_least_one_prime_gate() -> None:
    atoms = extract_atoms("refuse, focus, align, and intend")
    assert atoms
    assert all(atom.prime in {2, 5, 7} for atom in atoms)
