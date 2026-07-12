from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI

from backend.fieldx_kernel.informational_unit import (
    BODY_TIER_START,
    KERNEL_EQ_TO_PRIME,
    MMF_DOMAINS,
    all_mmf_states_from_metadata,
    attach_core_informational_unit,
    build_core_informational_unit,
    build_mmf_projection_exponents,
    default_kernel_exponents_for_entry,
    default_mmf_domains_for_entry,
    entry_class_for_metadata,
    information_unit_distance,
    kernel_state_from_metadata,
    mmf_state_from_metadata,
    validate_core_informational_unit,
)
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.p_adic import PrimeLatticeState
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2
from backend.search.token_index import TokenPrimeIndex


@pytest.fixture
def token_index_and_db() -> tuple[TokenPrimeIndex, dict]:
    app = FastAPI()
    storage: dict = {}
    app.state.db = storage
    return TokenPrimeIndex(app), storage


def _make_entry(
    namespace: str,
    identifier: str,
    text: str | None = None,
    phase: str | None = "chat",
    metadata: dict | None = None,
) -> LedgerEntry:
    meta = dict(metadata or {})
    if text is not None:
        meta["text"] = text
    return LedgerEntry(
        key=LedgerKey(namespace=namespace, identifier=identifier),
        state=ContinuousState({}, phase, meta),
        created_at=datetime.now(timezone.utc),
    )


# --- Schema and constants ---------------------------------------------------


def test_kernel_prime_map_has_ten_entries() -> None:
    assert len(KERNEL_EQ_TO_PRIME) == 10
    assert KERNEL_EQ_TO_PRIME[0] == 2
    assert KERNEL_EQ_TO_PRIME[9] == 139


def test_mmf_domains_each_have_eight_primes() -> None:
    for domain, primes in MMF_DOMAINS.items():
        assert len(primes) == 8, domain
        assert len(set(primes)) == 8, domain


def test_mmf_domains_do_not_overlap_s1_s2_node_primes() -> None:
    # The ten kernel nodes 0-7 plus the two mediator primes are part of the
    # shared computational lattice. Domain cubes are isolated from the eight
    # S1/S2 node primes (2, 3, 5, 7, 11, 13, 17, 19). Mediator primes 137/139
    # appear in the reference auditory/olfactory cubes and are treated as
    # shared bridge primes, not as S1/S2 node primes.
    s1_s2_nodes = {2, 3, 5, 7, 11, 13, 17, 19}
    for domain, primes in MMF_DOMAINS.items():
        for prime in primes:
            assert prime not in s1_s2_nodes, f"{domain} overlaps S1/S2 node prime {prime}"


# --- Entry-class inference --------------------------------------------------


def test_entry_class_inference_from_phase() -> None:
    assert entry_class_for_metadata({}, phase="chat") == "turn"
    assert entry_class_for_metadata({}, phase="attachment") == "attachment"


def test_entry_class_inference_from_kind() -> None:
    assert entry_class_for_metadata({"kind": "topic"}) == "topic"
    assert entry_class_for_metadata({"kind": "event"}) == "event"
    assert entry_class_for_metadata({"kind": "turn"}, phase="chat") == "turn"


# --- Default mappings -------------------------------------------------------


def test_default_kernel_exponents_activate_persistence_and_recall() -> None:
    exponents = default_kernel_exponents_for_entry("turn")
    assert exponents[KERNEL_EQ_TO_PRIME[5]] == 1  # Persistence Cost
    assert exponents[BODY_TIER_START] == 1        # Recall


def test_default_mmf_domains_for_attachment_respects_content_type() -> None:
    assert default_mmf_domains_for_entry("attachment", "image/png") == ["visual"]
    assert default_mmf_domains_for_entry("attachment", "audio/wav") == ["auditory"]
    assert default_mmf_domains_for_entry("attachment", "text/plain") == ["verbal"]


# --- Builders ---------------------------------------------------------------


def test_build_core_informational_unit_populates_all_fields() -> None:
    unit = build_core_informational_unit(
        entry_class="turn",
        token_primes=[29, 31, 43, 13, 23],
    )
    assert unit["core_info_entry_class"] == "turn"
    assert unit["kernel_prime_exponents"][13] >= 1  # Persistence Cost
    assert unit["kernel_prime_exponents"][23] >= 1  # Recall
    assert "verbal" in unit["mmf_projection_exponents"]
    assert unit["mmf_projection_exponents"]["verbal"][29] == 1
    assert unit["flow_rule_tags"] == []
    assert unit["relationship_links"] == []


def test_build_mmf_projection_ignores_non_domain_primes() -> None:
    projections = build_mmf_projection_exponents("turn", [2, 3, 13, 23, 29])
    assert "verbal" in projections
    assert set(projections["verbal"]) == {29}


# --- Ledger-store integration -----------------------------------------------


def test_attach_core_informational_unit_populates_metadata(
    token_index_and_db: tuple[TokenPrimeIndex, dict]
) -> None:
    token_index, db = token_index_and_db
    store = LedgerStoreV2(db, token_index=token_index)
    entry = _make_entry("ns", "1001", text="one two three four five")
    store.write(entry)

    written = store.read(entry.key.as_path())
    assert written is not None
    meta = written.state.metadata
    assert meta.get("core_info_entry_class") == "turn"
    assert isinstance(meta.get("kernel_prime_exponents"), dict)
    assert isinstance(meta.get("mmf_projection_exponents"), dict)
    assert isinstance(meta.get("flow_rule_tags"), list)
    assert isinstance(meta.get("relationship_links"), list)


def test_attach_populates_verbal_domain_when_token_primes_are_verbal() -> None:
    entry = _make_entry("ns", "1002", metadata={"token_primes": [29, 31, 43]})
    attach_core_informational_unit(entry)
    projections = entry.state.metadata["mmf_projection_exponents"]
    assert "verbal" in projections
    assert projections["verbal"][29] == 1


def test_attach_derives_relationships_from_parent_coord() -> None:
    entry = _make_entry(
        "ns",
        "1002",
        metadata={"parent_coord": "ns:WX-123", "token_primes": [29]},
    )
    attach_core_informational_unit(entry)
    links = entry.state.metadata["relationship_links"]
    assert any(link["coord"] == "ns:WX-123" and link["relation"] == "parent" for link in links)


# --- Validation -------------------------------------------------------------


def test_validate_core_informational_unit_passes_for_valid_unit() -> None:
    unit = build_core_informational_unit("turn", [29, 13, 23])
    assert validate_core_informational_unit(unit) == []


def test_validate_detects_unknown_prime_in_kernel() -> None:
    unit = build_core_informational_unit("turn", [29, 13, 23])
    unit["kernel_prime_exponents"][999] = 1
    errors = validate_core_informational_unit(unit)
    assert any("unknown prime 999" in err for err in errors)


def test_validate_detects_non_domain_prime_in_mmf_projection() -> None:
    unit = build_core_informational_unit("turn", [29, 13, 23])
    unit["mmf_projection_exponents"]["verbal"][999] = 1
    errors = validate_core_informational_unit(unit)
    assert any("non-domain prime 999" in err for err in errors)


def test_validate_detects_domain_isolation_violation() -> None:
    unit = build_core_informational_unit("turn", [29, 13, 23])
    unit["mmf_projection_exponents"]["visual"] = {29: 1}
    errors = validate_core_informational_unit(unit)
    assert any("appears in both verbal and visual" in err for err in errors)


def test_validate_allows_kernel_bridge_primes_in_mmf() -> None:
    # The kernel central-axis primes 137 (EQ8) and 139 (EQ9) are intentionally
    # shared with the auditory/olfactory MMF domains as bridge primes.
    unit = build_core_informational_unit("turn", [13, 23])
    unit["kernel_prime_exponents"][137] = 1
    unit["kernel_prime_exponents"][139] = 1
    unit["mmf_projection_exponents"]["auditory"] = {137: 1}
    unit["mmf_projection_exponents"]["olfactory"] = {139: 1}
    errors = validate_core_informational_unit(unit)
    assert not errors


# --- Lattice operations -----------------------------------------------------


def test_kernel_state_from_metadata() -> None:
    unit = build_core_informational_unit("turn", [13, 23])
    state = kernel_state_from_metadata(unit)
    assert isinstance(state, PrimeLatticeState)
    assert state.valuation(13) >= 1
    assert state.valuation(23) >= 1


def test_mmf_state_from_metadata() -> None:
    unit = build_core_informational_unit("turn", [29, 31])
    state = mmf_state_from_metadata(unit, "verbal")
    assert state is not None
    assert state.valuation(29) == 1
    assert state.valuation(31) == 1


def test_all_mmf_states_from_metadata() -> None:
    unit = build_core_informational_unit("attachment", [61, 79], content_type="image/png")
    states = all_mmf_states_from_metadata(unit)
    assert "visual" in states
    assert states["visual"].valuation(61) == 1


# --- Distance ---------------------------------------------------------------


def test_information_unit_distance_is_zero_for_identical_units() -> None:
    unit = build_core_informational_unit("turn", [13, 23])
    assert information_unit_distance(unit, unit) == 0.0


def test_information_unit_distance_is_finite_when_primes_overlap() -> None:
    a = build_core_informational_unit("turn", [13, 23])
    b = build_core_informational_unit("turn", [13, 23, 29])
    distance = information_unit_distance(a, b)
    assert 0.0 < distance < float("inf")


def test_information_unit_distance_is_infinite_when_no_overlap() -> None:
    a = {"kernel_prime_exponents": {13: 1}}
    b = {"kernel_prime_exponents": {17: 1}}
    assert information_unit_distance(a, b) == float("inf")
