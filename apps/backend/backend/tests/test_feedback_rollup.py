from datetime import datetime, timezone

from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate import LedgerStoreV2


def _make_entry(entry_id: str = "ns:WX-1") -> LedgerEntry:
    namespace, identifier = entry_id.split(":", 1)
    return LedgerEntry(
        key=LedgerKey(namespace=namespace, identifier=identifier),
        state=ContinuousState({}, "chat", {"content": "hello"}),
        created_at=datetime.now(timezone.utc),
    )


def test_feedback_rollup_uses_one_effective_value_per_actor_per_day() -> None:
    store = LedgerStoreV2({})
    entry = _make_entry()
    store.write(entry)

    store.submit_feedback(
        entry.key.as_path(),
        actor_id="user:alice",
        actor_type="human",
        rating=3,
        reason="first",
        ts="2026-02-20T01:00:00+00:00",
    )
    store.submit_feedback(
        entry.key.as_path(),
        actor_id="user:alice",
        actor_type="human",
        rating=1,
        reason="same-day overwrite",
        ts="2026-02-20T05:00:00+00:00",
    )
    store.submit_feedback(
        entry.key.as_path(),
        actor_id="user:alice",
        actor_type="human",
        rating=0,
        reason="next-day",
        ts="2026-02-21T01:00:00+00:00",
    )

    feedback = store.get_feedback(entry.key.as_path())
    assert feedback is not None
    rollup = feedback.get("rollup") or {}
    by_actor = rollup.get("by_actor") or {}
    alice = by_actor.get("user:alice") or {}
    # day1 effective score=1 (overwritten), day2=0 => actor total=0.5
    assert float(alice.get("score")) == 0.5
    assert int(alice.get("days")) == 2


def test_feedback_overlay_does_not_mutate_base_entry_chain_hash() -> None:
    store = LedgerStoreV2({})
    entry = _make_entry("ns:WX-immutable")
    store.write(entry)

    before = store.read(entry.key.as_path())
    assert before is not None
    before_hash = str(before.state.metadata.get("ledger_hash") or "")
    assert before_hash

    store.submit_feedback(
        entry.key.as_path(),
        actor_id="user:bob",
        actor_type="human",
        rating=3,
        reason="approved",
        ts="2026-02-22T01:00:00+00:00",
    )

    after = store.read(entry.key.as_path())
    assert after is not None
    after_hash = str(after.state.metadata.get("ledger_hash") or "")
    assert after_hash == before_hash
    assert "feedback" not in (after.state.metadata or {})
