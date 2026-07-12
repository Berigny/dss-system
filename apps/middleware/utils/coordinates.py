"""Utilities for formatting message coordinates."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


def _parse_timestamp(raw_timestamp: Any) -> datetime | None:
    """Parse assorted timestamp representations into a ``datetime``.

    Returns ``None`` when the input cannot be parsed.
    """

    if raw_timestamp in (None, ""):
        return None

    # Try numeric timestamps first (seconds or milliseconds).
    try:
        timestamp_val = float(raw_timestamp)
    except (TypeError, ValueError):
        timestamp_val = None

    if timestamp_val is not None:
        if timestamp_val > 1_000_000_000_000:  # handle millisecond timestamps
            timestamp_val /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp_val, tz=timezone.utc)
        except (OSError, ValueError):
            return None

    # Fall back to parsing string timestamps.
    if isinstance(raw_timestamp, str):
        try:
            normalized = raw_timestamp.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    return None


def _format_timestamp(raw_timestamp: Any) -> tuple[str, str]:
    """Return display and stable timestamp strings.

    The display string is used in the UI, while the stable string is used when
    synthesizing coordinates.
    """

    parsed = _parse_timestamp(raw_timestamp)
    if parsed:
        display = parsed.strftime("%d/%m/%Y %H:%M")
        stable = parsed.isoformat()
    else:
        display = "unknown"
        stable = "unknown" if raw_timestamp in (None, "") else str(raw_timestamp)

    return display, stable


def format_coordinate(
    *,
    timestamp: Any,
    coordinate: str | None,
    message_id: Any,
    content: str,
) -> tuple[str, str]:
    """Build coordinate metadata using backend data or a deterministic mock.

    Returns a 2-tuple of ``(timestamp_display, coordinate_value)``. The
    coordinate value is copied directly if provided, otherwise a mock value is
    generated from the message id, timestamp, and a short content hash to keep
    it stable across renders.
    """

    timestamp_display, timestamp_stable = _format_timestamp(timestamp)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    coordinate_value = coordinate or f"{message_id}:{timestamp_stable}:{content_hash}"

    return timestamp_display, coordinate_value
