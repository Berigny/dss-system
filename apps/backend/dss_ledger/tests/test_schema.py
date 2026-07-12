"""Config schema tests."""

from __future__ import annotations

import pytest

from dss_ledger.schema import LedgerSchema


def test_schema_loads():
    schema = LedgerSchema.from_config_dir()
    assert "autonomy" in schema.ontology
    assert "agent" in schema.slots
    assert "patch_001" in schema.relations


def test_no_prime_collisions(schema: LedgerSchema):
    slot_bases = {s["base"] for s in schema.slots.values()}
    ontology_primes = {c["prime"] for c in schema.ontology.values()}
    relation_primes = {r["prime"] for r in schema.relations.values()}

    assert not slot_bases & ontology_primes
    assert not slot_bases & relation_primes
    assert not ontology_primes & relation_primes


def test_quaternary_primes_not_used_as_process_primes(schema: LedgerSchema):
    quaternary = {5, 7, 2}
    ontology_primes = {c["prime"] for c in schema.ontology.values()}
    relation_primes = {r["prime"] for r in schema.relations.values()}
    assert not quaternary & ontology_primes
    assert not quaternary & relation_primes


def test_unknown_concept_raises(schema: LedgerSchema):
    with pytest.raises(ValueError, match="Unknown concept"):
        schema.concept_prime("not_a_concept")
