"""Telemetry stats endpoints."""

from __future__ import annotations

import json
import math
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.metrics.benchmark_context import attach_request_benchmark_context
from backend.metrics.prod_benchmark_contract import SurfaceName
from backend.metrics.telemetry import (
    TelemetryIds,
    TelemetryReferences,
    TelemetrySearchFlags,
    TurnTelemetry,
)
from backend.services.authz import authorize_or_raise
from backend.services.ledger_service import LedgerService
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2

router = APIRouter(prefix="/stats", tags=["stats"])

_TOKENS_PER_WORD = 1_000_000.0 / 750_000.0


class TelemetryEventRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str = Field(..., description="Session identifier for the turn.")
    namespace: str | None = Field(None, description="Namespace for telemetry aggregation.")
    entity: str | None = Field(None, description="Entity identifier (namespace fallback).")
    turn_id: str | None = Field(None, description="Turn identifier for the event.")
    timestamp: datetime | None = Field(None, description="Event timestamp (UTC).")
    model: str | None = Field(None, description="Model identifier for chat turns.")
    provider: str | None = Field(None, description="Provider identifier for chat turns.")
    cost: float | None = Field(None, description="Total cost for the turn.")
    gen_input_tokens: int | None = Field(None, description="Input tokens used.")
    gen_output_tokens: int | None = Field(None, description="Output tokens used.")
    memory_cost: float | None = Field(None, description="Memory ingest cost.")
    memory_tokens: int | None = Field(None, description="Memory token count.")
    ingest_words: int | None = Field(None, description="Ingested word count.")
    latency_ms: float | None = Field(None, description="Turn latency in milliseconds.")
    emitted_refs: int | None = Field(None, description="References emitted.")
    resolve_attempts: int | None = Field(None, description="Resolve attempts.")
    resolve_successes: int | None = Field(None, description="Resolve successes.")
    search_requested: bool | None = Field(None, description="Search requested flag.")
    search_used: bool | None = Field(None, description="Search used flag.")
    search_succeeded: bool | None = Field(None, description="Search succeeded flag.")
    authz_denied: bool | None = Field(None, description="Authorization denied flag for the turn.")
    authz_reason: str | None = Field(None, description="Authorization decision reason code.")
    authz_principal_source: str | None = Field(None, description="Principal source classification.")
    authz_principal_mode: str | None = Field(None, description="Principal mode classification.")
    auth_error_class: str | None = Field(None, description="Authorization/token error class label.")
    auth_token_validation_failed: bool | None = Field(
        None,
        description="Token validation failure flag.",
    )
    eq9_eval_source: str | None = Field(None, description="EQ9 evaluation source label.")
    meta_patch_status: str | None = Field(None, description="Meta patch status.")
    meta_patch_reason: str | None = Field(None, description="Meta patch reason when skipped.")


def _get_db(request: Request):
    return _get_ledger_service(request).db


def _get_ledger_service(request: Request) -> LedgerService:
    return LedgerService.from_request(request)


def _get_telemetry_store(request: Request):
    return _get_ledger_service(request).telemetry_store()


def _decode_counter(raw: Any) -> dict[str, float | int]:
    if raw is None:
        return {}
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        data = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, (int, float))}


def _iter_rollups(db: Any, namespace: str) -> Iterable[dict[str, float | int]]:
    prefix = f"metrics:rollup:{namespace}:".encode()
    if hasattr(db, "iterkeys"):
        keys_iterator = db.iterkeys()  # type: ignore[attr-defined]
        keys_iterator.seek(prefix)
        for raw_key in keys_iterator:
            key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
            if not key_bytes.startswith(prefix):
                break
            yield _decode_counter(db.get(key_bytes))
        return

    for raw_key, raw_value in db.items():
        key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
        if key_bytes.startswith(prefix):
            yield _decode_counter(raw_value)


def _iter_all_rollups(db: Any) -> Iterator[Tuple[str, dict[str, float | int]]]:
    prefix = b"metrics:rollup:"
    if hasattr(db, "iterkeys"):
        keys_iterator = db.iterkeys()  # type: ignore[attr-defined]
        keys_iterator.seek(prefix)
        for raw_key in keys_iterator:
            key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
            if not key_bytes.startswith(prefix):
                break
            key_text = key_bytes.decode(errors="ignore")
            parts = key_text.split(":", 3)
            if len(parts) < 4:
                continue
            namespace = parts[2]
            payload = _decode_counter(db.get(key_bytes))
            if payload:
                yield namespace, payload
        return

    for raw_key, raw_value in db.items():
        key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
        if not key_bytes.startswith(prefix):
            continue
        key_text = key_bytes.decode(errors="ignore")
        parts = key_text.split(":", 3)
        if len(parts) < 4:
            continue
        namespace = parts[2]
        payload = _decode_counter(raw_value)
        if payload:
            yield namespace, payload


def _iter_db_keys(db: Any) -> Iterator[Any]:
    try:
        with db.iter() as iterator:  # type: ignore[attr-defined]
            for raw_key, _ in iterator:
                yield raw_key
        return
    except Exception:
        pass

    try:
        for raw_key in db.keys():  # type: ignore[attr-defined]
            yield raw_key
    except Exception:
        return


def _discover_entry_namespaces(db: Any) -> set[str]:
    namespaces: set[str] = set()
    skip_prefixes = (
        "metrics:", "tp:", "ix:", "bucket:", "entity:", "body:", "chain:",
        "feedback:", "attachment:", "overlay-history:", "overlay-seq:",
    )
    for raw_key in _iter_db_keys(db):
        key = raw_key.decode() if isinstance(raw_key, (bytes, bytearray)) else str(raw_key)
        if not key or key.startswith("__") or key.startswith(skip_prefixes):
            continue
        # Entries are stored under ``overlay:<namespace>:<identifier>``.
        if key.startswith("overlay:"):
            key = key[len("overlay:") :]
        namespace, sep, _identifier = key.rpartition(":")
        if not sep or not namespace:
            continue
        namespaces.add(namespace)
    return namespaces


def _provenance_dual_write_status_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    marker = metadata.get("provenance_dual_write")
    if isinstance(marker, dict):
        return {
            "status": str(marker.get("status") or "missing_identity"),
            "legacy_tuple_present": bool(marker.get("legacy_tuple_present")),
            "did_fields_present": bool(marker.get("did_fields_present")),
        }

    contributor = metadata.get("contributor") if isinstance(metadata.get("contributor"), dict) else {}
    principal_id = str(contributor.get("principal_id") or "").strip()
    principal_type = str(contributor.get("principal_type") or "").strip()
    contributor_id = str(metadata.get("contributor_id") or "").strip()
    expected = f"{principal_type}:{principal_id}" if principal_id and principal_type else ""
    legacy_tuple_present = bool(expected and contributor_id == expected)
    did_fields_present = bool(str(contributor.get("principal_did") or "").strip())
    if legacy_tuple_present and did_fields_present:
        status = "dual_write_ok"
    elif legacy_tuple_present and not did_fields_present:
        status = "legacy_only"
    elif did_fields_present and not legacy_tuple_present:
        status = "did_only"
    else:
        status = "missing_identity"
    return {
        "status": status,
        "legacy_tuple_present": legacy_tuple_present,
        "did_fields_present": did_fields_present,
    }


def _iter_events(db: Any, namespace: str, limit: int) -> list[dict[str, Any]]:
    prefix = f"metrics:events:{namespace}:".encode()
    events: deque[dict[str, Any]] = deque(maxlen=limit)
    if hasattr(db, "iterkeys"):
        keys_iterator = db.iterkeys()  # type: ignore[attr-defined]
        keys_iterator.seek(prefix)
        for raw_key in keys_iterator:
            key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
            if not key_bytes.startswith(prefix):
                break
            raw_value = db.get(key_bytes)
            if raw_value is None:
                continue
            try:
                decoded = raw_value.decode() if isinstance(raw_value, (bytes, bytearray)) else raw_value
                payload = json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return list(events)

    for raw_key, raw_value in db.items():
        key_bytes = raw_key.encode() if isinstance(raw_key, str) else raw_key
        if not key_bytes.startswith(prefix):
            continue
        try:
            decoded = raw_value.decode() if isinstance(raw_value, (bytes, bytearray)) else raw_value
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return list(events)


def _safe_divide(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _estimate_memory_tokens(memory_tokens: float, ingest_words: float) -> float:
    if memory_tokens > 0:
        return memory_tokens
    if ingest_words > 0:
        return ingest_words * _TOKENS_PER_WORD
    return 0.0


def _normalize_search_counts(search_requested: float, search_used: float) -> tuple[float, float, float]:
    requested = max(float(search_requested), 0.0)
    used_raw = max(float(search_used), 0.0)
    used = min(used_raw, requested)
    repaired = max(used_raw - requested, 0.0)
    return requested, used, repaired


def _build_search_integrity_alert(search_invariant_repairs: int) -> dict[str, Any]:
    threshold = int(float(os.getenv("STATS_SEARCH_REPAIR_ALERT_THRESHOLD", "0")))
    return {
        "search_invariant_repair_active": search_invariant_repairs > threshold,
        "search_invariant_repair_threshold": threshold,
        "search_invariant_repairs": search_invariant_repairs,
    }


def _build_quarantine_alert(
    *,
    quarantine_writes: int,
    chat_turns: int,
    quarantine_loop_blocked: int,
    quarantine_audit_blocked: int,
    quarantine_persistence_error: int,
) -> dict[str, Any]:
    count_threshold = int(float(os.getenv("STATS_QUARANTINE_WRITE_COUNT_ALERT_THRESHOLD", "0")))
    rate_threshold = float(os.getenv("STATS_QUARANTINE_WRITE_RATE_ALERT_THRESHOLD", "0.05"))
    rate = _safe_divide(float(quarantine_writes), float(chat_turns))
    active = quarantine_writes > count_threshold and rate >= rate_threshold
    dominant_reason = "none"
    reason_counts = {
        "loop_blocked": int(quarantine_loop_blocked),
        "audit_blocked": int(quarantine_audit_blocked),
        "persistence_error": int(quarantine_persistence_error),
    }
    if quarantine_writes > 0:
        dominant_reason = max(reason_counts, key=reason_counts.get)
    return {
        "quarantine_write_alert_active": active,
        "quarantine_write_count_threshold": count_threshold,
        "quarantine_write_rate_threshold": rate_threshold,
        "quarantine_writes": int(quarantine_writes),
        "quarantine_write_rate": rate,
        "quarantine_reason_breakdown": reason_counts,
        "quarantine_dominant_reason": dominant_reason,
    }


def _build_auth_observability_alert(
    *,
    authz_denied: int,
    authz_decisions: int,
    auth_token_validation_failures: int,
) -> dict[str, Any]:
    deny_count_threshold = int(float(os.getenv("STATS_AUTHZ_DENY_COUNT_ALERT_THRESHOLD", "5")))
    deny_rate_threshold = float(os.getenv("STATS_AUTHZ_DENY_RATE_ALERT_THRESHOLD", "0.25"))
    token_validation_threshold = int(float(os.getenv("STATS_AUTH_TOKEN_VALIDATION_FAILURE_THRESHOLD", "1")))
    deny_rate = _safe_divide(float(authz_denied), float(authz_decisions))
    deny_spike = authz_denied >= deny_count_threshold and deny_rate >= deny_rate_threshold
    token_validation_alert = auth_token_validation_failures >= token_validation_threshold
    return {
        "authz_deny_spike_active": bool(deny_spike),
        "authz_deny_count_threshold": deny_count_threshold,
        "authz_deny_rate_threshold": deny_rate_threshold,
        "authz_deny_rate": deny_rate,
        "auth_token_validation_failure_active": bool(token_validation_alert),
        "auth_token_validation_failure_threshold": token_validation_threshold,
        "auth_token_validation_failures": int(auth_token_validation_failures),
    }


def _calculate_metrics_from_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total_events = 0
    verifiable_turns = 0
    resolve_attempts = 0
    resolve_successes = 0
    search_requested_total = 0.0
    search_used_total = 0.0
    search_invariant_repairs = 0.0
    memory_cost_samples: list[float] = []
    memory_cost_token_samples: list[float] = []
    total_cost = 0.0
    chat_turns = 0
    chat_cost = 0.0
    chat_resolve_successes = 0
    emitted_refs_total = 0
    total_chat_tokens = 0
    quarantine_writes = 0
    quarantine_loop_blocked = 0
    quarantine_audit_blocked = 0
    quarantine_persistence_error = 0
    eq9_eval_source_pre_commit = 0
    eq9_eval_source_post_commit_metadata = 0
    eq9_eval_source_post_commit_cache = 0
    eq9_eval_source_pending_post_commit_introspect = 0
    eq9_eval_source_post_commit_introspect = 0
    meta_patch_applied = 0
    meta_patch_skipped = 0
    meta_patch_timeout = 0
    meta_patch_error = 0
    meta_patch_other_skip = 0
    authz_decisions = 0
    authz_denied = 0
    authz_allowed = 0
    authz_reason_did_principal_required = 0
    authz_reason_context_not_allowed = 0
    authz_reason_write_requires_owner_or_tenant = 0
    authz_reason_read_requires_owner_or_tenant = 0
    authz_reason_admin_principal_required = 0
    authz_reason_unknown_ledger = 0
    authz_reason_other = 0
    auth_principal_source_legacy_header = 0
    auth_principal_source_did_header = 0
    auth_principal_source_other = 0
    auth_principal_mode_compat = 0
    auth_principal_mode_did_strict = 0
    auth_error_class_token_validation_failed = 0
    auth_error_class_other = 0
    auth_token_validation_failures = 0

    for payload in events:
        total_events += 1
        references = payload.get("references") or {}
        emitted_refs = int(references.get("emitted_refs") or 0)
        emitted_refs_total += emitted_refs
        event_resolve_attempts = int(references.get("resolve_attempts") or 0)
        event_resolve_successes = int(references.get("resolve_successes") or 0)
        resolve_attempts += event_resolve_attempts
        resolve_successes += event_resolve_successes
        if emitted_refs >= 1 and event_resolve_successes >= 1:
            verifiable_turns += 1

        search = payload.get("search") or {}
        requested_raw = 1.0 if search.get("requested") is True else 0.0
        used_raw = 1.0 if search.get("used") is True else 0.0
        requested_norm, used_norm, repaired = _normalize_search_counts(requested_raw, used_raw)
        search_requested_total += requested_norm
        search_used_total += used_norm
        search_invariant_repairs += repaired

        memory_cost = payload.get("memory_cost")
        ingest_words = payload.get("ingest_words")
        memory_tokens = payload.get("memory_tokens")
        if isinstance(memory_cost, (int, float)) and isinstance(ingest_words, (int, float)):
            if ingest_words > 0 and memory_cost > 0:
                memory_cost_samples.append(memory_cost * 10000.0 / ingest_words)
        memory_tokens_est = _estimate_memory_tokens(
            float(memory_tokens) if isinstance(memory_tokens, (int, float)) else 0.0,
            float(ingest_words) if isinstance(ingest_words, (int, float)) else 0.0,
        )
        if isinstance(memory_cost, (int, float)) and memory_cost > 0 and memory_tokens_est > 0:
            memory_cost_token_samples.append(memory_cost * 1_000_000.0 / memory_tokens_est)

        cost = payload.get("cost")
        if not isinstance(cost, (int, float)):
            cost = payload.get("gen_cost")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)

        is_chat_turn = bool(payload.get("provider") or payload.get("model"))
        if is_chat_turn:
            chat_turns += 1
            if isinstance(cost, (int, float)):
                chat_cost += float(cost)
            chat_resolve_successes += event_resolve_successes
            gen_input = payload.get("gen_input_tokens")
            gen_output = payload.get("gen_output_tokens")
            if isinstance(gen_input, (int, float)):
                total_chat_tokens += int(gen_input)
            if isinstance(gen_output, (int, float)):
                total_chat_tokens += int(gen_output)
        if payload.get("quarantine_write") is True:
            quarantine_writes += 1
        reason = payload.get("quarantine_reason")
        if isinstance(reason, str):
            normalized_reason = reason.strip().lower()
            if normalized_reason == "loop_blocked":
                quarantine_loop_blocked += 1
            elif normalized_reason == "audit_blocked":
                quarantine_audit_blocked += 1
            elif normalized_reason == "persistence_error":
                quarantine_persistence_error += 1
        eq9_eval_source = payload.get("eq9_eval_source")
        if isinstance(eq9_eval_source, str):
            normalized_source = eq9_eval_source.strip().lower()
            if normalized_source == "pre_commit":
                eq9_eval_source_pre_commit += 1
            elif normalized_source == "post_commit_metadata":
                eq9_eval_source_post_commit_metadata += 1
            elif normalized_source == "post_commit_cache":
                eq9_eval_source_post_commit_cache += 1
            elif normalized_source == "pending_post_commit_introspect":
                eq9_eval_source_pending_post_commit_introspect += 1
            elif normalized_source == "post_commit_introspect":
                eq9_eval_source_post_commit_introspect += 1
        meta_patch_status = payload.get("meta_patch_status")
        meta_patch_reason = payload.get("meta_patch_reason")
        if isinstance(meta_patch_status, str):
            normalized_status = meta_patch_status.strip().lower()
            normalized_patch_reason = (
                meta_patch_reason.strip().lower() if isinstance(meta_patch_reason, str) else ""
            )
            if normalized_status == "applied":
                meta_patch_applied += 1
            elif normalized_status == "skipped":
                meta_patch_skipped += 1
                if normalized_patch_reason == "post_introspect_timeout":
                    meta_patch_timeout += 1
                elif normalized_patch_reason == "post_introspect_error":
                    meta_patch_error += 1
                else:
                    meta_patch_other_skip += 1
        authz_reason_raw = payload.get("authz_reason")
        authz_reason = authz_reason_raw.strip().lower() if isinstance(authz_reason_raw, str) else ""
        authz_denied_raw = payload.get("authz_denied")
        authz_has_signal = isinstance(authz_denied_raw, bool) or bool(authz_reason)
        authz_deny_from_reason = authz_reason in {
            "did_principal_required",
            "context_not_allowed",
            "write_requires_owner_or_tenant",
            "read_requires_owner_or_tenant",
            "admin_principal_required",
            "unknown_ledger",
        }
        authz_denied_effective = bool(authz_denied_raw is True or authz_deny_from_reason)
        if authz_has_signal:
            authz_decisions += 1
            if authz_denied_effective:
                authz_denied += 1
            else:
                authz_allowed += 1
        if authz_reason == "did_principal_required":
            authz_reason_did_principal_required += 1
        elif authz_reason == "context_not_allowed":
            authz_reason_context_not_allowed += 1
        elif authz_reason == "write_requires_owner_or_tenant":
            authz_reason_write_requires_owner_or_tenant += 1
        elif authz_reason == "read_requires_owner_or_tenant":
            authz_reason_read_requires_owner_or_tenant += 1
        elif authz_reason == "admin_principal_required":
            authz_reason_admin_principal_required += 1
        elif authz_reason == "unknown_ledger":
            authz_reason_unknown_ledger += 1
        elif authz_reason:
            authz_reason_other += 1

        principal_source_raw = payload.get("authz_principal_source")
        principal_source = principal_source_raw.strip().lower() if isinstance(principal_source_raw, str) else ""
        if principal_source == "legacy_header":
            auth_principal_source_legacy_header += 1
        elif principal_source == "did_header":
            auth_principal_source_did_header += 1
        elif principal_source:
            auth_principal_source_other += 1

        principal_mode_raw = payload.get("authz_principal_mode")
        principal_mode = principal_mode_raw.strip().lower() if isinstance(principal_mode_raw, str) else ""
        if principal_mode == "compat":
            auth_principal_mode_compat += 1
        elif principal_mode == "did_strict":
            auth_principal_mode_did_strict += 1

        auth_error_class_raw = payload.get("auth_error_class")
        auth_error_class = auth_error_class_raw.strip().lower() if isinstance(auth_error_class_raw, str) else ""
        token_validation_failed = bool(
            payload.get("auth_token_validation_failed") is True
            or auth_error_class in {"token_validation_failed", "token_invalid", "token_expired", "token_signature_invalid"}
        )
        if token_validation_failed:
            auth_error_class_token_validation_failed += 1
            auth_token_validation_failures += 1
        elif auth_error_class:
            auth_error_class_other += 1

    memory_cost_per_10k = (
        sum(memory_cost_samples) / len(memory_cost_samples)
        if memory_cost_samples
        else 0.0
    )
    memory_cost_per_1m_tokens = (
        sum(memory_cost_token_samples) / len(memory_cost_token_samples)
        if memory_cost_token_samples
        else 0.0
    )
    chat_cost_per_1m_tokens = (
        (chat_cost / total_chat_tokens) * 1_000_000.0 if total_chat_tokens > 0 else 0.0
    )
    eq9_eval_source_samples = (
        eq9_eval_source_pre_commit
        + eq9_eval_source_post_commit_metadata
        + eq9_eval_source_post_commit_cache
        + eq9_eval_source_pending_post_commit_introspect
        + eq9_eval_source_post_commit_introspect
    )
    meta_patch_events = meta_patch_applied + meta_patch_skipped

    repairs_int = int(search_invariant_repairs)
    quarantine_alert = _build_quarantine_alert(
        quarantine_writes=quarantine_writes,
        chat_turns=chat_turns,
        quarantine_loop_blocked=quarantine_loop_blocked,
        quarantine_audit_blocked=quarantine_audit_blocked,
        quarantine_persistence_error=quarantine_persistence_error,
    )
    alerts = _build_search_integrity_alert(repairs_int)
    alerts.update(quarantine_alert)
    alerts.update(
        _build_auth_observability_alert(
            authz_denied=authz_denied,
            authz_decisions=authz_decisions,
            auth_token_validation_failures=auth_token_validation_failures,
        )
    )
    return {
        "totals": {
            "events": total_events,
            "cost": total_cost,
            "emitted_refs": emitted_refs_total,
            "resolve_attempts": resolve_attempts,
            "resolve_successes": resolve_successes,
            "search_requested": int(search_requested_total),
            "search_used": int(search_used_total),
            "search_invariant_repairs": repairs_int,
            "chat_turns": chat_turns,
            "chat_cost": chat_cost,
            "chat_resolve_successes": chat_resolve_successes,
            "quarantine_writes": quarantine_writes,
            "quarantine_loop_blocked": quarantine_loop_blocked,
            "quarantine_audit_blocked": quarantine_audit_blocked,
            "quarantine_persistence_error": quarantine_persistence_error,
            "eq9_eval_source_pre_commit": eq9_eval_source_pre_commit,
            "eq9_eval_source_post_commit_metadata": eq9_eval_source_post_commit_metadata,
            "eq9_eval_source_post_commit_cache": eq9_eval_source_post_commit_cache,
            "eq9_eval_source_pending_post_commit_introspect": eq9_eval_source_pending_post_commit_introspect,
            "eq9_eval_source_post_commit_introspect": eq9_eval_source_post_commit_introspect,
            "meta_patch_applied": meta_patch_applied,
            "meta_patch_skipped": meta_patch_skipped,
            "meta_patch_timeout": meta_patch_timeout,
            "meta_patch_error": meta_patch_error,
            "meta_patch_other_skip": meta_patch_other_skip,
            "authz_decisions": authz_decisions,
            "authz_denied": authz_denied,
            "authz_allowed": authz_allowed,
            "authz_reason_did_principal_required": authz_reason_did_principal_required,
            "authz_reason_context_not_allowed": authz_reason_context_not_allowed,
            "authz_reason_write_requires_owner_or_tenant": authz_reason_write_requires_owner_or_tenant,
            "authz_reason_read_requires_owner_or_tenant": authz_reason_read_requires_owner_or_tenant,
            "authz_reason_admin_principal_required": authz_reason_admin_principal_required,
            "authz_reason_unknown_ledger": authz_reason_unknown_ledger,
            "authz_reason_other": authz_reason_other,
            "auth_principal_source_legacy_header": auth_principal_source_legacy_header,
            "auth_principal_source_did_header": auth_principal_source_did_header,
            "auth_principal_source_other": auth_principal_source_other,
            "auth_principal_mode_compat": auth_principal_mode_compat,
            "auth_principal_mode_did_strict": auth_principal_mode_did_strict,
            "auth_error_class_token_validation_failed": auth_error_class_token_validation_failed,
            "auth_error_class_other": auth_error_class_other,
            "auth_token_validation_failures": auth_token_validation_failures,
        },
        "metrics": {
            "verifiable_response_rate": _safe_divide(verifiable_turns, total_events),
            "resolve_success_rate": _safe_divide(resolve_successes, resolve_attempts),
            "search_avoided_rate": _safe_divide(
                max(search_requested_total - search_used_total, 0.0),
                search_requested_total,
            ),
            "memory_cost_per_10k_words": memory_cost_per_10k,
            "memory_cost_per_1m_tokens": memory_cost_per_1m_tokens,
            "resolved_coords_per_turn": _safe_divide(chat_resolve_successes, chat_turns),
            "chat_cost_per_turn_cents": _safe_divide(chat_cost * 100.0, chat_turns),
            "chat_cost_per_1m_tokens": chat_cost_per_1m_tokens,
            "quarantine_write_rate": _safe_divide(quarantine_writes, chat_turns),
            "authz_deny_rate": _safe_divide(authz_denied, authz_decisions),
            "did_principal_usage_rate": _safe_divide(
                auth_principal_source_did_header,
                auth_principal_source_did_header + auth_principal_source_legacy_header,
            ),
            "legacy_principal_usage_rate": _safe_divide(
                auth_principal_source_legacy_header,
                auth_principal_source_did_header + auth_principal_source_legacy_header,
            ),
            "auth_token_validation_failure_rate": _safe_divide(
                auth_token_validation_failures,
                authz_decisions,
            ),
            "meta_patch_applied_rate": _safe_divide(meta_patch_applied, meta_patch_events),
            "meta_patch_timeout_rate": _safe_divide(meta_patch_timeout, meta_patch_events),
            "meta_patch_error_rate": _safe_divide(meta_patch_error, meta_patch_events),
        },
        "metrics_coverage": {
            "search_requested_samples": int(search_requested_total),
            "search_used_samples": int(search_used_total),
            "search_invariant_repairs": repairs_int,
            "memory_cost_token_samples": len(memory_cost_token_samples),
            "authz_decision_samples": authz_decisions,
            "auth_principal_source_samples": (
                auth_principal_source_legacy_header
                + auth_principal_source_did_header
                + auth_principal_source_other
            ),
            "auth_error_class_samples": auth_error_class_token_validation_failed + auth_error_class_other,
            "eq9_eval_source_samples": eq9_eval_source_samples,
            "meta_patch_samples": meta_patch_events,
        },
        "alerts": alerts,
    }


def _pick_latest_chat_event(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for payload in reversed(list(events)):
        ids = payload.get("ids") if isinstance(payload, dict) else None
        turn_id = ids.get("turn_id") if isinstance(ids, dict) else None
        provider = payload.get("provider")
        model = payload.get("model")
        if provider or model:
            return payload
        if isinstance(turn_id, str) and not turn_id.startswith("enrich-"):
            return payload
    return None


def _extract_e6_diagnostics(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    mode = event.get("e6_mode")
    route = event.get("e6_route")
    tier = event.get("e6_quality_tier")
    bridge = event.get("e6_bridge_allowed")
    promotion = event.get("e6_promotion_allowed")
    v_int_mean_3 = event.get("e6_v_int_mean_3")
    v_int_std_3 = event.get("e6_v_int_std_3")
    if all(value is None for value in (mode, route, tier, bridge, promotion, v_int_mean_3, v_int_std_3)):
        return None
    return {
        "mode": mode,
        "route": route,
        "quality_tier": tier,
        "bridge_allowed": bridge,
        "promotion_allowed": promotion,
        "V_int_mean_3": v_int_mean_3,
        "V_int_std_3": v_int_std_3,
    }


def _auth_observability_runbook_links() -> dict[str, str]:
    default_ops = "backend/utils/ref/migration-runbook-commands.md"
    default_auth = "backend/utils/ref/rollout.md#p0-06-observability-baseline"
    return {
        "ops_runbook": str(os.getenv("STATS_AUTH_OBS_RUNBOOK_URL", default_ops)).strip() or default_ops,
        "auth_rollout_runbook": str(os.getenv("STATS_AUTH_ROLLOUT_RUNBOOK_URL", default_auth)).strip() or default_auth,
    }


def _calculate_metrics_from_rollup(rollup: dict[str, float | int]) -> dict[str, Any]:
    events = float(rollup.get("events", 0))
    emitted_refs = float(rollup.get("emitted_refs", 0))
    resolve_attempts = float(rollup.get("resolve_attempts", 0))
    resolve_successes = float(rollup.get("resolve_successes", 0))
    search_requested_raw = float(rollup.get("search_requested", 0))
    search_used_raw = float(rollup.get("search_used", 0))
    search_requested, search_used, repaired_search_used = _normalize_search_counts(
        search_requested_raw,
        search_used_raw,
    )
    memory_cost = float(rollup.get("memory_cost", 0.0))
    ingest_words = float(rollup.get("ingest_words", 0))
    memory_tokens = float(rollup.get("memory_tokens", 0))
    total_cost = float(rollup.get("cost", 0.0))
    chat_turns = float(rollup.get("chat_turns", 0))
    chat_cost = float(rollup.get("chat_cost", 0.0))
    chat_resolve_successes = float(rollup.get("chat_resolve_successes", 0))
    quarantine_writes = float(rollup.get("quarantine_writes", 0))
    quarantine_loop_blocked = float(rollup.get("quarantine_loop_blocked", 0))
    quarantine_audit_blocked = float(rollup.get("quarantine_audit_blocked", 0))
    quarantine_persistence_error = float(rollup.get("quarantine_persistence_error", 0))
    eq9_eval_source_pre_commit = float(rollup.get("eq9_eval_source_pre_commit", 0))
    eq9_eval_source_post_commit_metadata = float(rollup.get("eq9_eval_source_post_commit_metadata", 0))
    eq9_eval_source_post_commit_cache = float(rollup.get("eq9_eval_source_post_commit_cache", 0))
    eq9_eval_source_pending_post_commit_introspect = float(
        rollup.get("eq9_eval_source_pending_post_commit_introspect", 0)
    )
    eq9_eval_source_post_commit_introspect = float(rollup.get("eq9_eval_source_post_commit_introspect", 0))
    meta_patch_applied = float(rollup.get("meta_patch_applied", 0))
    meta_patch_skipped = float(rollup.get("meta_patch_skipped", 0))
    meta_patch_timeout = float(rollup.get("meta_patch_timeout", 0))
    meta_patch_error = float(rollup.get("meta_patch_error", 0))
    meta_patch_other_skip = float(rollup.get("meta_patch_other_skip", 0))
    authz_decisions = float(rollup.get("authz_decisions", 0))
    authz_denied = float(rollup.get("authz_denied", 0))
    authz_allowed = float(rollup.get("authz_allowed", 0))
    authz_reason_did_principal_required = float(rollup.get("authz_reason_did_principal_required", 0))
    authz_reason_context_not_allowed = float(rollup.get("authz_reason_context_not_allowed", 0))
    authz_reason_write_requires_owner_or_tenant = float(
        rollup.get("authz_reason_write_requires_owner_or_tenant", 0)
    )
    authz_reason_read_requires_owner_or_tenant = float(
        rollup.get("authz_reason_read_requires_owner_or_tenant", 0)
    )
    authz_reason_admin_principal_required = float(rollup.get("authz_reason_admin_principal_required", 0))
    authz_reason_unknown_ledger = float(rollup.get("authz_reason_unknown_ledger", 0))
    authz_reason_other = float(rollup.get("authz_reason_other", 0))
    auth_principal_source_legacy_header = float(rollup.get("auth_principal_source_legacy_header", 0))
    auth_principal_source_did_header = float(rollup.get("auth_principal_source_did_header", 0))
    auth_principal_source_other = float(rollup.get("auth_principal_source_other", 0))
    auth_principal_mode_compat = float(rollup.get("auth_principal_mode_compat", 0))
    auth_principal_mode_did_strict = float(rollup.get("auth_principal_mode_did_strict", 0))
    auth_error_class_token_validation_failed = float(
        rollup.get("auth_error_class_token_validation_failed", 0)
    )
    auth_error_class_other = float(rollup.get("auth_error_class_other", 0))
    auth_token_validation_failures = float(rollup.get("auth_token_validation_failures", 0))
    gen_input_tokens = float(rollup.get("gen_input_tokens", 0))
    gen_output_tokens = float(rollup.get("gen_output_tokens", 0))
    total_chat_tokens = gen_input_tokens + gen_output_tokens

    memory_cost_per_10k = memory_cost * 10000.0 / ingest_words if ingest_words > 0 else 0.0
    memory_tokens_est = _estimate_memory_tokens(memory_tokens, ingest_words)
    memory_cost_per_1m_tokens = memory_cost * 1_000_000.0 / memory_tokens_est if memory_tokens_est > 0 else 0.0
    search_avoided = max(search_requested - search_used, 0.0)
    eq9_eval_source_samples = (
        eq9_eval_source_pre_commit
        + eq9_eval_source_post_commit_metadata
        + eq9_eval_source_post_commit_cache
        + eq9_eval_source_pending_post_commit_introspect
        + eq9_eval_source_post_commit_introspect
    )
    meta_patch_events = meta_patch_applied + meta_patch_skipped
    chat_cost_per_1m_tokens = (
        (chat_cost / total_chat_tokens) * 1_000_000.0 if total_chat_tokens > 0 else 0.0
    )

    repairs_int = int(float(rollup.get("search_invariant_repairs", 0)) + repaired_search_used)
    quarantine_alert = _build_quarantine_alert(
        quarantine_writes=int(quarantine_writes),
        chat_turns=int(chat_turns),
        quarantine_loop_blocked=int(quarantine_loop_blocked),
        quarantine_audit_blocked=int(quarantine_audit_blocked),
        quarantine_persistence_error=int(quarantine_persistence_error),
    )
    alerts = _build_search_integrity_alert(repairs_int)
    alerts.update(quarantine_alert)
    alerts.update(
        _build_auth_observability_alert(
            authz_denied=int(authz_denied),
            authz_decisions=int(authz_decisions),
            auth_token_validation_failures=int(auth_token_validation_failures),
        )
    )
    return {
        "totals": {
            "events": int(events),
            "cost": total_cost,
            "emitted_refs": int(emitted_refs),
            "resolve_attempts": int(resolve_attempts),
            "resolve_successes": int(resolve_successes),
            "search_requested": int(search_requested),
            "search_used": int(search_used),
            "search_invariant_repairs": repairs_int,
            "chat_turns": int(chat_turns),
            "chat_cost": chat_cost,
            "chat_resolve_successes": int(chat_resolve_successes),
            "quarantine_writes": int(quarantine_writes),
            "quarantine_loop_blocked": int(quarantine_loop_blocked),
            "quarantine_audit_blocked": int(quarantine_audit_blocked),
            "quarantine_persistence_error": int(quarantine_persistence_error),
            "eq9_eval_source_pre_commit": int(eq9_eval_source_pre_commit),
            "eq9_eval_source_post_commit_metadata": int(eq9_eval_source_post_commit_metadata),
            "eq9_eval_source_post_commit_cache": int(eq9_eval_source_post_commit_cache),
            "eq9_eval_source_pending_post_commit_introspect": int(
                eq9_eval_source_pending_post_commit_introspect
            ),
            "eq9_eval_source_post_commit_introspect": int(eq9_eval_source_post_commit_introspect),
            "meta_patch_applied": int(meta_patch_applied),
            "meta_patch_skipped": int(meta_patch_skipped),
            "meta_patch_timeout": int(meta_patch_timeout),
            "meta_patch_error": int(meta_patch_error),
            "meta_patch_other_skip": int(meta_patch_other_skip),
            "authz_decisions": int(authz_decisions),
            "authz_denied": int(authz_denied),
            "authz_allowed": int(authz_allowed),
            "authz_reason_did_principal_required": int(authz_reason_did_principal_required),
            "authz_reason_context_not_allowed": int(authz_reason_context_not_allowed),
            "authz_reason_write_requires_owner_or_tenant": int(authz_reason_write_requires_owner_or_tenant),
            "authz_reason_read_requires_owner_or_tenant": int(authz_reason_read_requires_owner_or_tenant),
            "authz_reason_admin_principal_required": int(authz_reason_admin_principal_required),
            "authz_reason_unknown_ledger": int(authz_reason_unknown_ledger),
            "authz_reason_other": int(authz_reason_other),
            "auth_principal_source_legacy_header": int(auth_principal_source_legacy_header),
            "auth_principal_source_did_header": int(auth_principal_source_did_header),
            "auth_principal_source_other": int(auth_principal_source_other),
            "auth_principal_mode_compat": int(auth_principal_mode_compat),
            "auth_principal_mode_did_strict": int(auth_principal_mode_did_strict),
            "auth_error_class_token_validation_failed": int(auth_error_class_token_validation_failed),
            "auth_error_class_other": int(auth_error_class_other),
            "auth_token_validation_failures": int(auth_token_validation_failures),
        },
        "metrics": {
            "verifiable_response_rate": _safe_divide(resolve_successes, emitted_refs),
            "resolve_success_rate": _safe_divide(resolve_successes, resolve_attempts),
            "search_avoided_rate": _safe_divide(search_avoided, search_requested),
            "memory_cost_per_10k_words": memory_cost_per_10k,
            "memory_cost_per_1m_tokens": memory_cost_per_1m_tokens,
            "resolved_coords_per_turn": _safe_divide(chat_resolve_successes, chat_turns),
            "chat_cost_per_turn_cents": _safe_divide(chat_cost * 100.0, chat_turns),
            "chat_cost_per_1m_tokens": chat_cost_per_1m_tokens,
            "quarantine_write_rate": _safe_divide(quarantine_writes, chat_turns),
            "authz_deny_rate": _safe_divide(authz_denied, authz_decisions),
            "did_principal_usage_rate": _safe_divide(
                auth_principal_source_did_header,
                auth_principal_source_did_header + auth_principal_source_legacy_header,
            ),
            "legacy_principal_usage_rate": _safe_divide(
                auth_principal_source_legacy_header,
                auth_principal_source_did_header + auth_principal_source_legacy_header,
            ),
            "auth_token_validation_failure_rate": _safe_divide(
                auth_token_validation_failures,
                authz_decisions,
            ),
            "meta_patch_applied_rate": _safe_divide(meta_patch_applied, meta_patch_events),
            "meta_patch_timeout_rate": _safe_divide(meta_patch_timeout, meta_patch_events),
            "meta_patch_error_rate": _safe_divide(meta_patch_error, meta_patch_events),
        },
        "metrics_coverage": {
            "search_requested_samples": int(search_requested),
            "search_used_samples": int(search_used),
            "search_invariant_repairs": repairs_int,
            "authz_decision_samples": int(authz_decisions),
            "auth_principal_source_samples": int(
                auth_principal_source_legacy_header
                + auth_principal_source_did_header
                + auth_principal_source_other
            ),
            "auth_error_class_samples": int(
                auth_error_class_token_validation_failed + auth_error_class_other
            ),
            "eq9_eval_source_samples": int(eq9_eval_source_samples),
            "meta_patch_samples": int(meta_patch_events),
            "memory_cost_token_basis": "memory_tokens"
            if memory_tokens > 0
            else ("ingest_words_estimate" if ingest_words > 0 else "none"),
        },
        "alerts": alerts,
    }


def _score_tier(score: float) -> str:
    tier_t = float(os.getenv("COORD_TIER_T", "0.85"))
    tier_l = float(os.getenv("COORD_TIER_L", "0.70"))
    tier_q = float(os.getenv("COORD_TIER_Q", "0.50"))
    if score >= tier_t:
        return "T"
    if score >= tier_l:
        return "L"
    if score >= tier_q:
        return "Q"
    return "S"


def _include_recent_tier(tier: str, age_turns: int) -> bool:
    ttl_by_tier = {
        "S": 1,
        "Q": 2,
        "L": 3,
        "T": 4,
    }
    ttl = ttl_by_tier.get(tier, 1)
    return age_turns <= ttl


def _normalize_session_namespace(session_id: str) -> str:
    cleaned = session_id.strip()
    if cleaned.startswith("chat-"):
        return cleaned
    return f"chat-{cleaned}"


def _header_ledger_scope(request: Request) -> str | None:
    for header in ("x-ledger-id", "x-ledger", "x-ledger-id-h64"):
        value = request.headers.get(header)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _session_namespace_candidates(request: Request, session_id: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(value: str | None) -> None:
        if not isinstance(value, str):
            return
        clean = value.strip()
        if not clean or clean in seen:
            return
        seen.add(clean)
        candidates.append(clean)

    _append(_header_ledger_scope(request))
    _append(session_id)
    _append(_normalize_session_namespace(session_id))
    return candidates


def _rollup_has_signal(rollup: dict[str, float | int]) -> bool:
    return any(isinstance(value, (int, float)) and float(value) > 0 for value in rollup.values())


def _aggregate_namespace_rollup(db: Any, namespace: str) -> dict[str, float | int]:
    aggregated: dict[str, float | int] = {}
    for row in _iter_rollups(db, namespace):
        for metric, value in row.items():
            aggregated[metric] = aggregated.get(metric, 0) + value
    return aggregated


def _extract_telemetry_namespace(payload: TelemetryEventRequest, request: Request) -> tuple[str, bool]:
    payload_scope = (payload.namespace or payload.entity or "").strip()
    header_scope = ""
    for header in ("x-ledger-id", "x-ledger", "x-ledger-id-h64"):
        value = request.headers.get(header)
        if isinstance(value, str) and value.strip():
            header_scope = value.strip()
            break
    if payload_scope and header_scope and payload_scope != header_scope:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "ledger_scope_mismatch",
                "payload_ledger_id": payload_scope,
                "header_ledger_id": header_scope,
            },
        )
    if payload_scope:
        return payload_scope, True
    if header_scope:
        return header_scope, True
    return _normalize_session_namespace(payload.session_id), False


@router.get("/session")
async def session_stats(
    request: Request,
    session_id: str = Query(..., description="Session identifier"),
    limit: int = Query(200, ge=1, le=5000, description="Maximum events to inspect"),
) -> Dict[str, Any]:
    db = _get_db(request)
    namespace_candidates = _session_namespace_candidates(request, session_id)
    namespace = namespace_candidates[0] if namespace_candidates else _normalize_session_namespace(session_id)
    authorize_or_raise(
        request,
        ledger_id=namespace,
        action="ledger.read",
        explicit_context=True,
    )

    selected_namespace = namespace
    events: list[dict[str, Any]] = []
    for candidate in namespace_candidates:
        candidate_events = _iter_events(db, candidate, limit)
        if candidate_events:
            selected_namespace = candidate
            events = candidate_events
            break
    latest_event = events[-1] if events else None
    latest_chat_event = _pick_latest_chat_event(events) if events else None
    if events:
        payload = _calculate_metrics_from_events(events)
        payload.update(
            {
                "session_id": session_id,
                "namespace": selected_namespace,
                "requested_namespace": namespace,
                "namespace_candidates": namespace_candidates,
                "source": "events",
                "event_count": len(events),
                "latest_event": latest_event,
                "e6_diagnostics": _extract_e6_diagnostics(latest_chat_event),
            }
        )
        return payload

    telemetry_store = _get_telemetry_store(request)
    rollup = telemetry_store.read_session(session_id)
    source = "rollup_session"
    selected_rollup_namespace: str | None = None
    if not _rollup_has_signal(rollup):
        for candidate in namespace_candidates:
            candidate_rollup = _aggregate_namespace_rollup(db, candidate)
            if _rollup_has_signal(candidate_rollup):
                rollup = candidate_rollup
                selected_rollup_namespace = candidate
                source = "rollup_namespace"
                break
    payload = _calculate_metrics_from_rollup(rollup)
    payload.update(
        {
            "session_id": session_id,
            "namespace": selected_rollup_namespace or selected_namespace,
            "requested_namespace": namespace,
            "namespace_candidates": namespace_candidates,
            "source": source,
            "latest_event": latest_event,
            "e6_diagnostics": _extract_e6_diagnostics(latest_chat_event),
        }
    )
    return payload


@router.get("/ledger")
async def ledger_stats(
    request: Request,
    namespace: str = Query(..., description="Namespace identifier"),
) -> Dict[str, Any]:
    db = _get_db(request)
    authorize_or_raise(
        request,
        ledger_id=namespace,
        action="ledger.read",
        explicit_context=True,
    )
    telemetry_store = _get_telemetry_store(request)

    now = datetime.now(timezone.utc)
    last_24h_rollup = {}
    for offset in (0, 1):
        rollup = telemetry_store.read_rollup(namespace, now - timedelta(days=offset))
        for metric, value in rollup.items():
            last_24h_rollup[metric] = last_24h_rollup.get(metric, 0) + value

    lifetime_rollup: dict[str, float | int] = {}
    for rollup in _iter_rollups(db, namespace):
        for metric, value in rollup.items():
            lifetime_rollup[metric] = lifetime_rollup.get(metric, 0) + value

    return {
        "namespace": namespace,
        "last_24h": _calculate_metrics_from_rollup(last_24h_rollup),
        "lifetime": _calculate_metrics_from_rollup(lifetime_rollup),
    }


@router.get("/global")
async def global_stats(request: Request) -> Dict[str, Any]:
    db = _get_db(request)
    lifetime_rollup: dict[str, float | int] = {}
    namespaces: set[str] = set()
    for namespace, rollup in _iter_all_rollups(db):
        namespaces.add(namespace)
        for metric, value in rollup.items():
            lifetime_rollup[metric] = lifetime_rollup.get(metric, 0) + value

    payload = _calculate_metrics_from_rollup(lifetime_rollup)
    payload.update(
        {
            "scope": "global",
            "namespace_count": len(namespaces),
            "source": "rollup",
        }
    )
    return payload


@router.get("/observability/auth")
async def auth_observability_stats(request: Request) -> Dict[str, Any]:
    db = _get_db(request)
    lifetime_rollup: dict[str, float | int] = {}
    namespaces: set[str] = set()
    for namespace, rollup in _iter_all_rollups(db):
        namespaces.add(namespace)
        for metric, value in rollup.items():
            lifetime_rollup[metric] = lifetime_rollup.get(metric, 0) + value

    calculated = _calculate_metrics_from_rollup(lifetime_rollup)
    totals = calculated.get("totals") or {}
    metrics = calculated.get("metrics") or {}
    alerts = calculated.get("alerts") or {}
    runbooks = _auth_observability_runbook_links()
    return {
        "scope": "global_auth_observability",
        "namespace_count": len(namespaces),
        "source": "rollup",
        "dashboards": {
            "deny_reasons": {
                "did_principal_required": int(totals.get("authz_reason_did_principal_required") or 0),
                "context_not_allowed": int(totals.get("authz_reason_context_not_allowed") or 0),
                "write_requires_owner_or_tenant": int(
                    totals.get("authz_reason_write_requires_owner_or_tenant") or 0
                ),
                "read_requires_owner_or_tenant": int(
                    totals.get("authz_reason_read_requires_owner_or_tenant") or 0
                ),
                "admin_principal_required": int(totals.get("authz_reason_admin_principal_required") or 0),
                "unknown_ledger": int(totals.get("authz_reason_unknown_ledger") or 0),
                "other": int(totals.get("authz_reason_other") or 0),
            },
            "principal_source_usage": {
                "did_header": int(totals.get("auth_principal_source_did_header") or 0),
                "legacy_header": int(totals.get("auth_principal_source_legacy_header") or 0),
                "other": int(totals.get("auth_principal_source_other") or 0),
                "did_usage_rate": float(metrics.get("did_principal_usage_rate") or 0.0),
                "legacy_usage_rate": float(metrics.get("legacy_principal_usage_rate") or 0.0),
            },
            "auth_error_classes": {
                "token_validation_failed": int(totals.get("auth_error_class_token_validation_failed") or 0),
                "other": int(totals.get("auth_error_class_other") or 0),
            },
        },
        "alerts": {
            "authz_deny_spike_active": bool(alerts.get("authz_deny_spike_active")),
            "authz_deny_rate": float(alerts.get("authz_deny_rate") or 0.0),
            "authz_deny_count_threshold": int(alerts.get("authz_deny_count_threshold") or 0),
            "authz_deny_rate_threshold": float(alerts.get("authz_deny_rate_threshold") or 0.0),
            "auth_token_validation_failure_active": bool(
                alerts.get("auth_token_validation_failure_active")
            ),
            "auth_token_validation_failures": int(alerts.get("auth_token_validation_failures") or 0),
            "auth_token_validation_failure_threshold": int(
                alerts.get("auth_token_validation_failure_threshold") or 0
            ),
        },
        "runbook_links": runbooks,
    }


@router.get("/observability/provenance")
async def provenance_observability_stats(
    request: Request,
    limit_per_namespace: int = Query(25, ge=1, le=200),
    sample_limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    db = _get_db(request)
    store = LedgerStoreV2(db)
    namespaces = sorted(_discover_entry_namespaces(db))
    status_counts: dict[str, int] = {
        "dual_write_ok": 0,
        "legacy_only": 0,
        "did_only": 0,
        "missing_identity": 0,
    }
    samples: list[dict[str, Any]] = []
    scanned_entries = 0

    for namespace in namespaces:
        entries = store.list_by_namespace(namespace, limit=limit_per_namespace, reverse=True)
        for entry in entries:
            scanned_entries += 1
            metadata = entry.state.metadata if isinstance(entry.state.metadata, dict) else {}
            status = _provenance_dual_write_status_from_metadata(metadata)
            label = str(status.get("status") or "missing_identity")
            status_counts[label] = status_counts.get(label, 0) + 1
            if label != "dual_write_ok" and len(samples) < sample_limit:
                samples.append(
                    {
                        "coordinate": entry.key.as_path(),
                        "namespace": namespace,
                        "status": label,
                    }
                )

    total = sum(status_counts.values())
    dual_ok = int(status_counts.get("dual_write_ok") or 0)
    return {
        "scope": "provenance_dual_write",
        "namespace_count": len(namespaces),
        "entry_sample_count": scanned_entries,
        "status_counts": status_counts,
        "coverage": {
            "sampled_entries": total,
            "dual_write_ok_rate": (dual_ok / total) if total > 0 else 0.0,
        },
        "samples": samples,
    }


@router.get("/latest")
async def latest_event(
    request: Request,
    session_id: str = Query(..., description="Session identifier"),
) -> Dict[str, Any]:
    db = _get_db(request)
    namespace = _normalize_session_namespace(session_id)
    authorize_or_raise(
        request,
        ledger_id=namespace,
        action="ledger.read",
        explicit_context=True,
    )
    events = _iter_events(db, namespace, limit=1)
    return {
        "session_id": session_id,
        "namespace": namespace,
        "latest_event": events[-1] if events else None,
    }


@router.get("/accuracy")
async def accuracy_stats(
    request: Request,
    session_id: str = Query(..., description="Session identifier"),
) -> Dict[str, Any]:
    db = _get_db(request)
    namespace = _normalize_session_namespace(session_id)
    authorize_or_raise(
        request,
        ledger_id=namespace,
        action="ledger.read",
        explicit_context=True,
    )
    telemetry_store = _get_telemetry_store(request)
    rollup = telemetry_store.read_session(session_id)
    payload = _calculate_metrics_from_rollup(rollup)
    totals = payload.get("totals") or {}
    metrics = payload.get("metrics") or {}
    verifiable_rate = float(metrics.get("verifiable_response_rate") or 0.0)
    total_events = int(totals.get("events") or 0)
    verifiable_numerator = int(round(verifiable_rate * total_events)) if total_events else 0
    return {
        "session_id": session_id,
        "verifiable_rate": verifiable_rate,
        "verifiable_rate_percent": int(round(verifiable_rate * 100)) if total_events else 0,
        "verifiable_rate_numerator": verifiable_numerator,
        "verifiable_rate_denominator": total_events,
        "resolve_success_rate": float(metrics.get("resolve_success_rate") or 0.0),
    }


@router.get("/tiers")
async def tier_snapshot(
    request: Request,
    session_id: str = Query(..., description="Session identifier"),
    limit: int = Query(10, ge=1, le=100, description="Recent entries to inspect"),
) -> Dict[str, Any]:
    db = _get_db(request)
    namespace = _normalize_session_namespace(session_id)
    authorize_or_raise(
        request,
        ledger_id=namespace,
        action="ledger.read",
        explicit_context=True,
    )
    store = _get_ledger_service(request).store
    entries = store.list_by_namespace(namespace, limit=limit, reverse=True)

    half_life = float(os.getenv("COORD_RECENCY_HALFLIFE_MIN", "60"))
    now = datetime.now(timezone.utc)

    items: list[dict[str, Any]] = []
    for age_turns, entry in enumerate(entries):
        meta = entry.state.metadata if entry and entry.state else {}
        teleology = meta.get("teleology_alignment")
        if not isinstance(teleology, (int, float)):
            appraisal = meta.get("appraisal") if isinstance(meta, dict) else None
            if isinstance(appraisal, dict) and isinstance(appraisal.get("score"), (int, float)):
                teleology = float(appraisal.get("score"))
            else:
                teleology = 0.5
        created_at = entry.created_at
        if created_at is None:
            recency = 0.5
        else:
            ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            minutes = max((now - ts).total_seconds() / 60.0, 0.0)
            recency = float(math.exp(-minutes / half_life))

        combined = float(teleology) * recency
        tier = _score_tier(combined)
        include = _include_recent_tier(tier, age_turns)

        items.append(
            {
                "age_turns": age_turns,
                "coordinate": entry.key.as_path(),
                "tier": tier,
                "include": include,
                "score": round(combined, 3),
                "teleology": round(float(teleology), 3),
                "recency": round(recency, 3),
                "created_at": created_at.isoformat() if created_at else None,
                "kind": meta.get("kind") if isinstance(meta, dict) else None,
                "topics": meta.get("topics") if isinstance(meta, dict) else None,
            }
        )

    return {
        "session_id": session_id,
        "namespace": namespace,
        "limit": limit,
        "items": items,
    }


@router.post("/telemetry")
async def record_telemetry(
    request: Request,
    payload: TelemetryEventRequest,
) -> Dict[str, Any]:
    telemetry_store = _get_telemetry_store(request)
    namespace, has_explicit_context = _extract_telemetry_namespace(payload, request)
    authorize_or_raise(
        request,
        ledger_id=namespace,
        action="ledger.write",
        explicit_context=has_explicit_context,
    )
    timestamp = payload.timestamp or datetime.now(timezone.utc)
    turn_id = payload.turn_id or f"frontend-{int(timestamp.timestamp() * 1000)}"

    legacy_gen_cost: float | None = None
    extra = payload.model_extra if isinstance(payload.model_extra, dict) else {}
    raw_gen_cost = extra.get("gen_cost")
    if isinstance(raw_gen_cost, (int, float)):
        legacy_gen_cost = float(raw_gen_cost)

    telemetry = TurnTelemetry(
        ids=TelemetryIds(
            session_id=payload.session_id,
            namespace=namespace,
            entity=payload.entity or namespace,
            turn_id=turn_id,
            timestamp=timestamp,
        ),
        model=payload.model,
        provider=payload.provider,
        cost=payload.cost if payload.cost is not None else legacy_gen_cost,
        gen_cost=legacy_gen_cost,
        gen_input_tokens=payload.gen_input_tokens,
        gen_output_tokens=payload.gen_output_tokens,
        memory_cost=payload.memory_cost,
        memory_tokens=payload.memory_tokens,
        ingest_words=payload.ingest_words,
        latency_ms=payload.latency_ms,
        references=TelemetryReferences(
            emitted_refs=int(payload.emitted_refs or 0),
            resolve_attempts=int(payload.resolve_attempts or 0),
            resolve_successes=int(payload.resolve_successes or 0),
        ),
        search=TelemetrySearchFlags(
            requested=payload.search_requested,
            used=payload.search_used,
            succeeded=payload.search_succeeded,
        ),
        authz_denied=payload.authz_denied,
        authz_reason=payload.authz_reason,
        authz_principal_source=payload.authz_principal_source,
        authz_principal_mode=payload.authz_principal_mode,
        auth_error_class=payload.auth_error_class,
        auth_token_validation_failed=payload.auth_token_validation_failed,
        eq9_eval_source=payload.eq9_eval_source,
        meta_patch_status=payload.meta_patch_status,
        meta_patch_reason=payload.meta_patch_reason,
    )
    telemetry = attach_request_benchmark_context(
        telemetry,
        request,
        surface=SurfaceName.CONTROL_PLANE,
        mode="stats_ingest",
        tenant_id=namespace,
    )
    telemetry_store.write_event(telemetry)
    # Flush so the event is observable by synchronous consumers (e.g. dashboards
    # and tests) immediately after the request returns.
    telemetry_store.flush_pending()
    return {"status": "ok"}


@router.get("/telemetry-exporter")
async def telemetry_exporter_stats(request: Request) -> Dict[str, Any]:
    telemetry_store = _get_telemetry_store(request)
    return {
        "status": "ok",
        "exporter": telemetry_store.read_exporter_stats(),
    }
