"""Tests for human-readable labels in activity/submission rows."""

import app as app_module


def test_normalize_submission_rows_uses_principal_display_name():
    did = "did:key:z6MkqKG9G8n2uH5Nh6PzQ7R8sT9uV0wX1yZ2aB3cD4eF5g"
    principals = [
        {
            "principal_did": did,
            "display_name": "Wizard Smoke",
        }
    ]
    submissions = [
        {
            "submission_ref": "sub-1",
            "target_entity_type": "principal",
            "target_entity_id": did,
            "submitted_by_principal_id": did,
            "submission_status": "submitted",
            "created_at": "2026-07-03T00:00:00Z",
        }
    ]
    rows = app_module._normalize_submission_rows(submissions, principals)
    assert len(rows) == 1
    assert rows[0]["display_label"] == "Wizard Smoke"
    assert rows[0]["actor"] == "Wizard Smoke"
    assert rows[0]["actor_did"] == did


def test_normalize_submission_rows_falls_back_to_principal_suffix():
    did = "did:key:z6MkExamplePrincipalDid1234567890"
    submissions = [
        {
            "submission_ref": "sub-2",
            "target_entity_type": "principal",
            "target_entity_id": did,
            "created_by_principal_id": did,
            "submission_status": "approved",
            "created_at": "2026-07-03T00:00:00Z",
        }
    ]
    rows = app_module._normalize_submission_rows(submissions)
    assert rows[0]["display_label"].startswith("Principal (")
    assert rows[0]["actor"].startswith("Principal (")


def test_normalize_submission_rows_leaves_control_plane_actor():
    submissions = [
        {
            "submission_ref": "sub-3",
            "target_entity_type": "ledger",
            "target_entity_id": "ledger:loam",
            "submission_status": "submitted",
            "created_at": "2026-07-03T00:00:00Z",
        }
    ]
    rows = app_module._normalize_submission_rows(submissions)
    assert rows[0]["actor"] == "control-plane"
    assert rows[0]["actor_did"] == ""
