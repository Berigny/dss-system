from backend.utils.resolve_format import resolve_response
from shared_types.coord_schema import parse_bigint


def test_resolve_response_exposes_prime_and_taxonomy_metadata() -> None:
    response = resolve_response(
        coord="chat-demo:WX-123",
        metadata={
            "content": "prime aware",
            "token_prime_product": 7420738134810,
            "body_prime": 101,
            "token_primes": [29, 31, 37],
            "taxonomy_topology_ref": "visual",
            "taxonomy_mode": "indefeasible",
            "configurational_foresight": {
                "quality": "favourable",
                "advisory_score": 0.82,
                "advisory_only": True,
                "veto_allowed": False,
            },
        },
        payload={"segments": [], "blobs": {}},
        refs={"inputs": [], "evidence": [], "context": [], "overlays": [], "governance": [], "walk_traces": [], "web4": []},
        walk=None,
        interpretation={"topics": [], "claims": [], "tags": []},
        governance={"appraisal": {}},
        meta={"namespace_used": "chat-demo", "identifier": "WX-123"},
    )

    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    # Coordinate scalars are emitted as BigInt-safe decimal strings.
    assert parse_bigint(meta.get("prime_multiplicative_value")) == 7420738134810
    assert parse_bigint(meta.get("body_prime")) == 101
    assert meta.get("token_primes") == [29, 31, 37]
    assert meta.get("taxonomy_topology_ref") == "visual"
    assert meta.get("taxonomy_mode") == "indefeasible"
    assert meta.get("configurational_foresight", {}).get("advisory_score") == 0.82


def test_resolve_response_falls_back_to_taxonomy_provenance_and_token_prime_product() -> None:
    response = resolve_response(
        coord="chat-demo:WX-456",
        metadata={
            "content": "prime aware",
            "body_prime": 103,
            "token_primes": [41, 43],
            "taxonomy_provenance": {
                "topology_ref": "kernel",
                "taxonomy_mode": "indefeasible",
            },
        },
        payload={"segments": [], "blobs": {}},
        refs={"inputs": [], "evidence": [], "context": [], "overlays": [], "governance": [], "walk_traces": [], "web4": []},
        walk=None,
        interpretation={"topics": [], "claims": [], "tags": []},
        governance={"appraisal": {}},
        meta={"namespace_used": "chat-demo", "identifier": "WX-456"},
    )

    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    assert parse_bigint(meta.get("prime_multiplicative_value")) == 1763
    assert parse_bigint(meta.get("body_prime")) == 103
    assert meta.get("token_primes") == [41, 43]
    assert meta.get("taxonomy_topology_ref") == "kernel"
    assert meta.get("taxonomy_mode") == "indefeasible"


def test_resolve_response_stringifies_large_prime_products() -> None:
    """A 200+ prime product must be emitted as a string, not a bare JSON int."""
    primes = [
        2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
        73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151, 157,
        163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233, 239, 241,
        251, 257, 263, 269, 271, 277, 281, 283, 293, 307, 311, 313, 317, 331, 337, 347,
        349, 353, 359, 367, 373, 379, 383, 389, 397, 401, 409, 419, 421, 431, 433, 439,
        443, 449, 457, 461, 463, 467, 479, 487, 491, 499, 503, 509, 521, 523, 541, 547,
        557, 563, 569, 571, 577, 587, 593, 599, 601, 607, 613, 617, 619, 631, 641, 643,
        647, 653, 659, 661, 673, 677, 683, 691, 701, 709, 719, 727, 733, 739, 743, 751,
        757, 761, 769, 773, 787, 797, 809, 811, 821, 823, 827, 829, 839, 853, 857, 859,
        863, 877, 881, 883, 887, 907, 911, 919, 929, 937, 941, 947, 953, 967, 971, 977,
        983, 991, 997, 1009, 1013, 1019, 1021, 1031, 1033, 1039, 1049, 1051, 1061, 1063,
        1069, 1087, 1091, 1093, 1097, 1103, 1109, 1117, 1123, 1129, 1151, 1153, 1163,
        1171, 1181, 1187, 1193, 1201, 1213, 1217, 1223,
    ]
    assert len(primes) == 200
    product = 1
    for p in primes:
        product *= p

    response = resolve_response(
        coord="chat-demo:BIG",
        metadata={
            "content": "big prime lattice",
            "token_prime_product": product,
            "body_prime": 1223,
            "token_primes": primes,
        },
        payload={"segments": [], "blobs": {}},
        refs={"inputs": [], "evidence": [], "context": [], "overlays": [], "governance": [], "walk_traces": [], "web4": []},
        walk=None,
        interpretation={"topics": [], "claims": [], "tags": []},
        governance={"appraisal": {}},
        meta={"namespace_used": "chat-demo", "identifier": "BIG"},
    )

    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    value = meta.get("prime_multiplicative_value")
    assert isinstance(value, str), "large prime_multiplicative_value must be emitted as a string"
    assert parse_bigint(value) == product
    assert meta.get("token_primes") == primes
