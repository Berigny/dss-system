import asyncio

import pytest

pytest.importorskip("cryptography")

from utils.mcp_server import DSMCPServer


def _make_server() -> DSMCPServer:
    server = DSMCPServer(backend_base="http://127.0.0.1:8080", timeout_s=2.0)
    server.default_context_id = "ctx:mcp-test"
    return server


def test_tool_defs_include_auto_rate_coord():
    server = _make_server()
    tool_names = [tool.get("name") for tool in server._tool_defs()]
    assert "ds.auto_rate_coord" in tool_names
    assert "ds:write" in server.tool_scopes.get("ds.auto_rate_coord", set())


def test_auto_rate_coord_posts_feedback_with_defaults():
    server = _make_server()
    calls: list[tuple[str, dict]] = []

    async def fake_decode(_coordinate: str, *, entity: str | None = None, session_id: str | None = None):
        return {"status": "ok", "coordinate": _coordinate, "entity": entity, "session_id": session_id}

    async def fake_post(path: str, payload: dict):
        calls.append((path, payload))
        return {
            "status": "ok",
            "rollup": {"score": 2.0, "actors": 1, "samples": 1},
            "applied": {"actor_id": "model:auto"},
        }

    server._decode_coordinate_backend = fake_decode  # type: ignore[method-assign]
    server._post = fake_post  # type: ignore[method-assign]

    result = asyncio.run(
        server._tool_auto_rate_coord(
            {
                "coordinate": "chat-demo:WX-123",
                "rating": 2,
                "reason": "good grounding",
                "model": "openai/gpt-4.1-mini",
            }
        )
    )

    assert result["status"] == "ok"
    assert result["coordinate"] == "chat-demo:WX-123"
    assert result["rollup"]["score"] == 2.0
    assert len(calls) == 1
    assert calls[0][0] == "/ledger/feedback/auto/chat-demo:WX-123"
    assert calls[0][1]["rating"] == 2
    assert calls[0][1]["context_id"] == "ctx:mcp-test"
    assert calls[0][1]["model"] == "openai/gpt-4.1-mini"

