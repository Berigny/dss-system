"""Smoke tests for the voice API contract."""

import importlib.util
import pathlib
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


spec = importlib.util.spec_from_file_location("voice_api", REPO_ROOT / "backend" / "api" / "voice.py")
voice_api = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(voice_api)


def test_router_has_voice_routes() -> None:
    routes = [route.path for route in voice_api.router.routes]
    assert "/v1/voice/session" in routes
    assert "/v1/voice/session/{session_id}" in routes
    assert "/v1/voice/stream/{session_id}" in routes


def test_session_store_initially_empty() -> None:
    voice_api._sessions.clear()
    assert voice_api._sessions == {}


def test_create_session_returns_stream_url() -> None:
    voice_api._sessions.clear()

    class FakeRequest:
        url = type("URL", (), {"scheme": "https", "hostname": "audio.dualsubstrate.com"})()
        headers = {"host": "audio.dualsubstrate.com"}

    import asyncio
    result = asyncio.run(voice_api.create_voice_session(FakeRequest()))
    assert "session_id" in result
    assert result["stream_url"].startswith("wss://audio.dualsubstrate.com/v1/voice/stream/")
    assert result["session_id"] in voice_api._sessions
