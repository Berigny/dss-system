"""Tests for DSS-133 part 3: hard refusal when explicit target cannot be resolved."""

import pytest

from routes.orchestrator import (
    _build_explicit_target_unresolved_reply,
    _evaluate_resolution_consistency,
    _response_denies_attachment_access,
    _response_is_weak_attachment_answer,
)


class TestBuildExplicitTargetUnresolvedReply:
    def test_refusal_with_single_target_no_fallback(self):
        reply = _build_explicit_target_unresolved_reply(
            explicit_targets=["chat-demo:WX-BB595843-4934034"],
            resolved_coords=None,
        )
        assert "WX-BB595843-4934034" in reply
        assert "could not find" in reply
        assert "fallback" not in reply

    def test_refusal_with_fallback_coords(self):
        reply = _build_explicit_target_unresolved_reply(
            explicit_targets=["chat-demo:WX-BB595843-4934034"],
            resolved_coords=["chat-demo:WX-OTHER-1234", "chat-demo:WX-OTHER-5678"],
        )
        assert "WX-BB595843-4934034" in reply
        assert "WX-OTHER-1234" in reply
        assert "fallback" in reply

    def test_refusal_with_empty_targets(self):
        reply = _build_explicit_target_unresolved_reply(
            explicit_targets=[],
            resolved_coords=None,
        )
        assert "the requested coordinate" in reply

    def test_refusal_does_not_trigger_attachment_denial(self):
        reply = _build_explicit_target_unresolved_reply(
            explicit_targets=["chat-demo:WX-BB595843-4934034"],
            resolved_coords=["chat-demo:WX-OTHER-1234"],
        )
        assert not _response_denies_attachment_access(reply)

    def test_refusal_is_not_weak_attachment_answer(self):
        reply = _build_explicit_target_unresolved_reply(
            explicit_targets=["chat-demo:WX-BB595843-4934034"],
            resolved_coords=["chat-demo:WX-OTHER-1234"],
        )
        assert not _response_is_weak_attachment_answer(reply)

    def test_refusal_does_not_trigger_contradiction_with_resolved_context(self):
        reply = _build_explicit_target_unresolved_reply(
            explicit_targets=["chat-demo:WX-BB595843-4934034"],
            resolved_coords=["chat-demo:WX-OTHER-1234"],
        )
        cc = _evaluate_resolution_consistency(reply, ["chat-demo:WX-OTHER-1234"])
        assert cc["status"] == "ok"
        assert cc["contradiction"] is False

    def test_refusal_does_not_trigger_contradiction_without_resolved_context(self):
        reply = _build_explicit_target_unresolved_reply(
            explicit_targets=["chat-demo:WX-BB595843-4934034"],
            resolved_coords=None,
        )
        cc = _evaluate_resolution_consistency(reply, [])
        assert cc["status"] == "ok"
        assert cc["contradiction"] is False
