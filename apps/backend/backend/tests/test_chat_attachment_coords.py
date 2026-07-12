from __future__ import annotations

from backend.api.chat import _extract_attachment_coords_with_fallbacks


def test_extract_attachment_coords_adds_fallback_namespaces() -> None:
    coords = _extract_attachment_coords_with_fallbacks(
        message="Please use ATT-deadbeef-123",
        default_namespace="chat-demo-session",
        fallback_namespaces=["chat-demo"],
    )

    assert "ATT-deadbeef-123" in coords
    assert "chat-demo-session:ATT-deadbeef-123" in coords
    assert "chat-demo:ATT-deadbeef-123" in coords


def test_extract_attachment_coords_namespaced_keeps_bare_for_recovery() -> None:
    coords = _extract_attachment_coords_with_fallbacks(
        message="Please use chat-demo-session:ATT-deadbeef-123",
        default_namespace="chat-demo-session",
        fallback_namespaces=["chat-demo"],
    )

    assert "chat-demo-session:ATT-deadbeef-123" in coords
    assert "ATT-deadbeef-123" in coords
    assert "chat-demo:ATT-deadbeef-123" in coords
