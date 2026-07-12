# DSS-CP-GOV-v1.0.0-alpha
"""Regression tests for the connection governance impact calculator."""

from __future__ import annotations

from backend.governance.impact import calculate_removal_impact


def _principal(pid: str, actor_type: str = "human", trust_class: str = "T3") -> dict:
    return {
        "principal_did": pid,
        "metadata": {"actor_type": actor_type},
        "standing_view": {"trust_class": trust_class},
    }


def _rel(
    rel_type: str,
    subject_type: str,
    subject_id: str,
    object_type: str,
    object_id: str,
    enabled_state: str = "enabled",
) -> dict:
    return {
        "relationship_id": f"{subject_type}::{subject_id}::{object_type}::{object_id}",
        "relationship_type": rel_type,
        "subject_entity_type": subject_type,
        "subject_entity_id": subject_id,
        "object_entity_type": object_type,
        "object_entity_id": object_id,
        "enabled_state": enabled_state,
    }


def test_remove_principal_no_cascade() -> None:
    relationships = {
        "r1": _rel("member_of", "principal", "p1", "ledger", "LOAM"),
    }
    report = calculate_removal_impact(
        "principal", "p1", "LOAM",
        relationships=relationships,
        principals={"p1": _principal("p1")},
        ledgers={"LOAM": {"ledger_id": "LOAM"}},
        surfaces={},
    )
    assert report.affected_principals == ["p1"]
    assert report.affected_surfaces == {}
    assert report.orphaned_surfaces == []
    assert all("ORPHAN_RISK" not in w for w in report.critical_warnings)


def test_remove_principal_cascades_to_surface() -> None:
    relationships = {
        "r1": _rel("member_of", "principal", "p1", "ledger", "LOAM"),
        "r2": _rel("member_of", "principal", "p2", "ledger", "SAND"),
        "r3": _rel("hosts", "surface", "surface:chat:primary", "principal", "p1"),
        "r4": _rel("hosts", "surface", "surface:chat:primary", "principal", "p2"),
        "r5": _rel("access_grant", "surface", "surface:chat:primary", "ledger", "LOAM"),
    }
    report = calculate_removal_impact(
        "principal", "p1", "LOAM",
        relationships=relationships,
        principals={"p1": _principal("p1"), "p2": _principal("p2")},
        ledgers={"LOAM": {"ledger_id": "LOAM"}, "SAND": {"ledger_id": "SAND"}},
        surfaces={"surface:chat:primary": {"surface_id": "surface:chat:primary"}},
    )
    assert "surface:chat:primary" in report.affected_surfaces
    assert report.affected_surfaces["surface:chat:primary"]["p1"] == ["LOAM"]
    assert "surface:chat:primary" not in report.orphaned_surfaces


def test_remove_principal_orphans_surface() -> None:
    relationships = {
        "r1": _rel("member_of", "principal", "p1", "ledger", "LOAM"),
        "r2": _rel("hosts", "surface", "surface:chat:primary", "principal", "p1"),
        "r3": _rel("access_grant", "surface", "surface:chat:primary", "ledger", "LOAM"),
    }
    report = calculate_removal_impact(
        "principal", "p1", "LOAM",
        relationships=relationships,
        principals={"p1": _principal("p1")},
        ledgers={"LOAM": {"ledger_id": "LOAM"}},
        surfaces={"surface:chat:primary": {"surface_id": "surface:chat:primary"}},
    )
    assert "surface:chat:primary" in report.orphaned_surfaces
    assert any("ORPHAN_RISK" in w for w in report.critical_warnings)


def test_remove_last_t1_operator_blocked() -> None:
    relationships = {
        "r1": _rel("member_of", "principal", "p1", "ledger", "LOAM"),
    }
    report = calculate_removal_impact(
        "principal", "p1", "LOAM",
        relationships=relationships,
        principals={"p1": _principal("p1", trust_class="T1")},
        ledgers={"LOAM": {"ledger_id": "LOAM"}},
        surfaces={},
    )
    assert any("LAST_T1_OPERATOR" in w for w in report.critical_warnings)


def test_remove_ledger_breaks_federation() -> None:
    relationships = {
        "r1": _rel("links_to", "ledger", "LOAM", "ledger", "SAND"),
        "r2": _rel("member_of", "principal", "p1", "ledger", "LOAM"),
    }
    report = calculate_removal_impact(
        "ledger", "LOAM", "SAND",
        relationships=relationships,
        principals={"p1": _principal("p1")},
        ledgers={
            "LOAM": {"ledger_id": "LOAM"},
            "SAND": {"ledger_id": "SAND"},
        },
        surfaces={},
    )
    assert "SAND" in report.affected_ledgers
    assert "LOAM" in report.affected_ledgers


def test_principal_keeps_access_via_other_ledger() -> None:
    relationships = {
        "r1": _rel("member_of", "principal", "p1", "ledger", "LOAM"),
        "r2": _rel("member_of", "principal", "p1", "ledger", "SAND"),
    }
    report = calculate_removal_impact(
        "principal", "p1", "LOAM",
        relationships=relationships,
        principals={"p1": _principal("p1")},
        ledgers={
            "LOAM": {"ledger_id": "LOAM"},
            "SAND": {"ledger_id": "SAND"},
        },
        surfaces={},
    )
    assert "SAND" not in report.affected_ledgers
    assert report.affected_surfaces == {}


def test_remove_organisation_dissolves_held_entities() -> None:
    relationships = {
        "r1": _rel("member_of", "principal", "org1", "ledger", "LOAM"),
        "r2": _rel("holds", "principal", "org1", "principal", "p1"),
        "r3": _rel("hosts", "surface", "surface:chat:primary", "principal", "p1"),
        "r4": _rel("access_grant", "surface", "surface:chat:primary", "ledger", "LOAM"),
    }
    report = calculate_removal_impact(
        "principal", "org1", "LOAM",
        relationships=relationships,
        principals={
            "org1": _principal("org1", actor_type="organisation", trust_class="T3"),
            "p1": _principal("p1"),
        },
        ledgers={"LOAM": {"ledger_id": "LOAM"}},
        surfaces={"surface:chat:primary": {"surface_id": "surface:chat:primary"}},
    )
    assert "org1" in report.affected_principals
    assert "p1" in report.affected_principals
    assert "surface:chat:primary" in report.affected_surfaces


def test_circular_federation_no_infinite_loop() -> None:
    relationships = {
        "r1": _rel("links_to", "ledger", "A", "ledger", "B"),
        "r2": _rel("links_to", "ledger", "B", "ledger", "C"),
        "r3": _rel("links_to", "ledger", "C", "ledger", "A"),
        "r4": _rel("member_of", "principal", "p1", "ledger", "A"),
    }
    report = calculate_removal_impact(
        "principal", "p1", "A",
        relationships=relationships,
        principals={"p1": _principal("p1")},
        ledgers={
            "A": {"ledger_id": "A"},
            "B": {"ledger_id": "B"},
            "C": {"ledger_id": "C"},
        },
        surfaces={},
    )
    assert {"A", "B", "C"}.issubset(set(report.affected_ledgers))


def test_self_removal_flagged() -> None:
    relationships = {
        "r1": _rel("member_of", "principal", "p1", "ledger", "LOAM"),
    }
    report = calculate_removal_impact(
        "principal", "p1", "LOAM",
        relationships=relationships,
        principals={"p1": _principal("p1")},
        ledgers={"LOAM": {"ledger_id": "LOAM"}},
        surfaces={},
        caller_principal_id="p1",
    )
    assert any("SELF_REMOVAL" in w for w in report.critical_warnings)


def test_surface_cannot_host_surface() -> None:
    from backend.governance.ontology import validate_relationship_type, OntologyError
    try:
        validate_relationship_type("hosts", "surface", "surface", subject_subtype=None)
        assert False, "expected OntologyError"
    except OntologyError:
        pass


def test_confirmation_token_is_deterministic() -> None:
    relationships = {
        "r1": _rel("member_of", "principal", "p1", "ledger", "LOAM"),
    }
    report1 = calculate_removal_impact(
        "principal", "p1", "LOAM",
        relationships=relationships,
        principals={"p1": _principal("p1")},
        ledgers={"LOAM": {"ledger_id": "LOAM"}},
        surfaces={},
    )
    report2 = calculate_removal_impact(
        "principal", "p1", "LOAM",
        relationships=relationships,
        principals={"p1": _principal("p1")},
        ledgers={"LOAM": {"ledger_id": "LOAM"}},
        surfaces={},
    )
    assert report1.confirmation_token == report2.confirmation_token
