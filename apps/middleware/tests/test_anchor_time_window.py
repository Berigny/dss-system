from datetime import datetime, timedelta, timezone

import routes.orchestrator as orchestrator_module


def test_anchor_time_window_yesterday_utc_boundary() -> None:
    reference = datetime(2026, 2, 23, 0, 30, tzinfo=timezone.utc)
    result = orchestrator_module._anchor_time_window("yesterday", reference_now=reference)
    assert result is not None
    start, end = result
    assert start == datetime(2026, 2, 22, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 2, 23, 0, 0, tzinfo=timezone.utc)


def test_anchor_time_window_yesterday_negative_offset_boundary() -> None:
    tz = timezone(timedelta(hours=-8))
    reference = datetime(2026, 2, 23, 0, 30, tzinfo=tz)
    result = orchestrator_module._anchor_time_window("yesterday", reference_now=reference)
    assert result is not None
    start, end = result
    assert start == datetime(2026, 2, 22, 0, 0, tzinfo=tz)
    assert end == datetime(2026, 2, 23, 0, 0, tzinfo=tz)


def test_anchor_time_window_yesterday_positive_offset_boundary() -> None:
    tz = timezone(timedelta(hours=11))
    reference = datetime(2026, 2, 23, 0, 30, tzinfo=tz)
    result = orchestrator_module._anchor_time_window("yesterday", reference_now=reference)
    assert result is not None
    start, end = result
    assert start == datetime(2026, 2, 22, 0, 0, tzinfo=tz)
    assert end == datetime(2026, 2, 23, 0, 0, tzinfo=tz)


def test_resolve_reference_now_prefers_payload_offsets() -> None:
    payload = {"utc_offset": "+11:00"}
    session = {"utc_offset": "-08:00"}
    now = orchestrator_module._resolve_reference_now(payload, session)
    assert now.utcoffset() == timedelta(hours=11)


def test_resolve_reference_now_falls_back_to_session_offset_minutes() -> None:
    payload = {}
    session = {"timezone_offset_minutes": -480}
    now = orchestrator_module._resolve_reference_now(payload, session)
    assert now.utcoffset() == timedelta(hours=-8)


def test_resolve_reference_now_uses_local_tz_when_hints_missing() -> None:
    now = orchestrator_module._resolve_reference_now({}, {})
    assert now.tzinfo is not None


def test_anchor_time_window_returns_none_without_day_hint() -> None:
    assert orchestrator_module._anchor_time_window(None) is None
