from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.api import chat as chat_module


def test_consistency_flags_contradiction_when_resolved_context_exists() -> None:
    summary = {"resolved_count": 2, "requested_count": 1}
    check = chat_module._evaluate_resolution_consistency(
        response_text="I cannot access that thread content from here.",
        resolve_summary=summary,
    )
    assert check["status"] == "contradiction"
    assert check["contradiction"] is True


def test_consistency_is_ok_without_resolved_context() -> None:
    summary = {"resolved_count": 0, "requested_count": 1}
    check = chat_module._evaluate_resolution_consistency(
        response_text="I cannot access that thread content from here.",
        resolve_summary=summary,
    )
    assert check["status"] == "ok"
    assert check["reason"] == "no_resolved_context"


def test_retry_on_resolution_contradiction_applies_once(monkeypatch) -> None:
    async def _fake_complete_chat(**_kwargs):
        usage = SimpleNamespace(prompt_tokens=15, completion_tokens=9)
        return "Grounded answer from resolved COORD context.", 0.001, 24.0, usage, "stop"

    monkeypatch.setattr(chat_module, "complete_chat", _fake_complete_chat)

    result = asyncio.run(
        chat_module._retry_on_resolution_contradiction(
            provider="openai",
            base_messages=[{"role": "user", "content": "resolve this"}],
            max_tokens=256,
            candidate_text="I cannot access that thread content from here.",
            resolve_summary={"resolved_count": 1, "requested_count": 1, "resolved_coords": ["chat-demo:WX-1"]},
        )
    )

    assert result["applied"] is True
    assert "Grounded answer" in str(result["text"])
    check = result["consistency_check"]
    assert check["retried"] is True
    assert check["retry_count"] == 1
