from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.benchmarks.rollup_prod_telemetry_benchmarks import (
    build_production_artifacts,
    iter_telemetry_events,
    write_artifacts,
)


def _event(
    *,
    timestamp: datetime,
    mode: str = "chat",
    namespace: str = "chat-demo",
    surface: str = "chat",
    latency_ms: float = 12.5,
    cost: float = 0.02,
    emitted_refs: int = 1,
    resolve_attempts: int = 1,
    resolve_successes: int = 1,
    search_requested: bool | None = True,
    search_used: bool | None = True,
    search_succeeded: bool | None = True,
    authz_denied: bool | None = False,
) -> dict[str, object]:
    return {
        "ids": {
            "session_id": "session-1",
            "namespace": namespace,
            "entity": namespace,
            "turn_id": f"turn-{int(timestamp.timestamp())}",
            "timestamp": timestamp.isoformat(),
        },
        "mode": mode,
        "surface": surface,
        "latency_ms": latency_ms,
        "cost": cost,
        "gen_input_tokens": 100,
        "gen_output_tokens": 50,
        "references": {
            "emitted_refs": emitted_refs,
            "resolve_attempts": resolve_attempts,
            "resolve_successes": resolve_successes,
        },
        "search": {
            "requested": search_requested,
            "used": search_used,
            "succeeded": search_succeeded,
        },
        "authz_denied": authz_denied,
    }


def test_build_production_artifacts_emits_schema_valid_rollups() -> None:
    now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    artifacts = build_production_artifacts(
        [
            _event(timestamp=now - timedelta(minutes=10), mode="chat"),
            _event(timestamp=now - timedelta(minutes=5), mode="resolve", emitted_refs=0, resolve_attempts=1, resolve_successes=1),
        ],
        checked_at=now,
        repo_commit_sha="9fead54",
    )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["suite_id"] == "prod_telemetry_benchmark"
    assert artifact["mode"] == "full_dss"
    assert artifact["run_config"]["evidence_source"] == "prod_telemetry"
    assert artifact["metrics"]["latency"]["status"] == "present"
    assert artifact["metrics"]["traceability"]["status"] == "present"


def test_iter_telemetry_events_filters_namespaces_and_time_window() -> None:
    now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    db = {
        b"metrics:events:chat-demo:1": json.dumps(_event(timestamp=now - timedelta(hours=1), namespace="chat-demo")).encode(),
        b"metrics:events:other:2": json.dumps(_event(timestamp=now - timedelta(hours=30), namespace="other")).encode(),
    }

    events = list(
        iter_telemetry_events(
            db,
            namespaces={"chat-demo"},
            since=now - timedelta(hours=24),
            until=now,
        )
    )

    assert len(events) == 1
    assert events[0]["ids"]["namespace"] == "chat-demo"


def test_write_artifacts_writes_named_json_files(tmp_path: Path) -> None:
    now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    artifacts = build_production_artifacts(
        [_event(timestamp=now - timedelta(minutes=1), mode="semantic_only", namespace="chat-demo")],
        checked_at=now,
        repo_commit_sha="9fead54",
    )

    paths = write_artifacts(artifacts, output_root=tmp_path)

    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["run_id"].startswith("prod_telemetry_benchmark-semantic_only-")
