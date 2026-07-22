"""Tests for relationship payload shape sent to the backend."""

from __future__ import annotations

import app as app_module


def test_relationship_defaults_use_dict_object_fields() -> None:
    record = app_module._relationship_defaults("surface", "surface:chat:primary", "ledger", "LOAM", "surface_bound_to_ledger")
    assert record["permission_payload"] == {}
    assert record["metadata"] == {}


def test_relationship_record_coerces_string_metadata() -> None:
    stored = {
        "relationship_id": "surface::surface:chat:primary::ledger::LOAM",
        "subject_entity_type": "surface",
        "subject_entity_id": "surface:chat:primary",
        "object_entity_type": "ledger",
        "object_entity_id": "LOAM",
        "relationship_type": "surface_bound_to_ledger",
        "metadata": '{"legacy": "json text"}',
        "permission_payload": '',
    }
    record = app_module._relationship_record(
        "surface", "surface:chat:primary", "ledger", "LOAM", relationship_records=[stored]
    )
    assert record["metadata"] == {"legacy": "json text"}
    assert record["permission_payload"] == {}


def test_relationship_record_coerces_empty_string_metadata_to_dict() -> None:
    stored = {
        "relationship_id": "surface::surface:chat:primary::ledger::LOAM",
        "subject_entity_type": "surface",
        "subject_entity_id": "surface:chat:primary",
        "object_entity_type": "ledger",
        "object_entity_id": "LOAM",
        "relationship_type": "surface_bound_to_ledger",
        "metadata": "",
        "permission_payload": "",
    }
    record = app_module._relationship_record(
        "surface", "surface:chat:primary", "ledger", "LOAM", relationship_records=[stored]
    )
    assert record["metadata"] == {}
    assert record["permission_payload"] == {}


def test_upsert_flow_relationship_produces_dict_metadata() -> None:
    relationships: list[dict] = []
    updated = app_module._upsert_flow_relationship(
        relationships,
        owner_entity_type="surface",
        owner_entity_id="surface:chat:primary",
        related_entity_type="ledger",
        related_entity_id="LOAM",
        label="LOAM",
        state={"metadata": '{"note": "from flow"}', "enabled": "on"},
    )
    record = updated[0]
    assert isinstance(record["metadata"], dict)
    assert record["metadata"] == {"note": "from flow"}
    assert isinstance(record["permission_payload"], dict)
