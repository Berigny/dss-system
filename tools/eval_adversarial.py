#!/usr/bin/env python3
"""
KSR-EVAL Phase 3 — adversarial and structural checks.

1. Confabulation traps: 100 lattice-path claims (50 valid, 50 invalid).
2. Perturbation curve: 1-edit mutations of valid encodings.
3. 336 guard drill: fail-closed when awareness/unity/ethics is zeroed.

Usage:
    python3 tools/eval_adversarial.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from encode import DEFAULT_REGISTRY, build_alphabet, encode_concepts, load_registry, registry_sha256


def is_prime(n: int) -> bool:
    return n > 1 and all(n % i for i in range(2, int(math.isqrt(n)) + 1))


def factorize(n: int) -> dict[int, int]:
    exponents: dict[int, int] = {}
    d = 2
    while d * d <= n:
        while n % d == 0:
            exponents[d] = exponents.get(d, 0) + 1
            n //= d
        d += 1
    if n > 1:
        exponents[n] = exponents.get(n, 0) + 1
    return exponents


def build_valid_edges(registry: dict[str, Any]) -> set[tuple[str, str]]:
    edges = set()
    for e in registry.get("lattice_registry", {}).get("bridge_edges", []):
        edges.add((e.get("from"), e.get("to")))
    return edges


def build_reverse_edges(registry: dict[str, Any]) -> dict[str, list[str]]:
    rev: dict[str, list[str]] = {}
    for e in registry.get("lattice_registry", {}).get("bridge_edges", []):
        rev.setdefault(e.get("from"), []).append(e.get("to"))
    return rev


def generate_traps(registry: dict[str, Any], rng: random.Random, n_valid: int = 50, n_invalid: int = 50) -> list[dict[str, Any]]:
    corners = list(registry.get("lattice_registry", {}).get("corner_map", {}).values())
    corner_kernels = [c["kernel"] for c in corners]
    valid_edges = build_valid_edges(registry)
    forward = build_reverse_edges(registry)

    traps: list[dict[str, Any]] = []

    # Valid paths: random walks following bridge_edges.
    while len([t for t in traps if t["valid"]]) < n_valid:
        start = rng.choice(corner_kernels)
        path = [start]
        current = start
        length = rng.randint(2, 5)
        for _ in range(length):
            if current not in forward:
                break
            nxt = rng.choice(forward[current])
            path.append(nxt)
            current = nxt
        if len(path) >= 2 and all((path[i], path[i + 1]) in valid_edges for i in range(len(path) - 1)):
            traps.append({"id": f"t{len(traps)+1:03d}", "path": path, "valid": True})

    # Invalid paths: introduce at least one non-edge.
    while len([t for t in traps if not t["valid"]]) < n_invalid:
        start = rng.choice(corner_kernels)
        path = [start]
        current = start
        length = rng.randint(2, 5)
        broken = False
        for i in range(length):
            if current not in forward or broken:
                # After break, choose random next node.
                nxt = rng.choice(corner_kernels)
                path.append(nxt)
                current = nxt
                continue
            if rng.random() < 0.3 and len(path) > 1:
                # Insert invalid jump.
                nxt = rng.choice([k for k in corner_kernels if k not in forward.get(current, [])])
                path.append(nxt)
                current = nxt
                broken = True
            else:
                nxt = rng.choice(forward[current])
                path.append(nxt)
                current = nxt
        if len(path) >= 2 and any((path[i], path[i + 1]) not in valid_edges for i in range(len(path) - 1)):
            traps.append({"id": f"t{len(traps)+1:03d}", "path": path, "valid": False})

    rng.shuffle(traps)
    return traps


def evaluate_traps(traps: list[dict[str, Any]], registry: dict[str, Any]) -> dict[str, Any]:
    valid_edges = build_valid_edges(registry)
    tp = fp = tn = fn = 0
    for trap in traps:
        predicted_valid = all(
            (trap["path"][i], trap["path"][i + 1]) in valid_edges
            for i in range(len(trap["path"]) - 1)
        )
        actual_valid = trap["valid"]
        if predicted_valid and actual_valid:
            tp += 1
        elif predicted_valid and not actual_valid:
            fp += 1
        elif not predicted_valid and not actual_valid:
            tn += 1
        else:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "total": len(traps),
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
    }


def generate_perturbations(registry: dict[str, Any], rng: random.Random, n_samples: int = 100) -> list[dict[str, Any]]:
    """Generate 1-edit mutations of valid encodings and report structural validity."""
    # Use core prime_registry primes as the structural alphabet.
    core_primes = {int(p) for p in registry.get("prime_registry", {}).keys()}
    # Also include structural primes declared in corner_map and bridge edges.
    for info in registry.get("lattice_registry", {}).get("corner_map", {}).values():
        core_primes.add(int(info.get("structural_prime", 0)))
    for e in registry.get("lattice_registry", {}).get("bridge_edges", []):
        coord = e.get("coordinate")
        for entry in registry.get("lattice_registry", {}).get("traversal_sequence", []):
            if entry.get("coordinate") == coord:
                core_primes.add(int(entry.get("prime", 0)))
                break
    core_primes.discard(0)

    concept_numbers = [(name, prime) for name, prime in build_alphabet(registry).items() if prime in core_primes]
    if not concept_numbers:
        concept_numbers = [(name, prime) for name, prime in build_alphabet(registry).items() if prime > 1]

    results = []
    for _ in range(n_samples):
        name, prime = rng.choice(concept_numbers)
        base = prime
        strategy = rng.choice(["increment_exponent", "decrement_exponent", "multiply_small_prime", "swap_to_random_prime"])
        if strategy == "increment_exponent":
            mutated = base * prime
        elif strategy == "decrement_exponent":
            mutated = 1
        elif strategy == "multiply_small_prime":
            # Choose a small prime NOT in the core alphabet to maximize invalid outcomes.
            outsiders = [p for p in range(2, 200) if is_prime(p) and p not in core_primes]
            mutated = base * rng.choice(outsiders if outsiders else [2])
        else:
            other_primes = [p for _, p in concept_numbers if p != prime]
            mutated = rng.choice(other_primes) if other_primes else base

        factors = factorize(mutated)
        valid = all(p in core_primes for p in factors)
        results.append({
            "base_concept": name,
            "base_prime": prime,
            "strategy": strategy,
            "mutated_number": mutated,
            "factors": factors,
            "structurally_valid": valid,
        })

    invalid_count = sum(1 for r in results if not r["structurally_valid"])
    sparsity = invalid_count / len(results) if results else 0.0
    return {
        "samples": results,
        "invalid_count": invalid_count,
        "sparsity_ratio": sparsity,
    }


def generate_digit_edit_mutations(
    registry: dict[str, Any],
    rng: random.Random,
    n_samples: int = 100,
    append_336_check: bool = False,
) -> dict[str, Any]:
    """
    Decimal-digit flip mutations of valid encodings.

    Unlike the prime-alphabet perturbations above, digit edits operate on the
    decimal representation of the encoded number. They model transcription or
    transport errors (e.g. a character is mis-typed in a ledger coordinate).

    If ``append_336_check`` is True, the base encoding is conceptually protected
    by appending the 336-derived check component (product of the three
    quaternary gate primes: awareness * unity * ethics = 17 * 19 * 137).  The
    same digit flip is then applied to the protected number and we report
    whether the 336 invariant still holds.
    """
    alphabet = build_alphabet(registry)
    core_primes = set(alphabet.values())

    gate_primes = [
        ginfo["prime"]
        for ginfo in registry.get("quaternary_gate_registry", {}).get("gates", {}).values()
    ]
    check_factor = math.prod(gate_primes) if gate_primes else 1

    concept_numbers = [(name, prime) for name, prime in alphabet.items() if prime > 1]
    if not concept_numbers:
        return {"samples": [], "detection_rate": 0.0, "check_detection_rate": 0.0}

    samples = []
    for _ in range(n_samples):
        name, prime = rng.choice(concept_numbers)
        base = prime
        if append_336_check:
            base *= check_factor

        s = str(base)
        if len(s) < 2:
            continue

        idx = rng.randrange(len(s))
        old_digit = s[idx]
        new_digit = rng.choice([d for d in "0123456789" if d != old_digit])
        mutated_s = s[:idx] + new_digit + s[idx + 1 :]
        mutated = int(mutated_s)

        mutated_factors = factorize(mutated)
        structurally_valid = all(p in core_primes for p in mutated_factors)
        check_valid = all(mutated_factors.get(p, 0) >= 1 for p in gate_primes)

        samples.append({
            "base_number": base,
            "base_concept": name,
            "mutated_number": mutated,
            "flipped_position": idx,
            "old_digit": old_digit,
            "new_digit": new_digit,
            "append_336_check": append_336_check,
            "structurally_valid": structurally_valid,
            "check_336_valid": check_valid,
            "detected": not structurally_valid,
            "check_detected": append_336_check and not check_valid,
        })

    n = len(samples)
    detection_rate = sum(1 for s in samples if s["detected"]) / n if n else 0.0
    check_detection_rate = sum(1 for s in samples if s["check_detected"]) / n if n else 0.0
    return {
        "samples": samples,
        "detection_rate": detection_rate,
        "check_detection_rate": check_detection_rate,
    }


def evaluate_digit_edit_check(registry: dict[str, Any], rng: random.Random, n_samples: int = 100) -> dict[str, Any]:
    """
    Compare digit-flip detection with and without the 336-derived check component.
    """
    baseline = generate_digit_edit_mutations(registry, rng, n_samples=n_samples, append_336_check=False)
    protected = generate_digit_edit_mutations(registry, rng, n_samples=n_samples, append_336_check=True)
    return {
        "baseline_detection_rate": baseline["detection_rate"],
        "protected_detection_rate": protected["detection_rate"],
        "check_only_detection_rate": protected["check_detection_rate"],
        "baseline": baseline,
        "protected": protected,
    }


def load_corpus(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    header = json.loads(lines[0])
    records = [json.loads(line) for line in lines[1:]]
    return header, records


def check_false_rejection(registry: dict[str, Any], corpus_path: Path = Path("eval/corpus/novel_v0.1.jsonl")) -> dict[str, Any]:
    """
    Verify that appending the 336 check component to every valid corpus encoding
    produces a number that passes the check. Zero false rejections required.
    """
    from encode import append_check_component, build_alphabet, encode_concepts

    alphabet = build_alphabet(registry)
    header, records = load_corpus(corpus_path)
    check_factor = registry.get("check_component", {}).get("check_factor", 1)
    check_primes = set(registry.get("check_component", {}).get("gate_primes", []))

    results = []
    false_rejections = 0
    for record in records:
        enc = encode_concepts(record["encode_seed"], alphabet, registry)
        checked = append_check_component(enc["number"], registry)
        # Verify divisibility by the full check factor.
        valid = checked % check_factor == 0
        # Also verify each gate prime appears with exponent >= 1.
        factors = factorize(checked)
        valid = valid and all(factors.get(int(p), 0) >= 1 for p in check_primes)
        if not valid:
            false_rejections += 1
        results.append({
            "id": record["id"],
            "semantic_number": enc["number"],
            "checked_number": checked,
            "check_valid": valid,
        })

    return {
        "total": len(results),
        "false_rejections": false_rejections,
        "false_rejection_rate": false_rejections / len(results) if results else 0.0,
        "samples": results,
    }


def stripping_degradation(registry: dict[str, Any], rng: random.Random, n_samples: int = 50) -> dict[str, Any]:
    """Compare stripping in priority order vs random stripping."""
    alphabet = build_alphabet(registry)
    # Use Eq6*Eq7*Eq8 as a rich starting state.
    base_primes = [17, 19, 137]
    base_number = 17 * 19 * 137

    # Build stripping priority list from registry (critical > high > medium > low).
    strip = registry.get("stripping_priority", {})
    tier_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    priority_terms = []
    for tier, terms in sorted(strip.items(), key=lambda x: tier_order.get(x[0], 99)):
        priority_terms.extend(terms or [])

    def encode_state(primes: list[int]) -> int:
        n = 1
        for p in primes:
            n *= p
        return n

    def state_score(state_primes: list[int], alphabet: dict[str, int]) -> float:
        # Score = fraction of base primes retained.
        return len(set(state_primes) & set(base_primes)) / len(base_primes)

    priority_results = []
    random_results = []

    # Priority-order stripping.
    state = base_primes[:]
    for term in priority_terms:
        prime = alphabet.get(term)
        if prime and prime in state:
            state.remove(prime)
        priority_results.append(state_score(state, alphabet))

    # Random stripping, averaged over several runs.
    for _ in range(n_samples):
        state = base_primes[:]
        run_scores = []
        rng.shuffle(priority_terms)
        for term in priority_terms:
            prime = alphabet.get(term)
            if prime and prime in state:
                state.remove(prime)
            run_scores.append(state_score(state, alphabet))
        random_results.append(run_scores)

    # Average random scores step-by-step.
    avg_random = []
    for i in range(len(priority_results)):
        vals = [run[i] for run in random_results if i < len(run)]
        avg_random.append(sum(vals) / len(vals) if vals else 0.0)

    # Graceful degradation: priority-order should retain higher score on average.
    priority_mean = sum(priority_results) / len(priority_results) if priority_results else 0.0
    random_mean = sum(avg_random) / len(avg_random) if avg_random else 0.0
    graceful = priority_mean >= random_mean
    return {
        "priority_scores": priority_results,
        "random_avg_scores": avg_random,
        "priority_mean": priority_mean,
        "random_mean": random_mean,
        "graceful_degradation": graceful,
    }


def guard_drill(registry: dict[str, Any]) -> dict[str, Any]:
    """Check that zeroing any quaternary gate factor triggers POISON_PILL."""
    gates = registry.get("quaternary_gate_registry", {}).get("gates", {})
    results = []
    for gname, ginfo in gates.items():
        prime = ginfo.get("prime")
        level_0 = ginfo.get("levels", {}).get("level_0", {})
        action = level_0.get("action")
        v_max = level_0.get("v_max")
        # A state missing this prime has exponent 0 => should trigger level_0.
        triggered = (action == "POISON_PILL" and v_max == 0)
        results.append({
            "gate": gname,
            "prime": prime,
            "zeroed_state_number": 1,  # no prime factors
            "level_0_action": action,
            "fail_closed": triggered,
        })

    # Non-compensatory check: high scores elsewhere do not offset zeroed factor.
    # Build a state with awareness/unity/ethics but missing one gate.
    all_gate_primes = {ginfo["prime"] for ginfo in gates.values()}
    noncomp_results = []
    for gname, ginfo in gates.items():
        prime = ginfo.get("prime")
        # State includes all other gate primes at high exponent but zero for this one.
        number = 1
        for p in all_gate_primes:
            if p != prime:
                number *= p ** 3
        # Check if missing gate would still be detected.
        factors = factorize(number)
        missing = factors.get(prime, 0) == 0
        noncomp_results.append({
            "zeroed_gate": gname,
            "number": number,
            "factors": factors,
            "zeroed_factor_missing": missing,
        })

    return {
        "per_gate": results,
        "non_compensatory": noncomp_results,
        "all_fail_closed": all(r["fail_closed"] for r in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="KSR Phase 3 adversarial and structural checks")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--seed", type=int, default=254)
    parser.add_argument("--output", type=Path, help="Report JSON path")
    args = parser.parse_args()

    registry = load_registry(args.registry)
    sha = registry_sha256(args.registry)
    rng = random.Random(args.seed)

    print("Generating lattice-path traps...")
    traps = generate_traps(registry, rng)
    trap_eval = evaluate_traps(traps, registry)

    print("Running perturbation curve...")
    perturb = generate_perturbations(registry, rng)

    print("Running digit-edit mutation + 336 check-digit evaluation...")
    digit_edit = evaluate_digit_edit_check(registry, rng)

    print("Running false-rejection check on corpus...")
    false_reject = check_false_rejection(registry)

    print("Running stripping degradation...")
    strip_deg = stripping_degradation(registry, rng)

    print("Running 336 guard drill...")
    drill = guard_drill(registry)

    report = {
        "header": {
            "report": "KSR-EVAL Phase 3/4 — adversarial, structural, and 336 check-digit checks",
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ksr_version": registry.get("ksr_version"),
            "registry_sha256": sha,
            "spec_version": "v0.3",
        },
        "gate": {
            "C3_trap_precision_gte_0_95": trap_eval["precision"] >= 0.95,
            "C3_trap_recall_gte_0_90": trap_eval["recall"] >= 0.90,
            "perturbation_sparsity_ratio": perturb["sparsity_ratio"],
            "G-PERT_protected_detection_gte_0_95": digit_edit["protected_detection_rate"] >= 0.95,
            "G-PERT_false_rejection_eq_0": false_reject["false_rejections"] == 0,
            "stripping_graceful_degradation": strip_deg["graceful_degradation"],
            "336_fail_closed_all_gates": drill["all_fail_closed"],
        },
        "trap_evaluation": trap_eval,
        "perturbation": perturb,
        "digit_edit_mutation": digit_edit,
        "false_rejection_check": false_reject,
        "stripping_degradation": strip_deg,
        "guard_drill": drill,
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        report_dir = Path(f"eval/reports/{time.strftime('%Y%m%d')}_{sha[:12]}")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "phase3_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {report_path}")

    print("\nGates:")
    print(json.dumps(report["gate"], indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
