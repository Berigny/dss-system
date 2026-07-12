"""Tests for the genuine Qp ball store (DS-REVIEW-193 P2-03)."""

from __future__ import annotations

import base64

import pytest

from backend.fieldx_kernel.qp_arithmetic import QpElement
from backend.fieldx_kernel.qp_coordinate import (
    DigitSymbol,
    QpCoordinate,
    _DUAL_COMPLEMENT,
    _TETRAHEDRON,
    _coordinate_hash,
)
from backend.fieldx_kernel.substrate.padic_ledger_store import QpCoordinateStore


def _make_coord(
    p: int,
    value: int | tuple[int, int],
    kernel_node: str = "Eq6",
    *,
    working_precision: int = 8,
    dual_state: QpCoordinate | None = None,
) -> QpCoordinate:
    """Build a valid QpCoordinate from an integer or rational value.

    Symbolic unit digits are taken from the QpElement's base-p digits.  This
    helper only works cleanly for primes p <= 10 because DigitSymbol only
    covers 0-9.
    """
    if isinstance(value, tuple):
        num, den = value
    else:
        num, den = value, 1
    qp = QpElement.from_rational(p, num, den, working_precision)
    unit_digits = tuple(DigitSymbol(d) for d in qp.unit_digits)
    valuation_offset = len(unit_digits)
    coordinate_id = _coordinate_hash(p, valuation_offset, unit_digits)
    return QpCoordinate(
        coordinate_id=coordinate_id,
        kernel_node=kernel_node,
        metric_prime=p,
        tetrahedron=_TETRAHEDRON[kernel_node],
        dual_complement=_DUAL_COMPLEMENT[kernel_node],
        unit_digits=unit_digits,
        valuation_offset=valuation_offset,
        working_precision=working_precision,
        rational_representative=qp,
        dual_state=dual_state,
    )


class TestQpCoordinateStore:
    def test_write_and_read_back_payload(self):
        db: dict[bytes, bytes] = {}
        store = QpCoordinateStore(db)
        coord = _make_coord(5, 42, kernel_node="Eq6")
        store.write("ns", coord, b"answer")
        record = store._load_record(store._record_key("ns", coord.coordinate_id))
        assert record is not None
        assert record["payload"] == base64.b64encode(b"answer").decode()

    def test_nearest_returns_exact_match(self):
        db: dict[bytes, bytes] = {}
        store = QpCoordinateStore(db)
        coord = _make_coord(5, 42, kernel_node="Eq6")
        store.write("ns", coord, b"exact")

        result = store.nearest("ns", coord)
        assert result is not None
        assert result[0] == b"exact"
        assert result[1] == 0.0

    def test_nearest_distance_increases_with_separation(self):
        db: dict[bytes, bytes] = {}
        store = QpCoordinateStore(db)
        c0 = _make_coord(5, 0, kernel_node="Eq6")
        c1 = _make_coord(5, 1, kernel_node="Eq6")
        c10 = _make_coord(5, 10, kernel_node="Eq6")
        store.write("ns", c0, b"zero")
        store.write("ns", c1, b"one")

        result = store.nearest("ns", c10)
        assert result is not None
        # 10 is closer to 0 (distance 1/5) than to 1 (distance 1).
        assert result[0] == b"zero"
        assert result[1] == pytest.approx(0.2)

    def test_nearest_requires_dual_for_s1(self):
        db: dict[bytes, bytes] = {}
        store = QpCoordinateStore(db)
        query = _make_coord(5, 0, kernel_node="Eq2")
        with pytest.raises(ValueError, match="dual_state"):
            store.nearest("ns", query)

    def test_dual_filter_rejects_incompatible(self):
        db: dict[bytes, bytes] = {}
        store = QpCoordinateStore(db)

        dual_q = _make_coord(5, 0, kernel_node="Eq6")
        query = _make_coord(5, 0, kernel_node="Eq2", dual_state=dual_q)

        # Candidate with matching dual.
        dual_c_match = _make_coord(5, 0, kernel_node="Eq6")
        candidate_match = _make_coord(5, 0, kernel_node="Eq2", dual_state=dual_c_match)

        # Candidate with mismatched dual kernel node (Eq7 instead of Eq6).
        dual_c_bad = _make_coord(5, 0, kernel_node="Eq7")
        candidate_bad = _make_coord(5, 1, kernel_node="Eq2", dual_state=dual_c_bad)

        store.write("ns", candidate_match, b"match")
        store.write("ns", candidate_bad, b"bad")

        result = store.nearest("ns", query, require_dual=True)
        assert result is not None
        assert result[0] == b"match"

    def test_ball_contains(self):
        db: dict[bytes, bytes] = {}
        store = QpCoordinateStore(db)
        center = _make_coord(5, 0, kernel_node="Eq6")
        inside = _make_coord(5, 10, kernel_node="Eq6")
        outside = _make_coord(5, 1, kernel_node="Eq6")
        assert store.contains(center, inside, 0.2) is True
        assert store.contains(center, outside, 0.2) is False

    def test_ball_overlap(self):
        db: dict[bytes, bytes] = {}
        store = QpCoordinateStore(db)
        a = _make_coord(5, 0, kernel_node="Eq6")
        b = _make_coord(5, 10, kernel_node="Eq6")
        # Balls of radius 1/5 around 0 and 10 overlap (distance 1/5).
        assert store.overlap(a, 0.2, b, 0.2) is True
        # Tiny balls around 0 and 1 do not overlap (distance 1).
        c = _make_coord(5, 1, kernel_node="Eq6")
        assert store.overlap(a, 0.01, c, 0.01) is False

    def test_ball_members(self):
        db: dict[bytes, bytes] = {}
        store = QpCoordinateStore(db)
        center = _make_coord(5, 0, kernel_node="Eq6")
        inside = _make_coord(5, 10, kernel_node="Eq6")
        outside = _make_coord(5, 1, kernel_node="Eq6")
        store.write("ns", inside, b"inside")
        store.write("ns", outside, b"outside")

        members = store.ball_members("ns", center, 0.2)
        assert len(members) == 1
        assert members[0][0] == b"inside"

import json
import math
from fractions import Fraction

from shared_types.coord_schema import parse_bigint


def test_persist_coordinate_with_bigint_fraction_representative() -> None:
    """QpCoordinateStore must preserve a 200-prime rational representative."""
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
    numerator = math.prod(primes)
    denominator = math.prod(primes[:100])
    fraction = Fraction(numerator, denominator)

    p = 5
    coordinate_id = _coordinate_hash(p, 0, ())
    coord = QpCoordinate(
        coordinate_id=coordinate_id,
        kernel_node="Eq0",
        metric_prime=p,
        tetrahedron="S1",
        dual_complement="Eq4",
        unit_digits=(),
        valuation_offset=0,
        working_precision=16,
        rational_representative=fraction,
        circulation_pass=0,
        sealed=True,
    )

    db: dict[bytes, bytes] = {}
    store = QpCoordinateStore(db)
    store.write("ns", coord, b"big-coord-payload")

    record = store._load_record(store._record_key("ns", coord.coordinate_id))
    assert record is not None
    raw_json = json.dumps(record["coordinate"])
    assert f'"{fraction.numerator}"' in raw_json
    assert f'"{fraction.denominator}"' in raw_json

    restored = QpCoordinate.from_dict(record["coordinate"])
    assert restored.rational_representative == fraction
    assert restored.metric_prime == p
