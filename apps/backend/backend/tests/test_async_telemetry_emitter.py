from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.stats import router as stats_router
from backend.metrics.async_emitter import AsyncTelemetryEmitter
from backend.metrics.store import TelemetryStore, close_telemetry_emitter
from backend.metrics.telemetry import TelemetryIds, TurnTelemetry


def _sample_turn() -> TurnTelemetry:
    return TurnTelemetry(
        ids=TelemetryIds(
            session_id="session-1",
            namespace="chat-demo",
            entity="chat-demo",
            turn_id="turn-1",
            timestamp=datetime.now(timezone.utc),
        ),
        latency_ms=12.5,
        request_id="req-1",
        tenant_id="chat-demo",
        surface="backend",
        mode="resolve",
        build_sha="b910a35",
        principal_hash="sha256:abc",
    )


def test_async_emitter_flushes_queued_events_into_rollups() -> None:
    db: dict[bytes, bytes] = {}
    store = TelemetryStore(db)
    telemetry = _sample_turn()

    store.write_event(telemetry)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        rollup = store.read_rollup("chat-demo", telemetry.ids.timestamp)
        if rollup.get("events") == 1:
            break
        time.sleep(0.05)

    rollup = store.read_rollup("chat-demo", telemetry.ids.timestamp)
    assert rollup.get("events") == 1
    exporter = store.read_exporter_stats()
    assert exporter["enqueued_total"] >= 1
    assert exporter["flushed_total"] >= 1
    close_telemetry_emitter(db)


def test_async_emitter_drops_when_queue_is_full() -> None:
    class _Writer:
        def __init__(self) -> None:
            self.items: list[TurnTelemetry] = []

        def _write_event_sync(self, telemetry: TurnTelemetry) -> None:
            self.items.append(telemetry)

    writer = _Writer()
    emitter = AsyncTelemetryEmitter(writer, buffer_capacity=1, flush_batch_size=8, auto_start=False)
    emitter.enqueue(_sample_turn())
    emitter.enqueue(_sample_turn().model_copy(update={"request_id": "req-2"}))

    stats = emitter.snapshot()
    assert stats["queue_depth"] == 1
    assert stats["dropped_total"] == 1

    emitter.flush_once()
    assert len(writer.items) == 1


def test_telemetry_exporter_stats_route_returns_drop_and_lag_counters() -> None:
    app = FastAPI()
    app.state.db = {}
    app.include_router(stats_router)
    client = TestClient(app)

    response = client.get("/stats/telemetry-exporter")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    exporter = body["exporter"]
    assert "dropped_total" in exporter
    assert "exporter_lag_ms" in exporter
    close_telemetry_emitter(app.state.db)
