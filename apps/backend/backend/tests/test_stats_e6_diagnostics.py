from backend.api.stats import _extract_e6_diagnostics


def test_extract_e6_diagnostics_from_event_payload() -> None:
    diag = _extract_e6_diagnostics(
        {
            "e6_mode": 1,
            "e6_route": 1,
            "e6_quality_tier": "probe",
            "e6_bridge_allowed": False,
            "e6_promotion_allowed": False,
            "e6_v_int_mean_3": 0.8,
            "e6_v_int_std_3": 0.02,
        }
    )
    assert isinstance(diag, dict)
    assert diag.get("mode") == 1
    assert diag.get("route") == 1
    assert diag.get("quality_tier") == "probe"
    assert diag.get("bridge_allowed") is False
    assert diag.get("promotion_allowed") is False
    assert diag.get("V_int_mean_3") == 0.8
    assert diag.get("V_int_std_3") == 0.02


def test_extract_e6_diagnostics_returns_none_without_e6_fields() -> None:
    assert _extract_e6_diagnostics({"provider": "llama3.2:latest"}) is None
