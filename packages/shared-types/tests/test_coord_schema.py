"""Tests for shared_types.coord_schema."""

from __future__ import annotations

import json

import pytest

from shared_types.coord_schema import (
    JS_MAX_SAFE_INTEGER,
    Coordinate,
    LedgerEntrySchema,
    bigint_str,
    format_coordinate,
    normalize_coordinate_metadata,
    normalize_coordinate_payload,
    parse_bigint,
    sanitize_coordinate_metadata,
)


def test_coordinate_as_path() -> None:
    coord = Coordinate(namespace="chat", identifier="turn-123")
    assert coord.as_path() == "chat:turn-123"


def test_ledger_entry_schema() -> None:
    entry = LedgerEntrySchema(
        coord=Coordinate(namespace="chat", identifier="turn-123"),
        metadata={"foo": "bar"},
    )
    assert entry.coord.as_path() == "chat:turn-123"
    assert entry.metadata == {"foo": "bar"}


def test_format_coordinate_provided() -> None:
    display, value = format_coordinate(
        timestamp="2024-01-01T00:00:00Z",
        coordinate="chat:turn-123",
        message_id="msg-1",
        content="hello",
    )
    assert display == "01/01/2024 00:00"
    assert value == "chat:turn-123"


def test_format_coordinate_fallback() -> None:
    display, value = format_coordinate(
        timestamp=None,
        coordinate=None,
        message_id="msg-1",
        content="hello",
    )
    assert display == "unknown"
    assert value.startswith("msg-1:unknown:")


def test_normalize_coordinate_payload_v2() -> None:
    decoded = {
        "data": {
            "type": "web4",
            "skim": {"one_line": "summary text"},
            "interpretation": {"claims": [{"name": "claim-1"}]},
            "governance": {
                "appraisal": {"coherence": 0.9},
                "policy_version": "v1",
                "risk_class": "low",
                "claim_source": "inferred",
                "policy_decision": "allow",
            },
        }
    }
    normalized = normalize_coordinate_payload(decoded)
    assert normalized["type"] == "web4"
    assert normalized["summary"] == "summary text"
    assert normalized["coherence"] == 0.9
    assert normalized["claims"] == [{"name": "claim-1"}]
    assert normalized["governance_contract"]["policy_version"] == "v1"


def test_normalize_coordinate_payload_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        normalize_coordinate_payload("not-a-dict")


# ---------------------------------------------------------------------------
# BigInt-safe coordinate encoding
# ---------------------------------------------------------------------------


def test_bigint_str_round_trip() -> None:
    value = 10**500
    assert bigint_str(value) == "1" + "0" * 500
    assert parse_bigint(bigint_str(value)) == value


def test_parse_bigint_accepts_int_str_and_whole_float() -> None:
    assert parse_bigint(42) == 42
    assert parse_bigint("42") == 42
    assert parse_bigint(" 42 ") == 42
    assert parse_bigint(42.0) == 42


def test_parse_bigint_rejects_bad_inputs() -> None:
    with pytest.raises(TypeError):
        parse_bigint(True)
    with pytest.raises(ValueError):
        parse_bigint("abc")
    with pytest.raises(ValueError):
        parse_bigint(42.5)
    with pytest.raises(TypeError):
        parse_bigint(None)


def test_sanitize_coordinate_metadata_stringifies_known_keys() -> None:
    payload = {
        "prime_multiplicative_value": 2310,
        "token_prime_product": 7420738134810,
        "body_prime": 101,
        "other_int": 123,
    }
    sanitized = sanitize_coordinate_metadata(payload)
    assert sanitized["prime_multiplicative_value"] == "2310"
    assert sanitized["token_prime_product"] == "7420738134810"
    assert sanitized["body_prime"] == "101"
    assert sanitized["other_int"] == 123


def test_sanitize_coordinate_metadata_stringifies_unsafe_ints() -> None:
    big = JS_MAX_SAFE_INTEGER + 1
    payload = {"unrelated_counter": big}
    sanitized = sanitize_coordinate_metadata(payload)
    assert sanitized["unrelated_counter"] == str(big)


def test_sanitize_coordinate_metadata_leaves_bools_and_floats() -> None:
    payload = {"flag": True, "score": 0.75, "count": 5}
    sanitized = sanitize_coordinate_metadata(payload)
    assert sanitized["flag"] is True
    assert sanitized["score"] == 0.75
    assert sanitized["count"] == 5


def test_normalize_coordinate_metadata_parses_known_keys() -> None:
    payload = {
        "prime_multiplicative_value": "7420738134810",
        "token_prime_product": "2310",
        "body_prime": "101",
        "label": "123",
    }
    normalized = normalize_coordinate_metadata(payload)
    assert normalized["prime_multiplicative_value"] == 7420738134810
    assert normalized["token_prime_product"] == 2310
    assert normalized["body_prime"] == 101
    assert normalized["label"] == "123"


def test_bigint_round_trip_through_json() -> None:
    value = 10**500
    payload = {"token_prime_product": value}
    json_text = json.dumps(sanitize_coordinate_metadata(payload))
    restored = normalize_coordinate_metadata(json.loads(json_text))
    assert restored["token_prime_product"] == value


def test_large_prime_product_regression() -> None:
    """A 200-prime product must survive JSON round-trip as a string."""
    # First 200 primes (2 is the 1st).
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

    payload = {"prime_multiplicative_value": product}
    json_text = json.dumps(sanitize_coordinate_metadata(payload))
    # The JSON text must contain the decimal string, not a bare number.
    assert f'"{product}"' in json_text
    restored = normalize_coordinate_metadata(json.loads(json_text))
    assert restored["prime_multiplicative_value"] == product
