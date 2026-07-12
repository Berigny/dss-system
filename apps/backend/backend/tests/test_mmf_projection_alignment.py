from __future__ import annotations

import pytest

from backend.fieldx_kernel.geometry.lattice import (
    Lattice,
    LatticePoint,
    mmf_projection_for_entry,
    phi_d,
    psi_d,
    validate_domain_isolation,
)
from backend.fieldx_kernel.informational_unit import MMF_DOMAINS
from backend.fieldx_kernel.p_adic import PrimeLatticeState


def test_lattice_origin_and_trace() -> None:
    lattice = Lattice(dimensions=3)
    origin = lattice.origin()
    assert origin.coordinates == (0, 0, 0)
    end = lattice.trace_path(origin, [1, 2, 3])
    assert end.coordinates == (1, 2, 3)


def test_mmf_domains_are_pairwise_disjoint() -> None:
    seen: set[int] = set()
    for domain, primes in MMF_DOMAINS.items():
        assert len(primes) == 8, domain
        assert len(set(primes)) == 8, domain
        for prime in primes:
            assert prime not in seen, f"prime {prime} appears in more than one MMF domain"
            seen.add(prime)


def test_mmf_projection_for_entry_turn_activates_verbal() -> None:
    token_primes = [29, 31, 43, 13, 23]
    projections = mmf_projection_for_entry("turn", token_primes)
    assert "verbal" in projections
    assert isinstance(projections["verbal"], PrimeLatticeState)
    assert projections["verbal"].valuation(29) == 1
    assert projections["verbal"].valuation(31) == 1
    assert projections["verbal"].valuation(43) == 1
    # Non-verbal primes are filtered out.
    for prime in (13, 23):
        assert projections["verbal"].valuation(prime) == 0


def test_mmf_projection_for_attachment_respects_content_type() -> None:
    visual_primes = [61, 67, 71]
    projections = mmf_projection_for_entry("attachment", visual_primes, content_type="image/png")
    assert set(projections) == {"visual"}
    assert projections["visual"].valuation(61) == 1

    auditory_primes = [101, 103, 107]
    projections = mmf_projection_for_entry("attachment", auditory_primes, content_type="audio/wav")
    assert set(projections) == {"auditory"}
    assert projections["auditory"].valuation(101) == 1


def test_mmf_projection_for_topic_uses_verbal_and_spatial() -> None:
    primes = [29, 181, 191]
    projections = mmf_projection_for_entry("topic", primes)
    assert "verbal" in projections
    assert "spatial" in projections
    assert projections["verbal"].valuation(29) == 1
    assert projections["spatial"].valuation(181) == 1
    assert projections["spatial"].valuation(191) == 1


def test_mmf_projection_for_event_activates_behavioral() -> None:
    primes = [229, 233]
    projections = mmf_projection_for_entry("event", primes)
    assert set(projections) == {"behavioral"}
    assert projections["behavioral"].valuation(229) == 1


def test_phi_d_projects_to_domain_cube() -> None:
    state = phi_d([29, 31, 61, 101], "verbal")
    assert state.valuation(29) == 1
    assert state.valuation(31) == 1
    assert state.valuation(61) == 0
    assert state.valuation(101) == 0


def test_phi_d_is_idempotent_via_psi_d_round_trip() -> None:
    domain = "visual"
    primes = [61, 67, 71, 79]
    projected = phi_d(primes, domain)
    exponents = psi_d(projected, domain)
    assert set(exponents) == set(primes)
    for prime in primes:
        assert exponents[prime] == 1


def test_validate_domain_isolation_passes_for_valid_projections() -> None:
    projections = {
        "verbal": {29: 1, 31: 1},
        "visual": {61: 1},
    }
    assert validate_domain_isolation(projections) is True


def test_validate_domain_isolation_fails_for_cross_domain_prime() -> None:
    projections = {
        "verbal": {29: 1},
        "visual": {29: 1},
    }
    assert validate_domain_isolation(projections) is False


def test_validate_domain_isolation_fails_for_unknown_domain_prime() -> None:
    projections = {
        "verbal": {999: 1},
    }
    assert validate_domain_isolation(projections) is False


def test_phi_d_rejects_unknown_domain() -> None:
    with pytest.raises(ValueError):
        phi_d([29], "tactile")


def test_psi_d_rejects_unknown_domain() -> None:
    state = PrimeLatticeState.from_primes([29])
    with pytest.raises(ValueError):
        psi_d(state, "tactile")
