from backend.fieldx_kernel.kernel_divination import compute_configurational_foresight


def test_compute_configurational_foresight_is_advisory_only() -> None:
    result = compute_configurational_foresight(
        teleology_alignment=0.91,
        law_score=0.88,
        grace_score=0.84,
        drift=0.06,
        walk_assessment={
            "topology_health": 0.9,
            "stability": 0.8,
            "diversity": 0.7,
        },
    )

    assert result.get("quality") == "favourable"
    assert 0.0 <= float(result.get("advisory_score", 0.0)) <= 1.0
    assert result.get("advisory_only") is True
    assert result.get("veto_allowed") is False
    assert result.get("dominant_tension") in {"law", "grace", "balanced"}


def test_compute_configurational_foresight_tracks_law_grace_tension() -> None:
    result = compute_configurational_foresight(
        teleology_alignment=0.55,
        law_score=0.95,
        grace_score=0.30,
        drift=0.2,
        walk_assessment=None,
    )

    assert result.get("dominant_tension") == "law"
    assert float(result.get("law_grace_tension", 0.0)) > 0.5
    assert float(result.get("alpha_uncertainty", 0.0)) > 0.0
