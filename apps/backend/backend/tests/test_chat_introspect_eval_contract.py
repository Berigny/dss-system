from __future__ import annotations

from dataclasses import dataclass

from backend.api.chat import _build_introspect_payload


@dataclass
class _Key:
    namespace: str
    identifier: str

    def as_path(self) -> str:
        return f"{self.namespace}:{self.identifier}"


@dataclass
class _State:
    metadata: dict


@dataclass
class _Entry:
    key: _Key
    state: _State


class _FakeStore:
    def __init__(self, entries: list[_Entry]) -> None:
        self._entries = entries

    def summarize(self, namespace: str) -> dict:
        return {"total_entries": len(self._entries), "namespace": namespace}

    def list_by_namespace(self, namespace: str, limit: int = 50):
        return self._entries[:limit]


def test_introspect_payload_includes_eval_contract_on_success_path() -> None:
    entry = _Entry(
        key=_Key(namespace="chat-demo", identifier="WX-1"),
        state=_State(
            metadata={
                "eq6_commit_allowed": True,
                "eq6_lawfulness_level": 2,
                "eq6_mediator_prime": 139,
                "e6_header_v0_fields": {"mode": 2, "route": 2, "K": 1, "P": 1, "E": 1, "V_q": 64000, "dW": 0},
                "eq9_target": {"score_min": 0.95},
                "gen_output_tokens": 120,
                "appraisal": {"law_score": 1.0, "grace_score": 1.0},
            }
        ),
    )
    payload = _build_introspect_payload(store=_FakeStore([entry]), namespace="chat-demo")
    contract = payload.get("eval_contract")
    posture = payload.get("posture_policy")

    assert isinstance(contract, dict)
    assert contract["commit_allowed"] is True
    assert contract["failed_eq"] is None
    assert contract["eq9_metrics"]["yield_per_token"] > 0.0
    assert isinstance(posture, dict)
    assert posture.get("policy_decision") in {"allow", "degrade"}
    assert posture.get("eq9_posture_class") in {"P1", "P2", "P3"}


def test_introspect_payload_includes_eval_contract_on_fail_path() -> None:
    entry = _Entry(
        key=_Key(namespace="chat-demo", identifier="WX-2"),
        state=_State(
            metadata={
                "eq6_commit_allowed": False,
                "eq6_lawfulness_level": 0,
                "eq6_mediator_prime": 137,
                "e6_header_v0_fields": {"mode": 1, "route": 1, "K": 1, "P": 0, "E": 1, "V_q": 10, "dW": 0},
                "eq9_target": {"score_min": 0.95},
                "gen_output_tokens": 42,
                "appraisal": {"law_score": 0.4, "grace_score": 0.9},
            }
        ),
    )
    payload = _build_introspect_payload(store=_FakeStore([entry]), namespace="chat-demo")
    contract = payload.get("eval_contract")
    posture = payload.get("posture_policy")

    assert isinstance(contract, dict)
    assert contract["blocked"] is True
    assert contract["commit_allowed"] is False
    assert contract["failed_eq"] in {"eq6_awareness", "eq7_unity", "eq9_telos"}
    assert isinstance(contract["repair_actions"], list) and contract["repair_actions"]
    assert isinstance(posture, dict)
    assert posture.get("policy_decision") == "deny"
    assert str(posture.get("reason_code") or "").startswith("eq_blocked")


def test_introspect_payload_exposes_runtime_witness_surfaces_from_latest_metadata() -> None:
    entry = _Entry(
        key=_Key(namespace="chat-demo", identifier="WX-3"),
        state=_State(
            metadata={
                "runtime_identity": {
                    "ledger_id": "chat-demo",
                    "runtime_namespace": "chat-demo",
                    "library_boundary": {
                        "foundation_identity": {
                            "name": "LOAM",
                            "foundation_identity_ref": "ledger:chat-demo:foundation_identity",
                        },
                        "history_continuity": {
                            "alias_aware_coord_history_lookup": True,
                            "surviving_governed_memory_boundary": "chat-demo",
                        },
                        "continuity_checkpoint": {
                            "checkpoint_ref": "chat-demo:registry",
                            "ledger_version": 3,
                        },
                        "latest_consolidation_event": {
                            "event": "merge",
                            "timestamp": "2026-05-04T01:00:13.645664+00:00",
                        },
                        "latest_consolidation_event_id": "chat-demo:merge:2026-05-04T01:00:13.645664+00:00",
                        "ledger_version": 3,
                        "async_consolidation_state": "settled_on_canonical_boundary",
                        "canonical_identity_post_consolidation": {
                            "canonical_ledger_id": "chat-demo",
                            "continuity_survived": True,
                        },
                    },
                },
                "retention_tier": "Clay",
                "retention_tier_reason": "durable_ledger_write_path",
                "gravity_tax_policy": {
                    "retention_tier_assignment": "durable_governed_memory_boundary",
                    "retention_decision_state": "durable_keep",
                },
            }
        ),
    )

    payload = _build_introspect_payload(store=_FakeStore([entry]), namespace="chat-demo")

    assert payload["runtime_identity"]["ledger_id"] == "chat-demo"
    assert payload["foundation_identity"]["foundation_identity_ref"] == "ledger:chat-demo:foundation_identity"
    assert payload["history_continuity"]["surviving_governed_memory_boundary"] == "chat-demo"
    assert payload["continuity_checkpoint"]["ledger_version"] == 3
    assert payload["latest_consolidation_event"]["event"] == "merge"
    assert payload["latest_consolidation_event_id"] == "chat-demo:merge:2026-05-04T01:00:13.645664+00:00"
    assert payload["ledger_version"] == 3
    assert payload["async_consolidation_state"] == "settled_on_canonical_boundary"
    assert payload["canonical_identity_post_consolidation"]["continuity_survived"] is True
    assert payload["retention_tier"] == "Clay"
    assert payload["gravity_tax_policy"]["retention_decision_state"] == "durable_keep"
