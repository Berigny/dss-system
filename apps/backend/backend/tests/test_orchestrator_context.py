from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.fieldx_kernel.orchestrator import assemble_context, _resolve_explicit_references


@dataclass
class _FakeKey:
    namespace: str
    identifier: str

    def as_path(self) -> str:
        return f"{self.namespace}:{self.identifier}"


@dataclass
class _FakeState:
    metadata: dict


@dataclass
class _FakeEntry:
    key: _FakeKey
    state: _FakeState
    created_at: datetime
    notes: str | None = None
    pinned: bool = False


class _FakeStore:
    def __init__(self, entries: list[_FakeEntry], blobs: dict[str, str] | None = None) -> None:
        self.entries = entries
        self.list_calls: list[tuple[str, int]] = []
        self.blobs = blobs or {}
        self._db: dict[bytes, bytes] = {}

    def list_by_namespace(self, namespace: str, limit: int = 0) -> list[_FakeEntry]:
        self.list_calls.append((namespace, limit))
        return self.entries[:limit]

    def read(self, entry_id: str) -> None:
        return None

    def read_blob_text(self, coord: str) -> str | None:
        return self.blobs.get(coord)


class _FakeSubstrate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def get_body_prime(self, entity: str, prime: int) -> dict:
        self.calls.append((entity, prime))
        return {"key": f"{entity}:{prime}", "state": {"metadata": {"content": "summary"}}}


class _MinimalLedger:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_s2_metadata(self, entity: str, prime: str = "11") -> dict:
        self.calls.append("get_s2_metadata")
        return {"appraisal": {"score": 0.92, "note": "ok"}}

    def get_s2_summary_ref(self, entity: str, prime: str = "11") -> int:
        self.calls.append("get_s2_summary_ref")
        return 23

    def get_s2_claims(self, entity: str, *, prime: str = "19", limit: int | None = None) -> list[dict]:
        self.calls.append("get_s2_claims")
        claims = [{"claim": "alpha"}, {"claim": "beta"}, {"claim": "gamma"}]
        return claims[:limit] if isinstance(limit, int) else claims

    def get_s1_recent(self, entity: str, *, limit: int | None = None) -> list[dict]:
        self.calls.append("get_s1_recent")
        recent = [{"state": {"metadata": {"content": "fallback"}}}]
        return recent[:limit] if isinstance(limit, int) else recent


def test_assemble_context_blob_full_tier_expands_payload() -> None:
    ledger = _MinimalLedger()
    entries = [
        _FakeEntry(
            key=_FakeKey("entity", "blob-1"),
            state=_FakeState({"full_payload_coord": "entity:blob-abc", "content": "summary"}),
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    store = _FakeStore(entries, blobs={"entity:blob-abc": "the full intact text"})

    result = asyncio.run(
        assemble_context(
            entity="entity",
            query=None,
            k=1,
            ledger=ledger,
            store=store,
            payload_tier="blob_full",
        )
    )

    recent = result["recent"]
    assert len(recent) == 1
    assert recent[0].get("payload_blob", {}).get("text") == "the full intact text"
    assert recent[0].get("payload_blob", {}).get("coordinate") == "entity:blob-abc"


def test_assemble_context_default_tier_does_not_expand_payload() -> None:
    ledger = _MinimalLedger()
    entries = [
        _FakeEntry(
            key=_FakeKey("entity", "blob-1"),
            state=_FakeState({"full_payload_coord": "entity:blob-abc", "content": "summary"}),
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    store = _FakeStore(entries, blobs={"entity:blob-abc": "the full intact text"})

    result = asyncio.run(
        assemble_context(
            entity="entity",
            query=None,
            k=1,
            ledger=ledger,
            store=store,
        )
    )

    assert "payload_blob" not in result["recent"][0]


def test_assemble_context_uses_minimal_ledger_accessors() -> None:
    ledger = _MinimalLedger()
    store = _FakeStore(
        [
            _FakeEntry(
                key=_FakeKey("entity", "one"),
                state=_FakeState({"content": "first"}),
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
            _FakeEntry(
                key=_FakeKey("entity", "two"),
                state=_FakeState({"content": "second"}),
                created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ),
        ]
    )
    substrate = _FakeSubstrate()

    result = asyncio.run(
        assemble_context(
            entity="entity",
            query=None,
            k=2,
            ledger=ledger,
            substrate=substrate,
            store=store,
        )
    )

    assert [entry["key"] for entry in result["recent"]] == ["entity:one", "entity:two"]
    assert result["claims"] == [{"claim": "alpha"}, {"claim": "beta"}]
    assert result["summary"]["key"] == "entity:23"
    assert result["assessments"] == {"score": 0.92, "note": "ok"}
    assert result["retrieved"] == []
    assert result["k"] == 2
    assert "get_s1_recent" not in ledger.calls


def test_assemble_context_falls_back_to_s1_when_store_missing() -> None:
    ledger = _MinimalLedger()

    result = asyncio.run(
        assemble_context(
            entity="entity",
            query=None,
            k=1,
            ledger=ledger,
            store=None,
        )
    )

    assert result["recent"] == [{"state": {"metadata": {"content": "fallback"}}}]
    assert "get_s1_recent" in ledger.calls


def test_assemble_context_limits_default_candidate_catalog_to_four() -> None:
    now = datetime.now(timezone.utc)
    ledger = _MinimalLedger()
    store = _FakeStore(
        [
            _FakeEntry(
                key=_FakeKey("entity", f"WX-{idx}"),
                state=_FakeState({"kind": "chat", "content": f"recent chat {idx}"}),
                created_at=now,
            )
            for idx in range(1, 7)
        ]
    )

    result = asyncio.run(
        assemble_context(
            entity="entity",
            query="what do you know from our conversation history",
            k=6,
            ledger=ledger,
            store=store,
        )
    )

    assert len(result["retrieved"]) == 4
    assert len(result["candidate_catalog"]) == 4
    assert all("origin_attestation" in item for item in result["retrieved"])


def test_resolve_explicit_references_falls_back_namespace(monkeypatch) -> None:
    class _ExplicitStore:
        def __init__(self, entry: _FakeEntry) -> None:
            self._entry = entry

        def read(self, entry_id: str):
            if entry_id == "chat-demo:ATT-deadbeef-123":
                return self._entry
            return None

    monkeypatch.setenv("COORD_DEFAULT_NAMESPACES", "chat-demo")
    entry = _FakeEntry(
        key=_FakeKey("chat-demo", "ATT-deadbeef-123"),
        state=_FakeState({"attachment": True}),
        created_at=datetime(2026, 3, 3, tzinfo=timezone.utc),
    )
    store = _ExplicitStore(entry)

    resolved = _resolve_explicit_references(
        "Please use chat-demo-session:ATT-deadbeef-123",
        "chat-demo-session",
        store,
    )

    assert resolved
    assert resolved[0].get("key") == "chat-demo:ATT-deadbeef-123"


def test_assemble_context_history_query_surfaces_recent_chat_candidates() -> None:
    now = datetime.now(timezone.utc)
    ledger = _MinimalLedger()
    store = _FakeStore(
        [
            _FakeEntry(
                key=_FakeKey("entity", "WX-1"),
                state=_FakeState({"kind": "chat", "content": "recent chat one"}),
                created_at=now,
            ),
            _FakeEntry(
                key=_FakeKey("entity", "WX-2"),
                state=_FakeState({"kind": "chat", "content": "recent chat two"}),
                created_at=now,
            ),
        ]
    )

    result = asyncio.run(
        assemble_context(
            entity="entity",
            query="what do you know from our conversation history",
            k=2,
            ledger=ledger,
            store=store,
        )
    )

    retrieved = result.get("retrieved") or []
    assert retrieved
    assert retrieved[0].get("tier_rank", 0) >= 1
    assert retrieved[0].get("source") == "recent"
