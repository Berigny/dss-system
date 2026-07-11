import asyncio
import json

import app as app_module


class _DummyStreamResponse:
    def __init__(self, status_code: int, lines: list[str]):
        self.status_code = status_code
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _DummyAsyncClient:
    def __init__(self, *args, **kwargs):
        self._lines = kwargs.pop("_lines")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method, url, json=None, headers=None):
        return _DummyStreamResponse(200, self._lines)


def _patch_async_client(monkeypatch, lines: list[str]):
    class _Factory(_DummyAsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, _lines=lines, **kwargs)

    monkeypatch.setattr(app_module.httpx, "AsyncClient", _Factory)


def test_pipeline_event_overhead_stays_bounded(monkeypatch):
    monkeypatch.setattr(app_module, "PIPELINE_WALK_METRIC_STRIDE", 4)
    monkeypatch.setattr(app_module, "MAX_PIPELINE_EVENTS", 8)

    lines = [json.dumps({"type": "token", "content": "Hello"})]
    for idx in range(80):
        lines.append(
            json.dumps(
                {
                    "type": "walk_metric_delta",
                    "payload": {
                        "hop": idx,
                        "coord": f"coord-{idx}",
                        "law": 1.0,
                        "drift": 0.0,
                        "score": 0.9,
                    },
                }
            )
        )
    lines.append(json.dumps({"type": "meta", "model": "mock", "tokens": {"input": 1, "output": 1}}))
    _patch_async_client(monkeypatch, lines)

    result = asyncio.run(
        app_module._run_openai_via_middleware_orchestrator(
            base_payload={"include_pipeline_events": True, "enable_ledger": True},
            model="mock",
            message="hello",
            history=[{"role": "user", "content": "hello"}],
            session_id="pipeline-overhead",
        )
    )

    events = result.get("pipeline_events")
    assert isinstance(events, list)
    assert len(events) <= 8


def test_pipeline_events_off_vs_on_budget(monkeypatch):
    monkeypatch.setattr(app_module, "PIPELINE_WALK_METRIC_STRIDE", 5)
    monkeypatch.setattr(app_module, "MAX_PIPELINE_EVENTS", 10)

    lines = [json.dumps({"type": "token", "content": "Hello"})]
    for idx in range(60):
        lines.append(
            json.dumps(
                {
                    "type": "walk_metric_delta",
                    "payload": {
                        "hop": idx,
                        "coord": f"coord-{idx}",
                        "law": 1.0,
                        "drift": 0.0,
                        "score": 0.9,
                    },
                }
            )
        )
    lines.append(json.dumps({"type": "meta", "model": "mock", "tokens": {"input": 1, "output": 1}}))

    _patch_async_client(monkeypatch, lines)
    result_off = asyncio.run(
        app_module._run_openai_via_middleware_orchestrator(
            base_payload={"include_pipeline_events": False, "enable_ledger": True},
            model="mock",
            message="hello",
            history=[{"role": "user", "content": "hello"}],
            session_id="pipeline-budget-off",
        )
    )
    assert "pipeline_events" not in result_off

    _patch_async_client(monkeypatch, lines)
    result_on = asyncio.run(
        app_module._run_openai_via_middleware_orchestrator(
            base_payload={"include_pipeline_events": True, "enable_ledger": True},
            model="mock",
            message="hello",
            history=[{"role": "user", "content": "hello"}],
            session_id="pipeline-budget-on",
        )
    )
    events_on = result_on.get("pipeline_events")
    assert isinstance(events_on, list)
    assert len(events_on) <= 10
