"""Tests for DSS-189: middleware p-adic contract forwarding."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import app
import routes.orchestrator as orchestrator_module
from api.client import APIClient
from config.settings import settings
from fastapi_app import SmartStreamRequest
from utils.auth_envelope import build_backend_auth_envelope


client = TestClient(app)


def _patch_permissive_runtime_actor(monkeypatch) -> None:
    def fake_resolve_runtime_actor(*, payload, auth_claims=None, provider=None, agent=None):
        actor_resolution = {
            "actor_did": "did:key:z6MkTestActor",
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "binding_candidates": [],
            "principal_status": "active",
            "tenant_id": "tenant:test",
            "session_jti": None,
            "auth_method": None,
            "verification_state": "claims_only" if auth_claims else "unverified",
            "resolution_reason": "test_fixture",
        }
        standing_envelope = {
            "standing_envelope_version": "se-v1",
            "standing_envelope_ref": "env:test",
            "actor_did": actor_resolution["actor_did"],
            "canonical_subject": None,
            "canonical_subject_source": None,
            "binding_ref": None,
            "verification_state": actor_resolution["verification_state"],
            "trust_class": "T2",
            "posture_class": "P2",
            "active_sanctions": [],
            "probation_status": None,
            "tool_scope": "full",
            "retrieval_scope": "tenant",
            "max_output_tokens": 4096,
            "write_commit_allowed": True,
            "credential_ref": None,
            "reason_code": "test_fixture",
            "resolved_at": "2026-03-15T00:00:00Z",
        }
        return actor_resolution, standing_envelope

    monkeypatch.setattr(orchestrator_module, "_resolve_runtime_actor", fake_resolve_runtime_actor)


async def _fake_assemble(**kwargs):
    _fake_assemble.calls.append(kwargs)
    return {"retrieved": [], "decoded_context": []}


_fake_assemble.calls: list[dict[str, Any]] = []


async def _fake_generate_response(**_kwargs):
    return {"text": "{}", "model": "mock", "tokens": {"input": 1, "output": 1}}


async def _fake_stream_response(**_kwargs):
    async def _gen():
        yield "ok"

    fut: asyncio.Future = asyncio.Future()
    fut.set_result({"model": "mock", "cost": 0.0, "tokens": {"input": 1, "output": 1}})
    return _gen(), fut


async def _fake_commit_answer(**_kwargs):
    return {
        "status": "success",
        "coordinate": "chat-demo:WX-test",
        "metadata": {},
        "appraisal": {"score": 1.0, "law_score": 1.0, "grace_score": 1.0, "drift": 0.0},
        "blocked": False,
    }


async def _fake_emit_telemetry(**_kwargs):
    return {}


async def _fake_decode_coordinate(_coord: str, *, entity: str | None = None, session_id: str | None = None):
    return {"status": "error"}


async def _fake_coord_walk(**_kwargs):
    return {}


async def _fake_write_walk(**_kwargs):
    return {}


def _patch_orchestrator_fakes(monkeypatch) -> None:
    _fake_assemble.calls = []
    _patch_permissive_runtime_actor(monkeypatch)
    monkeypatch.setattr(orchestrator_module.api, "assemble", _fake_assemble)
    monkeypatch.setattr(orchestrator_module.api, "decode_coordinate", _fake_decode_coordinate)
    monkeypatch.setattr(orchestrator_module.api, "coord_walk", _fake_coord_walk)
    monkeypatch.setattr(orchestrator_module.api, "write_walk", _fake_write_walk)
    monkeypatch.setattr(orchestrator_module.api, "emit_telemetry", _fake_emit_telemetry)
    monkeypatch.setattr(orchestrator_module.api, "commit_answer", _fake_commit_answer)
    monkeypatch.setattr(orchestrator_module.llm, "generate_response", _fake_generate_response)
    monkeypatch.setattr(orchestrator_module.llm, "stream_response", _fake_stream_response)
    monkeypatch.setattr(
        orchestrator_module,
        "_extract_explicit_coords",
        lambda _message: [],
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_select_choice_coord",
        lambda **kwargs: ("use_priors", None, "stop"),
    )


def _stream_events(payload: dict) -> list[dict]:
    with client.stream("POST", "/api/chat/smart_stream", json=payload) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    return [json.loads(line) for line in lines]


class TestSmartStreamRequestContract:
    def test_accepts_padic_fields(self):
        req = SmartStreamRequest(
            message="hello",
            query_primes=[2, 3, 5],
            hardening_level=2,
            include_padic_diagnostics=True,
        )
        assert req.query_primes == [2, 3, 5]
        assert req.hardening_level == 2
        assert req.include_padic_diagnostics is True

    def test_defaults_are_backward_compatible(self):
        req = SmartStreamRequest(message="hello")
        assert req.query_primes is None
        assert req.hardening_level is None
        assert req.include_padic_diagnostics is None


class TestSettingsContract:
    def test_chat_hardening_level_is_exposed(self):
        assert hasattr(settings, "CHAT_HARDENING_LEVEL")
        assert isinstance(settings.CHAT_HARDENING_LEVEL, int)


class TestAuthEnvelopeContract:
    def _make_request(self, payload: dict | None = None) -> Request:
        scope = {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("test", 80),
            "path": "/api/chat/smart_stream",
            "headers": [],
            "query_string": b"",
        }
        return Request(scope, receive=None)

    def test_advertises_diagnostics_scope(self):
        request = self._make_request()
        envelope = build_backend_auth_envelope(
            request=request,
            payload={
                "include_padic_diagnostics": True,
                "p_adic_scope": ["circulation_read"],
            },
        )
        claims = envelope.get("claims", {})
        assert "circulation_read" in claims.get("p_adic_scope", "").split(",")

    def test_advertises_query_primes_scope(self):
        request = self._make_request()
        envelope = build_backend_auth_envelope(
            request=request,
            payload={"query_primes": [2, 3], "p_adic_scope": ["prime_lattice_read"]},
        )
        claims = envelope.get("claims", {})
        assert "prime_lattice_read" in claims.get("p_adic_scope", "").split(",")

    def test_advertises_hardening_level_claim(self):
        request = self._make_request()
        envelope = build_backend_auth_envelope(
            request=request,
            payload={"hardening_level": 3},
        )
        claims = envelope.get("claims", {})
        assert claims.get("p_adic_hardening_level") == "3"

    def test_no_scope_when_fields_absent(self):
        request = self._make_request()
        envelope = build_backend_auth_envelope(request=request, payload={"message": "hello"})
        claims = envelope.get("claims", {})
        assert "p_adic_scope" not in claims


class TestAPIClientAssembleContract:
    def test_forwards_padic_fields(self, monkeypatch):
        captured: dict[str, Any] = {}

        class DummyResponse:
            status_code = 200

            def json(self):
                return {"retrieved": []}

            def raise_for_status(self):
                pass

        class DummyClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, json=None, headers=None, timeout=None):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return DummyResponse()

        monkeypatch.setattr(httpx, "AsyncClient", DummyClient)
        api = APIClient(base_url="https://example.com", api_key="test")

        async def _call():
            await api.assemble(
                "session-1",
                message="hello",
                query_primes=[2, 3, 5],
                include_padic_diagnostics=True,
                hardening_level=2,
            )

        asyncio.run(_call())

        payload = captured["json"]
        assert payload.get("query_primes") == [2, 3, 5]
        assert payload.get("include_padic_diagnostics") is True
        assert payload.get("hardening_level") == 2

    def test_omits_none_padic_fields(self, monkeypatch):
        captured: dict[str, Any] = {}

        class DummyResponse:
            status_code = 200

            def json(self):
                return {"retrieved": []}

            def raise_for_status(self):
                pass

        class DummyClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, json=None, headers=None, timeout=None):
                captured["json"] = json
                return DummyResponse()

        monkeypatch.setattr(httpx, "AsyncClient", DummyClient)
        api = APIClient(base_url="https://example.com", api_key="test")

        async def _call():
            await api.assemble("session-1", message="hello")

        asyncio.run(_call())

        payload = captured["json"]
        assert "query_primes" not in payload
        assert "include_padic_diagnostics" not in payload
        assert "hardening_level" not in payload


class TestOrchestratorAssembleForwarding:
    def test_forwards_padic_fields_to_assemble(self, monkeypatch):
        _patch_orchestrator_fakes(monkeypatch)
        events = _stream_events(
            {
                "session_id": "padic-forward-test",
                "message": "hello p-adic contract forwarding test",
                "history": [],
                "provider": "mock",
                "agent": "mock",
                "enable_ledger": True,
                "backend_stream": False,
                "_stream_passthrough": True,
                "query_primes": [2, 3, 5],
                "include_padic_diagnostics": True,
                "hardening_level": 2,
            }
        )
        assert events
        assert len(_fake_assemble.calls) == 1
        call = _fake_assemble.calls[0]
        assert call.get("query_primes") == [2, 3, 5]
        assert call.get("include_padic_diagnostics") is True
        assert call.get("hardening_level") == 2

    def test_backward_compatibility_without_padic_fields(self, monkeypatch):
        _patch_orchestrator_fakes(monkeypatch)
        events = _stream_events(
            {
                "session_id": "padic-backward-compat-test",
                "message": "hello backward compatibility test",
                "history": [],
                "provider": "mock",
                "agent": "mock",
                "enable_ledger": True,
                "backend_stream": False,
                "_stream_passthrough": True,
            }
        )
        assert events
        assert len(_fake_assemble.calls) == 1
        call = _fake_assemble.calls[0]
        assert call.get("query_primes") is None
        assert call.get("include_padic_diagnostics") is False
        assert call.get("hardening_level") == settings.CHAT_HARDENING_LEVEL


class TestOrchestratorPadicDiagnostics:
    def test_emits_padic_diagnostics_in_meta_events(self, monkeypatch):
        _patch_orchestrator_fakes(monkeypatch)

        async def fake_assemble_diagnostics(**_kwargs):
            return {
                "retrieved": [
                    {
                        "coord": "test:needle",
                        "p_adic_score": 0.9,
                        "p_adic_distance": 0.04,
                        "p_adic_norm": 0.04,
                        "source": "retrieved",
                    }
                ],
                "decoded_context": [],
                "padic_diagnostics": {
                    "query_prime_count": 1,
                    "ball_hit_count": 2,
                    "top_p_adic_score": 0.9,
                    "top_p_adic_write_cost": 0.12,
                    "metric_prime": 5,
                    "circulation_pass": 1,
                    "hysteresis_depth": 0.1,
                    "dual_sync_status": "synced",
                    "mediator_state": {"kernel_node": "Eq8"},
                },
            }

        monkeypatch.setattr(orchestrator_module.api, "assemble", fake_assemble_diagnostics)
        events = _stream_events(
            {
                "session_id": "padic-diagnostics-test",
                "message": "hello p-adic diagnostics test",
                "history": [],
                "provider": "mock",
                "agent": "mock",
                "enable_ledger": True,
                "backend_stream": False,
                "_stream_passthrough": True,
                "query_primes": [5],
                "include_padic_diagnostics": True,
            }
        )
        context_meta = next((e for e in events if e.get("type") == "context_meta"), {})
        final_meta = next((e for e in events if e.get("type") == "meta"), {})
        for evt in (context_meta, final_meta):
            diag = evt.get("padic_diagnostics") or {}
            assert diag.get("p_adic_score") == 0.9
            assert diag.get("p_adic_write_cost") == 0.12
            assert diag.get("padic_ball_hit_count") == 2
            assert diag.get("circulation_pass") == 1
            assert diag.get("hysteresis_depth") == 0.1
            assert diag.get("dual_sync_status")
            assert diag.get("mediator_state")


class TestOrchestratorBackendStreamForwarding:
    def test_backend_payload_forwards_padic_fields(self, monkeypatch):
        _patch_orchestrator_fakes(monkeypatch)
        captured: dict[str, Any] = {}

        class DummyStreamResponse:
            def __init__(self, status_code: int, lines: list[str]):
                self.status_code = status_code
                self._lines = lines
                self.text = ""

            async def aiter_lines(self):
                for line in self._lines:
                    yield line

            async def aclose(self):
                pass

        class DummyAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, json=None, headers=None, timeout=None):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                lines = [
                    '{"type":"token","content":"Hello"}',
                    '{"type":"done"}',
                ]
                return DummyStreamResponse(200, lines)

        monkeypatch.setattr(orchestrator_module.httpx, "AsyncClient", DummyAsyncClient)

        events = _stream_events(
            {
                "session_id": "padic-backend-stream-test",
                "message": "hello backend stream p-adic forwarding test",
                "history": [],
                "provider": "mock",
                "agent": "mock",
                "enable_ledger": True,
                "backend_stream": True,
                "_stream_passthrough": True,
                "query_primes": [7, 11],
                "include_padic_diagnostics": True,
                "hardening_level": 3,
            }
        )
        assert events
        assert captured.get("url", "").endswith("/chat/stream")
        payload = captured.get("json", {})
        assert payload.get("query_primes") == [7, 11]
        assert payload.get("include_padic_diagnostics") is True
        assert payload.get("hardening_level") == 3

import math

import routes.orchestrator as orchestrator_module
from shared_types.coord_schema import parse_bigint


class TestCoordinateBigIntNormalization:
    def test_meta_bigint_parses_int_str_and_float(self):
        assert orchestrator_module._meta_bigint(42) == 42
        assert orchestrator_module._meta_bigint("42") == 42
        assert orchestrator_module._meta_bigint(42.0) == 42
        assert orchestrator_module._meta_bigint(None) is None
        assert orchestrator_module._meta_bigint("not-a-number") is None

    def test_meta_bigint_parses_200_prime_product_string(self):
        primes = [
            2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
            73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151, 157,
            163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233, 239, 241,
            251, 257, 263, 269, 271, 277, 281, 283, 293, 307, 311, 313, 317, 331, 337, 347,
            349, 353, 359, 367, 373, 379, 383, 389, 397, 401, 409, 419, 421, 431, 433, 439,
            443, 449, 457, 461, 463, 467, 479, 487, 491, 499, 503, 509, 521, 523, 541, 547,
            557, 563, 569, 571, 577, 587, 593, 599, 601, 607, 613, 617, 619, 631, 641, 643,
            647, 653, 659, 661, 673, 677, 683, 691, 701, 709, 719, 727, 733, 739, 743, 751,
            757, 761, 769, 773, 787, 797, 809, 811, 821, 823, 827, 829, 839, 853, 857, 859,
            863, 877, 881, 883, 887, 907, 911, 919, 929, 937, 941, 947, 953, 967, 971, 977,
            983, 991, 997, 1009, 1013, 1019, 1021, 1031, 1033, 1039, 1049, 1051, 1061, 1063,
            1069, 1087, 1091, 1093, 1097, 1103, 1109, 1117, 1123, 1129, 1151, 1153, 1163,
            1171, 1181, 1187, 1193, 1201, 1213, 1217, 1223,
        ]
        assert len(primes) == 200
        product = math.prod(primes)
        parsed = orchestrator_module._meta_bigint(str(product))
        assert parsed == product
        assert parsed == parse_bigint(str(product))
