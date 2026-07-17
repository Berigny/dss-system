#!/usr/bin/env python3
"""
KSR-EVAL Phase 2 v0.2 — semantic decode evaluation.

Pipeline:
  1. Script factorizes the encoded number (deterministic).
  2. Model receives the prime factors and registry slice; it maps factors to
     KSR concepts and writes a grammatical English sentence.
  3. Metrics are scored against the encoded factor set, not the original seed.

Arms:
  A — full registry
  B — minimal slice
  C — shuffled codebook

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

try:
    from sentence_transformers import SentenceTransformer
except ImportError as exc:  # pragma: no cover
    print("sentence-transformers is required", file=sys.stderr)
    raise SystemExit(2) from exc

from encode import DEFAULT_REGISTRY, build_alphabet, encode_concepts, load_registry, registry_sha256


OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "moonshotai/kimi-k3"
COST_CAP_CALLS = 1200


class AsyncRateLimiter:
    """Simple token-bucket rate limiter for API requests."""

    def __init__(self, rpm: float | None):
        self.rpm = rpm
        self.min_interval = 60.0 / rpm if rpm else 0.0
        self._last_release = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if not self.rpm:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._last_release + self.min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._last_release = now


def load_corpus(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    header = json.loads(lines[0])
    records = [json.loads(line) for line in lines[1:]]
    return header, records


def build_minimal_registry(registry: dict[str, Any]) -> dict[str, Any]:
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


def prime_to_concepts(registry: dict[str, Any]) -> dict[int, set[str]]:
    """Build reverse map from prime to canonical concept names."""
    prime_to_concepts: dict[int, set[str]] = defaultdict(set)

    # Eq nodes via metric_prime_map.
    for eq_key, prime in registry.get("flow_topology", {}).get("metric_prime_map", {}).items():
        p = int(prime)
        prime_to_concepts[p].add(eq_key)
        digit = registry.get("digit_registry", {}).get(eq_key, {})
        if digit.get("symbol"):
            prime_to_concepts[p].add(digit["symbol"])
        for alias in digit.get("aliases", []):
            prime_to_concepts[p].add(alias)

    # Corner map.
    for coord, info in registry.get("lattice_registry", {}).get("corner_map", {}).items():
        p = int(info.get("structural_prime"))
        prime_to_concepts[p].update([coord, info.get("kernel"), info.get("hebrew_letter")])

    # Face centers.
    for face in registry.get("lattice_registry", {}).get("face_centers", []):
        p = int(face.get("prime"))
        prime_to_concepts[p].update([face.get("coordinate"), face.get("letter")])

    # Reset node.
    reset = registry.get("lattice_registry", {}).get("reset_node", {})
    if reset:
        p = int(reset.get("structural_prime"))
        prime_to_concepts[p].update([reset.get("coordinate"), reset.get("kernel")])

    # Prime registry.
    for prime_val, info in registry.get("prime_registry", {}).items():
        p = int(prime_val)
        for key in ("name", "mnemonic", "engineering_dimension"):
            if info.get(key):
                prime_to_concepts[p].add(info[key])

    # Quaternary gate dimension labels.
    for gname, ginfo in registry.get("quaternary_gate_registry", {}).get("gates", {}).items():
        p = int(ginfo.get("prime"))
        prime_to_concepts[p].update([gname, gname.lower()])

    return {p: names for p, names in prime_to_concepts.items() if p > 1}


def registry_to_text(registry: dict[str, Any], max_chars: int = 8000) -> str:
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


def format_prompt(factors: dict[int, int], registry_text: str, arm: str) -> str:
    factor_list = ", ".join(f"{p}^{e}" if e > 1 else str(p) for p, e in sorted(factors.items()))
    product = 1
    for p, e in factors.items():
        product *= p ** e
    return (
        "You are a semantic decoder for the Kernel Semantic Registry (KSR).\n\n"
        "A sentence has already been factorized by a script. Your job is ONLY the\n"
        "semantic step: map each prime factor to its KSR concept, then write a\n"
        "grammatical English sentence that the concepts evoke.\n\n"
        f"Arm: {arm}\n"
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


def parse_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"concepts": [], "reconstructed_text": text}


def is_grammatical_sentence(text: str) -> bool:
    """Heuristic: contains a verb and ends with punctuation, not just noun phrases."""
    text = text.strip()
    if not text:
        return False
    if not re.search(r"[.!?]$", text):
        return False
    # Reject if it looks like a list of concept names (no spaces between capitalized words).
    if re.match(r"^([A-Z][a-zA-Z]*\s*)+$", text.rstrip(".!?")):
        return False
    # Reject if every word is capitalized (title-case concept list).
    words = re.findall(r"[A-Za-z]+", text)
    if words and all(w[0].isupper() for w in words):
        return False
    return True


def compute_metrics(
    encoded_factors: dict[int, int],
    reconstructed_concepts: list[str],
    original_text: str,
    reconstructed_text: str,
    alphabet: dict[str, int],
    embedder: SentenceTransformer,
) -> dict[str, Any]:
    """Score against the encoded factor set (ground truth)."""
    ground_truth_primes = set(encoded_factors.keys())
    recon_primes = {alphabet.get(c, alphabet.get(c.lower())) for c in reconstructed_concepts}
    recon_primes.discard(None)

    tp = len(ground_truth_primes & recon_primes)
    fp = len(recon_primes - ground_truth_primes)
    fn = len(ground_truth_primes - recon_primes)

    node_recall = tp / len(ground_truth_primes) if ground_truth_primes else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = (2 * precision * node_recall / (precision + node_recall)) if (precision + node_recall) else 0.0

    grammatical = is_grammatical_sentence(reconstructed_text)
    try:
        embeddings = embedder.encode([original_text, reconstructed_text], convert_to_numpy=True)
        from numpy import dot
        from numpy.linalg import norm
        cos = float(dot(embeddings[0], embeddings[1]) / (norm(embeddings[0]) * norm(embeddings[1]))) if norm(embeddings[0]) and norm(embeddings[1]) else 0.0
    except Exception:
        cos = 0.0

    return {
        "node_recall": node_recall,
        "precision": precision,
        "f1": f1,
        "cosine_similarity": cos,
        "grammatical": grammatical,
        "ground_truth_primes": sorted(ground_truth_primes),
        "reconstructed_primes": sorted(recon_primes),
    }


async def run_trial(
    client: AsyncOpenAI,
    model: str,
    semaphore: asyncio.Semaphore,
    rate_limiter: AsyncRateLimiter,
    record: dict[str, Any],
    factors: dict[int, int],
    registry_text: str,
    arm: str,
    replicate: int,
    call_counter: dict[str, int],
) -> dict[str, Any]:
    async with semaphore:
        await rate_limiter.acquire()
        if call_counter["calls"] >= COST_CAP_CALLS:
            return {"id": record["id"], "arm": arm, "replicate": replicate, "error": "cost_cap_reached"}
        call_counter["calls"] += 1
        prompt = format_prompt(factors, registry_text, arm)
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
            return {"id": record["id"], "arm": arm, "replicate": replicate, "error": str(exc)}


async def main() -> int:
    parser = argparse.ArgumentParser(description="KSR Phase 2 v0.2 semantic decode evaluation")
    parser.add_argument("--corpus", type=Path, default=Path("eval/corpus/novel_v0.1.jsonl"))
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--arms", nargs="+", default=["A", "B", "C"], choices=["A", "B", "C"])
    parser.add_argument("--replicates", type=int, default=2)
    parser.add_argument("--max-concurrent", type=int, default=8)
    parser.add_argument("--rate-limit-rpm", type=float, default=18.0, help="Max API requests per minute (0 to disable)")
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
    prime_to_concepts_map = prime_to_concepts(registry)

    header, records = load_corpus(args.corpus)
    if args.sample:
        records = records[:args.sample]

    slices = {
        "A": registry,
        "B": build_minimal_registry(registry),
        "C": build_shuffled_registry(registry),
    }
    registry_texts = {arm: registry_to_text(slices[arm]) for arm in args.arms}

    # Encode corpus items and capture the actual factor set used.
    encoded: dict[str, dict[str, Any]] = {}
    for record in records:
        enc = encode_concepts(record["encode_seed"], alphabet, registry)
        encoded[record["id"]] = {
            "number": enc["number"],
            "factors": factorize(enc["number"]),
            "resolved_concepts": enc["resolved"],
            "unknown_seed_concepts": enc["unknown"],
        }

    trials = []
    call_counter = {"calls": 0}
    semaphore = asyncio.Semaphore(args.max_concurrent)
    rate_limiter = AsyncRateLimiter(rpm=args.rate_limit_rpm if args.rate_limit_rpm > 0 else None)

    for record in records:
        for arm in args.arms:
            for rep in range(args.replicates):
                trials.append(run_trial(
                    client, args.model, semaphore, rate_limiter,
                    record, encoded[record["id"]]["factors"], registry_texts[arm], arm, rep, call_counter
                ))

    print(f"Running {len(trials)} trials (cap: {COST_CAP_CALLS})...")
    start_time = time.time()
    trial_results = await asyncio.gather(*trials)
    elapsed = time.time() - start_time
    print(f"Completed {len(trial_results)} trials in {elapsed:.1f}s")

    print("Loading embedding model all-MiniLM-L6-v2...")
    embedder = SentenceTransformer("all-MiniLM-L6-v2")

    original_texts = {r["id"]: r["text"] for r in records}

    arm_metrics: dict[str, list[dict]] = defaultdict(list)
    for tr in trial_results:
        if "error" in tr:
            arm_metrics[tr["arm"]].append({"id": tr["id"], "error": tr["error"]})
            continue
        rid = tr["id"]
        factors = encoded[rid]["factors"]
        metrics = compute_metrics(
            factors,
            tr.get("reconstructed_concepts", []),
            original_texts[rid],
            tr.get("reconstructed_text", ""),
            alphabet,
            embedder,
        )
        stratum = next((r["stratum"] for r in records if r["id"] == rid), "unknown")
        arm_metrics[tr["arm"]].append({
            "id": rid,
            "stratum": stratum,
            "replicate": tr["replicate"],
            **metrics,
            "reconstructed_text": tr.get("reconstructed_text", ""),
            "reconstructed_concepts": tr.get("reconstructed_concepts", []),
        })

    def aggregate(metrics: list[dict]) -> dict[str, Any]:
        valid = [m for m in metrics if "error" not in m]
        if not valid:
            return {"count": 0, "node_recall_mean": 0.0, "precision_mean": 0.0, "f1_mean": 0.0, "cosine_mean": 0.0, "grammatical_fraction": 0.0}
        return {
            "count": len(valid),
            "node_recall_mean": sum(m["node_recall"] for m in valid) / len(valid),
            "precision_mean": sum(m["precision"] for m in valid) / len(valid),
            "f1_mean": sum(m["f1"] for m in valid) / len(valid),
            "cosine_mean": sum(m["cosine_similarity"] for m in valid) / len(valid),
            "grammatical_fraction": sum(1 for m in valid if m["grammatical"]) / len(valid),
            "node_recall_gte_0_90": sum(1 for m in valid if m["node_recall"] >= 0.90) / len(valid),
            "cosine_gte_0_85": sum(1 for m in valid if m["cosine_similarity"] >= 0.85) / len(valid),
        }

    summary = {"overall": {arm: aggregate(metrics) for arm, metrics in arm_metrics.items()}}
    summary["by_stratum"] = {}
    for arm, metrics in arm_metrics.items():
        by_stratum = defaultdict(list)
        for m in metrics:
            if "error" not in m:
                by_stratum[m["stratum"]].append(m)
        summary["by_stratum"][arm] = {s: aggregate(ms) for s, ms in by_stratum.items()}

    # Encoder coverage: fraction of seed concepts that are in the KSR alphabet.
    seed_coverage = []
    for record in records:
        known = sum(1 for c in record["encode_seed"] if c in alphabet or c.lower() in alphabet)
        seed_coverage.append(known / len(record["encode_seed"]) if record["encode_seed"] else 0.0)

    report = {
        "header": {
            "report": "KSR-EVAL Phase 2 v0.2 — semantic decode evaluation",
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
            "C1_precision_gte_0_90": summary["overall"].get("A", {}).get("precision_mean", 0.0) >= 0.90,
            "C1_f1_gte_0_90": summary["overall"].get("A", {}).get("f1_mean", 0.0) >= 0.90,
            "C1_cosine_gte_0_85": summary["overall"].get("A", {}).get("cosine_mean", 0.0) >= 0.85,
            "C1_grammatical_fraction_gte_0_90": summary["overall"].get("A", {}).get("grammatical_fraction", 0.0) >= 0.90,
            "C2_shuffled_lt_full": summary["overall"].get("C", {}).get("node_recall_mean", 1.0) < summary["overall"].get("A", {}).get("node_recall_mean", 0.0),
        },
        "encoder_coverage": {
            "mean_seed_coverage": sum(seed_coverage) / len(seed_coverage) if seed_coverage else 0.0,
            "per_item": {record["id"]: cov for record, cov in zip(records, seed_coverage)},
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
        report_path = report_dir / "phase2_report_v0.2.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report written to {report_path}")

    print("\nSummary:")
    print(json.dumps(summary["overall"], indent=2))
    print("\nGates:")
    print(json.dumps(report["gate"], indent=2))
    print(f"\nEncoder coverage (mean): {report['encoder_coverage']['mean_seed_coverage']:.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
