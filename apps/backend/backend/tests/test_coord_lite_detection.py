from __future__ import annotations

from backend.api.chat import _extract_attachment_coords_with_fallbacks, _extract_coords_from_text
from backend.api.chat import _canonical_commit_web4_key
from backend.api.agent_writes import _build_attachment_identifier


def test_extract_coords_from_text_accepts_wx_lite_and_canonical() -> None:
    message = (
        "Please review chat-demo:WX-1772596776159 and also "
        "chat-demo:WX-ABCD1234-1772596776."
    )
    coords = _extract_coords_from_text(message, default_namespace="chat-demo")
    assert "chat-demo:WX-1772596776159" in coords
    assert "chat-demo:WX-ABCD1234-1772596776" in coords


def test_extract_attachment_coords_accepts_att_lite_and_part() -> None:
    message = (
        "Use chat-demo:ATT-1772450375938 and part "
        "chat-demo:ATT-1772450375938-T001."
    )
    attachments = _extract_attachment_coords_with_fallbacks(
        message=message,
        default_namespace="chat-demo",
        fallback_namespaces=["chat-demo"],
    )
    assert "chat-demo:ATT-1772450375938" in attachments
    assert "chat-demo:ATT-1772450375938-T001" in attachments


def test_attachment_identifier_fallback_is_canonical_two_segment() -> None:
    identifier = _build_attachment_identifier("chat-demo", metadata=None, raw_text="hello world")
    assert identifier.startswith("ATT-")
    parts = identifier.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 8
    assert parts[2].isdigit()


def test_commit_web4_lite_is_upgraded_to_canonical() -> None:
    canonical = _canonical_commit_web4_key("chat-demo", "WX-1772596776159")
    assert canonical.startswith("WX-")
    parts = canonical.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 8
    assert parts[2] == "1772596776159"
