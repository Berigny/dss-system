from datetime import datetime, timezone

import pytest

from backend.fieldx_kernel.coord_walk import coord_walk
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate import LedgerStoreV2


def test_coord_walk_includes_lawfulness_fields(monkeypatch) -> None:
    def _fake_run_full_check(prime_sequence, current_coherence):
        return True, "ok", 137, 2

    monkeypatch.setattr(
        "backend.fieldx_kernel.coord_walk.run_full_check",
        _fake_run_full_check,
    )

    store = LedgerStoreV2({})
    start_entry = LedgerEntry(
        key=LedgerKey(namespace="ns", identifier="WX-001"),
        state=ContinuousState(
            metadata={
                "resolved_coords": ["ns:WX-002"],
                "token_primes": [2],
            }
        ),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    next_entry = LedgerEntry(
        key=LedgerKey(namespace="ns", identifier="WX-002"),
        state=ContinuousState(metadata={"token_primes": [3]}),
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    store.write(start_entry)
    store.write(next_entry)

    result = coord_walk(
        start_coord="ns:WX-001",
        max_steps=1,
        current_coherence=1.0,
        store=store,
    )

    assert result["status"] == "success"
    assert result["steps"]
    step = result["steps"][0]
    assert "lawfulness_level" in step
    assert "hop_lawfulness" in step


def test_coord_walk_surfaces_flow_violation_diagnostic() -> None:
    store = LedgerStoreV2({})

    # Prime signature forces sequence [2, 137, 13] when considering candidate,
    # which should trigger C-cross (S1 context routing to S2 odd) and block.
    start_entry = LedgerEntry(
        key=LedgerKey(namespace="ns", identifier="WX-101"),
        state=ContinuousState(
            metadata={
                "resolved_coords": ["ns:WX-102"],
                "token_primes": [2, 137],
            }
        ),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    candidate_entry = LedgerEntry(
        key=LedgerKey(namespace="ns", identifier="WX-102"),
        state=ContinuousState(metadata={"token_primes": [13]}),
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    store.write(start_entry)
    store.write(candidate_entry)

    result = coord_walk(
        start_coord="ns:WX-101",
        max_steps=1,
        current_coherence=1.0,
        store=store,
    )

    assert result["status"] == "success"
    assert result["termination_reason"] == "blocked"
    assert isinstance(result.get("flow_diagnostic"), str)
    assert "c-cross" in str(result.get("flow_diagnostic")).lower()


def test_coord_walk_is_replayable() -> None:
    """The same walk inputs must produce the same path and steps."""
    store = LedgerStoreV2({})
    start_entry = LedgerEntry(
        key=LedgerKey(namespace="ns", identifier="WX-201"),
        state=ContinuousState(
            metadata={
                "resolved_coords": ["ns:WX-202"],
                "token_primes": [2],
            }
        ),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    next_entry = LedgerEntry(
        key=LedgerKey(namespace="ns", identifier="WX-202"),
        state=ContinuousState(metadata={"token_primes": [3]}),
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    store.write(start_entry)
    store.write(next_entry)

    first = coord_walk(
        start_coord="ns:WX-201",
        max_steps=1,
        current_coherence=1.0,
        store=store,
    )
    second = coord_walk(
        start_coord="ns:WX-201",
        max_steps=1,
        current_coherence=1.0,
        store=store,
    )

    assert first["path"] == second["path"]
    assert first["termination_reason"] == second["termination_reason"]
    assert len(first["steps"]) == len(second["steps"])


def test_coord_walk_emits_structured_path_log(caplog) -> None:
    """A successful walk emits a structured log record containing the coordinate path."""
    store = LedgerStoreV2({})
    start_entry = LedgerEntry(
        key=LedgerKey(namespace="ns", identifier="WX-301"),
        state=ContinuousState(
            metadata={
                "resolved_coords": ["ns:WX-302"],
                "token_primes": [2],
            }
        ),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    next_entry = LedgerEntry(
        key=LedgerKey(namespace="ns", identifier="WX-302"),
        state=ContinuousState(metadata={"token_primes": [3]}),
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    store.write(start_entry)
    store.write(next_entry)

    with caplog.at_level("INFO", logger="backend.fieldx_kernel.coord_walk"):
        result = coord_walk(
            start_coord="ns:WX-301",
            max_steps=1,
            current_coherence=1.0,
            store=store,
        )

    assert result["status"] == "success"
    info_records = [r for r in caplog.records if r.levelname == "INFO"]
    assert info_records
    found = False
    for record in info_records:
        extra = getattr(record, "coord_walk_event", False)
        if extra:
            found = True
            assert getattr(record, "coord_walk_start") == "ns:WX-301"
            assert getattr(record, "coord_walk_path") == result["path"]
            assert getattr(record, "coord_walk_steps") == len(result["steps"])
            assert getattr(record, "coord_walk_duration_ms", 0.0) >= 0.0
    assert found, "structured coord_walk_event log record not found"


def test_coord_walk_error_start_emits_structured_log(caplog) -> None:
    store = LedgerStoreV2({})
    with caplog.at_level("INFO", logger="backend.fieldx_kernel.coord_walk"):
        result = coord_walk(
            start_coord="ns:missing",
            max_steps=1,
            current_coherence=1.0,
            store=store,
        )
    assert result["status"] == "error"
    info_records = [r for r in caplog.records if r.levelname == "INFO"]
    assert any(getattr(r, "coord_walk_event", False) for r in info_records)
