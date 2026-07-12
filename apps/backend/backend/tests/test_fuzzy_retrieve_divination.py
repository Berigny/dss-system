from backend.retrieval.fuzzy_retrieve import _configurational_foresight_score


def test_configurational_foresight_score_reads_advisory_score() -> None:
    metadata = {
        "configurational_foresight": {
            "advisory_score": 0.73,
            "advisory_only": True,
            "veto_allowed": False,
        }
    }

    assert _configurational_foresight_score(metadata) == 0.73


def test_configurational_foresight_score_clamps_invalid_values() -> None:
    assert _configurational_foresight_score({"configurational_foresight": {"advisory_score": 3.2}}) == 1.0
    assert _configurational_foresight_score({"configurational_foresight": {"advisory_score": -1.0}}) == 0.0
    assert _configurational_foresight_score({}) == 0.0
