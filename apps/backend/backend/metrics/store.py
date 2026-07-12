"""Storage helpers for telemetry metrics in RocksDB."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import RLock
from typing import Any, MutableMapping
from weakref import WeakValueDictionary

from backend.metrics.async_emitter import AsyncTelemetryEmitter
from backend.metrics.telemetry import TurnTelemetry

_EQ9_EVAL_SOURCES = {
    "pre_commit",
    "post_commit_metadata",
    "post_commit_cache",
    "pending_post_commit_introspect",
    "post_commit_introspect",
}

_META_PATCH_STATUSES = {"applied", "skipped"}
_AUTHZ_DENY_REASONS = {
    "did_principal_required",
    "context_not_allowed",
    "write_requires_owner_or_tenant",
    "read_requires_owner_or_tenant",
    "admin_principal_required",
    "unknown_ledger",
}

_EMITTERS_LOCK = RLock()
_EMITTERS_BY_DB_ID: WeakValueDictionary[int, AsyncTelemetryEmitter] = WeakValueDictionary()


class TelemetryStore:
    """Append-only telemetry store with rollups for fast stats."""

    def __init__(self, db: MutableMapping[bytes, bytes]):
        self._db = db
        self._lock = RLock()

    def write_event(self, telemetry: TurnTelemetry) -> None:
        self._emitter().enqueue(telemetry)

    def flush_pending(self, *, max_items: int | None = None) -> int:
        return self._emitter().flush_once(max_items=max_items)

    def read_exporter_stats(self) -> dict[str, int | float | bool]:
        return self._emitter().snapshot()

    def _write_event_sync(self, telemetry: TurnTelemetry) -> None:
        ids = telemetry.ids
        timestamp = _normalize_timestamp(ids.timestamp)
        event_key = _event_key(ids.namespace, timestamp, ids.turn_id)
        payload = telemetry.model_dump(mode="json")
        encoded = json.dumps(payload).encode()
        with self._lock:
            self._db[event_key] = encoded
            self._update_rollups(ids.session_id, ids.namespace, timestamp, telemetry)

    def _emitter(self) -> AsyncTelemetryEmitter:
        db_id = id(self._db)
        with _EMITTERS_LOCK:
            emitter = _EMITTERS_BY_DB_ID.get(db_id)
            if emitter is None:
                emitter = AsyncTelemetryEmitter(self)
                _EMITTERS_BY_DB_ID[db_id] = emitter
            return emitter

    def read_rollup(self, namespace: str, date: datetime) -> dict[str, float | int]:
        key = _rollup_key(namespace, date.astimezone(timezone.utc).date().isoformat())
        return self._read_counter(key)

    def read_session(self, session_id: str) -> dict[str, float | int]:
        key = _session_key(session_id)
        return self._read_counter(key)

    def _update_rollups(
        self,
        session_id: str,
        namespace: str,
        timestamp: datetime,
        telemetry: TurnTelemetry,
    ) -> None:
        date_key = _rollup_key(namespace, timestamp.date().isoformat())
        session_key = _session_key(session_id)
        deltas = _deltas_from_telemetry(telemetry)
        self._increment_counter(date_key, deltas)
        self._increment_counter(session_key, deltas)

    def _read_counter(self, key: bytes) -> dict[str, float | int]:
        raw = self._db.get(key)
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

    def _increment_counter(self, key: bytes, deltas: dict[str, float | int]) -> None:
        current = self._read_counter(key)
        for metric, value in deltas.items():
            current[metric] = current.get(metric, 0) + value
        self._db[key] = json.dumps(current, sort_keys=True).encode()


def _normalize_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _event_key(namespace: str, timestamp: datetime, turn_id: str) -> bytes:
    stamp = timestamp.isoformat()
    return f"metrics:events:{namespace}:{stamp}:{turn_id}".encode()


def _rollup_key(namespace: str, date_str: str) -> bytes:
    return f"metrics:rollup:{namespace}:{date_str}".encode()


def _session_key(session_id: str) -> bytes:
    return f"metrics:session:{session_id}".encode()


def _deltas_from_telemetry(telemetry: TurnTelemetry) -> dict[str, float | int]:
    search = telemetry.search
    references = telemetry.references
    gen_cost = telemetry.gen_cost if telemetry.gen_cost is not None else telemetry.cost
    cost_value = float(telemetry.cost if telemetry.cost is not None else (gen_cost or 0.0))
    is_chat_turn = bool(telemetry.provider or telemetry.model)
    chat_cost = cost_value if is_chat_turn else 0.0
    requested_flag = search.requested if isinstance(search.requested, bool) else None
    used_flag = search.used if isinstance(search.used, bool) else None
    search_invariant_repairs = 0
    if used_flag and requested_flag is not True:
        requested_flag = True
        search_invariant_repairs = 1
    quarantine_write = bool(telemetry.quarantine_write is True)
    quarantine_reason = (
        telemetry.quarantine_reason.strip().lower()
        if isinstance(telemetry.quarantine_reason, str)
        else ""
    )
    quarantine_loop_blocked = int(quarantine_write and quarantine_reason == "loop_blocked")
    quarantine_audit_blocked = int(quarantine_write and quarantine_reason == "audit_blocked")
    quarantine_persistence_error = int(quarantine_write and quarantine_reason == "persistence_error")
    eq9_eval_source_raw = (
        telemetry.eq9_eval_source.strip().lower()
        if isinstance(telemetry.eq9_eval_source, str)
        else ""
    )
    eq9_eval_source = eq9_eval_source_raw if eq9_eval_source_raw in _EQ9_EVAL_SOURCES else ""
    meta_patch_status_raw = (
        telemetry.meta_patch_status.strip().lower()
        if isinstance(telemetry.meta_patch_status, str)
        else ""
    )
    meta_patch_status = meta_patch_status_raw if meta_patch_status_raw in _META_PATCH_STATUSES else ""
    meta_patch_reason = (
        telemetry.meta_patch_reason.strip().lower()
        if isinstance(telemetry.meta_patch_reason, str)
        else ""
    )
    meta_patch_applied = int(meta_patch_status == "applied")
    meta_patch_skipped = int(meta_patch_status == "skipped")
    meta_patch_timeout = int(meta_patch_status == "skipped" and meta_patch_reason == "post_introspect_timeout")
    meta_patch_error = int(meta_patch_status == "skipped" and meta_patch_reason == "post_introspect_error")
    meta_patch_other_skip = int(
        meta_patch_status == "skipped" and meta_patch_timeout == 0 and meta_patch_error == 0
    )
    authz_reason = (
        telemetry.authz_reason.strip().lower()
        if isinstance(telemetry.authz_reason, str)
        else ""
    )
    authz_denied = bool(telemetry.authz_denied is True)
    authz_has_signal = bool(authz_reason) or telemetry.authz_denied is not None
    authz_denied_from_reason = authz_reason in _AUTHZ_DENY_REASONS
    authz_denied_effective = bool(authz_denied or authz_denied_from_reason)
    principal_source = (
        telemetry.authz_principal_source.strip().lower()
        if isinstance(telemetry.authz_principal_source, str)
        else ""
    )
    principal_mode = (
        telemetry.authz_principal_mode.strip().lower()
        if isinstance(telemetry.authz_principal_mode, str)
        else ""
    )
    auth_error_class = (
        telemetry.auth_error_class.strip().lower()
        if isinstance(telemetry.auth_error_class, str)
        else ""
    )
    token_validation_failed = bool(
        telemetry.auth_token_validation_failed is True
        or auth_error_class in {"token_validation_failed", "token_invalid", "token_expired", "token_signature_invalid"}
    )
    return {
        "events": 1,
        "cost": cost_value,
        "gen_input_tokens": int(telemetry.gen_input_tokens or 0),
        "gen_output_tokens": int(telemetry.gen_output_tokens or 0),
        "memory_cost": float(telemetry.memory_cost or 0.0),
        "memory_tokens": int(telemetry.memory_tokens or 0),
        "ingest_words": int(telemetry.ingest_words or 0),
        "latency_ms": float(telemetry.latency_ms or 0.0),
        "search_requested": int(bool(requested_flag)) if requested_flag is not None else 0,
        "search_used": int(bool(used_flag and requested_flag)),
        "search_succeeded": int(bool(search.succeeded)) if search.succeeded is not None else 0,
        "search_invariant_repairs": search_invariant_repairs,
        "quarantine_writes": int(quarantine_write),
        "quarantine_loop_blocked": quarantine_loop_blocked,
        "quarantine_audit_blocked": quarantine_audit_blocked,
        "quarantine_persistence_error": quarantine_persistence_error,
        "eq9_eval_source_pre_commit": int(eq9_eval_source == "pre_commit"),
        "eq9_eval_source_post_commit_metadata": int(eq9_eval_source == "post_commit_metadata"),
        "eq9_eval_source_post_commit_cache": int(eq9_eval_source == "post_commit_cache"),
        "eq9_eval_source_pending_post_commit_introspect": int(
            eq9_eval_source == "pending_post_commit_introspect"
        ),
        "eq9_eval_source_post_commit_introspect": int(eq9_eval_source == "post_commit_introspect"),
        "meta_patch_applied": meta_patch_applied,
        "meta_patch_skipped": meta_patch_skipped,
        "meta_patch_timeout": meta_patch_timeout,
        "meta_patch_error": meta_patch_error,
        "meta_patch_other_skip": meta_patch_other_skip,
        "authz_decisions": int(authz_has_signal),
        "authz_denied": int(authz_has_signal and authz_denied_effective),
        "authz_allowed": int(authz_has_signal and not authz_denied_effective),
        "authz_reason_did_principal_required": int(authz_reason == "did_principal_required"),
        "authz_reason_context_not_allowed": int(authz_reason == "context_not_allowed"),
        "authz_reason_write_requires_owner_or_tenant": int(authz_reason == "write_requires_owner_or_tenant"),
        "authz_reason_read_requires_owner_or_tenant": int(authz_reason == "read_requires_owner_or_tenant"),
        "authz_reason_admin_principal_required": int(authz_reason == "admin_principal_required"),
        "authz_reason_unknown_ledger": int(authz_reason == "unknown_ledger"),
        "authz_reason_other": int(bool(authz_reason) and authz_reason not in _AUTHZ_DENY_REASONS),
        "auth_principal_source_legacy_header": int(principal_source == "legacy_header"),
        "auth_principal_source_did_header": int(principal_source == "did_header"),
        "auth_principal_source_other": int(bool(principal_source) and principal_source not in {"legacy_header", "did_header"}),
        "auth_principal_mode_compat": int(principal_mode == "compat"),
        "auth_principal_mode_did_strict": int(principal_mode == "did_strict"),
        "auth_error_class_token_validation_failed": int(token_validation_failed),
        "auth_error_class_other": int(bool(auth_error_class) and not token_validation_failed),
        "auth_token_validation_failures": int(token_validation_failed),
        "emitted_refs": int(references.emitted_refs or 0),
        "resolve_attempts": int(references.resolve_attempts or 0),
        "resolve_successes": int(references.resolve_successes or 0),
        "chat_turns": 1 if is_chat_turn else 0,
        "chat_cost": chat_cost,
        "chat_resolve_successes": int(references.resolve_successes or 0) if is_chat_turn else 0,
    }


def close_telemetry_emitter(db: object) -> None:
    """Best-effort shutdown for the async emitter bound to a database instance."""

    with _EMITTERS_LOCK:
        emitter = _EMITTERS_BY_DB_ID.pop(id(db), None)
    if emitter is not None:
        emitter.stop()
