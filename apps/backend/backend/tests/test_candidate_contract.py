from __future__ import annotations

from datetime import datetime, timezone

from backend.api.chat import (
    _canonical_coord_from_item,
    _coerce_knowledge_tree_key_from_retrieved,
    _set_canonical_coord,
)
from backend.fieldx_kernel.orchestrator import _canonicalize_retrieved_candidate


def test_orchestrator_canonicalizes_retrieved_candidate_shape() -> None:
    now = datetime(2026, 3, 3, tzinfo=timezone.utc)
    entry = {
        "key": "chat-demo:WX-123",
        "created_at": now.isoformat(),
        "relevance_score": 0.91,
        "tier_rank": 3,
        "p_adic_similarity": 0.73,
        "explicit": True,
        "notes": "loaded",
        "source": "explicit",
    }

    out = _canonicalize_retrieved_candidate(entry, now=now)

    assert out["coord"] == "chat-demo:WX-123"
    assert out["namespace"] == "chat-demo"
    assert out["identifier"] == "WX-123"
    assert out["coord_type"] == "WX"
    assert out["origin_attestation"] == "explicit_user_referenced_coord"
    assert out["relevance_score"] == 0.91
    assert out["tier_rank"] == 3
    assert out["relevance_tier"] == 1
    assert out["p_adic_score"] == 0.73
    assert out["search_score"] == 0.91
    assert out["recency_score"] == 1.0
    assert out["payload_state"] == "opened"
    assert out["recommended_action"] == "reuse_already_opened"
    assert out["skip_reason"] is None
    assert out["semantic_score"] == 0.73
    assert out["explicit_mention"] is True
    assert out["resolved_payload_present"] is True
    assert out["source"] == "explicit"


def test_chat_retrieved_adapter_prefers_canonical_coord() -> None:
    item = {"coord": "chat-demo:WX-9", "key": "other:WX-0"}
    key = _coerce_knowledge_tree_key_from_retrieved(item)
    assert key == {"namespace": "chat-demo", "identifier": "WX-9"}


def test_chat_sets_coord_from_namespace_identifier() -> None:
    item = {"namespace": "chat-demo", "identifier": "WX-77"}
    out = _set_canonical_coord(item)
    assert out["coord"] == "chat-demo:WX-77"
    assert _canonical_coord_from_item(out) == "chat-demo:WX-77"
