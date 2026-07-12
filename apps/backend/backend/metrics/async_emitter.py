"""Bounded async telemetry emitter for low-overhead benchmark signal export."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Event, Lock, Thread
import time
from typing import Protocol

from backend.metrics.telemetry import TurnTelemetry


class _SyncTelemetryWriter(Protocol):
    def _write_event_sync(self, telemetry: TurnTelemetry) -> None: ...


@dataclass(slots=True)
class _QueuedTelemetry:
    telemetry: TurnTelemetry
    queued_at: float


class AsyncTelemetryEmitter:
    """Queue telemetry in memory and flush it off-thread with bounded loss."""

    def __init__(
        self,
        writer: _SyncTelemetryWriter,
        *,
        buffer_capacity: int = 2048,
        flush_batch_size: int = 64,
        flush_interval_s: float = 0.25,
        auto_start: bool = True,
    ) -> None:
        self._writer = writer
        self._buffer_capacity = max(1, int(buffer_capacity))
        self._flush_batch_size = max(1, int(flush_batch_size))
        self._flush_interval_s = max(0.01, float(flush_interval_s))
        self._queue: deque[_QueuedTelemetry] = deque()
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._enqueued_total = 0
        self._flushed_total = 0
        self._dropped_total = 0
        self._flush_error_total = 0
        self._last_flush_lag_ms = 0.0
        self._max_queue_depth = 0
        if auto_start:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = Thread(target=self._run, name="telemetry-async-emitter", daemon=True)
            self._thread.start()

    def stop(self, *, timeout: float = 1.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self.flush_once(max_items=self._flush_batch_size)

    def enqueue(self, telemetry: TurnTelemetry) -> None:
        queued = _QueuedTelemetry(telemetry=telemetry, queued_at=time.monotonic())
        with self._lock:
            if len(self._queue) >= self._buffer_capacity:
                self._dropped_total += 1
                return
            self._queue.append(queued)
            self._enqueued_total += 1
            self._max_queue_depth = max(self._max_queue_depth, len(self._queue))

    def flush_once(self, *, max_items: int | None = None) -> int:
        limit = self._flush_batch_size if max_items is None else max(1, int(max_items))
        batch: list[_QueuedTelemetry] = []
        with self._lock:
            while self._queue and len(batch) < limit:
                batch.append(self._queue.popleft())
        if not batch:
            return 0
        self._last_flush_lag_ms = max(0.0, (time.monotonic() - batch[0].queued_at) * 1000.0)
        flushed = 0
        for item in batch:
            try:
                self._writer._write_event_sync(item.telemetry)
                flushed += 1
            except Exception:
                self._flush_error_total += 1
        self._flushed_total += flushed
        return flushed

    def snapshot(self) -> dict[str, int | float | bool]:
        with self._lock:
            depth = len(self._queue)
            running = bool(self._thread and self._thread.is_alive())
        return {
            "running": running,
            "buffer_capacity": self._buffer_capacity,
            "flush_batch_size": self._flush_batch_size,
            "flush_interval_ms": int(self._flush_interval_s * 1000),
            "queue_depth": depth,
            "max_queue_depth": self._max_queue_depth,
            "enqueued_total": self._enqueued_total,
            "flushed_total": self._flushed_total,
            "dropped_total": self._dropped_total,
            "flush_error_total": self._flush_error_total,
            "exporter_lag_ms": round(self._last_flush_lag_ms, 3),
        }

    def _run(self) -> None:
        while not self._stop.wait(self._flush_interval_s):
            self.flush_once()
