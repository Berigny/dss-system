from utils.text_processing import extract_coords_from_text, truncate_text
from utils.coord_decode import normalize_coordinate_payload


def test_extract_coords_from_text_dedupes_and_handles_prefixes():
    text = "See WX-ABC-1, WX-ABC-1, and ns:ATT-XYZ-2 for details."

    assert extract_coords_from_text(text) == ["WX-ABC-1", "ns:ATT-XYZ-2"]


def test_extract_coords_from_text_empty_input():
    assert extract_coords_from_text("") == []
    assert extract_coords_from_text(None) == []


def test_extract_coords_from_text_normalizes_lite_coord_forms():
    text = "See 37a8eec1:ae95ca73:WX-1771400008259 and WX-1771400008259."

    assert extract_coords_from_text(text) == [
        "37a8eec1:ae95ca73:WX-1771400008259",
        "37a8eec1:ae95ca73:WX-1771400008259-0",
        "WX-1771400008259",
        "WX-1771400008259-0",
    ]


def test_truncate_text_no_truncation():
    assert truncate_text("hello", 10) == "hello"


def test_truncate_text_with_truncation():
    assert truncate_text("truncate me", 5) == "trun…"


def test_normalize_coordinate_payload_v2():
    decoded = {
        "coord": "chat-demo-session:WX-123",
        "type": "WX",
        "skim": {"one_line": "One line summary"},
        "payload": {
            "segments": [
                {"id": "ANS-01", "kind": "answer", "blob_ref": "BLOB:WX:ANS-01"},
            ],
            "blobs": {"BLOB:WX:ANS-01": "Answer text"},
        },
        "interpretation": {"claims": ["Claim A"]},
        "governance": {"appraisal": {"coherence": 139}},
        "meta": {},
    }

    normalized = normalize_coordinate_payload(decoded)

    assert normalized["type"] == "WX"
    assert normalized["summary"] == "One line summary"
    assert normalized["claims"] == ["Claim A"]
    assert normalized["coherence"] == 139
    assert normalized["governance_contract"]["policy_version"] == "mmf-gov-v2"
    assert normalized["governance_contract"]["policy_decision"] == "allow"


def test_normalize_coordinate_payload_preserves_claim_evidence_path_and_status():
    decoded = {
        "type": "WX",
        "interpretation": {
            "claims": [
                {
                    "label": "Grounded claim",
                    "confidence": 0.91,
                    "evidence_path": ["chat-demo:WX-42"],
                    "grounding_status": "grounded",
                }
            ]
        },
        "governance": {
            "claim_source": "observed",
            "grounding_coverage": 0.92,
        },
    }

    normalized = normalize_coordinate_payload(decoded)
    claim = normalized["claims"][0]
    assert claim["label"] == "Grounded claim"
    assert claim["evidence_path"] == ["chat-demo:WX-42"]
    assert claim["grounding_status"] == "grounded"


def test_normalize_coordinate_payload_includes_governance_contract_v2_fields():
    decoded = {
        "type": "WX",
        "interpretation": {"claims": []},
        "governance": {
            "policy_version": "mmf-gov-v2",
            "risk_class": "high",
            "claim_source": "speculative",
            "policy_decision": "degrade",
            "grounding_coverage": 0.42,
        },
    }

    normalized = normalize_coordinate_payload(decoded)
    contract = normalized["governance_contract"]

    assert contract["policy_version"] == "mmf-gov-v2"
    assert contract["risk_class"] == "high"
    assert contract["claim_source"] == "speculative"
    assert contract["policy_decision"] == "degrade"
    assert contract["grounding_coverage"] == 0.42
