"""Telemetry and dynamic admission tuning for the qp_pure prime-lattice path."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class QpPureMetrics:
    """Thread-safe counters and sliding-window ratio for qp_pure retrieval.

    Counters:
        - attempts: qp_pure requests received.
        - hits: qp_pure requests where at least one retrieved candidate was admitted.
        - fallbacks: qp_pure requests where no retrieved candidate was admitted.

    The fallback ratio is computed over a sliding time window. When the ratio
    exceeds the configured limit and auto-relax is enabled, the effective
    admission threshold is lowered for subsequent requests.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.attempts = 0
        self.hits = 0
        self.fallbacks = 0
        self._attempt_times: deque[float] = deque()
        self._fallback_times: deque[float] = deque()
        self.base_threshold = _env_float("QP_PURE_ADMISSION_THRESHOLD", 0.35)
        self.relax_factor = _env_float("QP_PURE_RELAX_FACTOR", 0.5)
        self.fallback_ratio_limit = _env_float("QP_PURE_FALLBACK_RATIO_LIMIT", 0.30)
        self.window_seconds = _env_int("QP_PURE_METRICS_WINDOW_SECONDS", 300)
        self.auto_relax = _env_bool("QP_PURE_AUTO_RELAX", True)

    def record_attempt(self) -> None:
        now = time.time()
        with self._lock:
            self.attempts += 1
            self._attempt_times.append(now)
            self._prune(now)

    def record_hit(self) -> None:
        now = time.time()
        with self._lock:
            self.hits += 1
            self._attempt_times.append(now)
            self._prune(now)

    def record_fallback(self) -> None:
        now = time.time()
        with self._lock:
            self.fallbacks += 1
            self._fallback_times.append(now)
            self._attempt_times.append(now)
            self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._attempt_times and self._attempt_times[0] < cutoff:
            self._attempt_times.popleft()
        while self._fallback_times and self._fallback_times[0] < cutoff:
            self._fallback_times.popleft()

    def fallback_ratio(self) -> float:
        now = time.time()
        with self._lock:
            self._prune(now)
            attempts_in_window = len(self._attempt_times)
            fallbacks_in_window = len(self._fallback_times)
            if attempts_in_window == 0:
                return 0.0
            return round(fallbacks_in_window / attempts_in_window, 4)

    def effective_threshold(self) -> float:
        """Return the admission signal threshold to use for the current request."""
        ratio = self.fallback_ratio()
        if self.auto_relax and ratio > self.fallback_ratio_limit:
            return round(self.base_threshold * self.relax_factor, 4)
        return self.base_threshold

    def is_relaxed(self) -> bool:
        ratio = self.fallback_ratio()
        return self.auto_relax and ratio > self.fallback_ratio_limit

    def reset(self) -> None:
        """Reset all counters and windows (intended for tests)."""
        with self._lock:
            self.attempts = 0
            self.hits = 0
            self.fallbacks = 0
            self._attempt_times.clear()
            self._fallback_times.clear()

    def snapshot(self) -> dict[str, Any]:
        # Call the locked helpers without holding the lock ourselves to avoid
        # deadlocking on the non-reentrant mutex.
        return {
            "attempts_total": self.attempts,
            "hits_total": self.hits,
            "fallbacks_total": self.fallbacks,
            "fallback_ratio": self.fallback_ratio(),
            "window_seconds": self.window_seconds,
            "base_threshold": self.base_threshold,
            "effective_threshold": self.effective_threshold(),
            "relaxed": self.is_relaxed(),
        }


# Singleton instance used by the orchestrator and health endpoint.
qp_pure_metrics = QpPureMetrics()
