from components.chat import (
    _answer_surface_integrity_text,
    _build_message_badges,
    _message_attribution_text,
    _public_object_resolution_text,
)
from routes.home import _sync_event_key


def test_sync_event_key_prefers_event_id() -> None:
    msg = {"metadata": {"event_id": "ABCD1234", "stream_key": "s", "seq": 2}}
    assert _sync_event_key(msg) == "abcd1234"


def test_sync_event_key_falls_back_to_stream_seq() -> None:
    msg = {"metadata": {"stream_key": "0001:0002:0003", "seq": 7}}
    assert _sync_event_key(msg) == "0001:0002:0003:7"


def test_build_message_badges_sync_and_quarantine() -> None:
    badges = _build_message_badges({"source": "sync_v0", "sync_state": "quarantine"})
    assert len(badges) == 2


def test_message_attribution_text_swaps_labels_for_distinct_delegated_turn() -> None:
    text = _message_attribution_text(
        {
            "model_id": "anthropic/claude-haiku-4.5",
            "delegated_prompt_path": {
                "prompt_principal_id": "openai:agent:codex",
                "prompt_principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                "requested_by_principal_did": "did:key:z6MkOperator",
                "requested_by_principal_id": "operator:david",
            },
        },
        role="assistant",
    )
    assert "asked by: operator:david" in text
    assert "answered by: openai/codex" in text
    assert "model: anthropic/claude-haiku-4.5" in text
    assert "requested by:" not in text


def test_message_attribution_text_keeps_legacy_labels_when_requester_is_prompt_principal() -> None:
    text = _message_attribution_text(
        {
            "model_id": "anthropic/claude-haiku-4.5",
            "delegated_prompt_path": {
                "prompt_principal_id": "openai:agent:codex",
                "prompt_principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
                "requested_by_principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            },
        },
        role="assistant",
    )
    assert "asked by: openai/codex" in text
    assert "answered by: anthropic/claude-haiku-4.5" in text


def test_message_attribution_text_formats_human_principal_slug_for_ui() -> None:
    text = _message_attribution_text(
        {
            "contributor": {
                "principal_type": "user",
                "principal_id": "wallet-user",
                "principal_did": "did:key:z6MkWalletUser",
            },
        },
        role="assistant",
    )
    assert "asked by: Wallet User" in text


def test_message_attribution_text_prefers_human_display_name_over_role_label() -> None:
    text = _message_attribution_text(
        {
            "prompt_principal_label": "operator",
            "principal_display_name": "David Berigny",
            "contributor": {
                "principal_type": "user",
                "principal_id": "ops-admin",
                "principal_did": "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
                "principal_display_name": "David Berigny",
            },
        },
        role="assistant",
    )
    assert "asked by: David Berigny" in text
    assert "asked by: operator" not in text


def test_answer_surface_integrity_text_marks_divergence() -> None:
    text = _answer_surface_integrity_text(
        {
            "answer_surface_integrity": {
                "status": "diverged",
                "reason": "assembly_summary_richer_than_visible_answer",
            }
        }
    )
    assert text == "summary richer than visible answer"


def test_answer_surface_integrity_text_marks_blocked_context_collapse() -> None:
    text = _answer_surface_integrity_text(
        {
            "answer_surface_integrity": {
                "status": "collapsed",
                "reason": "visible_answer_preamble_collapse_under_blocked_context",
            }
        }
    )
    assert text == "visible answer collapsed under blocked context"


def test_answer_surface_integrity_text_tolerates_null_integrity_payload() -> None:
    text = _answer_surface_integrity_text({"answer_surface_integrity": None})
    assert text == ""


def test_public_object_resolution_text_surfaces_resolver_contract_fields() -> None:
    text = _public_object_resolution_text(
        {
            "decision_artifact_identity": {
                "public_object_id": "https://id.example/o/decision-artifact/sha256:test",
                "resolverRef": "rrf_0123456789abcdef",
            },
            "evidence": {
                "resolverUrl": "https://id.example/v1/resolve?ref=https%3A%2F%2Fid.example%2Fo%2Fdecision-artifact%2Fsha256%3Atest",
            },
            "nativeCoordState": "present",
        }
    )
    assert "public object: https://id.example/o/decision-artifact/sha256:test" in text
    assert "resolver: rrf_0123456789abcdef" in text
    assert "native coord: present" in text
