#!/usr/bin/env python3
"""
KSR-EVAL Phase 6 — C2 at the geometry level.

Compare three arms on a 50-item corpus subset:
  A-arm: real registry (reference)
  G-arm: geometry shuffled (lattice edges/duals permuted), names intact
  N-arm: prime→name assignments shuffled, geometry intact

Reports decode recall per arm plus binomial p-values.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

# Import eval_decode helpers.
sys.path.insert(0, str(Path(__file__).parent))
from eval_decode import (
    AsyncOpenAI,
    AsyncRateLimiter,
    COST_CAP_CALLS,
    DEFAULT_MODEL,
    OPENROUTER_BASE,
    build_alphabet,
    build_minimal_registry,
    build_shuffled_registry,
    compute_metrics,
    encode_concepts,
    factorize,
    load_corpus,
    registry_sha256,
    run_trial,
)
from encode import load_registry


async def main() -> int:
    parser = argparse.ArgumentParser(description="KSR Phase 6 geometry-level C2 test")
    parser.add_argument("--corpus", type=Path, default=Path("eval/corpus/novel_v0.1.jsonl"))
    parser.add_argument("--registry", type=Path, default=Path("apps/backend/backend/kernel/semantic_registry.yaml"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sample", type=int, default=50)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--rate-limit-rpm", type=float, default=18.0)
    parser.add_argument("--output", type=Path, help="Report JSON path")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    api_key = __import__("os").getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("error: OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    client = AsyncOpenAI(base_url=OPENROUTER_BASE, api_key=api_key)
    registry = load_registry(args.registry)
    sha = registry_sha256(args.registry)
    rng = random.Random(args.seed)

    header, records = load_corpus(args.corpus)
    records = records[:args.sample]

    # Build three registry variants.
    a_registry = registry
    g_registry = build_geometry_shuffled(registry, rng)
    n_registry = build_names_shuffled(registry, rng)

    arms = {
        "A": a_registry,
        "G": g_registry,
        "N": n_registry,
    }

    # Build registry texts for prompts.
    from eval_decode import registry_to_text
    registry_texts = {arm: registry_to_text(reg) for arm, reg in arms.items()}

    trials = []
    call_counter = {"calls": 0}
    semaphore = __import__("asyncio").Semaphore(args.max_concurrent)
    rate_limiter = AsyncRateLimiter(rpm=args.rate_limit_rpm if args.rate_limit_rpm > 0 else None)

    for record in records:
        alphabet = build_alphabet(a_registry)
        enc = encode_concepts(record["encode_seed"], alphabet, a_registry)
        factors = factorize(enc["number"])
        for arm in arms:
            trials.append(run_trial(
                client, args.model, semaphore, rate_limiter,
                record, factors, registry_texts[arm], arm, 0, call_counter,
            ))

    print(f"Running {len(trials)} trials (cap: {COST_CAP_CALLS})...")
    trial_results = await __import__("asyncio").gather(*trials)

    # Compute metrics per arm.
    from sentence_transformers import SentenceTransformer
    embedder = SentenceTransformer("all-MiniLM-L6-v2")
    original_texts = {r["id"]: r["text"] for r in records}

    arm_recalls: dict[str, list[float]] = defaultdict(list)
    for tr in trial_results:
        if "error" in tr:
            continue
        rid = tr["id"]
        enc = encode_concepts(next(r["encode_seed"] for r in records if r["id"] == rid), build_alphabet(a_registry), a_registry)
        factors = factorize(enc["number"])
        metrics = compute_metrics(
            factors,
            tr.get("reconstructed_concepts", []),
            original_texts[rid],
            tr.get("reconstructed_text", ""),
            build_alphabet(a_registry),
            embedder,
        )
        arm_recalls[tr["arm"]].append(metrics["node_recall"])

    # Compute summary and p-values.
    from statistics import mean
    from math import comb

    def binomial_p(n_succ, n_tot, p_null):
        """Two-sided binomial p-value."""
        if n_tot == 0:
            return 1.0
        p = 0.0
        for k in range(n_tot + 1):
            bp = comb(n_tot, k) * (p_null ** k) * ((1 - p_null) ** (n_tot - k))
            if bp <= comb(n_tot, n_succ) * (p_null ** n_succ) * ((1 - p_null) ** (n_tot - n_succ)):
                p += bp
        return min(p, 1.0)

    summary = {}
    for arm in ["A", "G", "N"]:
        vals = arm_recalls.get(arm, [])
        summary[arm] = {
            "count": len(vals),
            "mean_recall": mean(vals) if vals else 0.0,
            "recalls": vals,
        }

    a_mean = summary["A"]["mean_recall"]
    p_a_vs_g = binomial_p(
        sum(1 for v in arm_recalls.get("A", []) if v >= a_mean),
        len(arm_recalls.get("A", [])),
        summary["G"]["mean_recall"] if arm_recalls.get("G") else 0.5,
    )
    p_a_vs_n = binomial_p(
        sum(1 for v in arm_recalls.get("A", []) if v >= a_mean),
        len(arm_recalls.get("A", [])),
        summary["N"]["mean_recall"] if arm_recalls.get("N") else 0.5,
    )

    report = {
        "header": {
            "report": "KSR-EVAL Phase 6 — C2 at the geometry level",
            "date": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
            "model": args.model,
            "ksr_version": registry.get("ksr_version"),
            "registry_sha256": sha,
            "corpus_sha256": header.get("corpus_sha256"),
            "spec_version": "v0.3",
            "attempted": len(trials),
            "completed": len([t for t in trial_results if "error" not in t]),
        },
        "summary": summary,
        "p_values": {
            "A_vs_G": p_a_vs_g,
            "A_vs_N": p_a_vs_n,
        },
        "interpretation": (
            "specificity_in_geometry" if summary.get("G", {}).get("mean_recall", 1.0) < summary.get("A", {}).get("mean_recall", 0.0)
            else "no_specificity"
        ),
        "raw_trials": trial_results,
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    return 0


def build_geometry_shuffled(registry: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Shuffle lattice bridge edges while keeping names intact."""
    reg = copy.deepcopy(registry)
    lat = reg.setdefault("lattice_registry", {})
    edges = lat.get("bridge_edges", [])
    if not edges:
        return reg
    targets = [e.get("to") for e in edges]
    rng.shuffle(targets)
    for e, t in zip(edges, targets):
        e["to"] = t
    # Also shuffle dual complements in corner_map.
    corners = list(lat.get("corner_map", {}).values())
    comps = [c.get("dual_complement") for c in corners]
    rng.shuffle(comps)
    for c, comp in zip(corners, comps):
        c["dual_complement"] = comp
    return reg


def build_names_shuffled(registry: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Shuffle prime→name assignments while keeping geometry intact."""
    reg = copy.deepcopy(registry)
    pr = reg.get("prime_registry", {})
    names = [v.get("name") for v in pr.values() if v.get("name")]
    rng.shuffle(names)
    for (p, info), name in zip(pr.items(), names):
        info["name"] = name
    # Also shuffle metric_prime_map Eq key names superficially (not the primes).
    return reg


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))
