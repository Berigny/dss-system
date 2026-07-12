from __future__ import annotations

from backend.fieldx_kernel.orchestrator import build_chat_messages


def _assistant_context_message(messages: list[dict]) -> str:
    for item in messages:
        if item.get("role") == "assistant":
            content = item.get("content")
            if isinstance(content, str) and "OPTIONAL CONTEXT" in content:
                return content
    return ""


def test_required_coords_uses_canonical_retrieved_scores() -> None:
    memories = {
        "recent": [],
        "retrieved": [
            {"coord": "chat-demo:WX-1", "relevance_score": 0.92, "tier_rank": 3},
            {"coord": "chat-demo:WX-2", "relevance_score": 0.51, "tier_rank": 1},
            {"coord": "chat-demo:WX-3", "relevance_score": 0.77, "tier_rank": 2},
        ],
    }

    messages = build_chat_messages(
        user_message="use the most relevant coord",
        history=[],
        memories=memories,
        include_system_prompts=False,
    )
    context = _assistant_context_message(messages)
    assert "--- REQUIRED COORDS ---" in context
    assert context.find("- chat-demo:WX-1") < context.find("- chat-demo:WX-3")
    assert context.find("- chat-demo:WX-3") < context.find("- chat-demo:WX-2")

