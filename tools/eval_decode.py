#!/usr/bin/env python3
"""
KSR-EVAL Phase 2 — decode evaluation on novel corpus.

Runs scripted raw API decode trials across four arms:
  A: full registry
  B: minimal slice
  C: shuffled codebook
  D: famous-text control

Metrics:
  - node-recall: fraction of encoded concept nodes recovered
  - cosine similarity: original vs reconstructed text (local all-MiniLM-L6-v2)

Usage:
    OPENROUTER_API_KEY=... python3 tools/eval_decode.py --corpus eval/corpus/novel_v0.1.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from openai import AsyncOpenAI

# Use local sentence-transformers for cosine metric.
try:
    from sentence_transformers import SentenceTransformer
except ImportError as exc:  # pragma: no cover
    print("sentence-transformers is required; install with: pip install sentence-transformers", file=sys.stderr)
    raise SystemExit(2) from exc

from encode import DEFAULT_REGISTRY, build_alphabet, encode_concepts, load_registry, registry_sha256


OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemma-4-9b-a4b-it"
COST_CAP_CALLS = 1200

FAMOUS_TEXTS = [
    "Imagine there's no heaven, it's easy if you try.",
    "To be or not to be, that is the question.",
    "All animals are equal, but some animals are more equal than others.",
    "In the beginning God created the heaven and the earth.",
    "We hold these truths to be self-evident, that all men are created equal.",
    "It was the best of times, it was the worst of times.",
    "I think, therefore I am.",
    "The only thing we have to fear is fear itself.",
    "That's one small step for man, one giant leap for mankind.",
    "The unexamined life is not worth living.",
    "E = mc^2",
    "May the Force be with you.",
    "A rose by any other name would smell as sweet.",
    "We shall fight on the beaches, we shall fight on the landing grounds.",
    "Houston, we have a problem.",
]


def load_corpus(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    header = json.loads(lines[0])
    records = [json.loads(line) for line in lines[1:]]
    return header, records


def build_minimal_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal registry slice: core semantic layers only."""
    minimal: dict[str, Any] = {
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
    return minimal


def build_shuffled_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Return a registry with the same structure but shuffled prime assignments."""
    shuffled = json.loads(json.dumps(registry))
    primes = list(shuffled.get("prime_registry", {}).keys())
    if len(primes) < 2:
        return shuffled
    rng = random.Random(254)
    shuffled_primes = primes[:]
    rng.shuffle(shuffled_primes)
    prime_map = {int(old): int(new) for old, new in zip(primes, shuffled_primes)}

    def remap_prime(obj: Any) -> Any:
        if isinstance(obj, dict):
            new_obj = {}
            for k, v in obj.items():
                if k in ("structural_prime", "prime", "law_prime", "grace_prime") and isinstance(v, int):
                    new_obj[k] = prime_map.get(v, v)
                elif k == "metric_prime_map" and isinstance(v, dict):
                    new_obj[k] = {ek: prime_map.get(int(ev), ev) for ek, ev in v.items()}
                else:
                    new_obj[k] = remap_prime(v)
            return new_obj
        if isinstance(obj, list):
            return [remap_prime(item) for item in obj]
        return obj

    return remap_prime(shuffled)


def registry_to_text(registry: dict[str, Any], max_chars: int = 8000) -> str:
    """Compact YAML-like text representation of a registry slice."""
    # Serialize minimal subset of keys to keep prompt size reasonable.
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
    lines.append("face_centers:")
    for f in lat.get("face_centers", []):
        lines.append(f"  {f.get('coordinate')}: prime={f.get('prime')} element={f.get('element')}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text


def format_prompt(encoded_number: int, registry_text: str, arm: str) -> str:
    return (
        "You are a deterministic decoder for the Kernel Semantic Registry (KSR).\n"
        "A sentence was encoded as a product of prime powers. Your task is to recover\n"
        "the original KSR concepts and reconstruct the original sentence.\n\n"
        f"Arm: {arm}\n"
        f"Encoded number: {encoded_number}\n\n"
        "Registry slice:\n"
        "---\n"
        f"{registry_text}\n"
        "---\n\n"
        "Respond in this exact JSON format (no markdown, no explanation):\n"
        '{"concepts": ["concept1", "concept2", ...], "reconstructed_text": "..."}'
    )


def parse_response(text: str) -> dict[str, Any]:
    """Extract JSON from model response."""
    # Strip markdown fences.
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    # Find first JSON object.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"concepts": [], "reconstructed_text": text}


def compute_node_recall(original: list[str], reconstructed: list[str], alphabet: dict[str, int]) -> float:
    """Recall of original concept nodes, allowing alias matches."""
    if not original:
        return 0.0
    orig_primes = {alphabet.get(c, alphabet.get(c.lower())) for c in original}
    orig_primes.discard(None)
    recon_primes = {alphabet.get(c, alphabet.get(c.lower())) for c in reconstructed}
    recon_primes.discard(None)
    if not orig_primes:
        return 0.0
    return len(orig_primes & recon_primes) / len(orig_primes)


def cosine_similarity(a, b):
    from numpy import dot
    from numpy.linalg import norm
    if norm(a) == 0 or norm(b) == 0:
        return 0.0
    return float(dot(a, b) / (norm(a) * norm(b)))


async def run_trial(
    client: AsyncOpenAI,
    model: str,
    semaphore: asyncio.Semaphore,
    record: dict[str, Any],
    encoded_number: int,
    registry_text: str,
    arm: str,
    replicate: int,
    call_counter: dict[str, int],
) -> dict[str, Any]:
    async with semaphore:
        if call_counter["calls"] >= COST_CAP_CALLS:
            return {
                "id": record["id"],
                "arm": arm,
                "replicate": replicate,
                "error": "cost_cap_reached",
            }
        call_counter["calls"] += 1
        prompt = format_prompt(encoded_number, registry_text, arm)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=256,
            )
            raw = response.choices[0].message.content or ""
            parsed = parse_response(raw)
            return {
                "id": record["id"],
                "arm": arm,
                "replicate": replicate,
                "prompt": prompt,
                "raw_response": raw,
                "reconstructed_concepts": parsed.get("concepts", []),
                "reconstructed_text": parsed.get("reconstructed_text", ""),
            }
        except Exception as exc:
            return {
                "id": record["id"],
                "arm": arm,
                "replicate": replicate,
                "error": str(exc),
            }


async def main() -> int:
    parser = argparse.ArgumentParser(description="KSR Phase 2 decode evaluation")
    parser.add_argument("--corpus", type=Path, default=Path("eval/corpus/novel_v0.1.jsonl"))
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--arms", nargs="+", default=["A", "B", "C", "D"], choices=["A", "B", "C", "D"])
    parser.add_argument("--replicates", type=int, default=2)
    parser.add_argument("--max-concurrent", type=int, default=8)
    parser.add_argument("--output", type=Path, help="Report JSON path")
    parser.add_argument("--sample", type=int, help="Run on first N items only")
    args = parser.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("error: OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    client = AsyncOpenAI(base_url=OPENROUTER_BASE, api_key=api_key)

    registry = load_registry(args.registry)
    sha = registry_sha256(args.registry)
    alphabet = build_alphabet(registry)

    header, records = load_corpus(args.corpus)
    if args.sample:
        records = records[:args.sample]

    # Build registry slices.
    slices = {
        "A": registry,
        "B": build_minimal_registry(registry),
        "C": build_shuffled_registry(registry),
    }
    registry_texts = {arm: registry_to_text(slices[arm]) for arm in ["A", "B", "C"]}

    # Encode corpus items.
    encoded = {}
    for record in records:
        enc = encode_concepts(record["encode_seed"], alphabet, registry)
        encoded[record["id"]] = enc["number"]

    # Build trial list.
    trials = []
    call_counter = {"calls": 0}
    semaphore = asyncio.Semaphore(args.max_concurrent)

    for record in records:
        for arm in args.arms:
            if arm == "D":
                # Control arm: decode famous texts instead of corpus numbers.
                for rep in range(args.replicates):
                    # Use a fixed prime product as a nonsense encoding.
                    control_number = 2 * 3 * 5 * 7
                    trials.append(run_trial(
                        client, args.model, semaphore,
                        record, control_number, registry_texts["A"], arm, rep, call_counter
                    ))
            else:
                for rep in range(args.replicates):
                    trials.append(run_trial(
                        client, args.model, semaphore,
                        record, encoded[record["id"]], registry_texts[arm], arm, rep, call_counter
                    ))

    print(f"Running {len(trials)} trials (cap: {COST_CAP_CALLS})...")
    start_time = time.time()
    trial_results = await asyncio.gather(*trials)
    elapsed = time.time() - start_time
    print(f"Completed {len(trial_results)} trials in {elapsed:.1f}s")

    # Load embedding model.
    print("Loading embedding model all-MiniLM-L6-v2...")
    embedder = SentenceTransformer("all-MiniLM-L6-v2")

    # Compute metrics.
    original_texts = {r["id"]: r["text"] for r in records}
    original_concepts = {r["id"]: r["encode_seed"] for r in records}

    arm_metrics: dict[str, list[dict]] = defaultdict(list)
    for tr in trial_results:
        if "error" in tr:
            arm_metrics[tr["arm"]].append({"id": tr["id"], "error": tr["error"]})
            continue
        rid = tr["id"]
        recon_concepts = tr.get("reconstructed_concepts", [])
        recon_text = tr.get("reconstructed_text", "")
        recall = compute_node_recall(original_concepts[rid], recon_concepts, alphabet)

        # Cosine similarity between original and reconstructed text.
        try:
            embeddings = embedder.encode([original_texts[rid], recon_text], convert_to_numpy=True)
            cos = cosine_similarity(embeddings[0], embeddings[1])
        except Exception:
            cos = 0.0

        stratum = next((r["stratum"] for r in records if r["id"] == rid), "unknown")
        arm_metrics[tr["arm"]].append({
            "id": rid,
            "stratum": stratum,
            "replicate": tr["replicate"],
            "node_recall": recall,
            "cosine_similarity": cos,
            "reconstructed_text": recon_text,
            "reconstructed_concepts": recon_concepts,
        })

    # Aggregate.
    def aggregate(metrics: list[dict]) -> dict[str, Any]:
        valid = [m for m in metrics if "error" not in m]
        if not valid:
            return {"count": 0, "node_recall_mean": 0.0, "cosine_mean": 0.0}
        return {
            "count": len(valid),
            "node_recall_mean": sum(m["node_recall"] for m in valid) / len(valid),
            "cosine_mean": sum(m["cosine_similarity"] for m in valid) / len(valid),
            "node_recall_gte_0_90": sum(1 for m in valid if m["node_recall"] >= 0.90) / len(valid),
            "cosine_gte_0_85": sum(1 for m in valid if m["cosine_similarity"] >= 0.85) / len(valid),
        }

    summary = {
        "overall": {arm: aggregate(metrics) for arm, metrics in arm_metrics.items()},
        "by_stratum": {},
    }
    for arm, metrics in arm_metrics.items():
        by_stratum = defaultdict(list)
        for m in metrics:
            if "error" not in m:
                by_stratum[m["stratum"]].append(m)
        summary["by_stratum"][arm] = {s: aggregate(ms) for s, ms in by_stratum.items()}

    report = {
        "header": {
            "report": "KSR-EVAL Phase 2 — decode evaluation",
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": args.model,
            "ksr_version": registry.get("ksr_version"),
            "registry_sha256": sha,
            "corpus_sha256": header.get("corpus_sha256"),
            "calls_made": call_counter["calls"],
            "cost_cap": COST_CAP_CALLS,
        },
        "gate": {
            "C1_node_recall_gte_0_90": summary["overall"].get("A", {}).get("node_recall_mean", 0.0) >= 0.90,
            "C1_cosine_gte_0_85": summary["overall"].get("A", {}).get("cosine_mean", 0.0) >= 0.85,
            "C2_shuffled_lt_full": summary["overall"].get("C", {}).get("node_recall_mean", 1.0) < summary["overall"].get("A", {}).get("node_recall_mean", 0.0),
        },
        "summary": summary,
        "raw_trials": trial_results,
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        report_dir = Path(f"eval/reports/{time.strftime('%Y%m%d')}_{sha[:12]}")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "phase2_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {report_path}")

    print("\nSummary:")
    print(json.dumps(summary["overall"], indent=2))
    print("\nGates:")
    print(json.dumps(report["gate"], indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
