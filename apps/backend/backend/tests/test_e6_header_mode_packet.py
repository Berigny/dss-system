from backend.api.agent_writes import _build_e6_header_metadata


class _Pack:
    def __init__(self, metrics: dict):
        self.metrics = metrics


def test_e6_header_strict_mode_packet_follows_route_override(monkeypatch) -> None:
    monkeypatch.setenv("E6_MODE_PACKET_STRICT", "1")
    result = _build_e6_header_metadata(
        latest_meta={},
        metrics_pack=_Pack({"K": 1, "P": 1, "E": 1, "dW": 0, "V": 1.0}),
        governance_error=None,
        quality_tier="stabilise",
        eq6_lawfulness_level=2,
        flow_last_even=10,
        route_override=1,
    )
    fields = result.get("e6_header_v0_fields") or {}
    assert fields.get("route") == 1
    assert fields.get("mode") == 1
    assert fields.get("ptype") == 2


def test_e6_header_halt_forces_hr_block_even_with_route_override() -> None:
    result = _build_e6_header_metadata(
        latest_meta={},
        metrics_pack=_Pack({"K": 1, "P": 1, "E": 1, "dW": 0, "V": 1.0}),
        governance_error={"blocked": True, "reason": "test_block"},
        quality_tier="stabilise",
        eq6_lawfulness_level=2,
        flow_last_even=10,
        route_override=3,
    )
    fields = result.get("e6_header_v0_fields") or {}
    assert fields.get("route") == 0
    assert fields.get("mode") == 0
    assert fields.get("ptype") == 1


def test_e6_header_strict_mode_maps_route_ladder_0_to_3(monkeypatch) -> None:
    monkeypatch.setenv("E6_MODE_PACKET_STRICT", "1")
    expected = {
        0: (0, 1),
        1: (1, 2),
        2: (2, 3),
        3: (3, 0),
    }
    for route, (mode, ptype) in expected.items():
        result = _build_e6_header_metadata(
            latest_meta={},
            metrics_pack=_Pack({"K": 1, "P": 1, "E": 1, "dW": 0, "V": 1.0}),
            governance_error=None,
            quality_tier="stabilise",
            eq6_lawfulness_level=2,
            flow_last_even=10,
            route_override=route,
        )
        fields = result.get("e6_header_v0_fields") or {}
        assert fields.get("route") == route
        assert fields.get("mode") == mode
        assert fields.get("ptype") == ptype


def test_e6_header_hard_fail_from_express_drops_to_halt() -> None:
    result = _build_e6_header_metadata(
        latest_meta={},
        metrics_pack=_Pack({"K": 1, "P": 1, "E": 1, "dW": 0, "V": 1.0}),
        governance_error={"blocked": True, "reason": "genesis_ladder_blocked"},
        quality_tier="express",
        eq6_lawfulness_level=3,
        flow_last_even=10,
        route_override=3,
    )
    fields = result.get("e6_header_v0_fields") or {}
    assert fields.get("route") == 0
    assert fields.get("mode") == 0
    assert fields.get("ptype") == 1


def test_e6_mode_gating_strict_alias_enables_route_to_mode_mapping(monkeypatch) -> None:
    monkeypatch.setenv("E6_MODE_GATING_STRICT", "1")
    result = _build_e6_header_metadata(
        latest_meta={},
        metrics_pack=_Pack({"K": 1, "P": 1, "E": 1, "dW": 0, "V": 1.0}),
        governance_error=None,
        quality_tier="stabilise",
        eq6_lawfulness_level=2,
        flow_last_even=10,
        route_override=1,
    )
    fields = result.get("e6_header_v0_fields") or {}
    assert fields.get("mode") == 1
    assert fields.get("ptype") == 2
