#!/usr/bin/env python3
"""
Phase R retry — refill empty raw_response trials in phase2_report_v0.2.json
using the delegated Kimi Code chat-surface path, then recompute aggregates.

Usage:
    DSS_SESSION_TOKEN=... DSS_REFRESH_TOKEN=... \
        .venv-ksr/bin/python tools/phase2_retry_empty.py

The script only re-runs trials whose raw_response is empty, preserves every
answered trial, and updates arm/stratum aggregates by replacing the previous
zero contributions with the new scores.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib import error, request


try:
    from sentence_transformers import SentenceTransformer
except Exception as exc:  # pragma: no cover
    print("sentence-transformers is required for cosine similarity", file=sys.stderr)
    raise SystemExit(2) from exc


DEFAULT_REPORT = Path("eval/reports/2026-07-17_6b0fb395_v0.2/phase2_report_v0.2.json")
DEFAULT_CORPUS = Path("eval/corpus/novel_v0.1.jsonl")
DEFAULT_CHAT_BASE_URL = os.getenv("DSS_CHAT_BASE_URL", "https://chat.dualsubstrate.com")
DEFAULT_OPERATOR_DID = os.getenv(
    "DSS_OPERATOR_DID",
    "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
)
DEFAULT_OPERATOR_ID = os.getenv("DSS_OPERATOR_ID", "ops-admin")
DEFAULT_LEDGER_ID = os.getenv("DSS_LEDGER_ID", "loam")
DEFAULT_SURFACE_ID = os.getenv("DSS_SURFACE_ID", "surface:chat:primary")
DEFAULT_MODEL = os.getenv("DSS_PHASE2_MODEL", "moonshotai/kimi-k3")


def _event_text_value(event: dict[str, Any]) -> str:
    if not isinstance(event, dict):
        return ""
    for key in ("text", "content", "delta", "message", "assistant_reply"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("text", "content", "delta", "message", "assistant_reply"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _iter_ndjson_lines(resp) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _refresh_session_token(refresh_token: str, base_url: str, timeout: float = 20.0) -> tuple[str | None, str | None, dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/auth/session/refresh"
    req = request.Request(
        url,
        data=b"{}",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
        },
        method="POST",
    )
    req.add_header("cookie", f"ds_backend_refresh_token={refresh_token}")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read()
            try:
                body = json.loads(raw.decode("utf-8", errors="ignore"))
            except json.JSONDecodeError:
                return None, None, {"status": status, "error": "refresh_non_json_response"}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body[:1000]}
        return None, None, {"status": exc.code, "error": "refresh_failed", "body": parsed}
    except error.URLError as exc:
        return None, None, {"error": "refresh_request_failed", "detail": str(exc.reason)}

    if not isinstance(body, dict):
        return None, None, {"status": status, "error": "refresh_invalid_response", "body": body}

    session = body.get("session") or {}
    refresh_session = body.get("refresh_session") or {}
    new_session_token = str(session.get("token") or "").strip() or None
    new_refresh_token = str(refresh_session.get("token") or "").strip() or None
    if not new_session_token:
        return None, None, {"status": status, "error": "refresh_missing_session_token", "body": body}
    return new_session_token, new_refresh_token, {"status": status, "body": body}


async def _post_chat_smart_stream(
    prompt: str,
    *,
    session_token: str,
    chat_base_url: str,
    operator_did: str,
    operator_id: str,
    ledger_id: str,
    surface_id: str,
    model: str,
    timeout: float = 180.0,
) -> dict[str, Any]:
    session_id = f"phase2-retry-{uuid.uuid4().hex[:12]}"
    request_id = f"phase2-retry-req-{uuid.uuid4().hex}"
    payload = {
        "message": prompt,
        "provider": model,
        "agent": model,
        "model": model,
        "entity": ledger_id,
        "ledger_id": ledger_id,
        "surface_id": surface_id,
        "session_id": session_id,
        "request_id": request_id,
        "enable_ledger": True,
        "history": [],
        "include_pipeline_events": False,
        "prompt_principal_mode": "kimi",
    }
    headers = {
        "content-type": "application/json",
        "accept": "application/x-ndjson, application/json",
        "x-principal-did": operator_did,
        "x-principal-id": operator_id,
        "x-principal-type": "user",
        "cookie": f"ds_backend_session_token={session_token}",
    }
    url = f"{chat_base_url.rstrip('/')}/api/chat/smart_stream"
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status >= 400:
                body = resp.read().decode("utf-8", errors="ignore")
                return {"error": f"http_{status}", "detail": body[:1000]}
            content_type = str(resp.headers.get("content-type") or "")
            if "text/html" in content_type:
                snippet = resp.read(2048).decode("utf-8", errors="ignore")
                title_match = re.search(r"<title>([^<]+)</title>", snippet, re.IGNORECASE)
                return {
                    "error": "auth_failed_html",
                    "detail": "chat surface returned HTML (session token may be invalid/expired)",
                    "page_title": title_match.group(1).strip() if title_match else None,
                }
            if "json" in content_type and "ndjson" not in content_type:
                body = json.loads(resp.read().decode("utf-8", errors="ignore"))
                return {"error": "unexpected_json_response", "detail": body}
            events = _iter_ndjson_lines(resp)
            chunks: list[str] = []
            for event in events:
                etype = str(event.get("type") or "")
                if etype in {"token", "delta", "message"}:
                    chunks.append(_event_text_value(event))
            raw = "".join(chunks).strip()
            parsed = _extract_json_object(raw) if raw else None
            return {
                "raw_response": raw,
                "parsed_response": parsed,
                "event_count": len(events),
                "session_id": session_id,
                "request_id": request_id,
            }
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        return {"error": f"http_{exc.code}", "detail": body[:1000]}
    except error.URLError as exc:
        return {"error": "request_failed", "detail": str(exc.reason)}


def _parse_factorization_from_prompt(prompt: str) -> set[int]:
    match = re.search(r"Prime factorization:\s*([^\n]+)", prompt)
    if not match:
        return set()
    parts = [p.strip() for p in match.group(1).split(",") if p.strip()]
    primes: set[int] = set()
    for part in parts:
        m = re.match(r"(\d+)(?:\^(\d+))?", part)
        if not m:
            continue
        base = int(m.group(1))
        exp = int(m.group(2)) if m.group(2) else 1
        if base > 1:
            primes.add(base)
    return primes


def _build_concept_to_prime_from_prompt(prompt: str) -> dict[str, int]:
    """Best-effort reverse map from the registry slice embedded in the prompt."""
    mapping: dict[str, int] = {}

    # prime_registry: "2: name=Novelty node_index=0"
    for m in re.finditer(r"^\s*(\d+):\s*name=([^\n]+)$", prompt, re.MULTILINE):
        prime = int(m.group(1))
        name = m.group(2).split()[0].strip()
        if name:
            mapping[name.lower()] = prime

    # metric_prime_map: "Eq0: 2"
    for m in re.finditer(r"^\s*(Eq\d+):\s*(\d+)\s*$", prompt, re.MULTILINE):
        eq = m.group(1)
        prime = int(m.group(2))
        mapping[eq.lower()] = prime

    # Build Eq -> prime lookup first so digit symbols resolve correctly.
    eq_to_prime: dict[str, int] = {}
    for m in re.finditer(r"^\s*(Eq\d+):\s*(\d+)\s*$", prompt, re.MULTILINE):
        eq_to_prime[m.group(1).lower()] = int(m.group(2))

    # digit_registry: "Eq0: symbol=ORIGIN value=0"
    for m in re.finditer(r"^\s*(Eq\d+):\s*symbol=([^\s]+)\s+value=\d+", prompt, re.MULTILINE):
        eq = m.group(1).lower()
        symbol = m.group(2).strip()
        prime = eq_to_prime.get(eq)
        if symbol and prime:
            mapping[symbol.lower()] = prime

    # corner_map: "000: kernel=K0 prime=2"
    for m in re.finditer(r"^\s*\d+:\s*kernel=([^\s]+)\s+prime=(\d+)", prompt, re.MULTILINE):
        kernel = m.group(1).strip()
        prime = int(m.group(2))
        if kernel:
            mapping[kernel.lower()] = prime

    # face_centers: "202: prime=5 element=Fire"
    for m in re.finditer(r"^\s*\d+:\s*prime=(\d+)\s+element=([^\s]+)", prompt, re.MULTILINE):
        prime = int(m.group(1))
        element = m.group(2).strip()
        if element:
            mapping[element.lower()] = prime

    return mapping


def _is_grammatical_sentence(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if not re.search(r"[.!?]$", text):
        return False
    if re.match(r"^([A-Z][a-zA-Z]*\s*)+$", text.rstrip(".!?")):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    if words and all(w[0].isupper() for w in words):
        return False
    return True


def _compute_metrics(
    prompt: str,
    concepts: list[str],
    reconstructed_text: str,
    original_text: str,
    embedder: SentenceTransformer,
) -> dict[str, Any]:
    ground_truth_primes = _parse_factorization_from_prompt(prompt)
    concept_to_prime = _build_concept_to_prime_from_prompt(prompt)
    recon_primes: set[int] = set()
    for concept in concepts:
        key = str(concept).strip().lower()
        prime = concept_to_prime.get(key)
        if prime:
            recon_primes.add(prime)

    tp = len(ground_truth_primes & recon_primes)
    fp = len(recon_primes - ground_truth_primes)
    fn = len(ground_truth_primes - recon_primes)

    node_recall = tp / len(ground_truth_primes) if ground_truth_primes else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = (2 * precision * node_recall / (precision + node_recall)) if (precision + node_recall) else 0.0

    grammatical = _is_grammatical_sentence(reconstructed_text)
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


def _load_corpus(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    header = json.loads(lines[0])
    records = [json.loads(line) for line in lines[1:]]
    return header, records


def _arm_stratum_sums(report: dict[str, Any]) -> tuple[dict[str, dict[str, float]], dict[tuple[str, str], dict[str, float]]]:
    """Return mutable sum dictionaries for arm and (arm, stratum) aggregates."""
    arm_sums: dict[str, dict[str, float]] = {}
    for arm, agg in report["summary"]["overall"].items():
        count = agg["count"]
        arm_sums[arm] = {
            "count": count,
            "node_recall": agg["node_recall_mean"] * count,
            "precision": agg["precision_mean"] * count,
            "f1": agg["f1_mean"] * count,
            "cosine": agg["cosine_mean"] * count,
            "grammatical": agg["grammatical_fraction"] * count,
            "node_recall_gte_0_90": agg.get("node_recall_gte_0_90", 0.0) * count,
            "cosine_gte_0_85": agg.get("cosine_gte_0_85", 0.0) * count,
        }

    stratum_sums: dict[tuple[str, str], dict[str, float]] = {}
    for arm, strata in report["summary"]["by_stratum"].items():
        for stratum, agg in strata.items():
            count = agg["count"]
            stratum_sums[(arm, stratum)] = {
                "count": count,
                "node_recall": agg["node_recall_mean"] * count,
                "precision": agg["precision_mean"] * count,
                "f1": agg["f1_mean"] * count,
                "cosine": agg["cosine_mean"] * count,
                "grammatical": agg["grammatical_fraction"] * count,
                "node_recall_gte_0_90": agg.get("node_recall_gte_0_90", 0.0) * count,
                "cosine_gte_0_85": agg.get("cosine_gte_0_85", 0.0) * count,
            }
    return arm_sums, stratum_sums


def _add_metric_sums(sums: dict[str, float], metrics: dict[str, Any]) -> None:
    sums["node_recall"] += metrics["node_recall"]
    sums["precision"] += metrics["precision"]
    sums["f1"] += metrics["f1"]
    sums["cosine"] += metrics["cosine_similarity"]
    sums["grammatical"] += 1.0 if metrics["grammatical"] else 0.0
    sums["node_recall_gte_0_90"] += 1.0 if metrics["node_recall"] >= 0.90 else 0.0
    sums["cosine_gte_0_85"] += 1.0 if metrics["cosine_similarity"] >= 0.85 else 0.0


def _finalize_aggregate(sums: dict[str, float]) -> dict[str, Any]:
    count = sums["count"]
    if not count:
        return {
            "count": 0,
            "node_recall_mean": 0.0,
            "precision_mean": 0.0,
            "f1_mean": 0.0,
            "cosine_mean": 0.0,
            "grammatical_fraction": 0.0,
        }
    return {
        "count": int(count),
        "node_recall_mean": sums["node_recall"] / count,
        "precision_mean": sums["precision"] / count,
        "f1_mean": sums["f1"] / count,
        "cosine_mean": sums["cosine"] / count,
        "grammatical_fraction": sums["grammatical"] / count,
        "node_recall_gte_0_90": sums["node_recall_gte_0_90"] / count,
        "cosine_gte_0_85": sums["cosine_gte_0_85"] / count,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Phase R retry for empty phase2 trials")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--chat-base-url", default=DEFAULT_CHAT_BASE_URL)
    parser.add_argument("--operator-did", default=DEFAULT_OPERATOR_DID)
    parser.add_argument("--operator-id", default=DEFAULT_OPERATOR_ID)
    parser.add_argument("--ledger-id", default=DEFAULT_LEDGER_ID)
    parser.add_argument("--surface-id", default=DEFAULT_SURFACE_ID)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, help="Output path (defaults to in-place update)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between chat-surface calls")
    parser.add_argument("--max-empty", type=int, help="Retry at most N empty trials (for testing)")
    args = parser.parse_args()

    session_token = os.getenv("DSS_SESSION_TOKEN", "").strip()
    refresh_token = os.getenv("DSS_REFRESH_TOKEN", "").strip()
    if not session_token and not refresh_token:
        print("error: DSS_SESSION_TOKEN or DSS_REFRESH_TOKEN required", file=sys.stderr)
        return 2

    if not session_token and refresh_token:
        new_session, new_refresh, info = _refresh_session_token(refresh_token, args.chat_base_url)
        if not new_session:
            print(f"error: failed to refresh session token: {info}", file=sys.stderr)
            return 2
        session_token = new_session
        if new_refresh:
            os.environ["DSS_REFRESH_TOKEN"] = new_refresh
        print(f"Refreshed session token (principal {info.get('principal_did') or '?'})", file=sys.stderr)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    header, records = _load_corpus(args.corpus)
    id_to_record = {r["id"]: r for r in records}
    trials = report["raw_trials"]
    empty_trials = [t for t in trials if not str(t.get("raw_response", "")).strip()]
    if args.max_empty:
        empty_trials = empty_trials[: args.max_empty]
    if not empty_trials:
        print("No empty trials to retry.")
        return 0

    print(f"Retrying {len(empty_trials)} empty trials via delegated Kimi...")
    embedder = SentenceTransformer("all-MiniLM-L6-v2")
    arm_sums, stratum_sums = _arm_stratum_sums(report)

    attempted = 0
    succeeded = 0
    failed = 0
    for idx, trial in enumerate(empty_trials):
        record = id_to_record.get(trial["id"])
        if not record:
            print(f"[{idx + 1}/{len(empty_trials)}] missing corpus record {trial['id']}", file=sys.stderr)
            failed += 1
            continue
        attempted += 1
        response = await _post_chat_smart_stream(
            trial["prompt"],
            session_token=session_token,
            chat_base_url=args.chat_base_url,
            operator_did=args.operator_did,
            operator_id=args.operator_id,
            ledger_id=args.ledger_id,
            surface_id=args.surface_id,
            model=args.model,
        )
        if response.get("error"):
            print(f"[{idx + 1}/{len(empty_trials)}] {trial['id']} {trial['arm']} r{trial['replicate']}: {response['error']}", file=sys.stderr)
            failed += 1
            continue
        parsed = response.get("parsed_response") or {}
        concepts = parsed.get("concepts") if isinstance(parsed.get("concepts"), list) else []
        reconstructed = str(parsed.get("reconstructed_text") or "").strip()
        trial["raw_response"] = response.get("raw_response", "")
        trial["reconstructed_concepts"] = concepts
        trial["reconstructed_text"] = reconstructed
        trial.pop("error", None)
        trial.pop("transport_failure", None)
        trial["retried_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        metrics = _compute_metrics(
            trial["prompt"],
            concepts,
            reconstructed,
            record["text"],
            embedder,
        )
        trial["metrics"] = metrics
        arm = trial["arm"]
        stratum = record.get("stratum", "unknown")
        _add_metric_sums(arm_sums[arm], metrics)
        _add_metric_sums(stratum_sums[(arm, stratum)], metrics)
        succeeded += 1
        print(f"[{idx + 1}/{len(empty_trials)}] {trial['id']} {arm} r{trial['replicate']}: recall={metrics['node_recall']:.2f}")
        if args.delay > 0:
            await asyncio.sleep(args.delay)

    # Rebuild aggregates
    new_overall: dict[str, dict[str, Any]] = {}
    for arm, sums in arm_sums.items():
        new_overall[arm] = _finalize_aggregate(sums)
    new_by_stratum: dict[str, dict[str, dict[str, Any]]] = {}
    for (arm, stratum), sums in stratum_sums.items():
        new_by_stratum.setdefault(arm, {})[stratum] = _finalize_aggregate(sums)

    report["summary"] = {"overall": new_overall, "by_stratum": new_by_stratum}

    completed = sum(1 for t in trials if "error" not in t and str(t.get("raw_response", "")).strip())
    transport_failures = sum(1 for t in trials if t.get("transport_failure") is True)
    retried = sum(t.get("retries", 0) for t in trials) + attempted
    report["header"]["date"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["header"]["completed"] = completed
    report["header"]["transport_failures"] = transport_failures
    report["header"]["retried"] = retried
    report["header"]["calls_made"] = report["header"].get("calls_made", 0) + attempted
    report["header"]["phase_r_note"] = f"Retried {attempted} empty trials; {succeeded} succeeded, {failed} failed."

    attempted_total = len(trials)
    a_agg = new_overall.get("A", {})
    c_agg = new_overall.get("C", {})
    report["gate"] = {
        "completed_gte_0_95": (completed / attempted_total if attempted_total else 0.0) >= 0.95,
        "C1_node_recall_gte_0_90": a_agg.get("node_recall_mean", 0.0) >= 0.90,
        "C1_precision_gte_0_90": a_agg.get("precision_mean", 0.0) >= 0.90,
        "C1_f1_gte_0_90": a_agg.get("f1_mean", 0.0) >= 0.90,
        "C1_cosine_gte_0_85": a_agg.get("cosine_mean", 0.0) >= 0.85,
        "C1_grammatical_fraction_gte_0_90": a_agg.get("grammatical_fraction", 0.0) >= 0.90,
        "C2_shuffled_lt_full": c_agg.get("node_recall_mean", 1.0) < a_agg.get("node_recall_mean", 0.0),
    }

    output_path = args.output or args.report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nUpdated report: {output_path}")
    print(f"Empty trials attempted: {attempted}, succeeded: {succeeded}, failed: {failed}")
    print("New overall aggregates:")
    print(json.dumps(new_overall, indent=2))
    print("Gates:")
    print(json.dumps(report["gate"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
