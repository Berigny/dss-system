#!/usr/bin/env python3
"""
KSR-ENCODE v0.1 — canonical encoder for semantic_registry.yaml 1.2.0.

Produces an exponent-vector over positional primes, not a raw product, so that
×1 (Origin/Eq0) nodes remain distinguishable and absence/presence are explicit.

Usage:
    python3 tools/encode.py --concepts Eq0 Eq1 K0
    python3 tools/encode.py --concepts Eq0 Eq1 K0 --to-number
    python3 tools/encode.py --seed eval/seed_v0.1.jsonl --output report.json
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


DEFAULT_REGISTRY = Path("apps/backend/backend/kernel/semantic_registry.yaml")


def load_registry(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def registry_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_alphabet(registry: dict[str, Any]) -> dict[str, int]:
    """
    Build a canonical name -> prime mapping from all KSR namespaces.

    A concept may have multiple names; each alias maps to the same prime.
    The canonical output ordering is by prime value.
    """
    alphabet: dict[str, int] = {}

    # Eq0..Eq9 via metric_prime_map (single source of truth).
    for eq_key, prime in registry.get("flow_topology", {}).get("metric_prime_map", {}).items():
        alphabet[eq_key] = int(prime)
        # Also register digit symbols.
        digit = registry.get("digit_registry", {}).get(eq_key, {})
        sym = digit.get("symbol")
        if sym:
            alphabet[sym] = int(prime)
        for alias in digit.get("aliases", []):
            alphabet[alias] = int(prime)

    # Traversal sequence first (bridge edges, intermediate coordinates).
    # Corner kernels and coordinates are authoritative and will override below.
    trav = registry.get("lattice_registry", {}).get("traversal_sequence", [])
    for entry in trav:
        prime = entry.get("prime")
        coord = entry.get("coordinate")
        # Only register coordinates; kernels/hebrew letters here are non-authoritative.
        if coord and prime is not None:
            alphabet[coord] = int(prime)

    # Face centers override traversal_sequence face coordinates.
    for face in registry.get("lattice_registry", {}).get("face_centers", []):
        prime = face.get("prime")
        coord = face.get("coordinate")
        if coord and prime is not None:
            alphabet[coord] = int(prime)
        hebrew = face.get("letter")
        if hebrew and prime is not None:
            alphabet[hebrew] = int(prime)

    # Corner map is authoritative for corner coordinates and kernels.
    for coord, info in registry.get("lattice_registry", {}).get("corner_map", {}).items():
        prime = int(info.get("structural_prime"))
        alphabet[coord] = prime
        kernel = info.get("kernel")
        if kernel:
            alphabet[kernel] = prime
        hebrew = info.get("hebrew_letter")
        if hebrew:
            alphabet[hebrew] = prime

    # Reset node.
    reset = registry.get("lattice_registry", {}).get("reset_node", {})
    if reset:
        prime = reset.get("structural_prime")
        coord = reset.get("coordinate")
        kernel = reset.get("kernel")
        if coord and prime is not None:
            alphabet[coord] = int(prime)
        if kernel and prime is not None:
            alphabet[kernel] = int(prime)

    # Quaternary gate dimension labels (awareness/unity/ethics) -> their primes.
    for gname, ginfo in registry.get("quaternary_gate_registry", {}).get("gates", {}).items():
        prime = ginfo.get("prime")
        if prime is not None:
            alphabet[gname] = int(prime)
            alphabet[gname.lower()] = int(prime)

    # Prime registry names / mnemonics / dimensions.
    for prime_val, info in registry.get("prime_registry", {}).items():
        p = int(prime_val)
        for key in ("name", "mnemonic", "engineering_dimension"):
            val = info.get(key)
            if val:
                alphabet[val] = p

    # Glossary terms -> replacement synonym key, resolved via synonym_registry.
    syn = registry.get("synonym_registry", {})
    syn_norm: dict[str, set[str]] = {}
    for canon, forms in syn.items():
        forms = forms if isinstance(forms, list) else [forms]
        syn_norm[canon] = {str(f).lower() for f in forms}

    for entry in registry.get("glossary", []):
        term = entry.get("term")
        replacement = entry.get("replacement")
        if term:
            # Resolve replacement to a prime if it is a synonym key.
            if replacement in alphabet:
                alphabet[term] = alphabet[replacement]
            elif replacement in syn_norm:
                # Pick the first canonical form that maps to a prime.
                for form in syn_norm[replacement]:
                    if form in alphabet:
                        alphabet[term] = alphabet[form]
                        break

    # Cross-domain registry names / relation types.
    def walk_cross(o: Any, prime_hint: int | None = None):
        if isinstance(o, dict):
            name = o.get("name") or o.get("term")
            prime = o.get("prime")
            if prime is not None:
                prime_hint = int(prime)
            if name and prime_hint is not None:
                alphabet[name] = prime_hint
            for v in o.values():
                walk_cross(v, prime_hint)
        elif isinstance(o, list):
            for v in o:
                walk_cross(v, prime_hint)

    walk_cross(registry.get("cross_domain_registry"))

    return alphabet


def encode_concepts(
    concepts: list[str],
    alphabet: dict[str, int],
    registry: dict[str, Any],
) -> dict[str, Any]:
    """
    Encode a list of concept names into a canonical exponent-vector.

    Returns a dict with:
      - input: original concept list
      - resolved: {name: prime} for each concept
      - unknown: list of unrecognized names
      - exponent_vector: ordered dict prime -> exponent
      - number: materialized product (for transport/decode testing)
      - canonical_bytes: stable UTF-8 bytes of the exponent_vector JSON
    """
    unknown: list[str] = []
    resolved: dict[str, int] = {}
    exponents: Counter[int] = Counter()

    for name in concepts:
        key = name.strip()
        # Try exact match, then case-insensitive.
        prime = alphabet.get(key)
        if prime is None:
            prime = alphabet.get(key.lower())
        if prime is None:
            # Try stripping loam: prefix if present.
            if key.lower().startswith("loam:"):
                prime = alphabet.get(key[5:])
        if prime is None:
            unknown.append(name)
        else:
            resolved[name] = prime
            exponents[prime] += 1

    # Canonical ordering by prime value.
    ordered = {str(p): exponents[p] for p in sorted(exponents)}

    # Materialized product for decode testing.
    number = 1
    for p, exp in exponents.items():
        number *= int(p) ** exp

    # Stable canonical bytes.
    canonical_json = json.dumps(
        ordered,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    canonical_bytes = canonical_json.encode("utf-8")

    return {
        "input": concepts,
        "resolved": resolved,
        "unknown": unknown,
        "exponent_vector": ordered,
        "number": number,
        "canonical_bytes": canonical_bytes.hex(),
    }


def materialize_number(exponent_vector: dict[str, int]) -> int:
    number = 1
    for prime_str, exp in exponent_vector.items():
        number *= int(prime_str) ** exp
    return number


def compute_check_factor(registry: dict[str, Any]) -> int:
    """Return the 336-derived check factor from the registry (product of quaternary gate primes)."""
    cc = registry.get("check_component", {})
    if cc.get("algorithm") == "product_of_quaternary_gates" and "check_factor" in cc:
        return int(cc["check_factor"])
    # Fallback: product of quaternary gate primes.
    primes = [ginfo["prime"] for ginfo in registry.get("quaternary_gate_registry", {}).get("gates", {}).values() if ginfo.get("prime")]
    factor = 1
    for p in primes:
        factor *= int(p)
    return factor


def append_check_component(number: int, registry: dict[str, Any]) -> int:
    """Append the 336-derived check component to a materialized encoding."""
    return number * compute_check_factor(registry)


def encode_concepts(
    concepts: list[str],
    alphabet: dict[str, int],
    registry: dict[str, Any],
    append_check: bool = False,
) -> dict[str, Any]:
    """
    Encode a list of concept names into a canonical exponent-vector.

    Returns a dict with:
      - input: original concept list
      - resolved: {name: prime} for each concept
      - unknown: list of unrecognized names
      - exponent_vector: ordered dict prime -> exponent
      - number: materialized product (for transport/decode testing)
      - canonical_bytes: stable UTF-8 bytes of the exponent_vector JSON
      - check_factor: check component factor (if append_check=True)
      - checked_number: number * check_factor (if append_check=True)
      - check_valid: True for encoding (if append_check=True)
    """
    result = _encode_concepts_semantic(concepts, alphabet)
    if append_check:
        cf = compute_check_factor(registry)
        result["check_factor"] = cf
        result["checked_number"] = result["number"] * cf
        result["check_valid"] = True
    return result


def _encode_concepts_semantic(
    concepts: list[str],
    alphabet: dict[str, int],
) -> dict[str, Any]:
    unknown: list[str] = []
    resolved: dict[str, int] = {}
    exponents: Counter[int] = Counter()

    for name in concepts:
        key = name.strip()
        prime = alphabet.get(key)
        if prime is None:
            prime = alphabet.get(key.lower())
        if prime is None:
            if key.lower().startswith("loam:"):
                prime = alphabet.get(key[5:])
        if prime is None:
            unknown.append(name)
        else:
            resolved[name] = prime
            exponents[prime] += 1

    ordered = {str(p): exponents[p] for p in sorted(exponents)}

    number = 1
    for p, exp in exponents.items():
        number *= int(p) ** exp

    canonical_json = json.dumps(
        ordered,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    canonical_bytes = canonical_json.encode("utf-8")

    return {
        "input": concepts,
        "resolved": resolved,
        "unknown": unknown,
        "exponent_vector": ordered,
        "number": number,
        "canonical_bytes": canonical_bytes.hex(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KSR canonical encoder")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--concepts", nargs="+", help="Concept names to encode")
    parser.add_argument("--seed", type=Path, help="JSONL seed file with 'concepts' lists")
    parser.add_argument("--to-number", action="store_true", help="Emit materialized number instead of full JSON")
    parser.add_argument("--check", action="store_true", help="Append 336-derived check component to encodings")
    parser.add_argument("--output", type=Path, help="Write full report JSON to path")
    args = parser.parse_args(argv)

    if not args.concepts and not args.seed:
        parser.error("Provide --concepts or --seed")

    registry = load_registry(args.registry)
    alphabet = build_alphabet(registry)
    sha = registry_sha256(args.registry)

    header = {
        "ksr_version": registry.get("ksr_version"),
        "registry_sha256": sha,
        "registry_path": str(args.registry),
        "alphabet_size": len(alphabet),
    }

    seeds: list[dict[str, Any]] = []
    if args.seed:
        with args.seed.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                seeds.append(record)
    else:
        seeds = [{"id": "cli", "concepts": args.concepts}]

    results: list[dict[str, Any]] = []
    for record in seeds:
        concepts = record.get("concepts", [])
        encoded = encode_concepts(concepts, alphabet, registry, append_check=args.check)
        encoded["id"] = record.get("id")
        results.append(encoded)

    if args.to_number and len(results) == 1:
        print(results[0]["checked_number"] if args.check else results[0]["number"])
        return 0

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
