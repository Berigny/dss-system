"""Roll up production telemetry into canonical Epic 5 benchmark artefacts."""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from backend.benchmarks.artifact_schema import (
    BenchmarkArtifact,
    MetricEntry,
    MetricGroup,
    validate_benchmark_artifact,
)
from backend.benchmarks.publish_dashboard_benchmarks import build_publication_payload

APP_ROOT = Path(__file__).resolve().parents[2]
DB_FILE = "ledger.db"
DEFAULT_OUTPUT_ROOT = APP_ROOT / "artifacts" / "prod_benchmark_rollups"

_ALLOWED_MODES = {"semantic_only", "coordinate_guided", "full_dss"}


def _load_json_bytes(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        decoded = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        payload = json.loads(decoded)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def iter_telemetry_events(
    db: Any,
    *,
    namespaces: set[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    prefix = b"metrics:events:"
    if hasattr(db, "iterkeys"):
        keys_iterator = db.iterkeys()  # type: ignore[attr-defined]
        keys_iterator.seek(prefix)
        for raw_key in keys_iterator:
            key = raw_key.encode() if isinstance(raw_key, str) else raw_key
            if not key.startswith(prefix):
                break
            payload = _load_json_bytes(db.get(key))
            if _event_matches(payload, namespaces=namespaces, since=since, until=until):
                yield payload
        return

    for raw_key, raw_value in db.items():
        key = raw_key.encode() if isinstance(raw_key, str) else raw_key
        if not key.startswith(prefix):
            continue
        payload = _load_json_bytes(raw_value)
        if _event_matches(payload, namespaces=namespaces, since=since, until=until):
            yield payload


def _event_matches(
    payload: dict[str, Any] | None,
    *,
    namespaces: set[str] | None,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    if not payload:
        return False
    ids = payload.get("ids") if isinstance(payload.get("ids"), dict) else {}
    namespace = str(ids.get("namespace") or "").strip()
    if namespaces and namespace not in namespaces:
        return False
    timestamp = _parse_dt(ids.get("timestamp"))
    if timestamp is None:
        return False
    if since is not None and timestamp < since:
        return False
    if until is not None and timestamp > until:
        return False
    return True


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_mode(raw_mode: Any) -> str:
    cleaned = str(raw_mode or "").strip().lower()
    if cleaned in _ALLOWED_MODES:
        return cleaned
    if cleaned in {"semantic", "semantic-search", "search_only"}:
        return "semantic_only"
    if cleaned in {"coordinate", "coordinate-guided", "coord_guided"}:
        return "coordinate_guided"
    return "full_dss"


def _safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _metric_group(metrics: dict[str, MetricEntry], *, absence_reason: str) -> MetricGroup:
    if metrics:
        return MetricGroup(status="present", metrics=metrics)
    return MetricGroup(status="absent", absence_reason=absence_reason)


def build_production_artifacts(
    events: Iterable[dict[str, Any]],
    *,
    executed_at: datetime | None = None,
    checked_at: datetime | None = None,
    max_age_hours: int = 24,
    repo_commit_sha: str | None = None,
    namespace_label: str = "all_namespaces",
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[_normalize_mode(event.get("mode"))].append(event)

    if not grouped:
        raise ValueError("no telemetry events were available for rollup")

    checked = checked_at or datetime.now(timezone.utc)
    commit_sha = str(repo_commit_sha or os.getenv("GIT_SHA") or "unknown").strip() or "unknown"
    artifacts: list[dict[str, Any]] = []
    for mode, rows in sorted(grouped.items()):
        artifacts.append(
            build_single_production_artifact(
                rows,
                mode=mode,
                executed_at=executed_at,
                checked_at=checked,
                max_age_hours=max_age_hours,
                repo_commit_sha=commit_sha,
                namespace_label=namespace_label,
            )
        )
    return artifacts


def build_single_production_artifact(
    events: Iterable[dict[str, Any]],
    *,
    mode: str,
    executed_at: datetime | None,
    checked_at: datetime,
    max_age_hours: int,
    repo_commit_sha: str,
    namespace_label: str,
) -> dict[str, Any]:
    rows = list(events)
    if not rows:
        raise ValueError("cannot build a production artefact from zero events")

    timestamps = [
        _parse_dt(((row.get("ids") or {}) if isinstance(row.get("ids"), dict) else {}).get("timestamp"))
        for row in rows
    ]
    parsed_timestamps = [stamp for stamp in timestamps if stamp is not None]
    executed = executed_at or max(parsed_timestamps)
    age_hours = max(0.0, (checked_at - executed).total_seconds() / 3600.0)
    freshness_status = "fresh" if age_hours <= max_age_hours else "stale"

    latencies = [float(row.get("latency_ms") or 0.0) for row in rows if row.get("latency_ms") is not None]
    emitted_refs = sum(int(((row.get("references") or {}) if isinstance(row.get("references"), dict) else {}).get("emitted_refs") or 0) for row in rows)
    resolve_attempts = sum(int(((row.get("references") or {}) if isinstance(row.get("references"), dict) else {}).get("resolve_attempts") or 0) for row in rows)
    resolve_successes = sum(int(((row.get("references") or {}) if isinstance(row.get("references"), dict) else {}).get("resolve_successes") or 0) for row in rows)
    search_requested = sum(1 for row in rows if ((row.get("search") or {}) if isinstance(row.get("search"), dict) else {}).get("requested") is True)
    search_used = sum(1 for row in rows if ((row.get("search") or {}) if isinstance(row.get("search"), dict) else {}).get("used") is True)
    search_succeeded = sum(1 for row in rows if ((row.get("search") or {}) if isinstance(row.get("search"), dict) else {}).get("succeeded") is True)
    authz_decisions = sum(1 for row in rows if row.get("authz_denied") is not None or row.get("authz_reason"))
    authz_denied = sum(1 for row in rows if row.get("authz_denied") is True)
    quarantine_writes = sum(1 for row in rows if row.get("quarantine_write") is True)
    total_cost = sum(float(row.get("cost") or 0.0) for row in rows)
    input_tokens = sum(int(row.get("gen_input_tokens") or 0) for row in rows)
    output_tokens = sum(int(row.get("gen_output_tokens") or 0) for row in rows)
    source_modes = sorted({str(row.get("mode") or "").strip() for row in rows if str(row.get("mode") or "").strip()})
    surfaces = sorted({str(row.get("surface") or "").strip() for row in rows if str(row.get("surface") or "").strip()})
    namespaces = sorted({
        str(((row.get("ids") or {}) if isinstance(row.get("ids"), dict) else {}).get("namespace") or "").strip()
        for row in rows
        if str(((row.get("ids") or {}) if isinstance(row.get("ids"), dict) else {}).get("namespace") or "").strip()
    })

    retrieval_metrics: dict[str, MetricEntry] = {}
    if rows:
        retrieval_metrics["requests"] = MetricEntry(value=len(rows), unit="count", description="Production requests included in this rollup window.")
    if search_requested:
        retrieval_metrics["search_usage_rate"] = MetricEntry(
            value=round(_safe_divide(search_used, search_requested), 4),
            unit="ratio",
            description="Fraction of search-requested turns that actually used search.",
        )
        retrieval_metrics["search_success_rate"] = MetricEntry(
            value=round(_safe_divide(search_succeeded, search_requested), 4),
            unit="ratio",
            description="Fraction of search-requested turns that completed successfully.",
        )
    if resolve_attempts:
        retrieval_metrics["resolve_success_rate"] = MetricEntry(
            value=round(_safe_divide(resolve_successes, resolve_attempts), 4),
            unit="ratio",
            description="Fraction of coordinate resolve attempts that succeeded in production traffic.",
        )

    traceability_metrics: dict[str, MetricEntry] = {}
    if emitted_refs:
        traceability_metrics["emitted_refs"] = MetricEntry(value=emitted_refs, unit="count", description="References emitted across sampled production requests.")
        traceability_metrics["ref_emission_rate"] = MetricEntry(
            value=round(_safe_divide(emitted_refs, len(rows)), 4),
            unit="refs_per_request",
            description="Average emitted references per request in the rollup window.",
        )
    if resolve_attempts:
        traceability_metrics["resolve_attempts"] = MetricEntry(value=resolve_attempts, unit="count", description="Coordinate resolution attempts observed in production traffic.")
        traceability_metrics["resolve_successes"] = MetricEntry(value=resolve_successes, unit="count", description="Coordinate resolution successes observed in production traffic.")

    governance_metrics: dict[str, MetricEntry] = {}
    if authz_decisions:
        governance_metrics["authz_denied_rate"] = MetricEntry(
            value=round(_safe_divide(authz_denied, authz_decisions), 4),
            unit="ratio",
            description="Fraction of recorded authorization decisions that were denied.",
        )
        governance_metrics["authz_decisions"] = MetricEntry(
            value=authz_decisions,
            unit="count",
            description="Authorization decisions captured during the rollup window.",
        )
    if quarantine_writes:
        governance_metrics["quarantine_write_rate"] = MetricEntry(
            value=round(_safe_divide(quarantine_writes, len(rows)), 4),
            unit="ratio",
            description="Fraction of production requests that attempted quarantined writes.",
        )

    latency_metrics: dict[str, MetricEntry] = {}
    if latencies:
        sorted_latencies = sorted(latencies)
        p95_index = max(0, math.ceil(0.95 * len(sorted_latencies)) - 1)
        latency_metrics["avg_latency_ms"] = MetricEntry(
            value=round(sum(sorted_latencies) / len(sorted_latencies), 3),
            unit="ms",
            description="Average request latency captured from production telemetry.",
        )
        latency_metrics["p95_latency_ms"] = MetricEntry(
            value=round(sorted_latencies[p95_index], 3),
            unit="ms",
            description="95th percentile request latency captured from production telemetry.",
        )

    cost_metrics: dict[str, MetricEntry] = {}
    if total_cost or input_tokens or output_tokens:
        cost_metrics["total_cost_usd"] = MetricEntry(
            value=round(total_cost, 6),
            unit="usd",
            description="Estimated total request cost represented by this production rollup.",
        )
        cost_metrics["avg_cost_per_request_cents"] = MetricEntry(
            value=round(_safe_divide(total_cost * 100.0, len(rows)), 4),
            unit="cents",
            description="Average request cost across production requests in the rollup.",
        )
        cost_metrics["input_tokens"] = MetricEntry(value=input_tokens, unit="tokens", description="Input tokens counted in production telemetry.")
        cost_metrics["output_tokens"] = MetricEntry(value=output_tokens, unit="tokens", description="Output tokens counted in production telemetry.")

    metrics = {
        "retrieval": _metric_group(
            retrieval_metrics,
            absence_reason="prod_telemetry_window_missing_retrieval_signals",
        ),
        "traceability": _metric_group(
            traceability_metrics,
            absence_reason="prod_telemetry_window_missing_traceability_signals",
        ),
        "governance": _metric_group(
            governance_metrics,
            absence_reason="prod_telemetry_window_missing_governance_signals",
        ),
        "latency": _metric_group(
            latency_metrics,
            absence_reason="prod_telemetry_window_missing_latency_signals",
        ),
        "cost": _metric_group(
            cost_metrics,
            absence_reason="prod_telemetry_window_missing_cost_signals",
        ),
    }
    absent_groups = [name for name, payload in metrics.items() if payload.status == "absent"]
    status = "partial" if absent_groups else "success"

    artifact = {
        "artefact_schema_version": "1.0.0",
        "run_id": f"prod_telemetry_benchmark-{mode}-{executed.strftime('%Y%m%dT%H%M%SZ')}",
        "suite_id": "prod_telemetry_benchmark",
        "suite_version": "v1",
        "executed_at": executed,
        "mode": mode,
        "status": status,
        "repos": [
            {
                "name": "ds-backend-local",
                "commit_sha": repo_commit_sha,
                "role": "production_telemetry_rollup_engine",
                "required_for_run": True,
            }
        ],
        "datasets": [
            {
                "name": "production_telemetry",
                "version": namespace_label,
                "split": "live_sample",
                "record_count": len(rows),
            }
        ],
        "metrics": metrics,
        "freshness": {
            "status": freshness_status,
            "checked_at": checked_at,
            "max_age_hours": max_age_hours,
            "age_hours": round(age_hours, 3),
        },
        "run_config": {
            "evidence_source": "prod_telemetry",
            "source_mode_count": len(source_modes),
            "surface_count": len(surfaces),
            "namespace_count": len(namespaces),
            "request_count": len(rows),
            "source_modes": ",".join(source_modes) if source_modes else "unknown",
            "surfaces": ",".join(surfaces) if surfaces else "unknown",
            "namespaces": ",".join(namespaces) if namespaces else namespace_label,
        },
    }
    validate_benchmark_artifact(BenchmarkArtifact.model_validate(artifact).model_dump(mode="json"))
    return BenchmarkArtifact.model_validate(artifact).model_dump(mode="json")


def open_db(db_root: Path):
    from rocksdict import Rdict

    return Rdict(str(db_root / DB_FILE))


def write_artifacts(artifacts: Iterable[dict[str, Any]], *, output_root: Path) -> list[Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for artifact in artifacts:
        path = output_root / f"{artifact['run_id']}.json"
        path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-root", type=Path, default=Path(os.getenv("DB_PATH", "./data")))
    parser.add_argument("--namespace", action="append", default=[], help="Optional namespace filter; may be repeated.")
    parser.add_argument("--since-hours", type=int, default=24, help="Roll up only telemetry newer than this many hours.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dashboard-output", type=Path, default=None, help="Optional DSS-Dashboard publication JSON path.")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=max(1, int(args.since_hours)))
    namespaces = {item.strip() for item in args.namespace if str(item).strip()} or None

    db = open_db(args.db_root)
    try:
        events = list(iter_telemetry_events(db, namespaces=namespaces, since=since, until=now))
    finally:
        db.close()
    if not events:
        raise SystemExit("No production telemetry events were found for the requested rollup window.")

    namespace_label = ",".join(sorted(namespaces)) if namespaces else "all_namespaces"
    artifacts = build_production_artifacts(
        events,
        checked_at=now,
        max_age_hours=max(1, int(args.since_hours)),
        namespace_label=namespace_label,
    )
    artifact_paths = write_artifacts(artifacts, output_root=args.output_root)
    if args.dashboard_output is not None:
        publication = build_publication_payload(
            artifact_paths,
            note=(
                "Published production benchmark artefacts are derived from sampled live telemetry. "
                "They are non-blocking rollups, not synchronous request-path benchmark executions."
            ),
        )
        args.dashboard_output.parent.mkdir(parents=True, exist_ok=True)
        args.dashboard_output.write_text(json.dumps(publication, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(artifact_paths)} production telemetry benchmark artefact(s) to {args.output_root}")


if __name__ == "__main__":
    main()
