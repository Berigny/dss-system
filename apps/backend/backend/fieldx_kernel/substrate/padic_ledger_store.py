"""P-adic ball ledger store backed by RocksDB or any ``MutableMapping[bytes, bytes]``.

The store persists each ledger state at every precision ``k = 1..N`` using the
key format ``padic:{namespace}:p={p}:k={k}:residue={a mod p^k}``.  This makes
p-adic ball retrieval a prefix scan: a coarser ball is a shorter key prefix.

To reduce write amplification, the payload is stored once under a canonical
payload key and each ball key holds a lightweight reference to that payload
key.

Code-level claims:

- CLAIM(definite): ``PAdicLedgerStore`` writes payload once and stores
  lightweight ball-reference keys at each precision level, so exact and graded
  nearest-ball retrieval are both supported with reduced write amplification.
  EVIDENCE: DSS-180, ``backend/fieldx_kernel/substrate/padic_ledger_store.py``

- CLAIM(definite): ``read`` requires an exact match at precision ``N``;
  ``nearest`` falls back through coarser balls until ``min_k`` and can return
  the ball radius / p-adic distance.
  EVIDENCE: DSS-180, tests in ``backend/tests/test_padic_ledger_store.py``
"""

from __future__ import annotations

import base64
import json
import math
from fractions import Fraction
from typing import Any, Iterable, Mapping, MutableMapping

from backend.fieldx_kernel.p_adic import PAdicInteger
from backend.fieldx_kernel.qp_arithmetic import QpElement
from backend.fieldx_kernel.qp_coordinate import (
    QpCoordinate,
    dual_state_compatible,
    qp_coordinate_distance,
)


class PAdicLedgerStore:
    """Store and retrieve ledger payloads via p-adic ball keys.

    The backing store must behave like ``MutableMapping[bytes, bytes]``.
    RocksDB and plain dictionaries both work.
    """

    def __init__(self, db: MutableMapping[bytes, bytes], p: int, N: int) -> None:
        self._db = db
        self._p = p
        self._N = N

    @property
    def p(self) -> int:
        return self._p

    @property
    def N(self) -> int:
        return self._N

    def _state_key(self, namespace: str, k: int, residue: int) -> bytes:
        return f"padic:{namespace}:p={self._p}:k={k}:residue={residue}".encode()

    def _payload_key(self, namespace: str, state: PAdicInteger) -> bytes:
        """Canonical key under which the payload is stored once."""
        return f"padic:{namespace}:p={self._p}:payload:residue={state.value_mod(self._N)}".encode()

    def _ball_prefix(self, namespace: str, k: int) -> bytes:
        return f"padic:{namespace}:p={self._p}:k={k}:".encode()

    def _validate_state(self, state: PAdicInteger) -> None:
        if state.p != self._p or state.N != self._N:
            raise ValueError(
                f"state must belong to p={self._p}, N={self._N}; "
                f"got p={state.p}, N={state.N}"
            )

    @staticmethod
    def _is_payload_key(value: bytes) -> bool:
        """Return True if ``value`` is a reference to a canonical payload key."""
        return (
            isinstance(value, (bytes, bytearray))
            and value.startswith(b"padic:")
            and b":payload:" in value
        )

    def _resolve_value(self, value: bytes | None) -> bytes | None:
        """Return the payload, resolving a payload-key reference if needed."""
        if value is None:
            return None
        if self._is_payload_key(value):
            return self._db.get(bytes(value))
        return value

    def write(self, namespace: str, state: PAdicInteger, payload: bytes) -> None:
        """Persist ``payload`` for ``state`` with one payload copy and ball refs."""
        self._validate_state(state)
        payload_key = self._payload_key(namespace, state)
        self._db[payload_key] = payload
        for k in range(1, self._N + 1):
            key = self._state_key(namespace, k, state.value_mod(k))
            self._db[key] = payload_key

    def read(self, namespace: str, state: PAdicInteger) -> bytes | None:
        """Return the payload for an exact match at full precision ``N``."""
        payload, _k, _distance = self.nearest_with_distance(
            namespace, state, min_k=self._N
        )
        return payload

    def nearest(
        self,
        namespace: str,
        query: PAdicInteger,
        min_k: int = 1,
    ) -> bytes | None:
        """Return the closest stored payload, falling back through coarser balls."""
        payload, _k, _distance = self.nearest_with_distance(
            namespace, query, min_k=min_k
        )
        return payload

    def nearest_with_distance(
        self,
        namespace: str,
        query: PAdicInteger,
        min_k: int = 1,
    ) -> tuple[bytes | None, int | None, float | None]:
        """Return ``(payload, k_found, p_adic_distance)`` for the nearest ball.

        Search order is ``N, N-1, ..., min_k``.  The first ball that contains
        a stored state wins.  If no ball matches, return ``(None, None, None)``.
        The distance is ``p ** (-k_found)``, i.e. the radius of the ball found.
        """
        self._validate_state(query)
        if not isinstance(min_k, int) or not 1 <= min_k <= self._N:
            raise ValueError(f"min_k must be in [1, {self._N}], got {min_k!r}")

        for k in range(self._N, min_k - 1, -1):
            key = self._state_key(namespace, k, query.value_mod(k))
            value = self._db.get(key)
            if value is not None:
                payload = self._resolve_value(value)
                if payload is not None:
                    distance = float(self._p) ** (-k)
                    return payload, k, distance
        return None, None, None

    def _iter_prefix(self, prefix: bytes) -> Iterable[bytes]:
        """Yield payloads whose keys start with ``prefix``.

        Uses the backing store's prefix iterator if available; otherwise falls
        back to a sorted in-memory scan.
        """
        iterator = getattr(self._db, "iteritems", None)
        if iterator is not None:
            try:
                for _key, value in iterator(prefix=prefix):
                    payload = self._resolve_value(value)
                    if payload is not None:
                        yield payload
                return
            except TypeError:
                pass

        for key, value in self._db.items():
            if isinstance(key, bytes) and key.startswith(prefix):
                payload = self._resolve_value(value)
                if payload is not None:
                    yield payload

    def ball_prefix_scan(
        self,
        namespace: str,
        k: int,
        residue: int | None = None,
    ) -> list[bytes]:
        """Return all payloads stored in the p-adic ball of radius ``p**(-k)``.

        If ``residue`` is supplied, restrict to the sub-ball around that
        specific residue modulo ``p**k``.
        """
        if not isinstance(k, int) or not 1 <= k <= self._N:
            raise ValueError(f"k must be in [1, {self._N}], got {k!r}")

        if residue is None:
            prefix = self._ball_prefix(namespace, k)
        else:
            prefix = self._state_key(namespace, k, residue)

        return list(self._iter_prefix(prefix))


class QpCoordinateStore:
    """True Qp ball store for ``QpCoordinate`` retrieval.

    Stores coordinates with rational centers and variable per-entry precision.
    Retrieval uses the genuine p-adic distance ``|query - center|_p`` rather than
    a ball-radius fallback, and supports dual-coordinate filtering for S1/S2
    queries.
    """

    PREFIX = "qp-coord:"

    def __init__(self, db: MutableMapping[bytes, bytes], default_N: int = 16) -> None:
        self._db = db
        self._default_N = default_N

    def _record_key(self, namespace: str, coordinate_id: str) -> bytes:
        return f"{self.PREFIX}{namespace}:id={coordinate_id}".encode()

    def _ball_key(self, namespace: str, p: int, k: int, residue: int) -> bytes:
        return f"{self.PREFIX}{namespace}:p={p}:k={k}:residue={residue}".encode()

    def _qp_from_coord(self, coord: QpCoordinate) -> QpElement:
        """Return the ``QpElement`` rational representative for ``coord``."""
        rep = coord.rational_representative
        if isinstance(rep, QpElement):
            return rep
        if isinstance(rep, Fraction):
            return QpElement.from_rational(
                coord.metric_prime,
                rep.numerator,
                rep.denominator,
                coord.working_precision,
            )
        raise ValueError("coordinate lacks a rational representative for ball storage")

    def _load_record(self, key: bytes) -> dict[str, Any] | None:
        """Load and parse a coordinate record."""
        raw = self._db.get(key)
        if raw is None:
            return None
        try:
            decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            payload = json.loads(decoded)
            return payload if isinstance(payload, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def write(self, namespace: str, coord: QpCoordinate, payload: bytes) -> None:
        """Persist ``coord`` and ``payload`` with a trie of p-adic ball keys.

        Ball keys are written at every precision ``k = 1..coord.working_precision``
        so nearest-neighbor search can fall back through coarser balls.
        """
        qp = self._qp_from_coord(coord)
        p = coord.metric_prime
        record_key = self._record_key(namespace, coord.coordinate_id)
        record = {
            "coordinate": coord.as_dict(),
            "payload": base64.b64encode(payload).decode(),
        }
        self._db[record_key] = json.dumps(record).encode()

        for k in range(1, coord.working_precision + 1):
            ball_key = self._ball_key(namespace, p, k, qp.value_mod(k))
            self._db[ball_key] = record_key

    def _iter_prefix(self, prefix: bytes) -> Iterable[bytes]:
        """Yield record keys whose ball keys start with ``prefix``."""
        iterator = getattr(self._db, "iteritems", None)
        if iterator is not None:
            try:
                for key, value in iterator(prefix=prefix):
                    if isinstance(key, bytes) and key.startswith(prefix):
                        yield value
                return
            except TypeError:
                pass

        for key, value in self._db.items():
            key_bytes = key.encode() if isinstance(key, str) else key
            value_bytes = value.encode() if isinstance(value, str) else value
            if key_bytes.startswith(prefix):
                yield value_bytes

    def _validate_dual_query(self, query: QpCoordinate) -> None:
        """Raise if an S1 query lacks the required dual state."""
        if query.tetrahedron == "S1" and query.dual_state is None:
            raise ValueError("S1 query requires a dual_state for dual-coordinate retrieval")

    def nearest(
        self,
        namespace: str,
        query: QpCoordinate,
        *,
        min_k: int = 1,
        require_dual: bool = False,
    ) -> tuple[bytes, float] | None:
        """Return the nearest stored payload and its true p-adic distance.

        Search walks the ball trie from the finest available precision down to
        ``min_k``, collecting candidates that share the query's residue at each
        level.  The candidate with the smallest genuine p-adic distance wins.
        If ``require_dual`` is true, candidates that are not dual-compatible with
        the query are discarded.
        """
        if query.tetrahedron == "S1":
            self._validate_dual_query(query)

        qp_query = self._qp_from_coord(query)
        p = query.metric_prime
        max_k = max(self._default_N, query.working_precision)
        if not isinstance(min_k, int) or not 1 <= min_k <= max_k:
            raise ValueError(f"min_k must be in [1, {max_k}], got {min_k!r}")

        seen: set[bytes] = set()
        best: tuple[bytes, float] | None = None

        for k in range(max_k, min_k - 1, -1):
            residue = qp_query.value_mod(k)
            prefix = self._ball_key(namespace, p, k, residue)
            for record_key in self._iter_prefix(prefix):
                if record_key in seen:
                    continue
                seen.add(record_key)
                record = self._load_record(record_key)
                if record is None:
                    continue
                candidate = QpCoordinate.from_dict(record["coordinate"])
                if require_dual and query.dual_state is not None:
                    if not dual_state_compatible(query, candidate):
                        continue
                distance = qp_coordinate_distance(query, candidate)
                if best is None or distance < best[1]:
                    payload = base64.b64decode(record["payload"])
                    best = (payload, distance)

        return best

    def contains(
        self,
        center: QpCoordinate,
        point: QpCoordinate,
        radius: float,
    ) -> bool:
        """Return True if ``point`` lies within ``radius`` of ``center``."""
        return qp_coordinate_distance(center, point) <= radius

    def overlap(
        self,
        center_a: QpCoordinate,
        radius_a: float,
        center_b: QpCoordinate,
        radius_b: float,
    ) -> bool:
        """Return True if two p-adic balls overlap.

        In an ultrametric space, two balls are either disjoint or one contains
        the other, so overlap holds when the distance between centers is not
        greater than the larger radius.
        """
        return qp_coordinate_distance(center_a, center_b) <= max(radius_a, radius_b)

    def ball_members(
        self,
        namespace: str,
        center: QpCoordinate,
        radius: float,
    ) -> list[tuple[bytes, float]]:
        """Return all stored payloads within ``radius`` of ``center``."""
        qp_center = self._qp_from_coord(center)
        p = center.metric_prime
        max_k = max(self._default_N, center.working_precision)
        members: list[tuple[bytes, float]] = []
        seen: set[bytes] = set()

        # Walk the trie from the precision implied by the radius up to max_k.
        k_min = 1
        if radius > 0:
            k_est = int(-math.log(radius, p)) if radius < 1 else 0
            k_min = max(1, min(max_k, k_est))

        for k in range(max_k, k_min - 1, -1):
            residue = qp_center.value_mod(k)
            prefix = self._ball_key(namespace, p, k, residue)
            for record_key in self._iter_prefix(prefix):
                if record_key in seen:
                    continue
                seen.add(record_key)
                record = self._load_record(record_key)
                if record is None:
                    continue
                candidate = QpCoordinate.from_dict(record["coordinate"])
                distance = qp_coordinate_distance(center, candidate)
                if distance <= radius:
                    payload = base64.b64decode(record["payload"])
                    members.append((payload, distance))

        return members
