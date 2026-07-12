from __future__ import annotations

import asyncio

from backend.fieldx_kernel.guardian import GuardianOutput, guardian_enrich_turn


class _Store:
    def summarize(self, entity: str) -> dict[str, int]:
        assert entity == "chat-demo"
        return {"total_entries": 17}


def test_guardian_dry_run_skips_periodic_slow_path(monkeypatch) -> None:
    async def _unexpected_call(messages):
        raise AssertionError("dry-run guardian enrichment should not call slow-path model")

    monkeypatch.setattr(
        "backend.fieldx_kernel.guardian._call_guardian",
        _unexpected_call,
    )

    result = asyncio.run(
        guardian_enrich_turn(
            entity="chat-demo",
            user_message="user",
            assistant_reply="assistant",
            ledger=None,
            substrate=None,
            store=_Store(),
            dry_run=True,
        )
    )

    assert result is not None
    assert isinstance(result.payload, GuardianOutput)


def test_guardian_persisted_periodic_turn_still_uses_slow_path(monkeypatch) -> None:
    called = False

    async def _fake_call(messages):
        nonlocal called
        called = True
        return GuardianOutput(summary="ok", appraisal={"law_score": 1.0, "grace_score": 1.0, "drift": 0.0})

    monkeypatch.setattr(
        "backend.fieldx_kernel.guardian._call_guardian",
        _fake_call,
    )

    result = asyncio.run(
        guardian_enrich_turn(
            entity="chat-demo",
            user_message="user",
            assistant_reply="assistant",
            ledger=None,
            substrate=None,
            store=_Store(),
            dry_run=False,
        )
    )

    assert called is True
    assert result is not None
    assert result.payload.summary == "ok"
