#!/usr/bin/env python3
"""
KSR-EVAL DSS-274 / DSS-291 — Retention smoke test on core-only prompt slice.

Verifies that the public ``ksr-core`` tree retains sufficient recall by building
a 50-item decode prompt slice using only nodes/fields present in
``ksr/core/ksr-core-*.yaml``. In ``--dry-run`` mode (default) the harness
validates the slice and computes deterministic encode/decode coverage without
calling an LLM, so it is safe for CI.

Live modes:
  OPENROUTER_API_KEY=... python3 tools/retention_smoke_test.py --model moonshotai/kimi-k3
  DSS_SESSION_TOKEN=...  python3 tools/retention_smoke_test.py --delegated-kimi

The ``--delegated-kimi`` mode posts through the chat surface smart stream using
the Kimi Code delegated principal, avoiding direct OpenRouter calls.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request

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

DEFAULT_CHAT_BASE_URL = os.getenv("DSS_CHAT_BASE_URL", "https://chat.dualsubstrate.com")
DEFAULT_OPERATOR_DID = os.getenv(
    "DSS_OPERATOR_DID",
    "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
)
DEFAULT_OPERATOR_ID = os.getenv("DSS_OPERATOR_ID", "ops-admin")
DEFAULT_LEDGER_ID = os.getenv("DSS_LEDGER_ID", "loam")
DEFAULT_SURFACE_ID = os.getenv("DSS_SURFACE_ID", "surface:chat:primary")


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


# ---------------------------------------------------------------------------
# Delegated Kimi Code chat-surface path (DSS-291)
# ---------------------------------------------------------------------------


def _refresh_session_token(
    refresh_token: str,
    base_url: str,
    *,
    timeout: float = 20.0,
) -> tuple[str | None, str | None, dict[str, Any]]:
    """Exchange a DSS refresh token for a new access token.

    Mirrors the helper in ds-review/scripts/dss_auth.py so this tool has no
    cross-repo import dependency.
    """
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
                return None, None, {
                    "status": status,
                    "error": "refresh_non_json_response",
                    "body_snippet": raw[:1024].decode("utf-8", errors="ignore"),
                }
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
    principal_did = str(body.get("principal_did") or "").strip() or None

    if not new_session_token:
        return None, None, {
            "status": status,
            "error": "refresh_missing_session_token",
            "body": body,
        }

    return (
        new_session_token,
        new_refresh_token,
        {
            "status": status,
            "principal_did": principal_did,
            "body": body,
        },
    )


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
    """Best-effort extraction of the first JSON object from model output."""
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


def _build_concept_to_prime(registry: dict[str, Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    prime_registry = registry.get("prime_registry", {})
    if isinstance(prime_registry, dict):
        for key, entry in prime_registry.items():
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            try:
                prime = int(key)
            except (ValueError, TypeError):
                continue
            if name and prime > 1:
                mapping[name.lower()] = prime
    return mapping


async def _post_chat_smart_stream(
    prompt: str,
    *,
    session_token: str,
    chat_base_url: str,
    operator_did: str,
    operator_id: str,
    ledger_id: str,
    surface_id: str,
    rate_limit_retries: int = 3,
    timeout_seconds: float = 240.0,
    transient_retries: int = 3,
) -> dict[str, Any]:
    """Post a single prompt to the chat surface smart stream and return parsed output."""
    session_id = f"retention-smoke-{uuid.uuid4().hex[:12]}"
    request_id = f"retention-smoke-req-{uuid.uuid4().hex}"
    payload = {
        "message": prompt,
        "provider": "moonshotai/kimi-k2.5",
        "agent": "moonshotai/kimi-k2.5",
        "model": "moonshotai/kimi-k2.5",
        "entity": ledger_id,
        "ledger_id": ledger_id,
        "surface_id": surface_id,
        "session_id": session_id,
        "request_id": request_id,
        "enable_ledger": True,
        "history": [],
        "include_pipeline_events": True,
        "include_post_introspect_snapshot": True,
        "prompt_principal_mode": "kimi",
    }
    payload_bytes = json.dumps(payload).encode("utf-8")

    headers = {
        "content-type": "application/json",
        "accept": "application/x-ndjson, application/json",
        "x-principal-did": operator_did,
        "x-principal-id": operator_id,
        "x-principal-type": "user",
        "cookie": f"ds_backend_session_token={session_token}",
    }

    url = f"{chat_base_url.rstrip('/')}/api/chat/smart_stream"

    def _make_request() -> request.Request:
        return request.Request(
            url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )

    rate_limit_failures = 0
    transient_failures = 0
    while True:
        try:
            with request.urlopen(_make_request(), timeout=timeout_seconds) as resp:
                status = getattr(resp, "status", 200)
                if status >= 400:
                    body = resp.read().decode("utf-8", errors="ignore")
                    if status == 429:
                        rate_limit_failures += 1
                        if rate_limit_failures > max(0, rate_limit_retries):
                            return {"error": "rate_limited", "detail": body[:1000]}
                        await asyncio.sleep(60)
                        continue
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
            if exc.code == 429:
                rate_limit_failures += 1
                if rate_limit_failures > max(0, rate_limit_retries):
                    return {"error": "rate_limited", "detail": body[:1000]}
                await asyncio.sleep(60)
                continue
            return {"error": f"http_{exc.code}", "detail": body[:1000]}
        except (TimeoutError, socket.timeout) as exc:
            transient_failures += 1
            if transient_failures > max(0, transient_retries):
                return {"error": "timeout", "detail": f"read timed out after {timeout_seconds}s ({exc})"}
            wait = 5 * (2 ** (transient_failures - 1))
            print(
                f"warning: chat surface timeout ({transient_failures}/{transient_retries}); retrying in {wait}s...",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(wait)
            continue
        except error.URLError as exc:
            transient_failures += 1
            if transient_failures > max(0, transient_retries):
                return {"error": "request_failed", "detail": str(exc.reason)}
            wait = 5 * (2 ** (transient_failures - 1))
            print(
                f"warning: chat surface URL error ({transient_failures}/{transient_retries}): {exc.reason}; retrying in {wait}s...",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(wait)
            continue


def _save_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


async def run_delegated_kimi_decode(
    records: list[dict[str, Any]],
    registry: dict[str, Any],
    chat_base_url: str,
    operator_did: str,
    operator_id: str,
    ledger_id: str,
    surface_id: str,
    report: dict[str, Any],
    output_path: Path,
) -> list[dict[str, Any]]:
    """Run live LLM decode trials via the chat surface Kimi Code delegated principal.

    Requires DSS_SESSION_TOKEN or DSS_REFRESH_TOKEN in the environment.
    """
    session_token = os.getenv("DSS_SESSION_TOKEN", "").strip()
    refresh_token = os.getenv("DSS_REFRESH_TOKEN", "").strip()

    if not session_token and not refresh_token:
        raise RuntimeError(
            "Delegated Kimi mode requires DSS_SESSION_TOKEN or DSS_REFRESH_TOKEN"
        )

    if not session_token and refresh_token:
        new_session, new_refresh, info = _refresh_session_token(refresh_token, chat_base_url)
        if not new_session:
            raise RuntimeError(f"Failed to refresh DSS session token: {info}")
        session_token = new_session
        if new_refresh:
            os.environ["DSS_REFRESH_TOKEN"] = new_refresh
        print(
            f"Refreshed DSS session token for principal {info.get('principal_did') or '?'}",
            file=sys.stderr,
        )

    alphabet = build_alphabet(registry)
    registry_text = _registry_to_prompt_text(registry)
    concept_to_prime = _build_concept_to_prime(registry)
    results: list[dict[str, Any]] = list(report.get("live_results") or [])
    completed_ids = {r["id"] for r in results}

    REFRESH_INTERVAL_SECONDS = 2700  # refresh session token every 45 min (token TTL ~1h)
    last_refresh_time = time.time()

    for idx, record in enumerate(records):
        if record["id"] in completed_ids:
            print(
                f"[{idx + 1}/{len(records)}] {record['id']} already completed; skipping",
                file=sys.stderr,
                flush=True,
            )
            continue
        if refresh_token and (time.time() - last_refresh_time) >= REFRESH_INTERVAL_SECONDS:
            new_session, new_refresh, info = _refresh_session_token(refresh_token, chat_base_url)
            if not new_session:
                print(
                    f"warning: periodic refresh failed: {info}; continuing with current token",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                session_token = new_session
                if new_refresh:
                    refresh_token = new_refresh
                    os.environ["DSS_REFRESH_TOKEN"] = new_refresh
                last_refresh_time = time.time()
                print(
                    f"[{idx + 1}/{len(records)}] refreshed session token",
                    file=sys.stderr,
                    flush=True,
                )

        enc = encode_concepts(record["encode_seed"], alphabet, registry)
        factors = factorize(enc["number"])
        prompt = format_prompt(registry_text, factors)
        ground_truth_primes = set(factors.keys())

        print(
            f"[{idx + 1}/{len(records)}] {record['id']} posting to chat surface...",
            file=sys.stderr,
            flush=True,
        )
        try:
            response = await _post_chat_smart_stream(
                prompt,
                session_token=session_token,
                chat_base_url=chat_base_url,
                operator_did=operator_did,
                operator_id=operator_id,
                ledger_id=ledger_id,
                surface_id=surface_id,
            )
        except Exception as exc:  # pragma: no cover - last-resort safety net
            print(
                f"[{idx + 1}/{len(records)}] {record['id']} UNEXPECTED ERROR: {exc}",
                file=sys.stderr,
                flush=True,
            )
            response = {"error": "unexpected_exception", "detail": str(exc)}

        if response.get("error"):
            print(
                f"[{idx + 1}/{len(records)}] {record['id']} ERROR: {response['error']}",
                file=sys.stderr,
                flush=True,
            )
            results.append(
                {
                    "id": record["id"],
                    "prompt": prompt,
                    "error": response["error"],
                    "error_detail": response.get("detail"),
                }
            )
            continue

        parsed = response.get("parsed_response") or {}
        raw = response.get("raw_response", "")
        concepts = parsed.get("concepts") if isinstance(parsed.get("concepts"), list) else []
        reconstructed = str(parsed.get("reconstructed_text") or "").strip()

        recovered_primes: set[int] = set()
        for concept in concepts:
            prime = concept_to_prime.get(str(concept).strip().lower())
            if prime:
                recovered_primes.add(prime)

        tp = len(ground_truth_primes & recovered_primes)
        fn = len(ground_truth_primes - recovered_primes)
        recall = tp / (tp + fn) if (tp + fn) else 0.0

        print(
            f"[{idx + 1}/{len(records)}] {record['id']} recall={recall:.2f} events={response.get('event_count')}",
            file=sys.stderr,
            flush=True,
        )
        result = {
            "id": record["id"],
            "prompt": prompt,
            "raw_response": raw,
            "concepts": concepts,
            "reconstructed_text": reconstructed,
            "ground_truth_primes": sorted(ground_truth_primes),
            "recovered_primes": sorted(recovered_primes),
            "node_recall": recall,
            "session_id": response.get("session_id"),
            "request_id": response.get("request_id"),
            "event_count": response.get("event_count"),
        }
        results.append(result)
        report["live_results"] = results
        _save_report(report, output_path)

    return results


def _default_output_dir(core_sha: str) -> Path:
    stamp = time.strftime("%Y-%m-%d")
    return Path(f"eval/reports/{stamp}_{core_sha[:16]}_v0.4")


def main() -> int:
    parser = argparse.ArgumentParser(description="KSR-EVAL DSS-274 / DSS-291 retention smoke test")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="Deterministic validation only; no LLM calls")
    parser.add_argument(
        "--delegated-kimi",
        action="store_true",
        help="Run live decode via the chat surface Kimi Code delegated principal (uses DSS_SESSION_TOKEN)",
    )
    parser.add_argument("--chat-base-url", default=DEFAULT_CHAT_BASE_URL)
    parser.add_argument("--operator-did", default=DEFAULT_OPERATOR_DID)
    parser.add_argument("--operator-id", default=DEFAULT_OPERATOR_ID)
    parser.add_argument("--ledger-id", default=DEFAULT_LEDGER_ID)
    parser.add_argument("--surface-id", default=DEFAULT_SURFACE_ID)
    parser.add_argument("--output", type=Path, help="Report output path")
    args = parser.parse_args()

    registry = load_registry(args.registry)
    core_only = build_core_only_registry(registry)
    core_sha = registry_sha256(args.registry)
    repo_sha = _repo_sha()

    header, records = load_corpus(args.corpus, args.sample)
    corpus_sha = header.get("corpus_sha256", _file_sha256(args.corpus))

    output_path = args.output
    if output_path is None:
        output_path = _default_output_dir(core_sha) / "retention_smoke_report.json"

    if args.dry_run:
        mode = "dry_run"
        model_value = None
        note = (
            "Dry-run mode reports deterministic encode/decode recall over ksr-core. "
            "Live LLM decode is deferred per Epic 38 agent-heavy benchmark policy."
        )
    elif args.delegated_kimi:
        mode = "delegated_kimi"
        model_value = "moonshotai/kimi-k2.5"
        note = (
            "Live decode completed via chat surface Kimi Code delegated principal. "
            "No OpenRouter API key required."
        )
    else:
        mode = "live_decode"
        model_value = args.model
        note = "Live decode completed under R1 transport rules."

    per_item, mean_recall = evaluate_deterministic_recall(records, core_only)

    # Load existing report for resume if output path already exists and mode matches.
    existing_live_results: list[dict[str, Any]] = []
    if output_path.exists():
        try:
            existing_report = json.loads(output_path.read_text(encoding="utf-8"))
            if existing_report.get("mode") == mode and existing_report.get("corpus_sha256") == corpus_sha:
                existing_live_results = list(existing_report.get("live_results") or [])
        except Exception:
            existing_live_results = []

    report: dict[str, Any] = {
        "report": "KSR-EVAL DSS-274 / DSS-291 retention smoke test",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "model": model_value,
        "ksr_version": registry.get("ksr_version"),
        "core_artifact_sha256": core_sha,
        "repo_commit_sha": repo_sha,
        "corpus_sha256": corpus_sha,
        "sample_size": len(records),
        "recall_gate": RECALL_GATE,
        "deterministic_mean_node_recall": mean_recall,
        "per_item": per_item,
        "live_results": existing_live_results,
        "note": note,
    }

    live_results: list[dict[str, Any]] | None = existing_live_results or None
    live_mean_recall: float | None = None
    if not args.dry_run:
        if args.delegated_kimi:
            live_results = asyncio.run(
                run_delegated_kimi_decode(
                    records,
                    core_only,
                    args.chat_base_url,
                    args.operator_did,
                    args.operator_id,
                    args.ledger_id,
                    args.surface_id,
                    report,
                    output_path,
                )
            )
        else:
            live_results = asyncio.run(run_live_decode(records, core_only, args.model))
            report["live_results"] = live_results
            _save_report(report, output_path)

        live_recalls = [
            r.get("node_recall")
            for r in live_results or []
            if isinstance(r.get("node_recall"), (int, float))
        ]
        if live_recalls:
            live_mean_recall = sum(live_recalls) / len(live_recalls)

    gate_pass = (
        (live_mean_recall is not None and live_mean_recall >= RECALL_GATE)
        if not args.dry_run
        else (mean_recall >= RECALL_GATE)
    )

    report["mean_node_recall"] = live_mean_recall if live_mean_recall is not None else mean_recall
    report["gate_pass"] = gate_pass
    if live_mean_recall is not None:
        report["live_mean_node_recall"] = live_mean_recall

    _save_report(report, output_path)

    print(f"DSS-274/DSS-291 retention smoke test | mode: {mode}")
    print(f"Mean node recall: {report['mean_node_recall']:.3f} | gate: {RECALL_GATE}")
    print(f"Gate: {'PASS' if gate_pass else 'FAIL'}")
    print(f"Report: {output_path}")
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
