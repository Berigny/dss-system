"""Tests for DSS-137 (telemetry compaction) and DSS-138 (lazy-load + HMAC replay)."""

from __future__ import annotations

import pytest
import time

# Import the functions under test from the orchestrator module
from routes.orchestrator import (
    _build_compact_runtime_witness,
    _build_packed_review_runtime_witness,
    _sanitize_model_context_items,
    _item_is_telemetry_overlay,
    _session_request_scoped,
    _session_get_request_scoped,
    _session_set_request_scoped,
    _session_pop_request_scoped,
    _assurance_nonce_consumed,
    _assurance_nonce_consume,
    _coord_origin_attestation,
)


class TestDSS137CompactRuntimeWitness:
    def test_compact_witness_includes_key_surfaces(self):
        snapshot = {
            "runtime_identity": {
                "ledger_id": "chat-demo",
                "library_boundary": {
                    "foundation_identity": {
                        "name": "DualSubstrate",
                        "source": "test",
                    }
                },
            },
            "s_mode": "s1",
            "control_dial": 3,
            "model": "claude-sonnet",
            "provider": "anthropic",
            "turn_count": 5,
        }
        result = _build_compact_runtime_witness(snapshot)
        assert result is not None
        assert "Ledger: chat-demo" in result
        assert "Foundation: DualSubstrate" in result
        assert "Mode: s1" in result
        assert "Dial: 3" in result
        assert "Model: claude-sonnet" in result
        assert "Provider: anthropic" in result
        assert "Turn: 5" in result

    def test_compact_witness_omits_empty_fields(self):
        snapshot = {"runtime_identity": {"ledger_id": "chat-demo"}, "s_mode": "s1"}
        result = _build_compact_runtime_witness(snapshot)
        assert result is not None
        assert "Ledger: chat-demo" in result
        assert "Mode: s1" in result
        assert "Dial:" not in result

    def test_compact_witness_returns_none_for_empty(self):
        assert _build_compact_runtime_witness({}) is None
        assert _build_compact_runtime_witness(None) is None

    def test_packed_review_compact_mode(self):
        snapshot = {
            "runtime_identity": {
                "ledger_id": "chat-demo",
                "library_boundary": {
                    "foundation_identity": {"name": "DS", "source": "test"}
                },
            },
            "s_mode": "s1",
        }
        result = _build_packed_review_runtime_witness(snapshot, message="hello", compact=True)
        assert result is not None
        assert result["coord"] == "runtime:introspect:chat-demo"
        assert "compact" in result["text"].lower()
        assert "Ledger: chat-demo" in result["text"]
        assert "Mode: s1" in result["text"]
        # Full runtime surface summary should NOT be present in compact mode
        assert "Canonical ledger" not in result["text"]

    def test_packed_review_full_mode(self):
        snapshot = {
            "runtime_identity": {
                "ledger_id": "chat-demo",
                "library_boundary": {
                    "canonical_ledger_id": "chat-demo",
                    "foundation_identity": {"name": "DS", "source": "test"},
                },
            },
        }
        result = _build_packed_review_runtime_witness(snapshot, message="hello", compact=False)
        assert result is not None
        assert "Current-turn runtime witness evidence object" in result["text"]
        assert "Canonical ledger: chat-demo" in result["text"]

    def test_packed_review_returns_none_when_no_summary_and_compact_missing(self):
        # When compact=False and no runtime surface summary, should return None
        assert _build_packed_review_runtime_witness({}, compact=False) is None


class TestDSS137TelemetryOverlayFiltering:
    def test_item_is_telemetry_overlay_by_coord(self):
        assert _item_is_telemetry_overlay({"coord": "EV-123", "text": "event"}) is True
        assert _item_is_telemetry_overlay({"coord": "MD-Run-001", "text": "run"}) is True
        assert _item_is_telemetry_overlay({"coord": "ATT-001", "text": "attachment"}) is False
        assert _item_is_telemetry_overlay({"coord": "WX-001", "text": "wx"}) is False

    def test_item_is_telemetry_overlay_by_text(self):
        assert _item_is_telemetry_overlay({"text": "[runtime:introspect:demo] witness"}) is True
        assert _item_is_telemetry_overlay({"text": "Current-turn runtime witness (compact):"}) is True
        assert _item_is_telemetry_overlay({"text": "Normal user context"}) is False

    def test_sanitize_filters_telemetry_overlay(self):
        items = [
            {"text": "[runtime:introspect:demo] witness data"},
            {"coord": "EV-123", "text": "event data"},
            {"text": "Normal context item"},
            {"coord": "ATT-001", "text": "attachment data"},
        ]
        sanitized = _sanitize_model_context_items(items)
        texts = [i["text"] for i in sanitized]
        assert "Normal context item" in texts
        assert "attachment data" in texts
        assert "witness data" not in texts
        assert "event data" not in texts

    def test_sanitize_preserves_coord_catalog(self):
        items = [
            {
                "kind": "coord_catalog",
                "payload": {"coords": ["ATT-001"]},
            },
            {"text": "[runtime:introspect:demo] witness"},
        ]
        sanitized = _sanitize_model_context_items(items)
        assert len(sanitized) == 1
        assert sanitized[0].get("kind") == "coord_catalog"


class TestDSS138RequestScopedSession:
    def test_session_request_scoped_creates_bucket(self):
        session = {}
        bucket = _session_request_scoped(session, "req-1")
        assert isinstance(bucket, dict)
        assert session["_request_scoped"]["req-1"] is bucket

    def test_session_get_set_pop_request_scoped(self):
        session = {}
        _session_set_request_scoped(session, "req-1", "key", "value")
        assert _session_get_request_scoped(session, "req-1", "key") == "value"
        assert _session_get_request_scoped(session, "req-1", "missing", "default") == "default"
        assert _session_pop_request_scoped(session, "req-1", "key") == "value"
        assert _session_get_request_scoped(session, "req-1", "key") is None

    def test_request_scoped_isolation(self):
        session = {}
        _session_set_request_scoped(session, "req-1", "key", "a")
        _session_set_request_scoped(session, "req-2", "key", "b")
        assert _session_get_request_scoped(session, "req-1", "key") == "a"
        assert _session_get_request_scoped(session, "req-2", "key") == "b"


class TestDSS138AssuranceNonceReplay:
    def test_nonce_consumed_tracking(self):
        session = {}
        assert _assurance_nonce_consumed(session, "nonce-1") is False
        _assurance_nonce_consume(session, "nonce-1")
        assert _assurance_nonce_consumed(session, "nonce-1") is True
        assert _assurance_nonce_consumed(session, "nonce-2") is False

    def test_nonce_bound_set_size(self):
        session = {}
        for i in range(300):
            _assurance_nonce_consume(session, f"nonce-{i}")
        consumed = session["_consumed_assurance_nonces"]
        assert len(consumed) <= 256

    def test_nonce_consumed_is_global_per_session(self):
        # Nonce consumption is session-global, not request-scoped,
        # because replay protection must span requests.
        session = {}
        _assurance_nonce_consume(session, "shared-nonce")
        assert _assurance_nonce_consumed(session, "shared-nonce") is True


class TestDSS138CoordOriginAttestation:
    def test_runtime_introspect_coord(self):
        assert _coord_origin_attestation("runtime:introspect:demo") == "system_runtime_witness"

    def test_ev_coords_are_telemetry_overlay(self):
        assert _coord_origin_attestation("EV-001") == "telemetry_overlay"
        assert _coord_origin_attestation("EV-WALK-001") == "telemetry_overlay"

    def test_md_coords_are_telemetry_overlay(self):
        assert _coord_origin_attestation("MD-Run-001") == "telemetry_overlay"
        assert _coord_origin_attestation("MD-Reset-001") == "telemetry_overlay"

    def test_wx_coord(self):
        assert _coord_origin_attestation("WX-001") == "model_response_wx"

    def test_user_role(self):
        assert _coord_origin_attestation("some-coord", role="user") == "user_message"
