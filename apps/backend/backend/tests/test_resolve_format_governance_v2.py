from __future__ import annotations

from backend.utils.resolve_format import build_governance, build_interpretation


def test_build_governance_includes_v2_defaults() -> None:
    out = build_governance({"appraisal": {"score": 0.8, "law": 0.4, "grace": 0.9, "drift": 0.2}})

    assert out["policy_version"] == "mmf-gov-v2"
    assert out["claim_source"] == "inferred"
    assert out["risk_class"] == "medium"
    assert out["policy_decision"] == "degrade"


def test_build_governance_blocks_when_governance_error_present() -> None:
    out = build_governance(
        {
            "appraisal": {"score": 0.8, "law": 0.9},
            "governance_error": {"blocked": True, "reason": "genesis_ladder_blocked"},
        }
    )

    assert out["policy_decision"] == "block"


def test_build_governance_honors_explicit_contract_fields() -> None:
    out = build_governance(
        {
            "appraisal": {
                "score": 0.95,
                "law": 0.95,
                "policy_version": "mmf-gov-v2",
                "policy_decision": "allow",
                "risk_class": "low",
                "claim_source": "observed",
            },
            "grounding_coverage": 0.92,
        }
    )

    assert out["policy_version"] == "mmf-gov-v2"
    assert out["policy_decision"] == "allow"
    assert out["risk_class"] == "low"
    assert out["claim_source"] == "observed"
    assert out["grounding_coverage"] == 0.92


def test_build_governance_handles_missing_appraisal() -> None:
    out = build_governance({})

    assert out["appraisal"] == {}
    assert out["policy_version"] == "mmf-gov-v2"
    assert out["policy_decision"] == "allow"


def test_build_interpretation_links_claims_to_evidence_coords_and_reports_grounding() -> None:
    out = build_interpretation(
        {
            "coord": "chat-demo:WX-88",
            "claims": ["Continuity survived consolidation"],
            "opened_payload_coords": ["chat-demo:WX-42"],
            "source_coords": ["chat-demo:WX-41"],
            "claim_source": "observed",
            "grounding_coverage": 0.91,
        }
    )

    assert out["claim_source"] == "observed"
    assert out["grounding_coverage"] == 0.91
    claim = out["claims"][0]
    assert claim["label"] == "Continuity survived consolidation"
    assert claim["evidence_path"] == ["chat-demo:WX-42", "chat-demo:WX-41"]
    assert claim["grounding_status"] == "grounded"
    assert claim["confidence"] == 0.99


def test_build_interpretation_marks_high_confidence_claims_incomplete_without_evidence_path() -> None:
    out = build_interpretation(
        {
            "claims": [{"label": "Ungrounded claim", "confidence": 0.99}],
            "claim_source": "inferred",
            "grounding_coverage": 0.12,
        }
    )

    claim = out["claims"][0]
    assert claim["evidence_path"] == []
    assert claim["grounding_status"] == "incomplete"
    assert claim["confidence"] == 0.49
