from __future__ import annotations

import pytest

from backend.fieldx_kernel.mmf_foundation import (
    CANONICAL_MMF_PRIME_TOPOLOGIES,
    MMF_CANONICAL_FACES,
    MMF_DOMAIN_PRIME_MAP,
    MMF_DOMAIN_EXTENSION_PRIME_MAP,
    MMF_DOMAIN_FACE_MAP,
    MMF_DOMAIN_SET,
    MMF_KERNEL_PRIME_MAP,
    KernelTopology,
    PacketBoundary,
    ProjectionEngine,
    build_mmf_prime_topologies,
    evaluate_e6_decision,
    mmf_required_extension_prime_count,
    validate_domain_extension_prime_map,
    validate_domain_face_map,
    validate_domain_set,
)


class _IdentityTransform:
    def phi_d(self, node_state):
        payload = dict(node_state)
        payload["projected"] = True
        return payload

    def psi_d(self, edge_flow):
        payload = dict(edge_flow)
        payload["projected"] = True
        return payload


def _make_engine() -> ProjectionEngine:
    return ProjectionEngine({domain: _IdentityTransform() for domain in MMF_DOMAIN_SET})


def test_validate_domain_set_requires_exact_six_and_known_values() -> None:
    assert set(validate_domain_set(MMF_DOMAIN_SET)) == set(MMF_DOMAIN_SET)

    with pytest.raises(ValueError, match="exactly six"):
        validate_domain_set(MMF_DOMAIN_SET[:-1])

    with pytest.raises(ValueError, match="duplicates"):
        validate_domain_set((MMF_DOMAIN_SET[0],) + MMF_DOMAIN_SET[:-1])

    with pytest.raises(ValueError, match="unknown"):
        validate_domain_set(MMF_DOMAIN_SET[:-1] + ("tactile",))


def test_projection_isomorphic_topology_is_preserved_across_domains() -> None:
    topology = KernelTopology(
        nodes=("S1-N0", "S1-N1", "S2-N0", "S2-N1"),
        edges=(("S1-N0", "S1-N1"), ("S2-N0", "S2-N1"), ("S1-N1", "S2-N0")),
    )
    node_state = {"S1-N0": {"v": 0.1}, "S1-N1": {"v": 0.2}, "S2-N0": {"v": 0.3}, "S2-N1": {"v": 0.4}}
    edge_flow = ({"a": "S1-N0", "b": "S1-N1", "w": 1.0},)

    engine = _make_engine()
    for domain in MMF_DOMAIN_SET:
        projection = engine.project(topology=topology, domain=domain, node_state=node_state, edge_flow=edge_flow)
        assert projection.nodes == topology.nodes
        assert projection.edges == topology.edges
        assert set(projection.node_state.keys()) == set(topology.nodes)
        assert all(value.get("projected") is True for value in projection.node_state.values())


def test_canonical_domain_face_mapping_is_complete_and_unique() -> None:
    normalized = validate_domain_face_map(MMF_DOMAIN_FACE_MAP)
    assert set(normalized.keys()) == set(MMF_DOMAIN_SET)
    assert len({frozenset(face) for face in normalized.values()}) == len(MMF_CANONICAL_FACES)


def test_face_mapping_rejects_non_canonical_or_duplicate_faces() -> None:
    duplicate_map = dict(MMF_DOMAIN_FACE_MAP)
    duplicate_map["visual"] = MMF_DOMAIN_FACE_MAP["verbal"]
    with pytest.raises(ValueError, match="duplicate face"):
        validate_domain_face_map(duplicate_map)

    invalid_map = dict(MMF_DOMAIN_FACE_MAP)
    invalid_map["visual"] = ("S1-N0", "S1-N1", "S2-N2", "S2-N0")
    with pytest.raises(ValueError, match="not a canonical"):
        validate_domain_face_map(invalid_map)


def test_domain_prime_count_is_48_for_six_indefeasible_domain_cubes() -> None:
    assert mmf_required_extension_prime_count(domains=6, anchors_per_domain=0, cube_size=8) == 48


def test_domain_primes_are_48_unique_and_disjoint_from_kernel() -> None:
    normalized = validate_domain_extension_prime_map(MMF_DOMAIN_PRIME_MAP, kernel_primes=MMF_KERNEL_PRIME_MAP)
    all_ext = [prime for ext in normalized.values() for prime in ext]

    assert len(all_ext) == 48
    assert len(set(all_ext)) == 48
    assert set(all_ext).isdisjoint(set(MMF_KERNEL_PRIME_MAP.values()))


def test_build_mmf_prime_topologies_produces_kernel_plus_six_domains() -> None:
    topologies = build_mmf_prime_topologies()
    assert len(topologies) == 7
    assert "kernel" in topologies
    assert set(MMF_DOMAIN_SET).issubset(topologies.keys())

    kernel = topologies["kernel"]
    assert len(kernel.cube_primes) == 8
    assert len(set(kernel.cube_primes)) == 8

    for domain in MMF_DOMAIN_SET:
        topo = topologies[domain]
        assert len(topo.anchor_nodes) == 4
        assert len(topo.anchor_primes) == 4
        assert len(topo.extension_primes) == 4
        assert len(topo.cube_primes) == 8
        assert set(topo.cube_primes).isdisjoint(set(kernel.cube_primes))
        assert set(topo.anchor_primes).issubset(set(topo.cube_primes))
        assert set(topo.extension_primes).issubset(set(topo.cube_primes))


def test_canonical_prime_topologies_are_frozen_and_indefeasible() -> None:
    assert CANONICAL_MMF_PRIME_TOPOLOGIES["kernel"].topology == "kernel"
    with pytest.raises(TypeError):
        CANONICAL_MMF_PRIME_TOPOLOGIES["kernel"] = CANONICAL_MMF_PRIME_TOPOLOGIES["kernel"]  # type: ignore[index]


def test_extension_prime_collision_is_rejected() -> None:
    bad = {domain: tuple(values) for domain, values in MMF_DOMAIN_EXTENSION_PRIME_MAP.items()}
    bad["visual"] = bad["visual"][:-1] + (bad["verbal"][0],)

    with pytest.raises(ValueError, match="globally unique"):
        validate_domain_extension_prime_map(bad, kernel_primes=MMF_KERNEL_PRIME_MAP)


def test_e6_deterministic_for_identical_inputs() -> None:
    first = evaluate_e6_decision(mode=2, K=1, P=1, E=1, V_q=52000, momentum_min=1000)
    second = evaluate_e6_decision(mode=2, K=1, P=1, E=1, V_q=52000, momentum_min=1000)
    assert first == second
    assert first["commit"] is True


@pytest.mark.parametrize(
    "gates",
    [
        {"K": 0, "P": 1, "E": 1},
        {"K": 1, "P": 0, "E": 1},
        {"K": 1, "P": 1, "E": 0},
    ],
)
def test_commit_denied_when_hard_gates_fail(gates) -> None:
    decision = evaluate_e6_decision(mode=3, V_q=64000, momentum_min=5000, **gates)
    assert decision["commit"] is False


def test_packet_boundary_roundtrip_and_crc_tamper_detection() -> None:
    packetizer = PacketBoundary(default_node=4)
    decision = evaluate_e6_decision(mode=3, K=1, P=1, E=1, V_q=61000, momentum_min=100)
    header = packetizer.pack(decision=decision, seq=77, t_ms=321, dW=1)
    parsed = packetizer.unpack(header)

    assert parsed["mode"] == 3
    assert parsed["route"] == 3
    assert parsed["K"] == 1
    assert parsed["P"] == 1
    assert parsed["E"] == 1
    assert parsed["crc_ok"] is True

    tampered = bytearray(header)
    tampered[8] ^= 0x01
    parsed_tampered = packetizer.unpack(bytes(tampered))
    assert parsed_tampered["crc_ok"] is False
