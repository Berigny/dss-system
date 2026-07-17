#!/usr/bin/env python3
"""
KSR-DECODE v0.1 — deterministic factorization decoder for semantic_registry.yaml 1.2.0.

No model calls. Accepts a materialized number or exponent-vector JSON and returns the
recovered concept path by mapping prime factors back to the KSR single source.

Usage:
    python3 tools/decode.py --number 42
    python3 tools/decode.py --vector '{"2":1,"3":1,"7":1}'
    python3 tools/decode.py --seed eval/seed_v0.1.jsonl --encoded-field number
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from encode import DEFAULT_REGISTRY, compute_check_factor, load_registry, registry_sha256


def build_prime_to_concept(registry: dict[str, Any]) -> dict[int, set[str]]:
    """Reverse the KSR namespaces to map each prime to its canonical concept names."""
    prime_to_concepts: dict[int, set[str]] = {}

    def add(prime: int, name: str):
        prime_to_concepts.setdefault(int(prime), set()).add(name)

    # Eq0..Eq9 via metric_prime_map.
    for eq_key, prime in registry.get("flow_topology", {}).get("metric_prime_map", {}).items():
        p = int(prime)
        add(p, eq_key)
        digit = registry.get("digit_registry", {}).get(eq_key, {})
        sym = digit.get("symbol")
        if sym:
            add(p, sym)
        for alias in digit.get("aliases", []):
            add(p, alias)

    # Corner map.
    for coord, info in registry.get("lattice_registry", {}).get("corner_map", {}).items():
        p = int(info.get("structural_prime"))
        add(p, coord)
        kernel = info.get("kernel")
        if kernel:
            add(p, kernel)
        hebrew = info.get("hebrew_letter")
        if hebrew:
            add(p, hebrew)

    # Traversal sequence.
    for entry in registry.get("lattice_registry", {}).get("traversal_sequence", []):
        p = entry.get("prime")
        if p is None:
            continue
        p = int(p)
        coord = entry.get("coordinate")
        if coord:
            add(p, coord)
        kernel = entry.get("kernel")
        if kernel:
            add(p, kernel)
        hebrew = entry.get("hebrew_letter")
        if hebrew:
            add(p, hebrew)

    # Face centers.
    for face in registry.get("lattice_registry", {}).get("face_centers", []):
        p = face.get("prime")
        if p is None:
            continue
        p = int(p)
        coord = face.get("coordinate")
        if coord:
            add(p, coord)
        hebrew = face.get("letter")
        if hebrew:
            add(p, hebrew)

    # Reset node.
    reset = registry.get("lattice_registry", {}).get("reset_node", {})
    if reset:
        p = reset.get("structural_prime")
        if p is not None:
            p = int(p)
            coord = reset.get("coordinate")
            kernel = reset.get("kernel")
            if coord:
                add(p, coord)
            if kernel:
                add(p, kernel)

    # Quaternary gate dimension labels.
    for gname, ginfo in registry.get("quaternary_gate_registry", {}).get("gates", {}).items():
        prime = ginfo.get("prime")
        if prime is not None:
            p = int(prime)
            add(p, gname)
            add(p, gname.lower())

    # Prime registry names / mnemonics / dimensions.
    for prime_val, info in registry.get("prime_registry", {}).items():
        p = int(prime_val)
        for key in ("name", "mnemonic", "engineering_dimension"):
            val = info.get(key)
            if val:
                add(p, val)

    # Glossary terms.
    syn = registry.get("synonym_registry", {})
    syn_norm: dict[str, set[str]] = {}
    for canon, forms in syn.items():
        forms = forms if isinstance(forms, list) else [forms]
        syn_norm[canon] = {str(f).lower() for f in forms}

    alphabet: dict[str, int] = {}
    for p, names in prime_to_concepts.items():
        for n in names:
            alphabet[n] = p

    for entry in registry.get("glossary", []):
        term = entry.get("term")
        replacement = entry.get("replacement")
        if term:
            if replacement in alphabet:
                add(alphabet[replacement], term)
            elif replacement in syn_norm:
                for form in syn_norm[replacement]:
                    if form in alphabet:
                        add(alphabet[form], term)
                        break

    # Cross-domain registry.
    def walk_cross(o: Any, prime_hint: int | None = None):
        if isinstance(o, dict):
            name = o.get("name") or o.get("term")
            prime = o.get("prime")
            if prime is not None:
                prime_hint = int(prime)
            if name and prime_hint is not None:
                add(prime_hint, name)
            for v in o.values():
                walk_cross(v, prime_hint)
        elif isinstance(o, list):
            for v in o:
                walk_cross(v, prime_hint)

    walk_cross(registry.get("cross_domain_registry"))

    return prime_to_concepts


def factorize(n: int) -> dict[int, int]:
    """Return prime factorization as {prime: exponent}."""
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


def get_check_primes(registry: dict[str, Any]) -> list[int]:
    """Return the list of primes whose product forms the check component."""
    cc = registry.get("check_component", {})
    if "gate_primes" in cc:
        return [int(p) for p in cc["gate_primes"]]
    return [ginfo["prime"] for ginfo in registry.get("quaternary_gate_registry", {}).get("gates", {}).values() if ginfo.get("prime")]


def decode_number(
    number: int,
    prime_to_concepts: dict[int, set[str]],
    registry: dict[str, Any],
    verify_check: bool = False,
) -> dict[str, Any]:
    """Decode a materialized number into exponent-vector and concept names."""
    check_factor = compute_check_factor(registry)
    check_primes = get_check_primes(registry)
    check_valid = True

    if number == 1:
        # Prime 1 is used as an out-of-band marker (reset_node). Factorization
        # cannot recover it, so we special-case the unity value when the registry
        # maps concepts to prime 1.
        if 1 in prime_to_concepts:
            exponents = {1: 1}
        else:
            exponents = {}
    else:
        exponents = factorize(number)

    if verify_check and check_factor > 1:
        # Strip exactly one factor of each check prime.
        for p in check_primes:
            if exponents.get(p, 0) < 1:
                check_valid = False
                break
            exponents[p] -= 1
        # Remove zero-exponent entries.
        exponents = {p: e for p, e in exponents.items() if e > 0}
        # After stripping check primes, if the semantic number is 1 and the
        # registry maps prime 1 (reset_node), preserve it.
        if check_valid and not exponents and 1 in prime_to_concepts:
            exponents = {1: 1}

    ordered = {str(p): e for p, e in sorted(exponents.items())}

    unknown_primes: list[int] = []
    recovered: list[dict[str, Any]] = []
    for prime, exp in sorted(exponents.items()):
        names = prime_to_concepts.get(prime, set())
        if not names:
            unknown_primes.append(prime)
        recovered.append({
            "prime": prime,
            "exponent": exp,
            "concepts": sorted(names),
        })

    canonical_json = json.dumps(
        ordered,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    result: dict[str, Any] = {
        "input_number": number,
        "exponent_vector": ordered,
        "canonical_bytes": canonical_json.encode("utf-8").hex(),
        "recovered_concepts": recovered,
        "unknown_primes": unknown_primes,
    }
    if verify_check:
        result["check_factor"] = check_factor
        result["check_valid"] = check_valid
    return result


def decode_vector(
    vector: dict[str, int],
    prime_to_concepts: dict[int, set[str]],
    registry: dict[str, Any],
    verify_check: bool = False,
) -> dict[str, Any]:
    """Decode an exponent-vector directly."""
    number = 1
    for prime_str, exp in vector.items():
        number *= int(prime_str) ** exp
    return decode_number(number, prime_to_concepts, registry, verify_check=verify_check)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KSR deterministic decoder")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--number", type=int, help="Materialized number to decode")
    group.add_argument("--vector", type=str, help="Exponent-vector JSON string")
    group.add_argument("--seed", type=Path, help="JSONL seed file")
    parser.add_argument("--encoded-field", default="number", help="Field in seed JSONL containing encoded value")
    parser.add_argument("--check", action="store_true", help="Verify and strip 336-derived check component before decoding")
    parser.add_argument("--output", type=Path, help="Write full report JSON to path")
    args = parser.parse_args(argv)

    registry = load_registry(args.registry)
    prime_to_concepts = build_prime_to_concept(registry)
    sha = registry_sha256(args.registry)

    header = {
        "ksr_version": registry.get("ksr_version"),
        "registry_sha256": sha,
        "registry_path": str(args.registry),
    }

    results: list[dict[str, Any]] = []

    if args.number is not None:
        decoded = decode_number(args.number, prime_to_concepts, registry, verify_check=args.check)
        decoded["id"] = "cli"
        results.append(decoded)
    elif args.vector is not None:
        vector = json.loads(args.vector)
        decoded = decode_vector(vector, prime_to_concepts, registry, verify_check=args.check)
        decoded["id"] = "cli"
        results.append(decoded)
    elif args.seed:
        with args.seed.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                rid = record.get("id", "unknown")
                value = record.get(args.encoded_field)
                if isinstance(value, dict):
                    decoded = decode_vector(value, prime_to_concepts, registry, verify_check=args.check)
                else:
                    decoded = decode_number(int(value), prime_to_concepts, registry, verify_check=args.check)
                decoded["id"] = rid
                results.append(decoded)

    report = {
        "header": header,
        "results": results,
    }

    report_json = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report_json, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(report_json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
