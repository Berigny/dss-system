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


def test_pipeline_events_gated_off(monkeypatch):
    lines = [
        json.dumps({"type": "token", "content": "Hello"}),
        json.dumps({"type": "walk_metric_delta", "payload": {"hop": 1, "coord": "c1", "law": 1.0, "drift": 0.0, "score": 0.9}}),
        json.dumps({"type": "meta", "model": "mock", "tokens": {"input": 2, "output": 3}}),
    ]
    _patch_async_client(monkeypatch, lines)

    result = asyncio.run(
        app_module._run_openai_via_middleware_orchestrator(
            base_payload={"include_pipeline_events": False, "enable_ledger": True},
            model="mock",
            message="hello",
            history=[{"role": "user", "content": "hello"}],
            session_id="pipeline-off",
        )
    )

    assert result["assistant_text"] == "Hello"
    assert "pipeline_events" not in result


def test_orchestrator_forward_context_coords(monkeypatch):
    captured: dict[str, object] = {}

    class _CaptureStreamResponse:
        def __init__(self):
            self.status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def aiter_lines(self):
            yield json.dumps({"type": "token", "content": "Hello"})
            yield json.dumps({"type": "meta", "model": "mock", "tokens": {"input": 2, "output": 3}})

        async def aread(self):
            return b""

    class _CaptureAsyncClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, json=None, headers=None):
            captured["payload"] = json
            return _CaptureStreamResponse()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", _CaptureAsyncClient)

    result = asyncio.run(
        app_module._run_openai_via_middleware_orchestrator(
            base_payload={
                "include_pipeline_events": False,
                "enable_ledger": True,
                "context_coords": ["chat-demo:ATT-1", " ", None],
            },
            model="mock",
            message="hello",
            history=[{"role": "user", "content": "hello"}],
            session_id="pipeline-context-coords",
        )
    )

    assert result["assistant_text"] == "Hello"
    payload = captured.get("payload")
    assert isinstance(payload, dict)
    assert payload.get("context_coords") == ["chat-demo:ATT-1"]


def test_pipeline_events_downsample_and_bound(monkeypatch):
    monkeypatch.setattr(app_module, "PIPELINE_WALK_METRIC_STRIDE", 3)
    monkeypatch.setattr(app_module, "MAX_PIPELINE_EVENTS", 5)

    lines: list[str] = [json.dumps({"type": "token", "content": "Hello"})]
    for idx in range(12):
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
                        "ignored_field": "x",
                    },
                }
            )
        )
    lines.extend(
        [
            json.dumps({"type": "anchor_resolution", "payload": {"status": "resolved"}}),
            json.dumps({"type": "meta_patch", "kind": "post_commit_eq9", "status": "applied", "eq9_eval_source": "post_commit_introspect", "eq9_eval_pending": False}),
            json.dumps({"type": "meta", "model": "mock", "tokens": {"input": 2, "output": 3}}),
        ]
    )
    _patch_async_client(monkeypatch, lines)

    result = asyncio.run(
        app_module._run_openai_via_middleware_orchestrator(
            base_payload={"include_pipeline_events": True, "enable_ledger": True},
            model="mock",
            message="hello",
            history=[{"role": "user", "content": "hello"}],
            session_id="pipeline-on",
        )
    )

    pipeline_events = result.get("pipeline_events")
    assert isinstance(pipeline_events, list)
    assert len(pipeline_events) <= 5

    # Verify compacted walk payload only keeps expected keys.
    walk_events = [e for e in pipeline_events if e.get("type") == "walk_metric_delta"]
    for event in walk_events:
        payload = event.get("payload")
        assert isinstance(payload, dict)
        assert set(payload.keys()) <= {"hop", "coord", "law", "drift", "score"}



def test_orchestrator_consumer_policy_deny_clears_token_leak(monkeypatch):
    lines = [
        json.dumps({"type": "token", "content": "advisory leak text"}),
        json.dumps({
            "type": "policy_envelope",
            "payload": {
                "policy_gate_version": "policy-gate-v1",
                "pp_version": "pp-v1",
                "cb_version": "cb-v1",
                "obs_posture_version": "obs-posture-v1",
                "policy_decision": "deny",
                "reason_code": "eq_blocked:eq9_telos",
                "failed_eq": "eq9_telos",
                "repair_actions": ["improve grounding", "cite evidence"],
                "trust_class": "T0",
                "eq9_posture_class": "P0",
            },
        }),
        json.dumps({"type": "pre_emission_deny", "reason": "eq_blocked:eq9_telos"}),
        json.dumps({"type": "meta", "model": "mock", "tokens": {"input": 2, "output": 3}}),
    ]
    _patch_async_client(monkeypatch, lines)

    result = asyncio.run(
        app_module._run_openai_via_middleware_orchestrator(
            base_payload={"include_pipeline_events": True, "enable_ledger": True},
            model="mock",
            message="hello",
            history=[{"role": "user", "content": "hello"}],
            session_id="policy-deny-no-leak",
        )
    )

    assistant_text = str(result.get("assistant_text") or "")
    assert "advisory leak text" not in assistant_text
    assert "Response blocked by policy gate." in assistant_text
    assert "reason_code=eq_blocked:eq9_telos" in assistant_text
    assert "failed_eq=eq9_telos" in assistant_text
    posture = ((result.get("governance") or {}).get("posture_policy") or {})
    assert posture.get("policy_gate_version") == "policy-gate-v1"
    assert posture.get("pp_version") == "pp-v1"
    assert posture.get("cb_version") == "cb-v1"
    assert posture.get("obs_posture_version") == "obs-posture-v1"
