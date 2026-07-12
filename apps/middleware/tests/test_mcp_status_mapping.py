import asyncio

import pytest

pytest.importorskip("cryptography")

from utils.mcp_server import DSMCPServer


class _DummyKey:
    def sign(self, _data: bytes) -> bytes:
        return b"\x00" * 64


def _make_server(tmp_path) -> DSMCPServer:
    server = DSMCPServer(backend_base="http://127.0.0.1:8080", timeout_s=2.0)
    server.append_pipeline_enabled = False
    server.queue_path = tmp_path / "mcp_queue.jsonl"
    server.state_path = tmp_path / "mcp_state.json"
    server._load_private_key = lambda: _DummyKey()  # type: ignore[method-assign]

    async def _fake_introspection(**_kwargs):
        return {}

    server._get_backend_introspection = _fake_introspection  # type: ignore[method-assign]
    return server


def test_append_event_status_committed(tmp_path):
    server = _make_server(tmp_path)

    async def fake_post(path: str, payload: dict):
        if path == "/sync/v0/push":
            return {"accepted": 1, "duplicate": 0, "quarantine": 0, "results": [{"status": "accepted", "event_id": "aa", "stream_key": "s", "seq": 1}]}
        raise AssertionError(path)

    server._post = fake_post  # type: ignore[method-assign]
    result = asyncio.run(server._tool_append_event({"payload": {"msg": "x"}}))
    assert result["status"] == "committed"


def test_append_event_status_duplicate(tmp_path):
    server = _make_server(tmp_path)

    async def fake_post(path: str, payload: dict):
        if path == "/sync/v0/push":
            return {"accepted": 0, "duplicate": 1, "quarantine": 0, "results": [{"status": "duplicate"}]}
        raise AssertionError(path)

    server._post = fake_post  # type: ignore[method-assign]
    result = asyncio.run(server._tool_append_event({"payload": {"msg": "x"}}))
    assert result["status"] == "duplicate"


def test_append_event_status_quarantine(tmp_path):
    server = _make_server(tmp_path)

    async def fake_post(path: str, payload: dict):
        if path == "/sync/v0/push":
            return {"accepted": 0, "duplicate": 0, "quarantine": 1, "results": [{"status": "quarantine"}]}
        raise AssertionError(path)

    server._post = fake_post  # type: ignore[method-assign]
    result = asyncio.run(server._tool_append_event({"payload": {"msg": "x"}}))
    assert result["status"] == "quarantine"


def test_append_event_status_queued_on_inconclusive(tmp_path):
    server = _make_server(tmp_path)

    async def fake_post(path: str, payload: dict):
        if path == "/sync/v0/push":
            return {"accepted": 0, "duplicate": 0, "quarantine": 0, "results": []}
        raise AssertionError(path)

    server._post = fake_post  # type: ignore[method-assign]
    result = asyncio.run(server._tool_append_event({"payload": {"msg": "x"}, "queue_on_failure": True}))
    assert result["status"] == "queued"
    assert result["push"]["accepted"] == 0
