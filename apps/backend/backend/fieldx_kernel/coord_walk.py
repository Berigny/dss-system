from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterable, Mapping, cast

from backend.fieldx_kernel.flow_rules import run_full_check
from backend.fieldx_kernel.schema import LAW_PRIME
from backend.fieldx_kernel.substrate import LedgerStoreV2
from backend.utils.coord import normalise_coord, namespace_candidates

logger = logging.getLogger(__name__)

LAW_PENALTY = {
    3: 0.0,  # LAW_FULL
    2: 0.3,  # LAW_CONDITIONAL
    1: 0.6,  # LAW_MARGINAL
    0: 1.5,  # LAW_UNLAWFUL
}
REPEAT_TYPE_PENALTY = 0.08
NOVELTY_DECAY_PER_STEP = 0.03
EQ6_MIN_LAWFULNESS = int(os.getenv("EQ6_WALK_MIN_LAWFULNESS", "2"))
EQ6_SCORE_WEIGHT = float(os.getenv("EQ6_WALK_WEIGHT", "0.25"))

LAW_CONSTRAINT = {
    3: "allowed",
    2: "conditional",
    1: "conditional",
    0: "forbidden",
}


@dataclass(frozen=True)
class WalkEntry:
    coordinate: str
    metadata: dict[str, Any]
    notes: str | None
    kind: str | None


def _as_list(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _extract_text_tokens(meta: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for key in ("topics", "summary_topics", "tags"):
        values = _as_list(meta.get(key))
        for item in values:
            if item:
                tokens.append(str(item).lower())
    return tokens


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else hi if value > hi else value


def _eq6_lawfulness_level(meta: Mapping[str, Any]) -> int | None:
    governance = meta.get("governance")
    if isinstance(governance, Mapping):
        eq6 = governance.get("eq6")
        if isinstance(eq6, Mapping):
            value = eq6.get("lawfulness_level")
            if isinstance(value, (int, float)):
                return _clamp_int(int(value), 0, 3)
    for key in ("eq6_lawfulness_level", "lawfulness_level"):
        value = meta.get(key)
        if isinstance(value, (int, float)):
            return _clamp_int(int(value), 0, 3)
    flow_diag = meta.get("flow_diag")
    if isinstance(flow_diag, Mapping):
        value = flow_diag.get("lawfulness_level")
        if isinstance(value, (int, float)):
            return _clamp_int(int(value), 0, 3)
    return None


def _eq6_commit_allowed(meta: Mapping[str, Any]) -> bool | None:
    governance = meta.get("governance")
    if isinstance(governance, Mapping):
        eq6 = governance.get("eq6")
        if isinstance(eq6, Mapping):
            value = eq6.get("commit_allowed")
            if isinstance(value, bool):
                return value
    value = meta.get("eq6_commit_allowed")
    if isinstance(value, bool):
        return value
    return None


def extract_prime_signature(entry: WalkEntry) -> list[int]:
    meta = entry.metadata or {}
    primes: list[int] = []
    for key in ("token_primes", "primes"):
        values = _as_list(meta.get(key))
        for value in values:
            try:
                primes.append(int(value))
            except (TypeError, ValueError):
                continue
    return primes


def semantic_score(a: WalkEntry, b: WalkEntry) -> float:
    a_primes = set(extract_prime_signature(a))
    b_primes = set(extract_prime_signature(b))
    if a_primes and b_primes:
        overlap = len(a_primes & b_primes)
        union = len(a_primes | b_primes)
        return float(overlap) / float(union) if union else 0.0
    a_tokens = set(_extract_text_tokens(a.metadata))
    b_tokens = set(_extract_text_tokens(b.metadata))
    if not a_tokens or not b_tokens:
        return 0.0
    return float(len(a_tokens & b_tokens)) / float(len(a_tokens | b_tokens))


def consilience_score(a: WalkEntry, b: WalkEntry) -> float:
    a_claims = set(_as_list(a.metadata.get("claims")))
    b_claims = set(_as_list(b.metadata.get("claims")))
    if a_claims and b_claims:
        return float(len(a_claims & b_claims)) / float(len(a_claims | b_claims))
    return semantic_score(a, b) * 0.6


def future_alignment_score(entry: WalkEntry) -> float:
    meta = entry.metadata or {}
    teleology = meta.get("teleology_alignment")
    if isinstance(teleology, (int, float)):
        return float(teleology)
    appraisal = meta.get("appraisal")
    if isinstance(appraisal, dict):
        score = appraisal.get("score")
        if isinstance(score, (int, float)):
            return float(score)
    return 0.5


def _normalize_candidates(values: Iterable[Any]) -> list[str]:
    coords: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("coordinate") or value.get("coord")
        if not value:
            continue
        coord = str(value).strip()
        if not coord or coord in seen:
            continue
        seen.add(coord)
        coords.append(coord)
    return coords


def get_related_coords(entry: WalkEntry, *, limit: int = 20) -> list[str]:
    meta = entry.metadata or {}
    related: list[str] = []

    if entry.kind == "attachment":
        related.extend(_normalize_candidates(meta.get("related_turns") or []))

    inputs_raw = meta.get("inputs")
    inputs = cast(dict[str, Any], inputs_raw) if isinstance(inputs_raw, dict) else {}
    related.extend(_normalize_candidates(inputs.get("attachments") or []))
    related.extend(_normalize_candidates(inputs.get("parts_used") or []))

    related.extend(_normalize_candidates(meta.get("knowledge_tree") or []))
    related.extend(_normalize_candidates(meta.get("resolved_coords") or []))

    sources_raw = meta.get("sources")
    sources = cast(dict[str, Any], sources_raw) if isinstance(sources_raw, dict) else {}
    related.extend(_normalize_candidates(sources.get("turns") or []))
    related.extend(_normalize_candidates(sources.get("attachments") or []))
    related.extend(_normalize_candidates(sources.get("parts") or []))

    seen: set[str] = set()
    unique: list[str] = []
    for coord in related:
        if coord in seen:
            continue
        seen.add(coord)
        unique.append(coord)
        if len(unique) >= limit:
            break
    return sorted(unique)


def resolve_coord(
    coord: str,
    *,
    store: LedgerStoreV2,
    namespace_hint: str | None = None,
) -> WalkEntry | None:
    normalized = normalise_coord(coord)
    if normalized.get("kind") == "web4":
        return None
    namespace = normalized.get("namespace")
    candidates = []
    if namespace:
        candidates.append(namespace)
    if namespace_hint and namespace_hint not in candidates:
        candidates.append(namespace_hint)
    for candidate in namespace_candidates():
        if candidate not in candidates:
            candidates.append(candidate)
    entry = None
    namespace_used = None
    for candidate in candidates:
        lookup = f"{candidate}:{normalized['bare']}"
        entry = store.read(lookup)
        if entry:
            namespace_used = candidate
            break
    if not entry:
        return None
    meta = entry.state.metadata or {}
    canonical = f"{namespace_used}:{normalized['bare']}" if namespace_used else normalized["bare"]
    return WalkEntry(
        coordinate=canonical,
        metadata=meta,
        notes=entry.notes,
        kind=normalized.get("kind") or meta.get("kind"),
    )


def _log_coord_walk_result(
    logger: logging.Logger,
    result: dict[str, Any],
    start_coord: str,
    start_time: float,
) -> None:
    """Emit a structured log record capturing the coordinate path."""
    duration_ms = (perf_counter() - start_time) * 1000.0
    extra = {
        "coord_walk_event": True,
        "coord_walk_start": start_coord,
        "coord_walk_status": result.get("status"),
        "coord_walk_path": result.get("path"),
        "coord_walk_steps": len(result.get("steps", [])),
        "coord_walk_termination_reason": result.get("termination_reason"),
        "coord_walk_candidates_considered": result.get("candidates_considered"),
        "coord_walk_mediator": result.get("mediator"),
        "coord_walk_flow_diagnostic": result.get("flow_diagnostic"),
        "coord_walk_duration_ms": round(duration_ms, 3),
    }
    logger.info("coord_walk completed", extra=extra)


def coord_walk(
    *,
    start_coord: str,
    max_steps: int,
    current_coherence: float,
    store: LedgerStoreV2,
    namespace_hint: str | None = None,
    max_candidates: int = 20,
) -> dict[str, Any]:
    start_time = perf_counter()
    path: list[str] = []
    visited: set[str] = set()
    steps: list[dict[str, Any]] = []
    termination_reason = "max_steps"
    candidates_considered = 0
    last_mediator = LAW_PRIME
    flow_diagnostic: str | None = None

    current_entry = resolve_coord(start_coord, store=store, namespace_hint=namespace_hint)
    if not current_entry:
        result = {"status": "error", "detail": "start_coord not found"}
        _log_coord_walk_result(logger, result, start_coord, start_time)
        return result

    current_primes = extract_prime_signature(current_entry)
    path.append(current_entry.coordinate)
    visited.add(current_entry.coordinate)
    path_types: list[str] = [str(current_entry.kind or "unknown")]

    for step in range(max_steps):
        candidates = get_related_coords(current_entry, limit=max_candidates)
        if not candidates:
            termination_reason = "no_candidates"
            break

        scored: list[tuple[float, str, WalkEntry, int, int | None, bool | None, str]] = []
        eq6_blocked = 0
        for cand_coord in candidates:
            if cand_coord in visited:
                continue
            cand_entry = resolve_coord(cand_coord, store=store, namespace_hint=namespace_hint)
            if not cand_entry:
                continue
            candidates_considered += 1

            cand_primes = extract_prime_signature(cand_entry)
            prime_seq = current_primes or [LAW_PRIME]
            if cand_primes:
                prime_seq = prime_seq + [cand_primes[0]]

            _, flow_msg, mediator_prime, lawfulness = run_full_check(
                prime_sequence=prime_seq,
                current_coherence=current_coherence,
            )
            last_mediator = mediator_prime
            topology_penalty = LAW_PENALTY.get(lawfulness, 1.0)
            eq6_lawfulness_level = _eq6_lawfulness_level(cand_entry.metadata)
            eq6_commit_allowed = _eq6_commit_allowed(cand_entry.metadata)
            if eq6_lawfulness_level is not None and eq6_lawfulness_level < EQ6_MIN_LAWFULNESS:
                eq6_blocked += 1
                continue

            s_sem = semantic_score(current_entry, cand_entry)
            s_con = consilience_score(current_entry, cand_entry)
            s_fut = future_alignment_score(cand_entry)

            score = (0.5 * s_sem) + (0.3 * s_con) + (0.2 * s_fut) - topology_penalty
            if path_types and str(cand_entry.kind or "unknown") == path_types[-1]:
                score -= REPEAT_TYPE_PENALTY
            score -= NOVELTY_DECAY_PER_STEP * float(step)
            if eq6_lawfulness_level is not None:
                eq6_score = float(eq6_lawfulness_level) / 3.0
                score += EQ6_SCORE_WEIGHT * (eq6_score - 0.5)
            scored.append(
                (
                    score,
                    cand_entry.coordinate,
                    cand_entry,
                    lawfulness,
                    eq6_lawfulness_level,
                    eq6_commit_allowed,
                    flow_msg,
                )
            )

            logger.debug(
                "COORD_WALK step=%d cand=%s score=%.3f lawfulness=L%d eq6=%s",
                step,
                cand_entry.coordinate,
                score,
                lawfulness,
                f"L{eq6_lawfulness_level}" if eq6_lawfulness_level is not None else "None",
            )

        if not scored:
            termination_reason = "eq6_blocked" if eq6_blocked else "no_candidates"
            break

        scored.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_coord, best_entry, best_lawfulness, best_eq6_lawfulness, best_eq6_commit, best_flow_msg = scored[0]
        if best_lawfulness == 0 and best_score < 0:
            termination_reason = "blocked"
            flow_diagnostic = best_flow_msg
            break

        top_candidates = [
            {
                "coord": coord,
                "score": round(score, 4),
                "eq6_lawfulness_level": eq6_law,
                "flow_diagnostic": flow_msg,
            }
            for score, coord, _entry, _law, eq6_law, _eq6_commit, flow_msg in scored[:5]
        ]
        step_payload = {
            "from": current_entry.coordinate,
            "to": best_coord,
            "score": round(best_score, 4),
            "lawfulness": best_lawfulness,
            "lawfulness_level": best_lawfulness,
            "constraint_type": LAW_CONSTRAINT.get(best_lawfulness, "conditional"),
            "candidates": top_candidates,
            "eq6_lawfulness_level": best_eq6_lawfulness,
            "eq6_commit_allowed": best_eq6_commit,
            "flow_diagnostic": best_flow_msg,
        }
        step_payload["lawfulness_level"] = step_payload.get("lawfulness", 0)
        step_payload["hop_lawfulness"] = (
            best_eq6_lawfulness
            if best_eq6_lawfulness is not None
            else step_payload.get("lawfulness_level", step_payload.get("lawfulness", 0))
        )
        steps.append(step_payload)
        path.append(best_coord)
        visited.add(best_coord)
        current_entry = best_entry
        current_primes = extract_prime_signature(current_entry)
        path_types.append(str(current_entry.kind or "unknown"))

    result = {
        "status": "success",
        "path": path,
        "steps": steps,
        "termination_reason": termination_reason,
        "candidates_considered": candidates_considered,
        "mediator": last_mediator,
        "flow_diagnostic": flow_diagnostic,
        "max_steps": max_steps,
        "current_coherence": current_coherence,
    }
    _log_coord_walk_result(logger, result, start_coord, start_time)
    return result


__all__ = [
    "coord_walk",
    "resolve_coord",
    "get_related_coords",
    "extract_prime_signature",
    "semantic_score",
    "consilience_score",
    "future_alignment_score",
]
