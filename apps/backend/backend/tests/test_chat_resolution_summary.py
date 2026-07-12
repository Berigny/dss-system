from backend.api.chat import _build_coord_resolution_summary


def test_build_coord_resolution_summary_counts_and_lists() -> None:
    summary = _build_coord_resolution_summary(
        requested_coords=["chat-demo:WX-1", "chat-demo:WX-2", "chat-demo:WX-1"],
        resolved_coords={"chat-demo:WX-2", "chat-demo:WX-9"},
    )
    assert summary["supports_coord_resolution"] is True
    assert summary["requested_count"] == 2
    assert summary["resolved_count"] == 1
    assert summary["unresolved_count"] == 1
    assert summary["requested_coords"] == ["chat-demo:WX-1", "chat-demo:WX-2"]
    assert summary["resolved_coords"] == ["chat-demo:WX-2"]
    assert summary["unresolved_coords"] == ["chat-demo:WX-1"]
    assert summary["available_resolved_context"] == ["chat-demo:WX-2", "chat-demo:WX-9"]

