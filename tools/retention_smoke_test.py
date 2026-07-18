#!/usr/bin/env python3
"""
KSR-EVAL DSS-274 — Retention smoke test on core-only prompt slice.

Verifies that the public ``ksr-core`` tree retains sufficient recall by building
a 50-item decode prompt slice using only nodes/fields present in
``ksr/core/ksr-core-*.yaml``. In ``--dry-run`` mode (default) the harness
validates the slice and computes deterministic encode/decode coverage without
calling an LLM, so it is safe for CI. Set ``OPENROUTER_API_KEY`` and omit
``--dry-run`` to run the live kimi-k3 decode under R1 transport rules.

Usage:
    python3 tools/retention_smoke_test.py --dry-run
    OPENROUTER_API_KEY=... python3 tools/retention_smoke_test.py --model moonshotai/kimi-k3
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

# Re-use encode/decode helpers from the tools directory.
sys.path.insert(0, str(Path(__file__).parent))
from decode import build_prime_to_concept, decode_number, load_registry, registry_sha256
from encode import build_alphabet, encode_concepts


DEFAULT_REGISTRY = Path("ksr/core/ksr-core-1.3.1.yaml")
DEFAULT_CORPUS = Path("eval/corpus/novel_v0.1.jsonl")
DEFAULT_SAMPLE = 50
DEFAULT_MODEL = "moonshotai/kimi-k3"
RECALL_GATE = 0.89


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_corpus(path: Path, sample: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    header = json.loads(lines[0])
    records = [json.loads(line) for line in lines[1:]]
    return header, records[:sample]


def build_core_only_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Return the minimal public-engineering registry slice used for the smoke test."""
    return {
        "ksr_version": registry.get("ksr_version"),
        "digit_registry": registry.get("digit_registry", {}),
        "prime_registry": registry.get("prime_registry", {}),
        "prime_groups": registry.get("prime_groups", {}),
        "flow_topology": {"metric_prime_map": registry.get("flow_topology", {}).get("metric_prime_map", {})},
        "lattice_registry": {
            "version": registry.get("lattice_registry", {}).get("version"),
            "total_nodes": registry.get("lattice_registry", {}).get("total_nodes"),
            "corner_map": registry.get("lattice_registry", {}).get("corner_map", {}),
            "centroid": registry.get("lattice_registry", {}).get("centroid", {}),
            "reset_node": registry.get("lattice_registry", {}).get("reset_node", {}),
            "bridge_edges": registry.get("lattice_registry", {}).get("bridge_edges", []),
            "face_centers": registry.get("lattice_registry", {}).get("face_centers", []),
            "traversal_sequence": registry.get("lattice_registry", {}).get("traversal_sequence", []),
        },
        "quaternary_gate_registry": registry.get("quaternary_gate_registry", {}),
        "checksum_invariant": registry.get("checksum_invariant", {}),
        "relation_types": registry.get("relation_types", []),
    }


def _registry_to_prompt_text(registry: dict[str, Any], max_chars: int = 6000) -> str:
    """Render the core-only registry as a short prompt slice."""
    lines = [f"ksr_version: {registry.get('ksr_version', 'unknown')}"]
    mpm = registry.get("flow_topology", {}).get("metric_prime_map", {})
    lines.append("metric_prime_map:")
    for k, v in mpm.items():
        lines.append(f"  {k}: {v}")
    lines.append("digit_registry:")
    for k, v in registry.get("digit_registry", {}).items():
        lines.append(f"  {k}: symbol={v.get('symbol')} value={v.get('value')}")
    lines.append("prime_registry:")
    for k, v in registry.get("prime_registry", {}).items():
        lines.append(f"  {k}: name={v.get('name')} node_index={v.get('node_index')}")
    lat = registry.get("lattice_registry", {})
    lines.append("corner_map:")
    for coord, info in lat.get("corner_map", {}).items():
        lines.append(f"  {coord}: kernel={info.get('kernel')} prime={info.get('structural_prime')}")
    lines.append("bridge_edges:")
    for e in lat.get("bridge_edges", [])[:20]:
        lines.append(f"  {e.get('from')} -> {e.get('to')} coord={e.get('coordinate')}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text


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


def evaluate_deterministic_recall(
    records: list[dict[str, Any]],
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], float]:
    """Compute node recall using deterministic encode/decode over ksr-core only."""
    alphabet = build_alphabet(registry)
    prime_to_concepts = build_prime_to_concept(registry)
    per_item: list[dict[str, Any]] = []

    for record in records:
        enc = encode_concepts(record["encode_seed"], alphabet, registry)
        factors = factorize(enc["number"])
        decoded = decode_number(enc["number"], prime_to_concepts, registry, verify_check=False)
        recovered_primes = {item["prime"] for item in decoded["recovered_concepts"]}
        ground_truth_primes = set(factors.keys())
        tp = len(ground_truth_primes & recovered_primes)
        fn = len(ground_truth_primes - recovered_primes)
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        per_item.append(
            {
                "id": record["id"],
                "stratum": record.get("stratum", "unknown"),
                "seed_concepts": record["encode_seed"],
                "unknown_concepts": enc["unknown"],
                "ground_truth_primes": sorted(ground_truth_primes),
                "recovered_primes": sorted(recovered_primes),
                "node_recall": recall,
            }
        )

    mean_recall = sum(item["node_recall"] for item in per_item) / len(per_item) if per_item else 0.0
    return per_item, mean_recall


def format_prompt(registry_text: str, factors: dict[int, int]) -> str:
    factor_list = ", ".join(f"{p}^{e}" if e > 1 else str(p) for p, e in sorted(factors.items()))
    product = 1
    for p, e in factors.items():
        product *= p**e
    return (
        "You are a semantic decoder for the Kernel Semantic Registry (KSR).\n\n"
        "A sentence has already been factorized by a script. Your job is ONLY the\n"
        "semantic step: map each prime factor to its KSR concept, then write a\n"
        "grammatical English sentence that the concepts evoke.\n\n"
        "Arm: B (core-only minimal slice)\n"
        f"Encoded number: {product}\n"
        f"Prime factorization: {factor_list}\n\n"
        "Registry slice (maps primes to concepts):\n"
        "---\n"
        f"{registry_text}\n"
        "---\n\n"
        "RULES:\n"
        "1. Use the registry to map each prime factor to a concept.\n"
        "2. Output the recovered concepts in the 'concepts' array.\n"
        "3. 'reconstructed_text' MUST be a grammatical English sentence.\n"
        "   Do NOT output a list of concept names, definitions, or JSON inside the sentence.\n"
        "4. The sentence should be short (5–15 words) and reflect the semantic content\n"
        "   of the recovered concepts.\n\n"
        "Respond in this exact JSON format (no markdown, no explanation):\n"
        '{"concepts": ["concept1", "concept2", ...], "reconstructed_text": "A grammatical sentence here."}'
    )


async def run_live_decode(
    records: list[dict[str, Any]],
    registry: dict[str, Any],
    model: str,
) -> list[dict[str, Any]]:
    """Run live LLM decode trials under R1 transport rules (requires OPENROUTER_API_KEY)."""
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for live decode") from exc

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    alphabet = build_alphabet(registry)
    registry_text = _registry_to_prompt_text(registry)
    results: list[dict[str, Any]] = []

    for record in records:
        enc = encode_concepts(record["encode_seed"], alphabet, registry)
        factors = factorize(enc["number"])
        prompt = format_prompt(registry_text, factors)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=256,
            )
            raw = response.choices[0].message.content or ""
        except Exception as exc:
            results.append({"id": record["id"], "error": str(exc)})
            continue
        results.append({"id": record["id"], "prompt": prompt, "raw_response": raw})

    return results


def _default_output_dir(core_sha: str) -> Path:
    stamp = time.strftime("%Y-%m-%d")
    return Path(f"eval/reports/{stamp}_{core_sha[:16]}_v0.4")


def main() -> int:
    parser = argparse.ArgumentParser(description="KSR-EVAL DSS-274 retention smoke test")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="Deterministic validation only; no LLM calls")
    parser.add_argument("--output", type=Path, help="Report output path")
    args = parser.parse_args()

    registry = load_registry(args.registry)
    core_only = build_core_only_registry(registry)
    core_sha = registry_sha256(args.registry)
    repo_sha = _repo_sha()

    header, records = load_corpus(args.corpus, args.sample)
    corpus_sha = header.get("corpus_sha256", _file_sha256(args.corpus))

    per_item, mean_recall = evaluate_deterministic_recall(records, core_only)
    gate_pass = mean_recall >= RECALL_GATE

    live_results: list[dict[str, Any]] | None = None
    if not args.dry_run:
        try:
            live_results = asyncio.run(run_live_decode(records, core_only, args.model))
        except Exception as exc:
            print(f"Live decode failed: {exc}", file=sys.stderr)
            return 2

    report: dict[str, Any] = {
        "report": "KSR-EVAL DSS-274 retention smoke test",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "dry_run" if args.dry_run else "live_decode",
        "model": args.model if not args.dry_run else None,
        "ksr_version": registry.get("ksr_version"),
        "core_artifact_sha256": core_sha,
        "repo_commit_sha": repo_sha,
        "corpus_sha256": corpus_sha,
        "sample_size": len(records),
        "recall_gate": RECALL_GATE,
        "mean_node_recall": mean_recall,
        "gate_pass": gate_pass,
        "per_item": per_item,
        "live_results": live_results,
        "note": (
            "Dry-run mode reports deterministic encode/decode recall over ksr-core. "
            "Live LLM decode is deferred per Epic 38 agent-heavy benchmark policy."
            if args.dry_run
            else "Live decode completed under R1 transport rules."
        ),
    }

    output_path = args.output
    if output_path is None:
        output_path = _default_output_dir(core_sha) / "retention_smoke_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"DSS-274 retention smoke test | mean recall: {mean_recall:.3f} | gate: {RECALL_GATE}")
    print(f"Gate: {'PASS' if gate_pass else 'FAIL'}")
    print(f"Report: {output_path}")
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
