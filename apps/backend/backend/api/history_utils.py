from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        seconds = float(value)
        if abs(seconds) > 1.0e11:
            seconds = seconds / 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.isdigit():
            seconds = float(cleaned)
            if abs(seconds) > 1.0e11:
                seconds = seconds / 1000.0
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def history_sort_key(message: dict[str, Any]) -> tuple[datetime, int, str]:
    ts = _parse_timestamp(message.get("timestamp") or message.get("ts") or message.get("time"))
    if ts is None:
        ts = datetime.min.replace(tzinfo=timezone.utc)

    role = message.get("role", "")
    role_priority = 0 if role == "user" else 1 if role == "assistant" else 2 if role == "system" else 3
    msg_id = (
        message.get("id")
        or message.get("message_id")
        or message.get("guid")
        or message.get("entry_id")
        or ""
    )
    return (ts, -role_priority, str(msg_id))
